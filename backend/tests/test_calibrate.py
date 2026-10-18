"""Section 10.2 calibration tool: record -> CSV, analyze -> suggested thresholds + Fusion replay."""

import csv
import shutil
from pathlib import Path

import pytest

from edge import config
from edge.calibrate import (HEADER, Recorder, is_fall_label, load_trials, replay,
                            suggest_thresholds, trial_peaks, write_config)
from edge.sensor_node import AccelDelta, Fusion


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
    rec.close()
    rec2 = Recorder(tmp_path, "clap")
    assert rec2.trial == 2
    rec2.close()


def test_garbage_and_non_sample_lines_are_skipped(tmp_path):
    rec = Recorder(tmp_path, "clap")
    for junk in ["", "garbage", "S,not,a,number", "S,1,2,3", "N,04A1B2C3D4", "B", "ACK,U"]:
        assert not rec.handle_line(junk)
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
    assert not sug["sound"]["overlap"] and not sug["accel"]["overlap"]


def test_overlap_is_reported_not_hidden(tmp_path):
    make_csv(tmp_path, "fall_beanbag", [[(0, 700, 0, 0, 1, 28, 2.0)], [(0, 750, 0, 0, 1, 28, 1.8)]])
    make_csv(tmp_path, "door_slam", [[(0, 800, 0, 0, 1, 120, 0.9)]])  # louder than a quiet fall
    sug = suggest_thresholds(load_trials(tmp_path))
    assert sug["sound"]["overlap"] is True
    assert sug["accel"]["overlap"] is False


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
