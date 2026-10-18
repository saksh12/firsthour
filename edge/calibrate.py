#!/usr/bin/env python3
"""Section 10.2 calibration: record labeled trials, pick fusion thresholds from the data.

Stdlib only — this runs on the Pi under QNX. Reuses parse_line, AccelDelta and
Fusion from sensor_node.py so the numbers here mean the same thing as at runtime.

Record labeled trials (Enter starts each trial, q quits):
    python3 edge/sim_sensors.py | python3 edge/calibrate.py record --stdin --label clap
    python3 edge/calibrate.py record --serial /dev/<port> --label fall_beanbag

Standard labels: fall_beanbag, sit_mat, bottle_drop, door_slam, footsteps, clap.
Labels starting with fall_ or sit_ count as falls; anything else counts as a non-fall.

Analyze what you recorded:
    python3 edge/calibrate.py analyze
    python3 edge/calibrate.py analyze --write    # also update edge/config.py
"""

import argparse
import csv
import re
import select
import sys
import time
from datetime import date
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge import config  # noqa: E402
from edge.sensor_node import (  # noqa: E402
    AccelDelta,
    Fusion,
    SerialSource,
    StdinSource,
    parse_line,
)

CAL_DIR = Path(__file__).resolve().parent / "calibration"
FALL_PREFIXES = ("fall_", "sit_")
HEADER = ["label", "trial", "ms", "sound_peak", "ax", "ay", "az", "dist_cm", "accel_delta_g"]


# --- record --------------------------------------------------------------------


