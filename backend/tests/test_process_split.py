"""Process split (issue #22 step 1): the safety process keeps the incident, alarm and outbox
while the uplink process hangs, dies or restarts, and the uplink signs nothing itself."""

import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from edge import sensor_node, supervisor
from edge.link import LineConn, UplinkServer
from edge.security import CommandVerifier, sign_audio, sign_event, sign_poll
from edge.sensor_node import Node
from edge.uplink import Uplink

SECRET = "test-secret"
DEVICE = "bath-01"


@pytest.fixture
def short_dir():
    # macOS tmp paths can exceed the 104-byte Unix socket path limit.
    d = Path(tempfile.mkdtemp(prefix="fh-", dir="/tmp"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def command(action, incident_id, nonce, clip_id=None):
    ts = int(time.time() * 1000)
    msg = f"{action}|{incident_id}|{ts}|{nonce}".encode()
    sig = hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()
    return {"id": clip_id or nonce, "action": action, "incident_id": incident_id, "ts": ts, "nonce": nonce, "sig": sig}


class FakeBackend:
    """Checks every signature the way backend/app/security.py does."""

    def __init__(self):
        self.events, self.polls, self.commands, self.bad = [], [], [], []
        self.lock = threading.Lock()

    def post(self, path, raw, headers):
        body = json.loads(raw)
        if headers.get("X-Signature") != sign_event(SECRET, body["ts"], raw):
            self.bad.append(path)
            return 401, None
        with self.lock:
            self.events.append(body)
        return 200, {}

    def get(self, path, headers=None):
        q = parse_qs(urlparse(path).query)
        ts = int(q["ts"][0])
        if headers.get("X-Signature") != sign_poll(SECRET, ts, q["device_id"][0]):
            self.bad.append(path)
            return 401, None
        with self.lock:
            self.polls.append(ts)
            cmds, self.commands = self.commands, []
        return 200, {"commands": cmds}

    def get_bytes(self, path, headers, timeout):
        u = urlparse(path)
        q = parse_qs(u.query)
        clip_id = u.path.rsplit("/", 1)[1]
        if headers.get("X-Signature") != sign_audio(SECRET, int(q["ts"][0]), q["device_id"][0], clip_id):
            self.bad.append(path)
            return 401, b""
        return 200, b"\0" * (44 + 32 * 1000)

    def types(self):
        with self.lock:
            return [e["type"] for e in self.events]


class Safety:
    """The safety process's loop, run in the test thread."""

    def __init__(self, d):
        self.writes = []
        self.node = Node(device_id=DEVICE, secret=SECRET, write_serial=self.writes.append, http=None,
                         outbox_path=d / "outbox.jsonl", state_path=d / "incident.json")
        self.server = self.node.uplink = UplinkServer(self.node, d / "up.sock")

    def until(self, check, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not check():
            assert time.monotonic() < deadline, "timed out"
            self.node.tick()
            self.server.pump()
            time.sleep(0.005)


def start_uplink(d, backend, stt=None):
    up = Uplink(socket_path=d / "up.sock", http=backend, device_id=DEVICE, stt=stt,
                poll_s=0.05, heartbeat_s=0.2, backoff_s=0.2)
    t = threading.Thread(target=up.run, daemon=True)
    t.start()
    return up, t


def kill_uplink(up, t):
    up.stop()
    t.join(2)
    if up.conn is not None:
        up.conn.close()


def test_events_heartbeats_polls_and_commands_cross_the_link_signed_by_the_safety_process(short_dir):
    backend, safety = FakeBackend(), Safety(short_dir)
    up, t = start_uplink(short_dir, backend, stt=lambda wav: "I need help")
    try:
        safety.until(lambda: "heartbeat" in backend.types() and backend.polls)
        safety.node.handle_line("B")
        incident_id = safety.node.incident["id"]
        safety.until(lambda: "help_button" in backend.types() and not len(safety.node.outbox))
        assert backend.types().count("fall_suspected") == 1

        backend.commands = [command("unlock", incident_id, "n1")]
        safety.until(lambda: "U" in safety.writes)

        backend.commands = [command("transcribe", incident_id, "n2", clip_id="clip-1")]
        safety.until(lambda: "speech_text" in backend.types())
        speech = next(e for e in backend.events if e["type"] == "speech_text")
        assert speech["local_incident_id"] == incident_id
        assert speech["speech"]["clip_id"] == "clip-1" and speech["speech"]["text"] == "I need help"
        assert speech["speech"]["audio_ms"] == 1000
        assert backend.bad == []
    finally:
        kill_uplink(up, t)
        safety.server.close()


def test_killed_uplink_never_stops_the_alarm_and_the_outbox_replays_on_restart(short_dir):
    backend, safety = FakeBackend(), Safety(short_dir)
    up, t = start_uplink(short_dir, backend)
    try:
        safety.until(lambda: "heartbeat" in backend.types())
        kill_uplink(up, t)
        safety.until(lambda: safety.server.conn is None)

        safety.writes.clear()
        safety.node.handle_line("B")
        assert safety.writes[:2] == ["A1", "D,TIME DOWN 00:00"]  # local alarm, no uplink involved
        safety.until(lambda: False if time.sleep(0.05) else True)
        assert len(safety.node.outbox) == 2

        up, t = start_uplink(short_dir, backend)
        safety.until(lambda: not len(safety.node.outbox))
        assert [x for x in backend.types() if x != "heartbeat"] == ["fall_suspected", "help_button"]
        assert safety.node.incident is not None
    finally:
        kill_uplink(up, t)
        safety.server.close()


def raw_client(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(path))
    return s


def test_speech_for_a_clip_the_safety_process_did_not_dispatch_is_refused(short_dir):
    safety = Safety(short_dir)
    c = raw_client(short_dir / "up.sock")
    try:
        safety.until(lambda: safety.server.conn is not None)
        c.sendall(b'{"t":"speech","clip_id":"forged","result":{"text":"I\'m fine"}}\n')
        safety.until(lambda: False if time.sleep(0.05) else True)
        assert not len(safety.node.outbox)
    finally:
        c.close()
        safety.server.close()


def test_pending_clip_fails_when_the_uplink_dies_so_silence_still_escalates(short_dir):
    safety = Safety(short_dir)
    c = raw_client(short_dir / "up.sock")
    try:
        safety.until(lambda: safety.server.conn is not None)
        safety.node.handle_line("B")
        incident_id = safety.node.incident["id"]
        safety.node._commands.put(command("transcribe", incident_id, "n3", clip_id="clip-9"))
        safety.until(lambda: "clip-9" in safety.server._clips)
        c.close()
        safety.until(lambda: safety.server.conn is None)
        events = [json.loads(i["raw"]) for i in safety.node.outbox.items]
        speech = [e["speech"] for e in events if e["type"] == "speech_text"]
        assert speech == [{"clip_id": "clip-9", "error": "uplink restarted"}]
    finally:
        safety.server.close()


def test_transcribe_with_no_uplink_answers_an_error_at_once(short_dir):
    safety = Safety(short_dir)
    try:
        safety.node.handle_line("B")
        safety.node._commands.put(command("transcribe", safety.node.incident["id"], "n4", clip_id="clip-2"))
        safety.node.tick()
        speech = [json.loads(i["raw"]).get("speech") for i in safety.node.outbox.items]
        assert {"clip_id": "clip-2", "error": "uplink down"} in speech
    finally:
        safety.server.close()


def test_an_uplink_that_stops_reading_is_dropped_without_blocking_the_safety_loop(short_dir, monkeypatch):
    monkeypatch.setattr(LineConn, "MAX_BUFFER", 4096)
    safety = Safety(short_dir)
    c = raw_client(short_dir / "up.sock")
    c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    try:
        safety.until(lambda: safety.server.conn is not None)
        for _ in range(400):
            safety.node._queue("heartbeat")
        t0 = time.monotonic()
        safety.until(lambda: safety.server.conn is None)
        assert time.monotonic() - t0 < 2
        assert len(safety.node.outbox) == 400  # nothing lost; resent to the next uplink
    finally:
        c.close()
        safety.server.close()


def test_restarted_safety_process_resumes_its_incident_and_refuses_older_commands(short_dir):
    now = [1000.0]
    writes = []
    kw = dict(device_id=DEVICE, secret=SECRET, write_serial=writes.append, http=None,
              outbox_path=short_dir / "outbox.jsonl", state_path=short_dir / "incident.json",
              clock=lambda: now[0])
    first = Node(**kw)
    first.handle_line("B")
    incident = dict(first.incident)
    now[0] += 7
    writes.clear()
    second = Node(**kw, resume=True)
    assert second.incident == incident
    assert writes == ["D,READY", "A1", "D,TIME DOWN 00:07"]
    old = {"action": "reset", "incident_id": "", "ts": int((now[0] - 1) * 1000), "nonce": "x"}
    old["sig"] = hmac.new(SECRET.encode(), f"reset||{old['ts']}|x".encode(), hashlib.sha256).hexdigest()
    assert second.verifier.check(old, local_incident_id=None) == (False, "signed before restart")

    second._close_incident("test")
    assert Node(**kw, resume=True).incident is None
    third = Node(**kw)
    third.handle_line("B")
    assert json.loads((short_dir / "incident.json").read_text())["incident"]
    assert Node(**kw).incident is None  # a full restart (no --resume) starts clean
    assert Node(**kw, resume=True).incident is None


def test_verifier_without_restart_accepts_fresh_commands():
    v = CommandVerifier(SECRET)
    cmd = command("reset", "", "n5")
    assert v.check(cmd, local_incident_id=None) == (True, "ok")


def test_supervisor_restarts_a_dead_child_with_its_restart_command_and_stops_it(tmp_path):
    marker = tmp_path / "restarted"
    child = supervisor.Child("x", [sys.executable, "-c", "pass"],
                             restart_cmd=[sys.executable, "-c",
                                          f"open({str(marker)!r}, 'w').close(); import time; time.sleep(60)"],
                             delay_s=0.1)
    child.poll(0.0)
    child.proc.wait(5)
    child.poll(10.0)  # notices the exit, schedules the restart
    assert child.proc is None
    child.poll(10.0 + child.delay_s)
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists() and child.starts == 2
    finally:
        child.stop()
    assert child.proc.poll() is not None


def test_supervisor_backs_off_a_child_that_keeps_dying_at_once():
    child = supervisor.Child("x", [sys.executable, "-c", "pass"], delay_s=1.0)
    child.poll(0.0)
    child.proc.wait(5)
    child.poll(0.5)
    assert child.delay_s == 2.0
    child.poll(2.5)
    child.proc.wait(5)
    child.poll(3.0)
    assert child.delay_s == 4.0


def test_supervisor_children_and_priorities(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.config, "WHISPER_MODEL", "")
    monkeypatch.setattr(supervisor.sys, "platform", "qnx8")
    monkeypatch.setattr(supervisor.shutil, "which", lambda name: "/usr/bin/" + name)
    safety, uplink = supervisor.build_children("--usb", "http://mac:8002", tmp_path / "s.sock")
    assert safety.cmd[:3] == ["on", "-p", "21"] and "--uplink-socket" in safety.cmd
    assert safety.restart_cmd == safety.cmd + ["--resume"]
    assert uplink.cmd[:3] == ["on", "-p", "10"] and "http://mac:8002" in uplink.cmd


def test_usb_worker_exits_cleanly_when_its_reader_dies_so_the_restart_can_claim_the_hub(monkeypatch, tmp_path):
    class FakeUsb:
        def reconnect(self):
            return False

        def readline(self, timeout):
            return "S,1,2,0,0,1,50"

        def write(self, cmd):
            pass

    class DeadPipe:
        def __init__(self, fd):
            self.fd = fd

        def write(self, s):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

        def fileno(self):
            return self.fd

    r, w = os.pipe()
    out = os.open(tmp_path / "stdout", os.O_WRONLY | os.O_CREAT)
    stdin = os.fdopen(r)
    monkeypatch.setattr(sensor_node, "UsbCdcSource", FakeUsb)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", DeadPipe(out))
    try:
        assert sensor_node._usb_worker() == 0
    finally:
        stdin.close()
        os.close(w)
        os.close(out)
