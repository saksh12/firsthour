"""Pi side of the unlock suite (IMPLEMENTATION.md section 9.4, cases 7-9 and 11).

Case 10 (the latch relocks after the hold time) lives in the Arduino sketch and has
to be checked on the hardware.
"""

import logging

import pytest

from app.security import make_command
from conftest import SECRET
from edge.security import CommandVerifier


@pytest.fixture
def verifier(clock) -> CommandVerifier:
    return CommandVerifier(SECRET, clock=clock)


def unlock_cmd(clock, **overrides) -> dict:
    cmd = make_command(SECRET, "unlock", "inc-1", now_ms=clock.ms()).model_dump()
    cmd.update(overrides)
    return cmd


def test_fresh_signed_unlock_with_local_incident_accepted(verifier, clock):
    assert verifier.check(unlock_cmd(clock), local_incident_id="inc-1") == (True, "ok")


def test_7_replayed_nonce_refused(verifier, clock):
    cmd = unlock_cmd(clock)
    assert verifier.check(cmd, local_incident_id="inc-1")[0]
    assert verifier.check(cmd, local_incident_id="inc-1") == (False, "replay")


def test_8_command_older_than_10s_refused(verifier, clock):
    cmd = unlock_cmd(clock)
    clock.advance(10.5)
    assert verifier.check(cmd, local_incident_id="inc-1") == (False, "stale")


def test_8b_command_from_the_future_refused(verifier, clock):
    # Backend clock 11 s ahead of the Pi: still outside the window.
    cmd = make_command(SECRET, "unlock", "inc-1", now_ms=clock.ms() + 11_000).model_dump()
    assert verifier.check(cmd, local_incident_id="inc-1") == (False, "stale")


def test_9_wrong_signature_refused(verifier, clock):
    cmd = make_command("wrong-secret", "unlock", "inc-1", now_ms=clock.ms()).model_dump()
    assert verifier.check(cmd, local_incident_id="inc-1") == (False, "bad signature")


@pytest.mark.parametrize("field,value", [("action", "lock"), ("incident_id", "inc-2"), ("ts", 1), ("nonce", "other")])
def test_9b_tampering_with_any_signed_field_refused(verifier, clock, field, value):
    cmd = unlock_cmd(clock, **{field: value})
    assert verifier.check(cmd, local_incident_id="inc-1") == (False, "bad signature")


def test_9c_malformed_command_refused(verifier):
    assert verifier.check({"action": "unlock"}, local_incident_id="inc-1") == (False, "malformed")


def test_unlock_without_local_incident_refused(verifier, clock):
    assert verifier.check(unlock_cmd(clock), local_incident_id=None) == (False, "no local incident")


def test_refused_unlock_does_not_burn_the_nonce(verifier, clock):
    # If the Pi hasn't opened its incident yet, the same command can succeed on retry.
    cmd = unlock_cmd(clock)
    assert not verifier.check(cmd, local_incident_id=None)[0]
    assert verifier.check(cmd, local_incident_id="inc-1") == (True, "ok")


@pytest.mark.parametrize("action", ["lock", "alarm_on"])
def test_device_actions_do_not_need_a_local_incident(verifier, clock, action):
    cmd = make_command(SECRET, action, "inc-1", now_ms=clock.ms()).model_dump()
    assert verifier.check(cmd, local_incident_id=None) == (True, "ok")


# Issue #13 bug 2: a command for a stale backend incident must not touch a newer local one.
@pytest.mark.parametrize("action", ["unlock", "alarm_off"])
def test_incident_actions_for_another_incident_refused(verifier, clock, action):
    cmd = make_command(SECRET, action, "inc-old", now_ms=clock.ms()).model_dump()
    assert verifier.check(cmd, local_incident_id="inc-new") == (False, "other incident")
    assert verifier.check(cmd, local_incident_id=None) == (False, "no local incident")


def test_nonce_cache_stays_bounded(verifier, clock):
    for _ in range(50):
        verifier.check(unlock_cmd(clock), local_incident_id="inc-1")
        clock.advance(1)
    assert len(verifier._seen) <= 25  # only nonces still inside 2x the freshness window


def test_14_edge_logs_every_decision_with_reason(verifier, clock, caplog):
    caplog.set_level(logging.INFO, logger="firsthour.edge.security")
    cmd = unlock_cmd(clock)
    verifier.check(cmd, local_incident_id="inc-1")
    verifier.check(cmd, local_incident_id="inc-1")
    verifier.check(unlock_cmd(clock), local_incident_id=None)
    lines = [r.getMessage() for r in caplog.records if r.name == "firsthour.edge.security"]
    assert len(lines) == 3
    assert "ok" in lines[0] and "replay" in lines[1] and "no local incident" in lines[2]


# --- signed poll (GET /edge/commands) ---------------------------------------------

def test_poll_signature_matches_on_both_sides(clock):
    from app.security import sign_poll as backend_sign, verify_poll
    from edge.security import sign_poll

    ts = clock.ms()
    sig = sign_poll(SECRET, ts, "bath-01")
    assert sig == backend_sign(SECRET, ts, "bath-01")
    assert verify_poll(SECRET, "bath-01", ts, sig, last_ts=0, now_ms=ts) == (True, "ok")
    assert verify_poll(SECRET, "bath-02", ts, sig, last_ts=0, now_ms=ts) == (False, "bad signature")
    assert verify_poll(SECRET, "bath-01", ts, None, last_ts=0, now_ms=ts) == (False, "missing signature")
    assert verify_poll(SECRET, "bath-01", ts, sig, last_ts=0, now_ms=ts + 10_001) == (False, "stale")
    assert verify_poll(SECRET, "bath-01", ts, sig, last_ts=ts, now_ms=ts) == (False, "replay")
