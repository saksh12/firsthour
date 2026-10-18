"""Section 10.2 calibration tool: record -> CSV, analyze -> suggested thresholds + Fusion replay."""

import csv
import shutil
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from edge import calibrate, config
from edge.calibrate import (HEADER, Recorder, complete_trials, is_fall_label,
                            load_trials, replay, suggest_thresholds,
                            trial_is_complete, trial_peaks, write_config)
from edge.sensor_node import AccelDelta, Fusion


class FakeSrc:
    """Feed lines instantly; stall once after `stall_after` lines to emulate a
    producer gap so the record loop's deadline can split trials."""

    def __init__(self, lines, stall_after=None, stall_s=0):
        self.lines = deque(lines)
        self.stall_after = stall_after
        self.stall_s = stall_s
        self.eof = False
        self.n = 0

    def readline(self, timeout=0):
        if self.stall_after is not None and self.n == self.stall_after:
            self.stall_after = None
            time.sleep(self.stall_s)
            return None
        if not self.lines:
            self.eof = True
            return None
        self.n += 1
        return self.lines.popleft()


def s_line(i, sound=30):
    return f"S,{i * 50},{sound},0.0,0.0,1.0,120"


def record_args(**kw):
    base = dict(stdin=True, serial=None, label="clap", seconds=4, trials=None, dir=None)
    base.update(kw)
    return SimpleNamespace(**base)


def analyze_args(**kw):
    base = dict(dir=None, write=False, force=False)
    base.update(kw)
    return SimpleNamespace(**base)


