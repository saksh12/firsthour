"""Pi side of the unlock suite (IMPLEMENTATION.md section 9.4, cases 7-12 and 14).

Case 13 (servo relocks after hold time) lives in the Arduino sketch and has to be
checked on the hardware.
"""

import logging

import pytest

from app.security import make_command
from conftest import SECRET
from edge.security import CommandVerifier, nfc_unlock_allowed


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


ENROLLED = "04A1B2C3D4"


def test_10_unknown_nfc_tag_during_incident_refused():
    assert nfc_unlock_allowed("DEADBEEF", [ENROLLED], local_incident_open=True) == (False, "unknown tag")


def test_11_enrolled_nfc_tag_during_incident_unlocks():
    assert nfc_unlock_allowed(ENROLLED, [ENROLLED], local_incident_open=True) == (True, "ok")


def test_11b_uid_format_is_normalized():
    assert nfc_unlock_allowed("04:a1:b2:c3:d4", "04a1b2c3d4", local_incident_open=True)[0]


def test_12_enrolled_nfc_tag_with_no_incident_refused():
    assert nfc_unlock_allowed(ENROLLED, [ENROLLED], local_incident_open=False) == (False, "no local incident")


def test_empty_nfc_allowlist_refuses_everything():
    assert nfc_unlock_allowed("", "", local_incident_open=True) == (False, "unknown tag")


def test_14_edge_logs_every_decision_with_reason(verifier, clock, caplog):
    caplog.set_level(logging.INFO, logger="firsthour.edge.security")
    cmd = unlock_cmd(clock)
    verifier.check(cmd, local_incident_id="inc-1")
    verifier.check(cmd, local_incident_id="inc-1")
    nfc_unlock_allowed("DEADBEEF", [ENROLLED], local_incident_open=True)
    lines = [r.getMessage() for r in caplog.records if r.name == "firsthour.edge.security"]
    assert len(lines) == 3
    assert "ok" in lines[0] and "replay" in lines[1] and "unknown tag" in lines[2]
