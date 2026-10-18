"""Local link between the Pi's safety process and its uplink (issue #22 step 1). Stdlib only.

One Unix stream socket carrying newline-delimited JSON. The safety process listens and never
blocks on it: it reads and writes only what is ready and drops an uplink that stops reading.
The uplink connects and may hang, crash or be killed; the safety process keeps its incident,
alarm, clock and outbox, and resends the outbox when an uplink connects again.

The uplink signs nothing. It asks the safety process for each signature, and the safety
process signs only what it built itself: heartbeats, command polls, its outbox events, and
audio fetches for clips it dispatched. Commands from the backend pass through the uplink
untouched and are verified in the safety process.
"""

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

from edge.security import sign_audio

log = logging.getLogger("firsthour.link")


class LinkClosed(Exception):
    """The other process is gone or misbehaving. Not an OSError, so callers never confuse it
    with the backend being unreachable."""


class LineConn:
    """Non-blocking JSON lines over a connected socket. send() is safe from two threads."""

    MAX_BUFFER = 1 << 20

    def __init__(self, sock: socket.socket) -> None:
        sock.setblocking(False)
        self.sock = sock
        self._in = b""
        self._out = b""
        self._lock = threading.Lock()

    def fileno(self) -> int:
        return self.sock.fileno()

    def send(self, msg: dict) -> None:
        with self._lock:
            self._out += (json.dumps(msg, separators=(",", ":")) + "\n").encode()
            if len(self._out) > self.MAX_BUFFER:
                raise LinkClosed("peer is not reading")
            self._flush()

    def flush(self) -> None:
        with self._lock:
            self._flush()

    def _flush(self) -> None:
        while self._out:
            try:
                n = self.sock.send(self._out)
            except BlockingIOError:
                return
            except OSError as e:
                raise LinkClosed(str(e) or type(e).__name__) from e
            self._out = self._out[n:]

    def read(self) -> list:
        """Every complete message available now. Raises LinkClosed when the peer has gone."""
        while True:
            try:
                data = self.sock.recv(65536)
            except BlockingIOError:
                break
            except OSError as e:
                raise LinkClosed(str(e) or type(e).__name__) from e
            if not data:
                raise LinkClosed("peer closed")
            self._in += data
            if len(self._in) > self.MAX_BUFFER:
                raise LinkClosed("message too long")
        msgs = []
        while b"\n" in self._in:
            line, self._in = self._in.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                log.warning("ignored malformed link message")
                continue
            if isinstance(msg, dict):
                msgs.append(msg)
        return msgs

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _clean_speech(result) -> dict:
    """Only the speech_text fields the backend accepts, with the right types."""
    out = {}
    if not isinstance(result, dict):
        return {"error": "malformed speech result"}
    if isinstance(result.get("text"), str):
        out["text"] = result["text"][:2000]
    for key in ("stt_ms", "audio_ms"):
        if isinstance(result.get(key), int) and not isinstance(result.get(key), bool):
            out[key] = result[key]
    if result.get("error") is not None:
        out["error"] = str(result["error"])[:200]
    return out


class UplinkServer:
    """Safety-process side. pump() runs in the sensor loop and never blocks."""

    CLIP_TIMEOUT_S = 30.0  # the uplink answers every clip within whisper's 10 s timeout
    MAX_CLIPS = 2  # a backlog of stale answers is worse than refusing one

    def __init__(self, node, path, clock=time.monotonic) -> None:
        self.node = node
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o077)  # only the same user (root on the Pi) may connect
        try:
            self._listen.bind(str(self.path))
        finally:
            os.umask(old)
        self._listen.listen(1)
        self._listen.setblocking(False)
        self.conn = None
        self._sent = set()  # outbox signatures already sent to this connection
        self._clips = {}  # clip_id -> (incident_id, dispatched_at)

    def pump(self) -> None:
        try:
            sock, _ = self._listen.accept()
        except BlockingIOError:
            pass
        else:
            if self.conn is not None:
                self._drop("replaced by a new uplink")
            self.conn = LineConn(sock)
            log.info("uplink connected; resending outbox=%d", len(self.node.outbox))
        self._expire_clips()
        if self.conn is None:
            return
        try:
            for msg in self.conn.read():
                self._handle(msg)
            for item in list(self.node.outbox.items):
                if item["sig"] not in self._sent:
                    self.conn.send({"t": "event", "raw": item["raw"], "sig": item["sig"]})
                    self._sent.add(item["sig"])
            self.conn.flush()
        except LinkClosed as e:
            self._drop(e)

    def transcribe(self, clip_id: str, incident_id: str):
        """Hand a verified clip to the uplink with a signed fetch. Returns an error string or None."""
        if self.conn is None:
            return "uplink down"
        if len(self._clips) >= self.MAX_CLIPS:
            return "speech-to-text busy"
        node = self.node
        ts = int(node._clock() * 1000)
        try:
            self.conn.send({"t": "transcribe", "clip_id": clip_id, "incident_id": incident_id, "ts": ts,
                            "sig": sign_audio(node.secret, ts, node.device_id, clip_id)})
        except LinkClosed as e:
            self._drop(e)
            return "uplink down"
        self._clips[clip_id] = (incident_id, self._clock())
        return None

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self._listen.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _handle(self, msg: dict) -> None:
        node, kind = self.node, msg.get("t")
        if kind == "ack":
            if node.outbox.remove(msg.get("sig")):
                self._sent.discard(msg.get("sig"))
        elif kind == "poll":
            ts, sig = node.signed_poll(node._clock())
            self.conn.send({"t": "poll", "ts": ts, "sig": sig})
        elif kind == "heartbeat":
            raw, sig = node.signed_heartbeat(node._clock())
            self.conn.send({"t": "heartbeat", "raw": raw.decode(), "sig": sig})
        elif kind == "commands":
            for cmd in msg.get("commands") or []:
                if isinstance(cmd, dict):
                    node._commands.put(cmd)
        elif kind == "speech":
            clip_id = msg.get("clip_id")
            if clip_id not in self._clips:
                # Never sign words for a clip this process didn't ask for.
                log.warning("speech refused: clip was not dispatched clip=%s", str(clip_id)[:40])
                return
            incident_id, _ = self._clips.pop(clip_id)
            node._queue("speech_text", speech={"clip_id": clip_id, **_clean_speech(msg.get("result"))},
                        incident_id=incident_id)

    def _expire_clips(self) -> None:
        now = self._clock()
        for clip_id, (incident_id, at) in list(self._clips.items()):
            if now - at > self.CLIP_TIMEOUT_S:
                self._fail_clip(clip_id, incident_id, "no answer from the uplink")

    def _fail_clip(self, clip_id: str, incident_id: str, error: str) -> None:
        # An error releases the backend's silence hold, so silence still escalates.
        self._clips.pop(clip_id, None)
        self.node._queue("speech_text", speech={"clip_id": clip_id, "error": error}, incident_id=incident_id)

    def _drop(self, why) -> None:
        log.warning("uplink disconnected (%s); safety continues, outbox=%d", why, len(self.node.outbox))
        self.conn.close()
        self.conn = None
        self._sent.clear()
        for clip_id, (incident_id, _) in list(self._clips.items()):
            self._fail_clip(clip_id, incident_id, "uplink restarted")
