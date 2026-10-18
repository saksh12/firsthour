#!/usr/bin/env python3
"""The Pi's safety node (IMPLEMENTATION.md sections 6.1-6.3, 9.3, 10). Stdlib only.

Reads the Arduino's serial lines, runs two-sensor fusion, owns the local incident
(buzzer + TIME DOWN clock), posts signed events to the backend,
and polls and verifies signed commands.

Rule: safety never waits on the network. handle_line() and tick() only touch the Arduino
and local state; all HTTP runs on its own thread (network_step), with a short timeout and
an offline backoff, and hands commands back through a queue.

Run on the laptop with fake sensors:
    python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin
On the Pi:
    sudo ~/fh-venv/bin/python edge/sensor_node.py --usb   # QNX 8: no USB-serial driver, raw libusb

With --uplink-socket this is only the safety process: HTTP and speech-to-text move to
edge/uplink.py at a lower priority, started together by edge/supervisor.py (IMPLEMENTATION.md 6.10).
"""

import argparse
import json
import logging
import math
import os
import queue
import select
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge import config  # noqa: E402
from edge.security import CommandVerifier, sign_audio, sign_event, sign_poll  # noqa: E402

log = logging.getLogger("firsthour.edge")


# --- serial protocol (section 6.1) ---------------------------------------------


def parse_line(line):
    """Arduino -> Pi line to a dict, or None for anything malformed (never crash on noise)."""
    parts = line.strip().split(",")
    kind = parts[0]
    try:
        if kind == "S" and len(parts) == 7:
            return {"kind": "S", "ms": int(parts[1]), "sound": int(float(parts[2])),
                    "ax": float(parts[3]), "ay": float(parts[4]), "az": float(parts[5]),
                    "dist": int(float(parts[6]))}
        if kind == "B" and len(parts) == 1:
            return {"kind": "B"}
        if kind == "ACK" and len(parts) == 2:
            return {"kind": "ACK", "cmd": parts[1]}
    except ValueError:
        pass
    return None


# --- fusion (section 10.1) -----------------------------------------------------


class AccelDelta:
    """How far the acceleration magnitude jumps from its slow-moving baseline, in g."""

    def __init__(self, alpha=0.02):
        self.alpha = alpha
        self.baseline = None

    def update(self, ax, ay, az):
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if self.baseline is None:
            self.baseline = mag
        delta = abs(mag - self.baseline)
        self.baseline = self.baseline * (1 - self.alpha) + mag * self.alpha
        return delta


class Fusion:
    """A fall is suspected when sound and vibration both spike within window_ms.

    Fires once per event, then ignores everything for cooldown_ms so one fall
    can't trigger twice. Timestamps are the Arduino's millis().
    """

    def __init__(self, sound_t, accel_t, window_ms=400, cooldown_ms=30_000):
        self.sound_t, self.accel_t = sound_t, accel_t
        self.window_ms, self.cooldown_ms = window_ms, cooldown_ms
        self._reset()
        self.cooldown_until = None

    def _reset(self):
        self.last_sound = self.last_accel = None
        self.last_ms = None

    def update(self, ms, sound_peak, accel_delta_g):
        if self.last_ms is not None and ms < self.last_ms - 1000:
            # millis() went backwards: the Arduino reset (opening the port can do that).
            log.warning("arduino clock reset detected, clearing fusion state")
            self._reset()
            self.cooldown_until = None
        self.last_ms = ms
        if self.cooldown_until is not None and ms < self.cooldown_until:
            return False
        if sound_peak > self.sound_t:
            self.last_sound = ms
        if accel_delta_g > self.accel_t:
            self.last_accel = ms
        if (self.last_sound is not None and self.last_accel is not None
                and abs(self.last_sound - self.last_accel) <= self.window_ms):
            self.last_sound = self.last_accel = None
            self.cooldown_until = ms + self.cooldown_ms
            return True
        return False


# --- offline outbox (section 10.3) ---------------------------------------------


class Outbox:
    """Signed events waiting for the backend, persisted so a Pi restart doesn't lose them."""

    def __init__(self, path):
        self.path = Path(path)
        self.items = []
        # The sensor loop adds and the network thread pops.
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self.items.append(json.loads(line))

    def add(self, item):
        with self._lock:
            self.items.append(item)
            self._save()

    def peek(self):
        with self._lock:
            return self.items[0] if self.items else None

    def pop(self):
        with self._lock:
            item = self.items.pop(0)
            self._save()
            return item

    def remove(self, sig) -> bool:
        """Drop the event with this signature (delivered by the uplink process)."""
        with self._lock:
            for i, item in enumerate(self.items):
                if item["sig"] == sig:
                    del self.items[i]
                    self._save()
                    return True
            return False

    def __len__(self):
        return len(self.items)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(i) + "\n" for i in self.items))
        tmp.replace(self.path)


