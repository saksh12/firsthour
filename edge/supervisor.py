#!/usr/bin/env python3
"""Starts the Pi's processes at fixed QNX priorities and restarts any that die (issue #22 step 1).

    safety          priority 21  sensors, fusion, incident, alarm, clock, signatures, command
                                 checks and the latch (sensor_node.py --uplink-socket)
    uplink          priority 10  HTTP to the backend, outbox delivery, clip fetch (uplink.py);
                                 allowed to hang or die
    whisper-server  priority  8  speech-to-text on 127.0.0.1 only, when WHISPER_MODEL is set

The safety process owns the USB helper, so the helper runs at its priority too. QNX's HAM is
not on the image, so this loop is the restarter. A restarted safety process resumes its open
incident (alarm, clock) and refuses commands signed before it started.

On the Pi (root, for libusb):
    sudo sh -c 'WHISPER_MODEL=... python3 edge/supervisor.py --usb --backend http://<mac>:8002'
On the laptop:
    python3 edge/sim_sensors.py | python3 edge/supervisor.py --stdin
Stop it with SIGTERM; it stops every child.
"""

import argparse
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge import config  # noqa: E402
from edge.sensor_node import Transcriber  # noqa: E402

log = logging.getLogger("firsthour.supervisor")
EDGE = Path(__file__).resolve().parent


def at_priority(priority: int) -> list:
    """`on -p` on QNX, so pidin shows the real priority. Elsewhere, run as is."""
    if sys.platform.startswith("qnx") and shutil.which("on"):
        return ["on", "-p", str(priority)]
    return []


class Child:
    """One supervised process, restarted after it exits. A child that dies within
    FAST_EXIT_S of starting waits twice as long each time, up to MAX_DELAY_S."""

    FAST_EXIT_S = 5.0
    MAX_DELAY_S = 30.0

    def __init__(self, name, cmd, restart_cmd=None, delay_s=1.0, inherit_stdin=False, quiet=False):
        self.name, self.cmd, self.restart_cmd = name, cmd, restart_cmd
        self.base_delay_s = self.delay_s = delay_s
        self.inherit_stdin, self.quiet = inherit_stdin, quiet
        self.proc = None
        self.starts = 0
        self._started_at = 0.0
        self._due = 0.0

    def poll(self, now: float) -> None:
        if self.proc is not None:
            status = self.proc.poll()
            if status is None:
                return
            if now - self._started_at < self.FAST_EXIT_S:
                self.delay_s = min(self.MAX_DELAY_S, self.delay_s * 2)
            else:
                self.delay_s = self.base_delay_s
            log.warning("%s exited pid=%s status=%s; restarting in %.0f s", self.name, self.proc.pid, status,
                        self.delay_s)
            self.proc = None
            self._due = now + self.delay_s
        if now < self._due:
            return
        cmd = self.restart_cmd if self.starts and self.restart_cmd else self.cmd
        out = subprocess.DEVNULL if self.quiet else None
        # Own process group, so stop() also reaches what `on` started.
        self.proc = subprocess.Popen(cmd, stdin=None if self.inherit_stdin else subprocess.DEVNULL,
                                     stdout=out, stderr=out, start_new_session=True)
        self.starts += 1
        self._started_at = now
        log.info("started %s pid=%s: %s", self.name, self.proc.pid, " ".join(str(c) for c in cmd))

    def stop(self, timeout_s: float = 5.0) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.proc.pid, sig)
            except OSError:
                self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout_s)
                return
            except subprocess.TimeoutExpired:
                log.warning("%s ignored SIGTERM; killing", self.name)


def build_children(source_flag: str, backend: str, socket_path: Path) -> list:
    py = sys.executable
    safety = [*at_priority(config.SAFETY_PRIORITY), py, "-u", str(EDGE / "sensor_node.py"), source_flag,
              "--uplink-socket", str(socket_path)]
    children = [
        Child("safety", safety, restart_cmd=safety + ["--resume"], inherit_stdin=source_flag == "--stdin"),
        Child("uplink", [*at_priority(config.UPLINK_PRIORITY), py, "-u", str(EDGE / "uplink.py"),
                         "--socket", str(socket_path), "--backend", backend]),
    ]
    if config.WHISPER_MODEL and Path(config.WHISPER_MODEL).is_file():
        server_cmd = Transcriber(config.WHISPER_MODEL).server_cmd  # already at WHISPER_PRIORITY
        if server_cmd:
            # Output discarded: it would include the person's words.
            children.append(Child("whisper-server", server_cmd, quiet=True))
    return children


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--usb", action="store_true", help="BOX-3 over raw USB (QNX; run as root)")
    src.add_argument("--stdin", action="store_true", help="sensor lines from stdin (sim_sensors.py)")
    ap.add_argument("--backend", default=config.BACKEND_URL)
    ap.add_argument("--socket", default=str(config.LOG_DIR / "uplink.sock"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    children = build_children("--usb" if args.usb else "--stdin", args.backend, Path(args.socket))
    stopping = []
    signal.signal(signal.SIGTERM, lambda signum, frame: stopping.append(signum))
    log.info("supervisor up pid=%d children=%s", os.getpid(), ",".join(c.name for c in children))
    try:
        while not stopping:
            for child in children:
                child.poll(time.monotonic())
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            child.stop()
        log.info("supervisor stopped")


if __name__ == "__main__":
    main()
