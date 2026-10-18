"""A hub reboot must not clear the Pi's incident or replay an unlock."""

import json
import subprocess
import sys
import time

import pytest

from edge.sensor_node import Node, UsbWorkerSource


def test_reconnect_restores_ready_without_sounding_alarm(tmp_path):
    writes = []
    node = Node(device_id="test", secret="test-secret", write_serial=writes.append,
                http=None, outbox_path=tmp_path / "outbox.jsonl")
    writes.clear()
    node.handle_line("I,box3 imu=ok mic=ok speaker=ok lcd=ok")
    assert writes == ["L", "A0", "D,READY"]
    assert node.incident is None
    assert not len(node.outbox)


def test_reconnect_preserves_incident_clock_and_security_but_fails_pending_unlock(tmp_path):
    writes, now = [], [1000.0]
    node = Node(device_id="test", secret="test-secret", write_serial=writes.append,
                http=None, outbox_path=tmp_path / "outbox.jsonl", clock=lambda: now[0])
    node.handle_line("B")
    incident = node.incident
    node.verifier._seen["used-nonce"] = 1_000_000
    node._pending_unlock = {"incident_id": incident["id"], "at": now[0]}
    node.fusion.last_ms, node.fusion.cooldown_until = 100_000, 130_000
    node.fusion.last_sound, node.fusion.last_accel = 100_000, 99_000
    writes.clear()
    now[0] += 5
    node.hardware_reconnected()
    assert writes == ["L", "A1", "D,TIME DOWN 00:05"]
    assert node.incident is incident and incident["opened_at"] == 1000.0
    assert node.verifier._seen == {"used-nonce": 1_000_000}
    assert node.fusion.last_sound is None and node.fusion.last_accel is None
    assert node.fusion.cooldown_until == 130_000  # a cable reconnect alone must not shorten cooldown
    assert node._pending_unlock is None
    node.handle_line("ACK,U")  # a late acknowledgement cannot claim the latch opened
    events = [json.loads(item["raw"])["type"] for item in node.outbox.items]
    assert events == ["fall_suspected", "help_button", "unlock_failed"]
    node.handle_line("S,50,20,0,0,-1,-1")  # restarted hub clock clears the old-clock cooldown
    assert node.fusion.cooldown_until is None
    assert node.incident is incident


@pytest.mark.parametrize("failure", ["abort", "signal"])
def test_native_worker_exit_keeps_parent_incident_and_does_not_replay_unlock(tmp_path, monkeypatch, failure):
    commands = tmp_path / "commands.log"
    launches = []
    popen = subprocess.Popen
    program = """
import os, signal, sys
print('I,box3 usb=connected', flush=True)
for command in sys.stdin:
    with open(sys.argv[1], 'a') as log:
        log.write(command)
    if command.strip() == 'U' and sys.argv[2] == 'crash':
        if sys.argv[3] == 'signal':
            os.kill(os.getpid(), signal.SIGTERM)
        os._exit(134)
"""

    def launch(args, **kwargs):
        launches.append(args)
        return popen([sys.executable, "-u", "-c", program, str(commands),
                      "crash" if len(launches) == 1 else "healthy", failure], **kwargs)

    monkeypatch.setattr("edge.sensor_node.subprocess.Popen", launch)
    source = UsbWorkerSource()
    node = Node(device_id="test", secret="test-secret", write_serial=source.write,
                http=None, outbox_path=tmp_path / "outbox.jsonl")

    def tick():
        source.reconnect()
        line = source.readline(0.02)
        if line:
            node.handle_line(line)
        node.tick()

    def until(check):
        deadline = time.monotonic() + 4
        while not check() and time.monotonic() < deadline:
            tick()
        assert check()

    try:
        until(lambda: source._ready)
        node.handle_line("B")
        incident = node.incident
        node.verifier._seen["already-used"] = 123
        source.write("U")
        node._pending_unlock = {"incident_id": incident["id"], "at": time.time()}
        until(lambda: len(launches) == 2 and source._ready)
        assert node.incident is incident
        assert node.verifier._seen == {"already-used": 123}
        assert node._pending_unlock is None
        events = [json.loads(item["raw"])["type"] for item in node.outbox.items]
        assert events == ["fall_suspected", "help_button", "unlock_failed"]
        # Wait for the helper to consume the restored outputs before examining its log.
        until(lambda: commands.exists() and commands.read_text().count("A1\n") >= 2)
        assert commands.read_text().splitlines().count("U") == 1
        assert commands.read_text().splitlines().count("L") == 2
    finally:
        source.close()


def test_worker_configuration_failure_is_visible_instead_of_restarted(tmp_path, monkeypatch):
    popen = subprocess.Popen
    monkeypatch.setattr("edge.sensor_node.subprocess.Popen", lambda args, **kwargs:
                        popen([sys.executable, "-c", "raise SystemExit(1)"], **kwargs))
    source = UsbWorkerSource()
    try:
        source.reconnect()
        source._proc.wait(timeout=3)
        with pytest.raises(RuntimeError, match="status 1"):
            source.reconnect()
    finally:
        source.close()
