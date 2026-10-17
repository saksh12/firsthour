#!/usr/bin/env python3
"""The Pi's safety node (IMPLEMENTATION.md sections 6.1-6.3, 9.3, 10). Stdlib only.

Reads the Arduino's serial lines, runs two-sensor fusion, owns the local incident
(buzzer + TIME DOWN clock + offline NFC unlock), posts signed events to the backend,
and polls and verifies signed commands.

Rule: safety never waits on the network. handle_line() only touches the Arduino and
local state; all HTTP happens in tick(), with a short timeout and an offline backoff.

Run on the laptop with fake sensors:
    python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin
On the Pi:
    python3 edge/sensor_node.py --serial /dev/<port>      # TODO(verify) QNX device path
"""

import argparse
import json
import logging
import math
import select
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge import config  # noqa: E402
from edge.security import CommandVerifier, nfc_unlock_allowed, sign_event  # noqa: E402

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
        if kind == "N" and len(parts) == 2 and parts[1].strip():
            return {"kind": "N", "uid": parts[1].strip()}
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
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self.items.append(json.loads(line))

    def add(self, item):
        self.items.append(item)
        self._save()

    def pop(self):
        item = self.items.pop(0)
        self._save()
        return item

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

    def post(self, path, raw, headers):
        return self._do(urllib.request.Request(self.base + path, data=raw, method="POST",
                                               headers={"Content-Type": "application/json", **headers}))

    def get(self, path):
        return self._do(urllib.request.Request(self.base + path))


# --- the node ------------------------------------------------------------------


def format_down(seconds):
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


class Node:
    def __init__(self, *, device_id, secret, nfc_allowed, write_serial, http, outbox_path,
                 clock=time.time, sound_t=config.SOUND_THRESHOLD, accel_t=config.ACCEL_THRESHOLD_G,
                 window_ms=config.FUSION_WINDOW_MS, cooldown_s=config.COOLDOWN_S,
                 incident_window_s=config.INCIDENT_WINDOW_SECONDS, poll_s=config.POLL_S,
                 heartbeat_s=config.HEARTBEAT_S, backoff_s=config.OFFLINE_BACKOFF_S):
        if not secret:
            raise ValueError("SHARED_SECRET is not set")
        self.device_id, self.secret, self.nfc_allowed = device_id, secret, nfc_allowed
        self._write = write_serial
        self.http = http  # None = offline mode (never contact the backend)
        self._clock = clock
        self.fusion = Fusion(sound_t, accel_t, window_ms, int(cooldown_s * 1000))
        self.accel = AccelDelta()
        self.verifier = CommandVerifier(secret, clock=clock)
        self.outbox = Outbox(outbox_path)
        self.incident_window_s = incident_window_s
        self.poll_s, self.heartbeat_s, self.backoff_s = poll_s, heartbeat_s, backoff_s
        self.incident = None  # {"id", "opened_at"}
        self.online = None
        self._net_ok_at = 0.0
        self._last_poll = self._last_hb = -1e18
        self._display = None
        self._show("READY")

    # inputs ---------------------------------------------------------------

    def handle_line(self, line):
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
        elif kind == "N":
            self._on_nfc(msg["uid"])
        elif kind == "B":
            log.info("help button pressed")
            if not self.incident:
                self._on_fall({})
            self._queue("help_button")
        elif kind == "ACK":
            log.debug("arduino ack %s", msg["cmd"])

    def tick(self):
        now = self._clock()
        inc = self.incident
        if inc and now - inc["opened_at"] > self.incident_window_s:
            self._close_incident("window expired")
        if self.incident:
            self._show("TIME DOWN " + format_down(now - self.incident["opened_at"]))
        if self.http is not None and now >= self._net_ok_at:
            self._network(now)

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
        log.info("local incident opened incident_id=%s", self.incident["id"])
        self._queue("fall_suspected", sensors)

    def _close_incident(self, reason):
        if not self.incident:
            return
        log.info("local incident closed incident_id=%s reason=%s", self.incident["id"], reason)
        self.incident = None
        self._write("A0")
        self._show("READY")

    def _on_nfc(self, uid):
        ok, reason = nfc_unlock_allowed(uid, self.nfc_allowed, local_incident_open=self.incident is not None)
        self._queue("nfc_tap")
        if ok:
            self._write("U")
            self._queue("local_unlock")

    def _show(self, text):
        if text != self._display:
            self._display = text
            self._write(f"D,{text}")

    # network --------------------------------------------------------------

    def _queue(self, type_, sensors=None):
        """Sign and persist an event. tick() delivers it, in order, when the backend is reachable."""
        ts = int(self._clock() * 1000)
        body = {"device_id": self.device_id, "ts": ts, "type": type_, "sensors": sensors or {},
                "local_incident_id": self.incident["id"] if self.incident else None}
        raw = json.dumps(body, separators=(",", ":"))
        self.outbox.add({"raw": raw, "sig": sign_event(self.secret, ts, raw.encode())})
        log.info("event queued type=%s local_incident_id=%s outbox=%d", type_, body["local_incident_id"], len(self.outbox))

    def _network(self, now):
        try:
            while len(self.outbox):
                item = self.outbox.items[0]
                status, _ = self.http.post("/edge/event", item["raw"].encode(), {"X-Signature": item["sig"]})
                if status >= 500:
                    raise OSError(f"backend {status}")
                if status != 200:
                    # 401/422 will never succeed on retry (wrong secret, bad body). Drop and shout.
                    log.error("event rejected status=%s event=%s", status, item["raw"][:120])
                self.outbox.pop()
            if now - self._last_hb >= self.heartbeat_s:
                ts = int(now * 1000)
                raw = json.dumps({"device_id": self.device_id, "ts": ts, "type": "heartbeat",
                                  "local_incident_id": self.incident["id"] if self.incident else None},
                                 separators=(",", ":")).encode()
                status, _ = self.http.post("/edge/event", raw, {"X-Signature": sign_event(self.secret, ts, raw)})
                if status >= 500:
                    raise OSError(f"backend {status}")
                self._last_hb = now
            if now - self._last_poll >= self.poll_s:
                status, body = self.http.get(f"/edge/commands?device_id={self.device_id}")
                if status >= 500:
                    raise OSError(f"backend {status}")
                self._last_poll = now
                for cmd in (body or {}).get("commands", []):
                    self._apply(cmd)
            self._set_online(True)
        except OSError as e:
            self._set_online(False, e)
            self._net_ok_at = now + self.backoff_s

    def _set_online(self, online, err=None):
        if online != self.online:
            if online:
                log.info("backend reachable")
            else:
                log.warning("backend unreachable (%s); running locally, outbox=%d", err, len(self.outbox))
            self.online = online

    def _apply(self, cmd):
        ok, reason = self.verifier.check(cmd, local_incident_open=self.incident is not None)
        if not ok:
            return
        action = cmd["action"]
        if action == "unlock":
            self._write("U")
        elif action == "lock":
            self._write("L")
        elif action == "alarm_on":
            self._write("A1")
        elif action == "alarm_off":
            self._close_incident("backend alarm_off")
        elif action == "display":
            log.info("display command has no text field in the contract yet; ignored")