# --- HTTP ----------------------------------------------------------------------


class UrlHttp:
    """Minimal client. Raises OSError when the backend is unreachable."""

    def __init__(self, base, timeout):
        self.base, self.timeout = base.rstrip("/"), timeout

    def _do(self, req):
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None
        except ValueError as e:
            # Not our backend (e.g. a captive portal's HTML): treat it as offline, never crash.
            raise OSError(f"bad response: {e}") from e

    def post(self, path, raw, headers):
        return self._do(urllib.request.Request(self.base + path, data=raw, method="POST",
                                               headers={"Content-Type": "application/json", **headers}))

    def get(self, path, headers=None):
        return self._do(urllib.request.Request(self.base + path, headers=headers or {}))

    def get_bytes(self, path, headers, timeout):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=headers),
                                        timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""


# --- speech-to-text (whisper.cpp, the oss.qnx.com AI module) ------------------------


class Transcriber:
    """whisper.cpp on one WAV clip, always in another process at a lower QNX priority than
    the reader, so transcription can never delay the alarm, clock or unlock checks.

    A resident whisper-server (loopback only) keeps the model loaded; whisper-cli is the
    fallback while it starts or if it dies. Measured on the Pi 5, 3 threads, beam search, live
    answers: whisper-server 2.7-3.1 s with base.en, 1.2-1.6 s with tiny.en; whisper-cli ~3.5 s
    with tiny.en (2 s is loading)."""

    # Beam search for accuracy, no temperature fallback: on noise, fallback retries ran past
    # 30 s on the Pi; without it a noise clip took 6 s and came back as no words.
    CLI_FLAGS = ["-nt", "-np", "-l", "en", "-bs", "5", "-bo", "5", "-nf"]
    SERVER_FIELDS = {"response_format": "json", "temperature": "0", "temperature_inc": "0",
                     "beam_size": "5", "best_of": "5"}

    def __init__(self, model, binary=config.WHISPER_BIN, server_binary=config.WHISPER_SERVER_BIN,
                 threads=config.WHISPER_THREADS, priority=config.WHISPER_PRIORITY,
                 timeout_s=config.WHISPER_TIMEOUT_S, port=config.WHISPER_PORT, external_server=False):
        """external_server: the supervisor runs whisper-server; this object only calls it."""
        self.timeout_s = timeout_s
        self.external_server = external_server
        if sys.platform.startswith("qnx") and shutil.which("on"):
            prefix = ["on", "-p", str(priority)]
        elif shutil.which("nice"):
            prefix = ["nice", "-n", "10"]
        else:
            prefix = []
        common = ["-m", str(model), "-t", str(threads)]
        self.cmd = prefix + [binary, *common, *self.CLI_FLAGS]
        self.server_cmd = (prefix + [server_binary, *common, "-l", "en", "-nt",
                                     "--host", "127.0.0.1", "--port", str(port)]
                           if server_binary and shutil.which(server_binary) else None)
        self.url = f"http://127.0.0.1:{port}/inference"
        self._server = None
        self._restart_at = 0.0

    def start(self) -> None:
        """Start (or restart) the resident server. Never blocks on the model loading."""
        if self.external_server or self.server_cmd is None or time.monotonic() < self._restart_at:
            return
        if self._server is not None and self._server.poll() is None:
            return
        if self._server is not None:
            log.warning("whisper-server exited status=%s; restarting", self._server.returncode)
        self._restart_at = time.monotonic() + 10
        # Its own process group, so close() also stops what `on` started. Output discarded:
        # it would include the person's words.
        self._server = subprocess.Popen(self.server_cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, start_new_session=True)

    def close(self) -> None:
        if self._server is not None and self._server.poll() is None:
            try:
                os.killpg(self._server.pid, 15)
            except OSError:
                self._server.kill()
            self._server.wait()

    @staticmethod
    def audio_ctx(wav_bytes: int) -> int:
        frames = (max(0, wav_bytes - 44) // 32 + 19) // 20  # 16 kHz mono 16-bit, 20 ms frames
        return min(1500, max(384, -(-frames // 64) * 64))

    def __call__(self, wav: bytes) -> str:
        ctx = str(self.audio_ctx(len(wav)))
        self.start()
        if (self.external_server and self.server_cmd) or (self._server is not None and self._server.poll() is None):
            try:
                return clean_transcript(self._via_server(wav, ctx))
            except ConnectionRefusedError:
                log.info("whisper-server not ready; using whisper-cli")
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            f.write(wav)
            f.flush()
            out = subprocess.run(self.cmd + ["-ac", ctx, "-f", f.name], capture_output=True, text=True,
                                 timeout=self.timeout_s, check=True)
        return clean_transcript(out.stdout)

    def _via_server(self, wav: bytes, ctx: str) -> str:
        b = os.urandom(12).hex()
        body = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in {**self.SERVER_FIELDS, "audio_ctx": ctx}.items())
        body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="clip.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n").encode() + wav + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(self.url, data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read()).get("text", "")
        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionRefusedError):
                raise e.reason from e
            raise


def clean_transcript(text: str) -> str:
    """whisper marks non-speech as [BLANK_AUDIO], (wind blowing) etc. Those are not answers,
    and neither is a decoding loop on noise ("the the the ...")."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*", " ", text)
    words = text.split()
    if len(words) >= 8 and len(set(w.lower().strip(".,!?") for w in words)) * 4 <= len(words):
        return ""
    return " ".join(words)

# --- the node ------------------------------------------------------------------


def format_down(seconds):
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


class Node:
    def __init__(self, *, device_id, secret, write_serial, http, outbox_path,
                 clock=time.time, sound_t=config.SOUND_THRESHOLD, accel_t=config.ACCEL_THRESHOLD_G,
                 window_ms=config.FUSION_WINDOW_MS, cooldown_s=config.COOLDOWN_S,
                 incident_window_s=config.INCIDENT_WINDOW_SECONDS, poll_s=config.POLL_S,
                 heartbeat_s=config.HEARTBEAT_S, backoff_s=config.OFFLINE_BACKOFF_S,
                 ack_timeout_s=config.UNLOCK_ACK_TIMEOUT_S, stt=None,
                 audio_timeout_s=config.AUDIO_FETCH_TIMEOUT_S, state_path=None, resume=False):
        if not secret:
            raise ValueError("SHARED_SECRET is not set")
        self.device_id, self.secret = device_id, secret
        self._write = write_serial
        self.http = http  # None = offline mode (never contact the backend)
        self._clock = clock
        self.fusion = Fusion(sound_t, accel_t, window_ms, int(cooldown_s * 1000))
        self.accel = AccelDelta()
        # After a restart the nonce cache is empty, so refuse anything signed before this start.
        self.verifier = CommandVerifier(secret, clock=clock,
                                        not_before_ms=int(clock() * 1000) if resume else 0)
        self.outbox = Outbox(outbox_path)
        self.incident_window_s = incident_window_s
        self.poll_s, self.heartbeat_s, self.backoff_s = poll_s, heartbeat_s, backoff_s
        self.ack_timeout_s = ack_timeout_s
        self.incident = None  # {"id", "opened_at"}
        self._pending_unlock = None  # {"incident_id", "at"}: U sent, waiting for ACK,U
        self.online = None
        self._net_ok_at = 0.0
        self._last_poll = self._last_hb = -1e18
        self._last_poll_ts = 0
        self._commands = queue.Queue()  # network thread -> sensor loop
        self.stt = stt  # wav bytes -> text; None = speech-to-text off on this Pi
        self.audio_timeout_s = audio_timeout_s
        # sensor loop -> STT thread. Small: a backlog of stale answers is worse than dropping one.
        self._stt_jobs = queue.Queue(maxsize=2)
        self._stop = threading.Event()
        self.uplink = None  # UplinkServer when HTTP runs in a separate uplink process
        self._state_path = Path(state_path) if state_path else None
        self._display = None
        self._show("READY")
        if self._state_path:
            self._load_state(resume)

    # inputs ---------------------------------------------------------------

    def handle_line(self, line):
        if line.startswith("I,box3 "):
            self.hardware_reconnected()
            return
        msg = parse_line(line)
        if msg is None:
            if line.strip():
                log.debug("ignored serial line %r", line.strip()[:80])
            return
        kind = msg["kind"]
        if kind == "S":
            delta = self.accel.update(msg["ax"], msg["ay"], msg["az"])
            if self.fusion.update(msg["ms"], msg["sound"], delta):
                sensors = {"sound_peak": msg["sound"], "accel_delta_g": round(delta, 3), "dist_cm": msg["dist"]}
                log.warning("FALL SUSPECTED sound=%s accel_delta_g=%.2f dist_cm=%s",
                            msg["sound"], delta, msg["dist"])
                self._on_fall(sensors)
        elif kind == "B":
            log.info("help button pressed")
            if not self.incident:
                self._on_fall({})
            self._queue("help_button")
        elif kind == "ACK":
            log.debug("arduino ack %s", msg["cmd"])
            if msg["cmd"] == "U":
                self._on_unlock_ack()

    def tick(self):
        """Sensor-loop housekeeping. Never touches the network."""
        while True:
            try:
                self._apply(self._commands.get_nowait())
            except queue.Empty:
                break
        now = self._clock()
        p = self._pending_unlock
        if p and now - p["at"] > self.ack_timeout_s:
            # The Mac only shows what the Pi confirms, so a silent latch is reported, not hidden.
            log.error("no ACK,U from the arduino after %.1f s incident_id=%s", self.ack_timeout_s, p["incident_id"])
            self._pending_unlock = None
            self._queue("unlock_failed")
        # No local timeout: the alarm stays up until alarm_off (I'm fine, resolve) or a reset.
        # The unlock window (rule 1) is enforced in _apply instead.
        if self.incident:
            self._show("TIME DOWN " + format_down(now - self.incident["opened_at"]))

    # incident -------------------------------------------------------------

    def _on_fall(self, sensors):
        if self.incident:
            log.info("fall while local incident already open incident_id=%s", self.incident["id"])
            return
        now = self._clock()
        self.incident = {"id": f"{self.device_id}-{int(now * 1000)}", "opened_at": now}
        # Local alarm first. Nothing below this line can delay it.
        self._write("A1")
        self._show("TIME DOWN 00:00")
        self._save_state()
        log.info("local incident opened incident_id=%s", self.incident["id"])
        self._queue("fall_suspected", sensors)

    def _close_incident(self, reason):
        if not self.incident:
            return
        log.info("local incident closed incident_id=%s reason=%s", self.incident["id"], reason)
        self.incident = None
        self._write("A0")
        self._show("READY")
        self._save_state()

    def _save_state(self):
        if not self._state_path:
            return
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"incident": self.incident}))
        tmp.replace(self._state_path)

    def _load_state(self, resume):
        """A restarted safety process picks its open incident back up: same id, same clock,
        alarm on. A fresh start (resume=False) begins clean."""
        if not resume:
            self._state_path.unlink(missing_ok=True)
            return
        try:
            incident = json.loads(self._state_path.read_text()).get("incident")
        except (OSError, ValueError):
            return
        if isinstance(incident, dict) and isinstance(incident.get("id"), str) \
                and isinstance(incident.get("opened_at"), (int, float)):
            self.incident = {"id": incident["id"], "opened_at": float(incident["opened_at"])}
            self._write("A1")
            self._show("TIME DOWN " + format_down(self._clock() - self.incident["opened_at"]))
            log.warning("resumed local incident after restart incident_id=%s", self.incident["id"])

    def _show(self, text):
        if text != self._display:
            self._display = text
            self._write(f"D,{text}")

    # network --------------------------------------------------------------

    def _queue(self, type_, sensors=None, speech=None, incident_id=None):
        """Sign and persist an event. tick() delivers it, in order, when the backend is reachable."""
        ts = int(self._clock() * 1000)
        if incident_id is None and self.incident:
            incident_id = self.incident["id"]
        body = {"device_id": self.device_id, "ts": ts, "type": type_, "sensors": sensors or {},
                "local_incident_id": incident_id}
        if speech is not None:
            body["speech"] = speech
        raw = json.dumps(body, separators=(",", ":"))
        self.outbox.add({"raw": raw, "sig": sign_event(self.secret, ts, raw.encode())})
        log.info("event queued type=%s local_incident_id=%s outbox=%d", type_, body["local_incident_id"], len(self.outbox))

    def start_network(self, interval_s=0.05):
        """Run network_step on a daemon thread, so a slow backend can't stall the sensor loop."""
        def run():
            while not self._stop.wait(interval_s):
                try:
                    self.network_step(self._clock())
                except Exception:  # never let the network thread die mid-incident
                    log.exception("network step failed")
        threading.Thread(target=run, daemon=True, name="edge-net").start()
        if self.stt is not None:
            def run_stt():
                while not self._stop.is_set():
                    try:
                        self.stt_step(timeout=0.5)
                    except Exception:
                        log.exception("speech-to-text step failed")
            threading.Thread(target=run_stt, daemon=True, name="edge-stt").start()

    def stop_network(self):
        self._stop.set()

    def network_step(self, now):
        """Deliver the outbox, heartbeat, poll. Commands go to the sensor loop via the queue."""
        if self.http is None or now < self._net_ok_at:
            return
        try:
            while True:
                item = self.outbox.peek()
                if item is None:
                    break
                status, _ = self.http.post("/edge/event", item["raw"].encode(), {"X-Signature": item["sig"]})
                if status >= 500:
                    raise OSError(f"backend {status}")
                if status != 200:
                    # 401/422 will never succeed on retry (wrong secret, bad body). Drop and shout.
                    log.error("event rejected status=%s event=%s", status, item["raw"][:120])
                self.outbox.pop()
            if now - self._last_hb >= self.heartbeat_s:
                raw, sig = self.signed_heartbeat(now)
                status, _ = self.http.post("/edge/event", raw, {"X-Signature": sig})
                if status >= 500:
                    raise OSError(f"backend {status}")
                self._last_hb = now
            if now - self._last_poll >= self.poll_s:
                ts, sig = self.signed_poll(now)
                status, body = self.http.get(f"/edge/commands?device_id={self.device_id}&ts={ts}",
                                             {"X-Signature": sig})
                if status >= 500:
                    raise OSError(f"backend {status}")
                self._last_poll = now
                if status != 200:
                    log.error("command poll rejected status=%s (wrong SHARED_SECRET, or the Pi clock is >10 s off)",
                              status)
                for cmd in (body or {}).get("commands", []) if status == 200 else []:
                    self._commands.put(cmd)
            self._set_online(True)
        except OSError as e:
            self._set_online(False, e)
            self._net_ok_at = now + self.backoff_s

    def signed_heartbeat(self, now):
        ts = int(now * 1000)
        raw = json.dumps({"device_id": self.device_id, "ts": ts, "type": "heartbeat",
                          "local_incident_id": self.incident["id"] if self.incident else None},
                         separators=(",", ":")).encode()
        return raw, sign_event(self.secret, ts, raw)

    def signed_poll(self, now):
        # Signed, so nobody else on the Wi-Fi can poll first and swallow the unlock.
        # The backend refuses a ts it has already seen.
        ts = self._last_poll_ts = max(int(now * 1000), self._last_poll_ts + 1)
        return ts, sign_poll(self.secret, ts, self.device_id)

    def _set_online(self, online, err=None):
        if online != self.online:
            if online:
                log.info("backend reachable")
            else:
                log.warning("backend unreachable (%s); running locally, outbox=%d", err, len(self.outbox))
            self.online = online

    def _apply(self, cmd):
        ok, reason = self.verifier.check(cmd, local_incident_id=self.incident["id"] if self.incident else None)
        if not ok:
            return
        action = cmd["action"]
        if action == "unlock":
            # Rule 1: unlocks die with the window. The local alarm itself does not.
            if self._clock() - self.incident["opened_at"] > self.incident_window_s:
                log.warning("unlock refused reason=outside window incident_id=%s", self.incident["id"])
                return
            self._write("U")
            # local_unlock waits for the Arduino's ACK,U: the Mac only shows what the latch confirms.
            self._pending_unlock = {"incident_id": self.incident["id"], "at": self._clock()}
        elif action == "reset":
            self._reset()
        elif action == "lock":
            self._write("L")
        elif action == "alarm_on":
            self._write("A1")
        elif action == "alarm_off":
            self._close_incident("backend alarm_off")
        elif action == "display":
            log.info("display command has no text field in the contract yet; ignored")
        elif action == "transcribe":
            self._start_transcribe(cmd["id"], self.incident["id"])

    # speech-to-text ---------------------------------------------------------

    def _start_transcribe(self, clip_id, incident_id):
        """Sensor loop: hand the clip to the STT thread (or the uplink process) and return at once."""
        if self.uplink is not None:
            error = self.uplink.transcribe(clip_id, incident_id)
            if error:
                log.warning("transcribe refused: %s clip=%s", error, clip_id)
                self._queue("speech_text", speech={"clip_id": clip_id, "error": error}, incident_id=incident_id)
            return
        if self.stt is None or self.http is None:
            log.warning("transcribe refused: speech-to-text is off on this Pi clip=%s", clip_id)
            self._queue("speech_text", speech={"clip_id": clip_id, "error": "speech-to-text is off on the Pi"},
                        incident_id=incident_id)
            return
        try:
            self._stt_jobs.put_nowait((clip_id, incident_id))
        except queue.Full:
            log.warning("transcribe dropped: speech-to-text busy clip=%s", clip_id)
            self._queue("speech_text", speech={"clip_id": clip_id, "error": "speech-to-text busy"},
                        incident_id=incident_id)

    def stt_step(self, timeout=0.0):
        """STT thread: fetch one clip, run whisper.cpp, queue speech_text. Never touches serial."""
        try:
            clip_id, incident_id = self._stt_jobs.get(timeout=timeout) if timeout else self._stt_jobs.get_nowait()
        except queue.Empty:
            return
        result = {"clip_id": clip_id}
        try:
            if not self.incident or self.incident["id"] != incident_id:
                raise RuntimeError("local incident closed")
            ts = int(self._clock() * 1000)
            status, wav = self.http.get_bytes(
                f"/edge/audio/{clip_id}?device_id={self.device_id}&ts={ts}",
                {"X-Signature": sign_audio(self.secret, ts, self.device_id, clip_id)}, self.audio_timeout_s)
            if status != 200:
                raise RuntimeError(f"clip fetch refused status={status}")
            result["audio_ms"] = max(0, len(wav) - 44) // 32  # 16 kHz mono 16-bit
            t0 = time.monotonic()
            text = self.stt(wav)
            result["stt_ms"] = int((time.monotonic() - t0) * 1000)
            result["text"] = text
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as e:
            result["error"] = str(e)[:200] or type(e).__name__
        # The words are the person's answer: log only timings and length.
        log.info("speech-to-text clip=%s incident_id=%s audio_ms=%s stt_ms=%s words=%d error=%s",
                 clip_id, incident_id, result.get("audio_ms"), result.get("stt_ms"),
                 len(result.get("text", "").split()), result.get("error"))
        self._queue("speech_text", speech=result, incident_id=incident_id)


    def _on_unlock_ack(self):
        p, self._pending_unlock = self._pending_unlock, None
        if not p:
            log.warning("ACK,U with no unlock pending (late, after unlock_failed); not reported")
            return
        log.info("latch confirmed incident_id=%s", p["incident_id"])
        self._queue("local_unlock")

    def hardware_reconnected(self) -> None:
        """Restore local outputs after a hub reset, without replaying an unlock."""
        self._write("L")
        if self._pending_unlock:
            self._pending_unlock = None
            self._queue("unlock_failed")
            log.warning("unlock failed: hub disconnected before confirmation")
        self.accel = AccelDelta()
        # Keep last_ms so Fusion still detects a restarted hub clock and clears its cooldown.
        self.fusion.last_sound = self.fusion.last_accel = None
        self._write("A1" if self.incident else "A0")
        self._display = None
        self._show("TIME DOWN " + format_down(self._clock() - self.incident["opened_at"])
                   if self.incident else "READY")
        log.info("hub reconnected: latch locked, local incident=%s",
                 self.incident["id"] if self.incident else "none")

    def _reset(self):
        """Demo reset between runs. Never ends a live incident: alarm_off does that."""
        if self.incident:
            log.warning("reset refused, local incident open incident_id=%s", self.incident["id"])
            return
        self._write("L")
        self._write("A0")
        self._pending_unlock = None
        # The 30 s cooldown would swallow the next demo fall.
        self.fusion._reset()
        self.fusion.cooldown_until = None
        self._display = None
        self._show("READY")
        log.info("demo reset: latch locked, alarm off, fusion cleared")


# --- I/O sources -----------------------------------------------------------------


class StdinSource:
    """Sensor lines from stdin. It also stands in for the Arduino's latch: it answers U with
    ACK,U unless auto_ack is off (--no-ack, a dead latch)."""

    def __init__(self, auto_ack=True):
        self.eof = False
        self.auto_ack = auto_ack
        self._replies = []

    def readline(self, timeout):
        if self._replies:
            return self._replies.pop(0)
        if self.eof:
            time.sleep(timeout)
            return None
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            return None
        line = sys.stdin.readline()
        if line == "":
            self.eof = True
            log.info("stdin closed")
            return None
        return line

    def write(self, cmd):
        print(f"-> ARDUINO {cmd}", file=sys.stderr, flush=True)
        if cmd == "U" and self.auto_ack:
            self._replies.append("ACK,U")


class SerialSource:
    def __init__(self, port, baud):
        try:
            import serial  # pyserial. TODO(verify): available on the QNX image?
        except ImportError:
            sys.exit("pyserial is not installed: pip install pyserial (or use --stdin)")
        self.eof = False
        self.ser = serial.Serial(port, baud, timeout=0.05)

    def readline(self, timeout):
        line = self.ser.readline()
        return line.decode(errors="replace") if line else None

    def write(self, cmd):
        self.ser.write((cmd + "\n").encode())


class UsbCdcSource:
    """The ESP32's USB serial port spoken to directly through libusb, because QNX 8 on the
    Pi has no USB-serial driver (no devc-serusb). Needs pyusb + `apk add libusb`, and root:
    libusb segfaults for other users on QNX.
    shortcut: whole node runs as root; switch to SerialSource if QNX ships devc-serusb."""

    VENDOR_ESPRESSIF = 0x303A
    LIBUSB = "/usr/lib/libusb-1.0.so.0"  # QNX apk path; pyusb can't find it on its own

    def __init__(self):
        try:
            import usb.backend.libusb1
            import usb.core
            import usb.util
        except ImportError:
            sys.exit("pyusb is not installed: pip install pyusb==1.3.1 (or use --serial/--stdin)")
        self._usb = usb
        self._backend = usb.backend.libusb1.get_backend(find_library=lambda _: self.LIBUSB)
        self._dev = None
        self._retry_at = 0.0
        self._buf = b""
        self.eof = False
        if not self._connect():
            raise FileNotFoundError("no ESP32 on USB: check the hub cable")

    def _connect(self):
        usb = self._usb
        dev = usb.core.find(idVendor=self.VENDOR_ESPRESSIF, backend=self._backend)
        if dev is None:
            return False
        self._dev = dev
        cfg = dev.get_active_configuration()
        comm = next(i for i in cfg if i.bInterfaceClass == 0x02)  # CDC control
        data = next(i for i in cfg if i.bInterfaceClass == 0x0A)  # CDC data
        for i in (comm, data):
            try:
                driver_active = dev.is_kernel_driver_active(i.bInterfaceNumber)
            except NotImplementedError:
                driver_active = False  # QNX has no kernel-driver query; claiming can still fail.
            if driver_active:
                dev.detach_kernel_driver(i.bInterfaceNumber)
            usb.util.claim_interface(dev, i.bInterfaceNumber)
        out = usb.util.ENDPOINT_OUT
        self._in = next(e for e in data if usb.util.endpoint_direction(e.bEndpointAddress) != out)
        self._out = next(e for e in data if usb.util.endpoint_direction(e.bEndpointAddress) == out)
        # SET_CONTROL_LINE_STATE with DTR on, RTS off: "a host is listening". Never send
        # RTS on with DTR off: on the ESP32's USB port that sequence resets the chip.
        dev.ctrl_transfer(0x21, 0x22, 0x0001, comm.bInterfaceNumber)
        self._buf = b""
        log.info("ESP32 on USB %04x:%04x", dev.idVendor, dev.idProduct)
        return True

    def _disconnected(self, error):
        # IO, NO_DEVICE, TIMEOUT and PIPE can accompany reset; access/busy errors must fail.
        if error.backend_error_code not in (-1, -4, -7, -9):
            raise error
        dev, self._dev = self._dev, None
        self._buf = b""
        self._retry_at = time.monotonic() + 1.0
        if dev is not None:
            self._usb.util.dispose_resources(dev)
        log.warning("hub USB unavailable (%s); waiting to reconnect", error)

    def reconnect(self) -> bool:
        """True once a lost device is reclaimed; never buffers or retries commands."""
        if self._dev is not None or time.monotonic() < self._retry_at:
            return False
        self._retry_at = time.monotonic() + 1.0
        try:
            return self._connect()
        except self._usb.core.USBError as error:
            self._disconnected(error)
            return False

    def readline(self, timeout):
        if self._dev is None:
            time.sleep(timeout)
            return None
        while b"\n" not in self._buf:
            try:
                # libusb treats zero as an infinite wait; calibration also polls with timeout=0.
                self._buf += bytes(self._in.read(self._in.wMaxPacketSize, timeout=max(1, int(timeout * 1000))))
            except self._usb.core.USBTimeoutError:
                return None
            except self._usb.core.USBError as error:
                self._disconnected(error)
                return None
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode(errors="replace")

    def write(self, cmd):
        if self._dev is None:
            log.warning("hub unavailable: command %s not sent", cmd.split(",", 1)[0])
            return
        try:
            self._out.write((cmd + "\n").encode(), timeout=500)
        except self._usb.core.USBError as error:
            self._disconnected(error)


class UsbWorkerSource:
    """Keep native USB aborts outside the process that owns incident/security state."""

    def __init__(self) -> None:
        self.eof = False
        self._proc = None
        self._retry_at = 0.0
        self._buf = b""
        self._ready = False

    def reconnect(self) -> None:
        if self._proc is not None:
            status = self._proc.poll()
            if status is None:
                return
            self._proc.stdin.close()
            self._proc.stdout.close()
            self._proc = None
            self._ready = False
            self._buf = b""
            if status > 0 and status not in (75, 134):
                raise RuntimeError(f"USB worker failed with status {status}; see its error log")
            log.warning("USB worker exited status=%s; restarting without clearing local incident", status)
            self._retry_at = time.monotonic() + 1.0
        if time.monotonic() < self._retry_at:
            return
        self._proc = subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__).resolve()), "--usb-worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
        )
        os.set_blocking(self._proc.stdin.fileno(), False)
        # Outputs are restored only after the worker confirms it has claimed USB.

    def readline(self, timeout: float) -> str | None:
        if self._proc is None:
            time.sleep(timeout)
            return None
        while b"\n" not in self._buf:
            ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
            if not ready:
                return None
            chunk = os.read(self._proc.stdout.fileno(), 4096)
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        text = line.decode(errors="replace")
        if text.startswith("I,box3 "):
            self._ready = True
        return text

    def write(self, cmd: str) -> None:
        if not self._ready or self._proc is None or self._proc.poll() is not None:
            log.warning("USB worker unavailable: command %s not sent", cmd.split(",", 1)[0])
            return
        raw = (cmd + "\n").encode()
        try:
            if os.write(self._proc.stdin.fileno(), raw) != len(raw):
                raise BlockingIOError("partial command")
        except (BrokenPipeError, BlockingIOError):
            # Never queue an unlock for a future helper or let a stuck helper block the Pi.
            self._ready = False
            self._proc.kill()
            log.error("USB worker command pipe failed; command not retried")

    def close(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.kill()
            self._proc.wait()
            self._proc.stdin.close()
            self._proc.stdout.close()


def _usb_worker() -> int:
    """Only this helper loads libusb. It has no incident or unlock-decision logic."""
    try:
        source = UsbCdcSource()
    except FileNotFoundError:
        log.warning("BOX-3 is not on USB yet")
        return 75
    except OSError as error:
        if getattr(error, "backend_error_code", None) not in (-1, -4, -7, -9):
            raise
        log.warning("BOX-3 is not ready: %s", error)
        return 75
    pending = b""
    try:
        print("I,box3 usb=connected", flush=True)
        while True:
            if source.reconnect():
                print("I,box3 usb=reconnected", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], 0)
            if ready:
                data = os.read(sys.stdin.fileno(), 4096)
                if not data:
                    return 0
                pending += data
                while b"\n" in pending:
                    cmd, pending = pending.split(b"\n", 1)
                    source.write(cmd.decode())
            line = source.readline(0.05)
            if line:
                print(line, flush=True)
    except BrokenPipeError:
        # The reader died (a supervisor restart is routine): exit so its replacement can claim the hub.
        # Pointing stdout at /dev/null stops the interpreter's exit flush from raising again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        log.info("USB worker: reader gone, releasing the hub")
        return 0


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--stdin", action="store_true", help="read sensor lines from stdin (sim_sensors.py)")
    src.add_argument("--serial", metavar="PORT", nargs="?", const=config.SERIAL_PORT,
                     help="Arduino serial port (default: SERIAL_PORT from .env)")
    src.add_argument("--usb", action="store_true", help="ESP32 over raw USB (QNX: no serial driver; run as root)")
    src.add_argument("--usb-worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--backend", default=config.BACKEND_URL)
    ap.add_argument("--offline", action="store_true", help="never contact the backend")
    ap.add_argument("--no-ack", action="store_true", help="with --stdin: the fake latch never answers ACK,U")
    ap.add_argument("--exit-on-eof", action="store_true", help="stop once stdin closes and the outbox is empty")
    ap.add_argument("--uplink-socket", metavar="PATH",
                    help="safety process only: HTTP and speech-to-text run in edge/uplink.py (see supervisor.py)")
    ap.add_argument("--resume", action="store_true", help="with --uplink-socket: pick up the open incident after a restart")
    args = ap.parse_args()

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stderr)
    if args.usb_worker:
        sys.exit(_usb_worker())
    fh = logging.FileHandler(config.LOG_DIR / "events.log")  # local audit trail, kept offline
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)

    if args.stdin:
        source = StdinSource(auto_ack=not args.no_ack)
    elif args.usb:
        source = UsbWorkerSource()
    elif args.serial:
        source = SerialSource(args.serial, config.SERIAL_BAUD)
    else:
        sys.exit("no serial port: pass --serial PORT, --usb, or set SERIAL_PORT in .env")
    split = bool(args.uplink_socket)
    stt = None
    if config.WHISPER_MODEL and not split:
        if Path(config.WHISPER_MODEL).is_file() and shutil.which(config.WHISPER_BIN):
            stt = Transcriber(config.WHISPER_MODEL)
        else:
            log.error("WHISPER_MODEL or WHISPER_BIN not found; speech-to-text off")
    node = Node(
        device_id=config.DEVICE_ID, secret=config.SHARED_SECRET,
        write_serial=source.write,
        http=None if args.offline or split else UrlHttp(args.backend, config.HTTP_TIMEOUT_S),
        outbox_path=config.LOG_DIR / "outbox.jsonl",
        stt=stt,
        state_path=config.LOG_DIR / "incident.json" if split else None,
        resume=args.resume,
    )
    server = None
    if split:
        from edge.link import UplinkServer
        server = node.uplink = UplinkServer(node, args.uplink_socket)
        log.info("safety process up device_id=%s pid=%d uplink_socket=%s resume=%s", config.DEVICE_ID,
                 os.getpid(), args.uplink_socket, args.resume)
    else:
        log.info("edge node up device_id=%s backend=%s speech_to_text=%s", config.DEVICE_ID,
                 "offline" if args.offline else args.backend,
                 " ".join(stt.server_cmd or stt.cmd) if stt else "off")

    def stop(signum, frame):
        raise KeyboardInterrupt

    # Launched detached (`nohup ... &`), the reader ignores SIGINT, so SIGTERM is how it is
    # stopped. Raising here runs the cleanup below; whisper-server has its own session and
    # would otherwise outlive the reader.
    signal.signal(signal.SIGTERM, stop)
    try:
        if stt:
            stt.start()
        if not split:
            node.start_network()
        while True:
            if args.usb:
                source.reconnect()
            line = source.readline(0.05)
            if line:
                node.handle_line(line)
            node.tick()
            if server:
                server.pump()
            if args.exit_on_eof and source.eof and (node.http is None or not len(node.outbox)):
                break
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("edge reader stopped")
        raise
    finally:
        node.stop_network()
        if server:
            server.close()
        if stt:
            stt.close()
        if args.usb:
            source.close()


if __name__ == "__main__":
    main()
