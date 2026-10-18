#!/usr/bin/env python3
"""The Pi's uplink process (issue #22 step 1). Stdlib only.

Everything that talks to the network lives here, at a lower QNX priority than the safety
process: event delivery, heartbeats, command polls and clip fetches for speech-to-text.
It is allowed to hang or die. The supervisor restarts it, and the safety process resends
its outbox when it reconnects.

It signs and verifies nothing. Each signature comes from the safety process over the local
link (edge/link.py), and every command it fetches goes back there to be verified.

Started by edge/supervisor.py:
    python3 edge/uplink.py --socket edge/logs/uplink.sock --backend http://<mac>:8002
"""

import argparse
import logging
import queue
import select
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge import config  # noqa: E402
from edge.link import LineConn, LinkClosed  # noqa: E402
from edge.sensor_node import Transcriber, UrlHttp  # noqa: E402

log = logging.getLogger("firsthour.uplink")


class Uplink:
    def __init__(self, *, socket_path, http, device_id, stt=None, clock=time.time,
                 poll_s=config.POLL_S, heartbeat_s=config.HEARTBEAT_S, backoff_s=config.OFFLINE_BACKOFF_S,
                 audio_timeout_s=config.AUDIO_FETCH_TIMEOUT_S, reply_timeout_s=1.0):
        self.socket_path = str(socket_path)
        self.http = http
        self.device_id = device_id
        self.stt = stt  # wav bytes -> text; None = speech-to-text off on this Pi
        self._clock = clock
        self.poll_s, self.heartbeat_s, self.backoff_s = poll_s, heartbeat_s, backoff_s
        self.audio_timeout_s = audio_timeout_s
        self.reply_timeout_s = reply_timeout_s
        self.conn = None
        self.events = []  # signed events from the safety process, oldest first
        self._replies = {}
        self.online = None
        self._net_ok_at = 0.0
        self._last_poll = self._last_hb = -1e18
        self._stt_jobs = queue.Queue(maxsize=2)
        self._stop = threading.Event()

    # link to the safety process -------------------------------------------------

    def connect(self) -> bool:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            return False
        self.conn = LineConn(sock)
        self.events, self._replies = [], {}
        log.info("connected to the safety process")
        return True

    def _drop(self, why) -> None:
        log.warning("safety process link lost (%s); reconnecting", why)
        if self.conn is not None:
            self.conn.close()
        self.conn = None
        self.events = []

    def pump(self, timeout: float) -> None:
        """Take in what the safety process sent. Raises LinkClosed if it is gone."""
        select.select([self.conn], [], [], timeout)
        for msg in self.conn.read():
            kind = msg.get("t")
            if kind == "event":
                if all(e["sig"] != msg.get("sig") for e in self.events):
                    self.events.append({"raw": msg["raw"], "sig": msg["sig"]})
            elif kind == "transcribe":
                try:
                    self._stt_jobs.put_nowait(msg)
                except queue.Full:
                    self._send_speech(msg.get("clip_id"), {"error": "speech-to-text busy"})
            elif kind in ("poll", "heartbeat"):
                self._replies[kind] = msg
        self.conn.flush()

    def _request(self, kind: str) -> dict:
        self._replies.pop(kind, None)
        self.conn.send({"t": kind})
        deadline = time.monotonic() + self.reply_timeout_s
        while kind not in self._replies:
            left = deadline - time.monotonic()
            if left <= 0:
                raise LinkClosed(f"safety process did not sign {kind}")
            self.pump(left)
        return self._replies.pop(kind)

    # backend --------------------------------------------------------------------

    def step(self, now: float) -> None:
        """Deliver events, heartbeat, poll. Same order and rules as Node.network_step."""
        if now < self._net_ok_at:
            return
        try:
            while self.events:
                item = self.events[0]
                status, _ = self.http.post("/edge/event", item["raw"].encode(), {"X-Signature": item["sig"]})
                if status >= 500:
                    raise OSError(f"backend {status}")
                if status != 200:
                    # 401/422 will never succeed on retry (wrong secret, bad body). Drop and shout.
                    log.error("event rejected status=%s event=%s", status, item["raw"][:120])
                self.events.pop(0)
                self.conn.send({"t": "ack", "sig": item["sig"]})
            if now - self._last_hb >= self.heartbeat_s:
                hb = self._request("heartbeat")
                status, _ = self.http.post("/edge/event", hb["raw"].encode(), {"X-Signature": hb["sig"]})
                if status >= 500:
                    raise OSError(f"backend {status}")
                self._last_hb = now
            if now - self._last_poll >= self.poll_s:
                poll = self._request("poll")
                status, body = self.http.get(f"/edge/commands?device_id={self.device_id}&ts={poll['ts']}",
                                             {"X-Signature": poll["sig"]})
                if status >= 500:
                    raise OSError(f"backend {status}")
                self._last_poll = now
                if status != 200:
                    log.error("command poll rejected status=%s (wrong SHARED_SECRET, or the Pi clock is >10 s off)",
                              status)
                commands = (body or {}).get("commands", []) if status == 200 else []
                if commands:
                    self.conn.send({"t": "commands", "commands": commands})
            self._set_online(True)
        except OSError as e:
            self._set_online(False, e)
            self._net_ok_at = now + self.backoff_s

    def _set_online(self, online, err=None) -> None:
        if online != self.online:
            if online:
                log.info("backend reachable")
            else:
                log.warning("backend unreachable (%s); safety process keeps running, events=%d",
                            err, len(self.events))
            self.online = online

    # speech-to-text -------------------------------------------------------------

    def stt_step(self, timeout: float = 0.0) -> None:
        """Fetch one dispatched clip with the safety process's signature and run whisper."""
        try:
            job = self._stt_jobs.get(timeout=timeout) if timeout else self._stt_jobs.get_nowait()
        except queue.Empty:
            return
        clip_id, result = job.get("clip_id"), {}
        try:
            if self.stt is None:
                raise RuntimeError("speech-to-text is off on the Pi")
            status, wav = self.http.get_bytes(
                f"/edge/audio/{clip_id}?device_id={self.device_id}&ts={job['ts']}",
                {"X-Signature": job["sig"]}, self.audio_timeout_s)
            if status != 200:
                raise RuntimeError(f"clip fetch refused status={status}")
            result["audio_ms"] = max(0, len(wav) - 44) // 32  # 16 kHz mono 16-bit
            t0 = time.monotonic()
            result["text"] = self.stt(wav)
            result["stt_ms"] = int((time.monotonic() - t0) * 1000)
        except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as e:
            result["error"] = str(e)[:200] or type(e).__name__
        # The words are the person's answer: log only timings and length.
        log.info("speech-to-text clip=%s audio_ms=%s stt_ms=%s words=%d error=%s", clip_id,
                 result.get("audio_ms"), result.get("stt_ms"), len(result.get("text", "").split()),
                 result.get("error"))
        self._send_speech(clip_id, result)

    def _send_speech(self, clip_id, result) -> None:
        conn = self.conn
        if conn is None:
            log.warning("speech result lost: safety process link down clip=%s", clip_id)
            return
        try:
            conn.send({"t": "speech", "clip_id": clip_id, "result": result})
        except LinkClosed as e:
            log.warning("speech result lost (%s) clip=%s", e, clip_id)

    # main loop ------------------------------------------------------------------

    def run(self) -> None:
        def run_stt():
            while not self._stop.is_set():
                try:
                    self.stt_step(timeout=0.5)
                except Exception:
                    log.exception("speech-to-text step failed")
        threading.Thread(target=run_stt, daemon=True, name="uplink-stt").start()
        while not self._stop.is_set():
            if self.conn is None and not self.connect():
                self._stop.wait(0.5)
                continue
            try:
                self.pump(0.05)
                self.step(self._clock())
            except LinkClosed as e:
                self._drop(e)

    def stop(self) -> None:
        self._stop.set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", required=True, help="the safety process's link (sensor_node.py --uplink-socket)")
    ap.add_argument("--backend", default=config.BACKEND_URL)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    stt = None
    if config.WHISPER_MODEL:
        if Path(config.WHISPER_MODEL).is_file() and shutil.which(config.WHISPER_BIN):
            # The supervisor runs whisper-server; whisper-cli is the fallback while it loads.
            stt = Transcriber(config.WHISPER_MODEL, external_server=True)
        else:
            log.error("WHISPER_MODEL or WHISPER_BIN not found; speech-to-text off")
    uplink = Uplink(socket_path=args.socket, http=UrlHttp(args.backend, config.HTTP_TIMEOUT_S),
                    device_id=config.DEVICE_ID, stt=stt)
    signal.signal(signal.SIGTERM, lambda signum, frame: uplink.stop())
    log.info("uplink up backend=%s speech_to_text=%s", args.backend, "on" if stt else "off")
    try:
        uplink.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
