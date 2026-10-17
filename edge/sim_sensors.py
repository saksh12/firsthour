#!/usr/bin/env python3
"""Fake Arduino: prints section 6.1 serial lines at 20 Hz, for testing without hardware.

    python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin

Interactive (type a letter, then Enter):
    f  fall (thud + floor vibration together)     c  clap (sound only)
    s  stomp (vibration only)                      b  help button
    q  quit

Scripted, for repeatable runs:
    python3 edge/sim_sensors.py --script "clap@1,stomp@2,fall@3" --duration 8

The numbers here are made up to exercise the code. They are NOT calibration data.
"""

import argparse
import os
import random
import select
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge import config  # noqa: E402

SAMPLE_S = 0.05


class FakeArduino:
    def __init__(self, out=sys.stdout, rng=None):
        self.out = out
        self.rng = rng or random.Random(7)
        self.t0 = time.monotonic()
        self.pending = []  # one-shot spikes for the next sample
        self.floor_until = 0.0  # ultrasonic sees a body on the floor until then

    def ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    def emit(self, line):
        self.out.write(line + "\n")
        self.out.flush()

    def trigger(self, event):
        if event == "fall":
            self.pending.append(("both", None))
            self.floor_until = time.monotonic() + 10
        elif event == "clap":
            self.pending.append(("sound", None))
        elif event == "stomp":
            self.pending.append(("accel", None))
        elif event == "button":
            self.emit("B")
        else:
            print(f"sim: unknown event {event!r}", file=sys.stderr)

    def sample(self):
        r = self.rng
        sound = r.randint(5, 40)
        ax, ay, az = r.gauss(0, 0.02), r.gauss(0, 0.02), 1 + r.gauss(0, 0.02)
        while self.pending:
            kind, _ = self.pending.pop()
            if kind in ("both", "sound"):
                sound = r.randint(750, 900)
            if kind in ("both", "accel"):
                ax, ay, az = 0.8, 0.5, 2.6
        dist = 28 if time.monotonic() < self.floor_until else 120
        self.emit(f"S,{self.ms()},{sound},{ax:.2f},{ay:.2f},{az:.2f},{dist}")


KEYS = {"f": "fall", "": "fall", "c": "clap", "s": "stomp", "b": "button"}


def parse_script(text):
    plan = []
    for item in filter(None, (x.strip() for x in text.split(","))):
        name, _, at = item.partition("@")
        plan.append((float(at or 0), name))
    return sorted(plan)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", help='e.g. "clap@1,stomp@2,fall@3"')
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    args = ap.parse_args()

    sim = FakeArduino()
    plan = parse_script(args.script) if args.script else []
    interactive = not args.script and sys.stdin.isatty()
    if interactive:
        print(__doc__.split("Scripted")[0].strip(), file=sys.stderr)
    start = time.monotonic()
    next_sample = start
    try:
        while True:
            now = time.monotonic()
            elapsed = now - start
            if args.duration and elapsed >= args.duration:
                break
            while plan and plan[0][0] <= elapsed:
                sim.trigger(plan.pop(0)[1])
            if interactive:
                ready, _, _ = select.select([sys.stdin], [], [], 0)
                if ready:
                    key = sys.stdin.readline().strip().lower()
                    if key == "q":
                        break
                    if key in KEYS:
                        sim.trigger(KEYS[key])
            if now >= next_sample:
                sim.sample()
                next_sample += SAMPLE_S
            time.sleep(0.005)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            os._exit(0)


if __name__ == "__main__":
    main()