def dense_trial(n=80, step_ms=50, sound=30, delta=0.05, peak_sound=800, peak_delta=2.0):
    """A realistic trial: n rows at the 20 Hz rate, one loud/jerky sample."""
    return [(i * step_ms, peak_sound if i == n // 2 else sound,
             0, 0, 1, 120, peak_delta if i == n // 2 else delta) for i in range(n)]


def make_csv(cal_dir, label, trials):
    """trials: list of trials, each a list of (ms, sound, ax, ay, az, dist, delta)."""
    path = Path(cal_dir) / f"{label}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for t, trial in enumerate(trials, start=1):
            for ms, sound, ax, ay, az, dist, delta in trial:
                w.writerow([label, t, ms, sound, ax, ay, az, dist, delta])
    return path


def test_record_writes_rows_from_serial_lines(tmp_path):
    rec = Recorder(tmp_path, "clap")
    assert rec.handle_line("S,1000,812,0.80,0.50,2.60,28")
    assert rec.handle_line("S,1050,30,0.01,0.02,1.00,120")
    rec.commit_trial()
    rec.close()
    rows = list(csv.DictReader((tmp_path / "clap.csv").open()))
    assert len(rows) == 2
    r = rows[0]
    assert (r["label"], r["trial"], r["ms"], r["sound_peak"], r["ax"], r["dist_cm"]) \
        == ("clap", "1", "1000", "812", "0.8", "28")
    # accel_delta_g is the real AccelDelta output for that exact sample stream
    acc = AccelDelta()
    acc.update(0.80, 0.50, 2.60)
    assert float(rows[1]["accel_delta_g"]) == pytest.approx(acc.update(0.01, 0.02, 1.00), abs=1e-4)


def test_record_appends_as_next_trial(tmp_path):
    rec = Recorder(tmp_path, "clap")
    rec.handle_line("S,1000,812,0.80,0.50,2.60,28")
    rec.commit_trial()
    rec.close()
    rec2 = Recorder(tmp_path, "clap")
    assert rec2.trial == 2
    rec2.close()


def test_garbage_and_non_sample_lines_are_skipped(tmp_path):
    rec = Recorder(tmp_path, "clap")
    for junk in ["", "garbage", "S,not,a,number", "S,1,2,3", "N,04A1B2C3D4", "B", "ACK,U"]:
        assert not rec.handle_line(junk)
    rec.commit_trial()
    rec.close()
    assert list(csv.DictReader((tmp_path / "clap.csv").open())) == []


def test_trial_peaks(tmp_path):
    make_csv(tmp_path, "clap", [[
        (0, 100, 0, 0, 1, 120, 0.1),
        (50, 500, 0, 0, 1, 120, 0.4),
        (100, 200, 0, 0, 1, 120, 0.2),
    ]])
    ((_, _, rows),) = load_trials(tmp_path)
    assert trial_peaks(rows) == (500, 0.4)


def test_load_trials_skips_malformed_rows(tmp_path):
    path = make_csv(tmp_path, "clap", [[(0, 100, 0, 0, 1, 120, 0.1)]])
    with path.open("a") as f:
        f.write("not,a,real,row\n")
    assert len(load_trials(tmp_path)) == 1


def test_sit_and_fall_labels_count_as_falls():
    assert is_fall_label("fall_beanbag") and is_fall_label("sit_mat") and is_fall_label("fall_anything")
    assert not is_fall_label("clap") and not is_fall_label("door_slam")


def test_suggest_clean_separation(tmp_path):
    make_csv(tmp_path, "fall_beanbag", [
        [(0, 800, 0, 0, 1, 28, 2.0)],
        [(0, 750, 0, 0, 1, 28, 1.8)],
    ])
    make_csv(tmp_path, "clap", [
        [(0, 300, 0, 0, 1, 120, 0.2)],
        [(0, 400, 0, 0, 1, 120, 0.3)],
    ])
    make_csv(tmp_path, "door_slam", [[(0, 500, 0, 0, 1, 120, 0.9)]])
    sug = suggest_thresholds(load_trials(tmp_path))
    # midpoint of the highest non-fall peak and the lowest fall peak, per axis
    assert sug["sound"]["suggested"] == pytest.approx((500 + 750) / 2)
    assert sug["accel"]["suggested"] == pytest.approx((0.9 + 1.8) / 2)
    assert sug["overlap"] is False


def test_single_axis_crossing_is_not_overlap(tmp_path):
    # The slam out-sounds a quiet fall, but its accel stays low: the accel axis
    # still separates it, and fusion needs both axes. Not an overlap.
    make_csv(tmp_path, "fall_beanbag", [[(0, 700, 0, 0, 1, 28, 2.0)], [(0, 750, 0, 0, 1, 28, 1.8)]])
    make_csv(tmp_path, "door_slam", [[(0, 800, 0, 0, 1, 120, 0.9)]])
    sug = suggest_thresholds(load_trials(tmp_path))
    assert sug["overlap"] is False
    # suggested sound threshold drops to the highest non-fall below fall-min (0)
    assert sug["sound"]["suggested"] == pytest.approx(350)
    assert sug["accel"]["suggested"] == pytest.approx(1.35)
    assert sug["sound"]["crossed_by"] == 1  # the slam, stopped on the accel axis


def test_true_overlap_is_reported_not_hidden(tmp_path):
    # A non-fall that crosses BOTH suggested thresholds cannot be separated.
    make_csv(tmp_path, "fall_beanbag", [[(0, 700, 0, 0, 1, 28, 2.0)], [(0, 750, 0, 0, 1, 28, 1.8)]])
    make_csv(tmp_path, "door_slam", [[(0, 800, 0, 0, 1, 120, 1.9)]])
    sug = suggest_thresholds(load_trials(tmp_path))
    assert sug["overlap"] is True
    assert sug["crossing"] == ["door_slam"]


def test_suggestion_needs_both_classes(tmp_path):
    make_csv(tmp_path, "clap", [[(0, 400, 0, 0, 1, 120, 0.3)]])
    sug = suggest_thresholds(load_trials(tmp_path))
    assert sug["sound"]["suggested"] is None and sug["accel"]["suggested"] is None


def test_replay_counts_match_fusion(tmp_path):
    fall = [(0, 30, 0, 0, 1, 120, 0.0), (100, 800, 0, 0, 1, 28, 2.5)]
    clap = [(0, 30, 0, 0, 1, 120, 0.0), (100, 400, 0, 0, 1, 120, 0.2)]
    slam = [(0, 30, 0, 0, 1, 120, 0.0), (100, 600, 0, 0, 1, 120, 1.1)]
    make_csv(tmp_path, "fall_beanbag", [fall, fall])
    make_csv(tmp_path, "clap", [clap])
    make_csv(tmp_path, "door_slam", [slam])
    trials = load_trials(tmp_path)

    assert replay(trials, 700, 1.75) == {"fall_beanbag": (2, 2), "clap": (0, 1), "door_slam": (0, 1)}
    # Looser thresholds let the door slam through, the clap still can't.
    assert replay(trials, 550, 1.0)["door_slam"] == (1, 1)

    # Sanity: same answer straight from the real Fusion for the fall trial.
    f = Fusion(700, 1.75, window_ms=400)
    assert any(f.update(ms, s, d) for ms, s, d in [(r[0], r[1], r[6]) for r in fall])


def test_write_config_updates_numbers_and_provenance(tmp_path):
    target = tmp_path / "config.py"
    shutil.copy(config.__file__, target)
    write_config(target, 625, 1.35, 42, when="2026-10-17")
    text = target.read_text()
    assert 'SOUND_THRESHOLD = _f("SOUND_THRESHOLD", "625")' in text
    assert 'ACCEL_THRESHOLD_G = _f("ACCEL_THRESHOLD_G", "1.35")' in text
    assert "# Fusion thresholds, from calibration on 2026-10-17, 42 trials." in text
    assert "PLACEHOLDER" not in text
    # Everything below the edited block is untouched.
    orig = Path(config.__file__).read_text()
    assert text.split("FUSION_WINDOW_MS", 1)[1] == orig.split("FUSION_WINDOW_MS", 1)[1]


def test_headless_run_records_exactly_one_trial(tmp_path, capsys):
    # 8 full-trial samples, a producer gap, then 6 tail samples: the tail must
    # not become a second trial. seconds=0.4 -> 8 expected at 20 Hz.
    lines = [s_line(i) for i in range(14)]
    src = FakeSrc(lines, stall_after=8, stall_s=0.5)
    calibrate.record(record_args(seconds=0.4, dir=str(tmp_path)), src=src, keys=None)
    rows = list(csv.DictReader((tmp_path / "clap.csv").open()))
    assert {r["trial"] for r in rows} == {"1"}
    assert len(rows) == 8
    assert "recording 1 trial(s)" in capsys.readouterr().err


def test_headless_trials_flag_records_n(tmp_path):
    lines = [s_line(i) for i in range(16)]
    src = FakeSrc(lines, stall_after=8, stall_s=0.5)
    calibrate.record(record_args(seconds=0.4, trials=2, dir=str(tmp_path)),
                     src=src, keys=None)
    rows = list(csv.DictReader((tmp_path / "clap.csv").open()))
    assert {r["trial"] for r in rows} == {"1", "2"}
    assert len(rows) == 16


def test_trial_cut_short_by_end_of_input_is_discarded(tmp_path, capsys):
    # 8 samples fill trial 1; the leftover 6-sample tail (<90% of 8) is dropped.
    lines = [s_line(i) for i in range(14)]
    src = FakeSrc(lines, stall_after=8, stall_s=0.5)
    calibrate.record(record_args(seconds=0.4, trials=2, dir=str(tmp_path)),
                     src=src, keys=None)
    rows = list(csv.DictReader((tmp_path / "clap.csv").open()))
    assert {r["trial"] for r in rows} == {"1"}
    assert len(rows) == 8
    assert "trial discarded: only 6 samples" in capsys.readouterr().err


def test_uncommitted_rows_never_reach_disk(tmp_path):
    rec = Recorder(tmp_path, "clap")
    rec.handle_line("S,1000,30,0,0,1,120")
    rec.close()  # no commit_trial()
    assert list(csv.DictReader((tmp_path / "clap.csv").open())) == []


def test_analyze_skips_undersized_trials(tmp_path, capsys):
    good = dense_trial()
    too_few_rows = dense_trial(n=5)                          # < 10 rows
    sparse = [(i * 400, 30, 0, 0, 1, 120, 0.1) for i in range(12)]  # 12 rows over 4.4 s
    make_csv(tmp_path, "clap", [good, too_few_rows, sparse])
    kept, dropped = complete_trials(load_trials(tmp_path))
    assert len(kept) == 1 and dropped == 2

    calibrate.analyze(analyze_args(dir=str(tmp_path)))
    out = capsys.readouterr().out
    assert "skipped 2 undersized trial(s)" in out
    assert "\n1 trials under" in out


def test_second_write_updates_provenance(tmp_path):
    target = tmp_path / "config.py"
    shutil.copy(config.__file__, target)
    write_config(target, 625, 1.35, 40, when="2026-10-17")
    write_config(target, 700, 1.5, 44, when="2026-10-18")
    text = target.read_text()
    assert "# Fusion thresholds, from calibration on 2026-10-18, 44 trials." in text
    assert "2026-10-17" not in text and "40 trials" not in text
    assert 'SOUND_THRESHOLD = _f("SOUND_THRESHOLD", "700")' in text
    assert 'ACCEL_THRESHOLD_G = _f("ACCEL_THRESHOLD_G", "1.5")' in text


def _overlapping_dir(tmp_path):
    # A door_slam that beats a fall on BOTH axes: genuinely inseparable.
    make_csv(tmp_path, "fall_beanbag", [dense_trial(peak_sound=700, peak_delta=2.0),
                                        dense_trial(peak_sound=750, peak_delta=1.8)])
    make_csv(tmp_path, "door_slam", [dense_trial(peak_sound=800, peak_delta=1.9)])


def test_write_refused_when_ranges_overlap(tmp_path, monkeypatch):
    target = tmp_path / "config.py"
    shutil.copy(config.__file__, target)
    monkeypatch.setattr(config, "__file__", str(target))
    _overlapping_dir(tmp_path)
    with pytest.raises(SystemExit) as e:
        calibrate.analyze(analyze_args(dir=str(tmp_path), write=True))
    assert "overlap" in str(e.value.code)
    assert "PLACEHOLDER" in target.read_text()  # untouched


def test_force_writes_and_marks_overlap(tmp_path, monkeypatch):
    target = tmp_path / "config.py"
    shutil.copy(config.__file__, target)
    monkeypatch.setattr(config, "__file__", str(target))
    _overlapping_dir(tmp_path)
    calibrate.analyze(analyze_args(dir=str(tmp_path), write=True, force=True))
    text = target.read_text()
    assert "(ranges overlapped)" in text
    assert 'SOUND_THRESHOLD = _f("SOUND_THRESHOLD", "350")' in text
    assert 'ACCEL_THRESHOLD_G = _f("ACCEL_THRESHOLD_G", "0.9")' in text