# --- I/O sources -----------------------------------------------------------------


class StdinSource:
    def __init__(self):
        self.eof = False

    def readline(self, timeout):
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

    @staticmethod
    def write(cmd):
        print(f"-> ARDUINO {cmd}", file=sys.stderr, flush=True)


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


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--stdin", action="store_true", help="read sensor lines from stdin (sim_sensors.py)")
    src.add_argument("--serial", metavar="PORT", help="Arduino serial port")
    ap.add_argument("--backend", default=config.BACKEND_URL)
    ap.add_argument("--offline", action="store_true", help="never contact the backend")
    ap.add_argument("--exit-on-eof", action="store_true", help="stop once stdin closes and the outbox is empty")
    args = ap.parse_args()

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stderr)
    fh = logging.FileHandler(config.LOG_DIR / "events.log")  # local audit trail, kept offline
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)

    source = StdinSource() if args.stdin else SerialSource(args.serial, config.SERIAL_BAUD)
    node = Node(
        device_id=config.DEVICE_ID, secret=config.SHARED_SECRET, nfc_allowed=config.NFC_ALLOWED_UIDS,
        write_serial=source.write,
        http=None if args.offline else UrlHttp(args.backend, config.HTTP_TIMEOUT_S),
        outbox_path=config.LOG_DIR / "outbox.jsonl",
    )
    log.info("edge node up device_id=%s backend=%s", config.DEVICE_ID, "offline" if args.offline else args.backend)
    try:
        while True:
            line = source.readline(0.05)
            if line:
                node.handle_line(line)
            node.tick()
            if args.exit_on_eof and source.eof and (node.http is None or not len(node.outbox)):
                break
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
