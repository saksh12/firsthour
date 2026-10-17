"""Backend side of the unlock suite (IMPLEMENTATION.md section 9.4, cases 1-6 and 14)."""

import logging

import pytest

from app.unlock import UnlockGuard, is_unlock_attempt
from conftest import CAREGIVER, STRANGER


@pytest.fixture
def granted() -> list[str]:
    return []


@pytest.fixture
def guard(clock, granted) -> UnlockGuard:
    return UnlockGuard(allowed=[CAREGIVER], window_s=600, clock=clock, on_grant=granted.append)


def test_1_correct_number_code_and_open_incident_unlocks(guard, granted):
    code = guard.open_incident("inc-1")
    result = guard.try_unlock(CAREGIVER, f"UNLOCK {code}")
    assert result.allowed and result.reason == "granted"
    assert granted == ["inc-1"]


def test_1b_grammar_is_case_and_whitespace_tolerant(guard):
    code = guard.open_incident("inc-1")
    assert guard.try_unlock(CAREGIVER, f"  unlock   {code} \n").allowed


def test_2_no_incident_refused(guard, granted):
    result = guard.try_unlock(CAREGIVER, "UNLOCK 1234")
    assert not result.allowed and result.reason == "no active incident"
    assert granted == []


def test_2b_code_dies_when_incident_closes(guard, granted):
    code = guard.open_incident("inc-1")
    guard.close_incident()
    result = guard.try_unlock(CAREGIVER, f"UNLOCK {code}")
    assert result.reason == "no active incident"
    assert granted == []


def test_2c_new_incident_gets_new_code_and_old_code_fails(guard, clock):
    # Codes are random, so loop until two incidents get different ones.
    old = guard.open_incident("inc-1")
    new = old
    while new == old:
        new = guard.open_incident("inc-2")
    assert guard.try_unlock(CAREGIVER, f"UNLOCK {old}").reason == "bad code"
    assert guard.try_unlock(CAREGIVER, f"UNLOCK {new}").allowed


def test_3_unknown_number_with_correct_code_refused(guard, granted):
    code = guard.open_incident("inc-1")
    result = guard.try_unlock(STRANGER, f"UNLOCK {code}")
    assert not result.allowed and result.reason == "not enrolled"
    assert granted == []


def test_3b_stranger_attempts_do_not_lock_out_the_caregiver(guard):
    code = guard.open_incident("inc-1")
    for _ in range(5):
        guard.try_unlock(STRANGER, "UNLOCK 0000")
    assert guard.try_unlock(CAREGIVER, f"UNLOCK {code}").allowed


def test_4_allowed_number_wrong_code_refused(guard, granted):
    code = guard.open_incident("inc-1")
    wrong = "0000" if code != "0000" else "1111"
    result = guard.try_unlock(CAREGIVER, f"UNLOCK {wrong}")
    assert not result.allowed and result.reason == "bad code"
    assert granted == []


@pytest.mark.parametrize("text", ["UNLOCK", "UNLOCK 12", "UNLOCK 12345", "UNLOCK abcd", "UNLOCK 1234 now"])
def test_4b_malformed_unlock_counts_as_bad_code(guard, text):
    guard.open_incident("inc-1")
    assert guard.try_unlock(CAREGIVER, text).reason == "bad code"


def test_5_three_wrong_codes_then_right_one_locked_out(guard, granted):
    code = guard.open_incident("inc-1")
    wrong = "0000" if code != "0000" else "1111"
    for _ in range(3):
        assert guard.try_unlock(CAREGIVER, f"UNLOCK {wrong}").reason == "bad code"
    result = guard.try_unlock(CAREGIVER, f"UNLOCK {code}")
    assert not result.allowed and result.reason == "locked out"
    assert granted == []


def test_5b_lockout_resets_with_a_new_incident(guard):
    guard.open_incident("inc-1")
    for _ in range(3):
        guard.try_unlock(CAREGIVER, "UNLOCK xxxx")
    code = guard.open_incident("inc-2")
    assert guard.try_unlock(CAREGIVER, f"UNLOCK {code}").allowed


def test_6_incident_older_than_window_refused(guard, clock, granted):
    code = guard.open_incident("inc-1")
    clock.advance(601)
    result = guard.try_unlock(CAREGIVER, f"UNLOCK {code}")
    assert not result.allowed and result.reason == "no active incident"
    assert granted == []


def test_6b_just_inside_window_still_works(guard, clock):
    code = guard.open_incident("inc-1")
    clock.advance(599)
    assert guard.try_unlock(CAREGIVER, f"UNLOCK {code}").allowed


def test_allowlist_ignores_blanks_and_spaces(clock):
    guard = UnlockGuard(allowed=" +13105550101 , ,+13105550102", clock=clock)
    assert guard.allowed == {"+13105550101", "+13105550102"}


def test_empty_allowlist_refuses_everyone(clock):
    guard = UnlockGuard(allowed="", clock=clock)
    code = guard.open_incident("inc-1")
    assert guard.try_unlock("", f"UNLOCK {code}").reason == "not enrolled"


def test_14_every_attempt_is_logged_with_its_reason(guard, caplog):
    caplog.set_level(logging.INFO, logger="firsthour.unlock")
    guard.try_unlock(CAREGIVER, "UNLOCK 1234")            # no incident
    code = guard.open_incident("inc-1")
    guard.try_unlock(STRANGER, f"UNLOCK {code}")           # not enrolled
    guard.try_unlock(CAREGIVER, "UNLOCK zzzz")             # bad code
    guard.try_unlock(CAREGIVER, f"UNLOCK {code}")          # granted

    reasons = [r.reason for r in guard.audit]
    assert reasons == ["no active incident", "not enrolled", "bad code", "granted"]
    logged = [rec.getMessage() for rec in caplog.records
              if rec.name == "firsthour.unlock" and rec.getMessage().startswith("unlock_attempt")]
    assert len(logged) == 4
    for reason, line in zip(reasons, logged):
        assert reason in line


def test_code_never_appears_in_logs(guard, caplog, monkeypatch):
    # Pin the code: a random one could appear by chance inside the phone number.
    monkeypatch.setattr("app.unlock.secrets.randbelow", lambda n: 4821)
    caplog.set_level(logging.DEBUG, logger="firsthour.unlock")
    code = guard.open_incident("inc-1")
    assert code == "4821"
    guard.try_unlock(CAREGIVER, f"UNLOCK {code}")
    assert all(code not in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize(
    "text,expected",
    [("UNLOCK 4821", True), ("unlock", True), ("  Unlock 12", True),
     ("I'm 2 minutes away", False), ("please unlock the door", False), ("", False)],
)
def test_reply_grammar_routes_unlock_vs_message(text, expected):
    assert is_unlock_attempt(text) is expected
