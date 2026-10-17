#!/usr/bin/env python3
"""Fire a fake fall through the whole backend, with stubs (the Phase 1 6:00 PM test).

Plays every part: the Pi (signed events, polling and verifying commands), the person
on the floor (answers), a stranger, and the caregiver (reading the stub inbox, replying).

Needs the backend running with STUB_PHOTON=1:
    cd backend && .venv/bin/uvicorn app.main:app --port 8000
    python3 scripts/simulate_fall.py            # person asks for help
    python3 scripts/simulate_fall.py --silent   # person says nothing; escalates after 8 s
    python3 scripts/simulate_fall.py --fine     # person says "I'm fine"; incident cancels

Stdlib only, so it also runs on the Pi.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from edge.security import CommandVerifier, sign_event  # noqa: E402

STRANGER = "+13105559999"  # fictional
ANSWERS = ["Help, I slipped and can't move my leg", "No", "My right hip", "No I can't", "No"]


def env(key: str, default: str = "") -> str:
    if key in os.environ:
        return os.environ[key]
    path = REPO / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].split(" #")[0].strip()
    return default


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body=None, headers=None, raw: bytes = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")


def step(msg: str) -> None:
    print(f"\n== {msg}")


def check(cond: bool, msg: str) -> None:
    print(f"   {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--device", default="bath-01")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--silent", action="store_true", help="person never answers")
    mode.add_argument("--fine", action="store_true", help="person says I'm fine")
    args = ap.parse_args()

    secret = env("SHARED_SECRET")
    caregiver = env("CAREGIVER_NUMBERS").split(",")[0].strip()
    if not secret or not caregiver:
        sys.exit("SHARED_SECRET and CAREGIVER_NUMBERS must be set (env or .env)")
    api = Api(args.url)
    pi = CommandVerifier(secret)

    step("Backend health")
    status, body = api.call("GET", "/health")
    check(status == 200, f"/health -> {status} {body}")
    status, body = api.call("GET", "/incident")
    if body and body.get("state") != "IDLE":
        print("   incident already open; resolving it first")
        api.call("POST", "/incident/resolve")

    step("Pi posts a signed fall_suspected event")
    ts = int(time.time() * 1000)
    raw = json.dumps({
        "device_id": args.device, "ts": ts, "type": "fall_suspected",
        "sensors": {"sound_peak": 812, "accel_delta_g": 1.9, "dist_cm": 28},
        "local_incident_id": f"{args.device}-{ts}",
    }).encode()
    status, body = api.call("POST", "/edge/event", raw=raw, headers={"X-Signature": sign_event(secret, ts, raw)})
    check(status == 200 and body["state"] == "CHECKING", f"event accepted, state {body.get('state')}")

    bad_raw = raw.replace(b"812", b"813")
    status, _ = api.call("POST", "/edge/event", raw=bad_raw, headers={"X-Signature": sign_event(secret, ts, raw)})
    check(status == 401, "tampered event rejected")

    if args.fine:
        step("Person: \"I'm fine\"")
        _, snap = api.call("POST", "/person/answer", {"text": "I'm fine"})
        check(snap["state"] == "IDLE", "incident cancelled, back to IDLE")
        _, inbox = api.call("GET", "/comms/stub/outbox")
        check(not any(m["incident_id"] == f"{args.device}-{ts}" for m in inbox["messages"]), "no alert sent")
        print("\nDONE (cancel path)")
        return

    if args.silent:
        step("Person says nothing; waiting for the 8 s silence escalation")
        for _ in range(40):
            time.sleep(0.25)
            _, snap = api.call("GET", "/incident")
            if snap["state"] == "ALERTING":
                break
        check(snap["state"] == "ALERTING", "silence escalated to ALERTING")
    else:
        step("Person answers the scripted questions")
        for a in ANSWERS:
            _, snap = api.call("POST", "/person/answer", {"text": a})
            print(f"   said: {a!r:45} state={snap['state']}")
        check(snap["state"] == "ALERTING", "escalated to ALERTING")
        t = snap["triage"]
        print(f"   triage: {t['summary']}")
        check(t["can_move"] is False and t["pain_location"] == "right hip", "triage record filled from answers")

    step("Caregiver phone (stub inbox) gets the alert")
    _, inbox = api.call("GET", "/comms/stub/outbox")
    alert = inbox["messages"][-1]["text"]
    print("   " + alert.replace("\n", "\n   "))
    code = re.search(r"UNLOCK (\d{4})", alert).group(1)

    step("Stranger texts UNLOCK with the right code")
    _, r = api.call("POST", "/comms/inbound", {"sender": STRANGER, "text": f"UNLOCK {code}"})
    check(r == {"type": "unlock_attempt", "allowed": False, "reason": "not enrolled"}, f"refused: {r['reason']}")
    _, cmds = api.call("GET", f"/edge/commands?device_id={args.device}")
    check(cmds["commands"] == [], "no command reached the Pi")

    step("Caregiver replies with a message")
    _, r = api.call("POST", "/comms/inbound", {"sender": caregiver, "text": "I'm 2 minutes away"})
    _, snap = api.call("GET", "/incident")
    check(r["accepted"] and snap["state"] == "RESPONDING", f"read aloud, state {snap['state']}")

    step("Caregiver replies UNLOCK <code>")
    _, r = api.call("POST", "/comms/inbound", {"sender": caregiver, "text": f"UNLOCK {code}"})
    check(r["allowed"] is True, "unlock granted by backend")

    step("Pi polls and verifies the signed command")
    _, cmds = api.call("GET", f"/edge/commands?device_id={args.device}")
    check(len(cmds["commands"]) == 1 and cmds["commands"][0]["action"] == "unlock", "one unlock command")
    cmd = cmds["commands"][0]
    ok, reason = pi.check(cmd, local_incident_open=True)
    check(ok, f"Pi accepts: {reason}  (real Pi would now send U to the Arduino)")
    ok, reason = pi.check(cmd, local_incident_open=True)
    check(not ok and reason == "replay", "replaying the same command is refused")

    step("Operator resolves")
    _, r = api.call("POST", "/incident/resolve")
    check(r["state"] == "IDLE", "back to IDLE")
    _, r = api.call("POST", "/comms/inbound", {"sender": caregiver, "text": f"UNLOCK {code}"})
    check(r["reason"] == "no active incident", "old code is dead")

    print("\nDONE: full stubbed vertical slice passed")


if __name__ == "__main__":
    main()
