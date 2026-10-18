"""The QNX recorder uses the real USB source contract without needing hardware."""

import csv
import sys
from types import SimpleNamespace

from edge import calibrate


def test_usb_recording_writes_a_complete_sensor_trial(monkeypatch, tmp_path):
    lines = iter(f"S,{i * 50},50,0,0,-1,-1\r\n" for i in range(80))
    source = SimpleNamespace(eof=False)

    def readline(timeout):
        line = next(lines, None)
        source.eof = line is None
        return line

    source.readline = readline
    monkeypatch.setattr(calibrate, "UsbCdcSource", lambda: source)
    args = SimpleNamespace(usb=True, stdin=False, serial=None, label="demo_bottle_impact",
                           seconds=4, trials=1, dir=tmp_path)
    calibrate.record(args, keys=None)
    with (tmp_path / "demo_bottle_impact.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 80
    assert calibrate.trial_is_complete(rows)
    assert {row["label"] for row in rows} == {"demo_bottle_impact"}
    assert not calibrate.is_fall_label("demo_bottle_impact")


def test_usb_record_cli_selects_usb(monkeypatch):
    captured = []
    monkeypatch.setattr(sys, "argv", ["calibrate", "record", "--usb", "--label", "demo_bottle_impact"])
    monkeypatch.setattr(calibrate, "record", captured.append)
    calibrate.main()
    assert captured[0].usb and not captured[0].stdin and captured[0].serial is None


def test_waiting_for_operator_keeps_up_with_twenty_hz_samples(monkeypatch):
    elapsed, drained = 0.0, 0
    keys = SimpleNamespace(readline=lambda: "\n")

    def select(readers, writers, errors, timeout):
        nonlocal elapsed
        elapsed = min(0.5, elapsed + timeout)
        return ([keys] if elapsed >= 0.5 else [], [], [])

    def readline(timeout):
        nonlocal drained
        if drained < int(elapsed * 20):
            drained += 1
            return "S,0,50,0,0,-1,-1"
        return None

    monkeypatch.setattr(calibrate.select, "select", select)
    source = SimpleNamespace(eof=False, readline=readline)
    assert calibrate._wait_for_start(keys, source)
    assert drained >= 9  # The half-second wait produces ten samples.