class Recorder:
    """Appends labeled trials to <cal_dir>/<label>.csv."""

    def __init__(self, cal_dir, label):
        self.label = label
        self.path = Path(cal_dir) / f"{label}.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.trial = self._next_trial()
        new = not self.path.exists() or self.path.stat().st_size == 0
        self._fh = self.path.open("a", newline="")
        self.writer = csv.writer(self._fh)
        if new:
            self.writer.writerow(HEADER)
        self.accel = AccelDelta()
        self.rows = 0

    def _next_trial(self):
        trials = set()
        if self.path.exists():
            with self.path.open(newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        trials.add(int(row["trial"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        return max(trials, default=0) + 1

    def handle_line(self, line):
        """One serial line -> one CSV row when it is a sensor sample. True if written."""
        msg = parse_line(line)
        if msg is None or msg["kind"] != "S":
            return False
        delta = self.accel.update(msg["ax"], msg["ay"], msg["az"])
        self.writer.writerow([self.label, self.trial, msg["ms"], msg["sound"],
                              msg["ax"], msg["ay"], msg["az"], msg["dist"], f"{delta:.4f}"])
        self._fh.flush()
        self.rows += 1
        return True

    def close(self):
        self._fh.close()


def _key_stream(stdin_is_sensors):
    """Where the operator's Enter/q comes from. With --stdin the sensors occupy
    stdin, so keys come from the controlling tty; headless -> None (auto-trials)."""
    if not stdin_is_sensors:
        return sys.stdin
    try:
        return open("/dev/tty")
    except OSError:
        return None


def _wait_for_start(keys, src):
    """True -> start a trial; False -> quit. Drains sensor lines while waiting."""
    while True:
        if getattr(src, "eof", False):
            return False
        if keys is None:
            return True
        ready, _, _ = select.select([keys], [], [], 0.15)
        if ready:
            line = keys.readline()
            if line == "" or line.strip().lower() == "q":
                return False
            return True
        src.readline(0)  # keep the pipe draining so the producer never blocks


def _record_trial(src, rec, seconds):
    start = rec.rows
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not getattr(src, "eof", False):
        line = src.readline(0.05)
        if line is not None:
            rec.handle_line(line)
    return rec.rows - start


def record(args):
    src = StdinSource() if args.stdin else SerialSource(args.serial, config.SERIAL_BAUD)
    keys = _key_stream(args.stdin)
    rec = Recorder(args.dir or CAL_DIR, args.label)
    print(f"recording to {rec.path}", file=sys.stderr)
    print(f"Enter starts a {args.seconds:g}s trial, q quits", file=sys.stderr)
    if keys is None:
        print("no tty for keypresses: trials record back to back until input ends", file=sys.stderr)
    try:
        while _wait_for_start(keys, src):
            n = _record_trial(src, rec, args.seconds)
            print(f"trial {rec.trial}: {n} samples", file=sys.stderr)
            rec.trial += 1
            if getattr(src, "eof", False):
                break
    except KeyboardInterrupt:
        pass
    finally:
        rec.close()


# --- analyze -------------------------------------------------------------------


def load_trials(cal_dir):
    """-> [(label, trial, rows)] from every CSV under cal_dir."""
    trials = []
    for path in sorted(Path(cal_dir).glob("*.csv")):
        by_trial = {}
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                try:
                    key = (row["label"], int(row["trial"]))
                except (KeyError, TypeError, ValueError):
                    continue  # a half-written or foreign line, skip it
                by_trial.setdefault(key, []).append(row)
        trials.extend((label, trial, rows)
                      for (label, trial), rows in sorted(by_trial.items()))
    return trials


def trial_peaks(rows):
    """(peak sound_peak, peak accel_delta_g) for one trial."""
    return (max(float(r["sound_peak"]) for r in rows),
            max(float(r["accel_delta_g"]) for r in rows))


def is_fall_label(label):
    return label.startswith(FALL_PREFIXES)


def suggest_thresholds(trials):
    """Per axis: midpoint between the highest non-fall peak and the lowest fall
    peak, plus whether the two ranges actually separate."""
    falls = [trial_peaks(r) for l, _, r in trials if is_fall_label(l)]
    non_falls = [trial_peaks(r) for l, _, r in trials if not is_fall_label(l)]
    out = {}
    for i, axis in enumerate(("sound", "accel")):
        if not falls or not non_falls:
            out[axis] = {"suggested": None,
                         "reason": "need both fall (fall_*/sit_*) and non-fall trials"}
            continue
        nf_max = max(p[i] for p in non_falls)
        f_min = min(p[i] for p in falls)
        out[axis] = {"suggested": (nf_max + f_min) / 2,
                     "non_fall_max": nf_max, "fall_min": f_min,
                     "overlap": nf_max >= f_min}
    return out


def replay(trials, sound_t, accel_t, window_ms=config.FUSION_WINDOW_MS):
    """Feed each trial through a fresh real Fusion. -> {label: (triggered, total)}."""
    per_label = {}
    for label, _, rows in trials:
        fusion = Fusion(sound_t, accel_t, window_ms=window_ms)
        hit = any(fusion.update(int(r["ms"]), float(r["sound_peak"]),
                                float(r["accel_delta_g"])) for r in rows)
        hit_n, total = per_label.get(label, (0, 0))
        per_label[label] = (hit_n + (1 if hit else 0), total + 1)
    return per_label


def write_config(config_path, sound_t, accel_t, n_trials, when=None):
    """Put the measured numbers into config.py with provenance."""
    when = when or date.today().isoformat()
    text = Path(config_path).read_text()
    text = re.sub(r"# Fusion thresholds\.[^\n]*\n(?:#[^\n]*\n)*",
                  f"# Fusion thresholds, from calibration on {when}, {n_trials} trials.\n",
                  text, count=1)
    text = re.sub(r'(SOUND_THRESHOLD = _f\("SOUND_THRESHOLD", ")[^"]*("\))',
                  rf"\g<1>{int(round(sound_t))}\g<2>", text, count=1)
    text = re.sub(r'(ACCEL_THRESHOLD_G = _f\("ACCEL_THRESHOLD_G", ")[^"]*("\))',
                  rf"\g<1>{accel_t:.3g}\g<2>", text, count=1)
    Path(config_path).write_text(text)


def analyze(args):
    cal_dir = Path(args.dir or CAL_DIR)
    trials = load_trials(cal_dir)
    if not trials:
        sys.exit(f"no CSVs under {cal_dir} - record some trials first")

    by_label = {}
    for label, _, rows in trials:
        by_label.setdefault(label, []).append(trial_peaks(rows))

    print(f"{len(trials)} trials under {cal_dir}  (* = counts as a fall)\n")
    print(f"{'label':<16} {'trials':>6} {'sound peak min/med/max':>26} {'accel peak min/med/max':>26}")
    for label in sorted(by_label):
        peaks = by_label[label]
        s = [p[0] for p in peaks]
        a = [p[1] for p in peaks]
        mark = "*" if is_fall_label(label) else " "
        print(f"{mark} {label:<15} {len(peaks):>6} "
              f"{f'{min(s):g} / {median(s):g} / {max(s):g}':>26} "
              f"{f'{min(a):.3g} / {median(a):.3g} / {max(a):.3g}':>26}")

    sug = suggest_thresholds(trials)
    print("\nSuggested thresholds (midpoint of highest non-fall and lowest fall peaks):")
    for axis in ("sound", "accel"):
        name = "SOUND_THRESHOLD" if axis == "sound" else "ACCEL_THRESHOLD_G"
        info = sug[axis]
        if info["suggested"] is None:
            print(f"  {name}: cannot suggest - {info['reason']}")
            continue
        note = "   <-- RANGES OVERLAP, no clean split" if info["overlap"] else ""
        print(f"  {name} = {info['suggested']:.4g}   "
              f"(non-fall max {info['non_fall_max']:.4g} / fall min {info['fall_min']:.4g}){note}")

    cur_sound, cur_accel = config.SOUND_THRESHOLD, config.ACCEL_THRESHOLD_G
    new_sound = sug["sound"]["suggested"] if sug["sound"]["suggested"] is not None else cur_sound
    new_accel = sug["accel"]["suggested"] if sug["accel"]["suggested"] is not None else cur_accel

    rep_new = replay(trials, new_sound, new_accel)
    rep_cur = replay(trials, cur_sound, cur_accel)
    print(f"\nReplay through Fusion (window {config.FUSION_WINDOW_MS} ms): trials that triggered")
    print(f"{'label':<16} {'suggested':>14} {'current':>14}")
    for label in sorted(rep_new):
        n1, m = rep_new[label]
        n2, _ = rep_cur[label]
        print(f"{label:<16} {f'{n1} of {m}':>14} {f'{n2} of {m}':>14}")
    false_hits = sum(rep_new[l][0] for l in rep_new if not is_fall_label(l))
    misses = sum(rep_new[l][1] - rep_new[l][0] for l in rep_new if is_fall_label(l))
    print(f"\nwith the suggested thresholds: {false_hits} non-fall trials triggered, "
          f"{misses} fall trials missed")

    if args.write:
        if sug["sound"]["suggested"] is None or sug["accel"]["suggested"] is None:
            print("not writing config.py: a suggestion needs fall and non-fall trials")
        else:
            write_config(config.__file__, new_sound, new_accel, len(trials))
            print(f"wrote {config.__file__}: SOUND_THRESHOLD={new_sound:g} "
                  f"ACCEL_THRESHOLD_G={new_accel:.3g}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record labeled trials to edge/calibration/")
    src = r.add_mutually_exclusive_group(required=True)
    src.add_argument("--stdin", action="store_true",
                     help="read serial lines from stdin (sim_sensors.py)")
    src.add_argument("--serial", metavar="PORT", help="Arduino serial port")
    r.add_argument("--label", required=True,
                   help="trial label, e.g. " + ", ".join(
                       ("fall_beanbag", "sit_mat", "bottle_drop", "door_slam",
                        "footsteps", "clap")) + ", or any custom string")
    r.add_argument("--seconds", type=float, default=4, help="seconds per trial (default 4)")
    r.add_argument("--dir", help="calibration directory (default edge/calibration)")

    a = sub.add_parser("analyze", help="summarize trials and suggest thresholds")
    a.add_argument("--write", action="store_true",
                   help="write the suggested thresholds into edge/config.py")
    a.add_argument("--dir", help="calibration directory (default edge/calibration)")

    args = ap.parse_args()
    (record if args.cmd == "record" else analyze)(args)


if __name__ == "__main__":
    main()
