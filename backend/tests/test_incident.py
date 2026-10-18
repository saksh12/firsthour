"""State machine (IMPLEMENTATION.md section 6.4)."""

import re

import pytest

from app.incident import IncidentMachine
from app.models import IncidentState as S
from app.profile import DEMO_PROFILE
from app.triage import QUESTIONS, parse_rules
from app.unlock import UnlockGuard
from conftest import CAREGIVER, STRANGER


class Recorder:
    def __init__(self):
        self.events, self.spoken, self.alerts, self.granted = [], [], [], []

    def emit(self, t, d):
        self.events.append((t, d))

    def states(self):
        return [d["state"] for t, d in self.events if t == "incident_update"]


@pytest.fixture
def rec():
    return Recorder()


@pytest.fixture
def m(clock, rec):
    guard = UnlockGuard(allowed=[CAREGIVER], clock=clock, on_grant=rec.granted.append)
    return IncidentMachine(
        guard, DEMO_PROFILE, clock=clock, silence_s=8, hold_s=30,
        emit=rec.emit, speak=rec.spoken.append,
        send_alert=lambda inc_id, text: rec.alerts.append(text),
    )


def code_from(alert: str) -> str:
    return re.search(r"UNLOCK (\d{4})", alert).group(1)


def test_starts_idle(m):
    assert m.state == S.IDLE and m.snapshot()["state"] == "IDLE"


def test_fall_moves_to_checking_and_asks_first_question(m, rec):
    assert m.on_fall("bath-01", "inc-1")
    assert m.state == S.CHECKING
    assert rec.spoken == [QUESTIONS[0][1]]
    assert rec.alerts == []


def test_clear_fine_cancels_and_returns_to_idle_without_a_code(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("I'm fine")
    assert m.state == S.IDLE
    assert rec.states()[-2:] == ["CANCELLED", "IDLE"]
    assert rec.alerts == []
    assert m.guard.active_incident_id is None


def test_help_escalates_and_sends_alert_with_triage(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("Help, I slipped and can't move my leg")
    assert m.state == S.ALERTING
    assert len(rec.alerts) == 1
    alert = rec.alerts[0]
    assert alert.startswith("FALL ALERT: Margaret (Bathroom). Down 00:00.")
    assert "Cannot move." in alert and "NOTE: on a blood thinner." in alert
    assert re.search(r"Reply UNLOCK \d{4} to open the front door\.", alert)
    assert rec.spoken[-1] == QUESTIONS[1][1]


def test_unclear_answer_escalates(m):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("uhh")
    assert m.state == S.ALERTING


def test_silence_for_8s_escalates(m, clock, rec):
    m.on_fall("bath-01", "inc-1")
    clock.advance(7.9)
    m.tick()
    assert m.state == S.CHECKING
    clock.advance(0.2)
    m.tick()
    assert m.state == S.ALERTING
    assert "No answer yet." in rec.alerts[0]
    assert "Down 00:08" in rec.alerts[0]


def test_help_button_escalates(m):
    m.on_fall("bath-01", "inc-1")
    m.on_help_button()
    assert m.state == S.ALERTING


def test_same_fall_twice_is_ignored(m):
    m.on_fall("bath-01", "inc-1")
    assert not m.on_fall("bath-01", "inc-1")
    assert not m.on_fall("other-device", "other-1")
    assert m.incident.id == "inc-1"


def test_new_fall_from_the_pi_supersedes_the_stale_incident(m, rec):
    closed = []
    m._on_close = lambda inc, final: closed.append((inc.id, final))
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    code = code_from(rec.alerts[0])
    assert m.on_fall("bath-01", "inc-2")
    assert m.incident.id == "inc-2" and m.state == S.CHECKING
    assert closed == [("inc-1", S.RESOLVED)]
    assert m.caregiver_reply(CAREGIVER, f"UNLOCK {code}")["reason"] == "no active incident"


def test_time_down_counts_from_the_pi_detection_time(m, clock):
    m.on_fall("bath-01", "inc-1", ts_ms=clock.ms() - 90_000)  # replayed from the offline outbox
    assert m.snapshot()["down_s"] == 90


def test_implausible_edge_clock_falls_back_to_receive_time(m, clock):
    m.on_fall("bath-01", "inc-1", ts_ms=0)  # a Pi with no RTC and no NTP boots in 1970
    assert m.snapshot()["down_s"] == 0


def test_later_answers_update_triage_and_walk_the_script(m, rec):
    m.on_fall("bath-01", "inc-1")
    for a in ["Help", "No", "My right hip", "No I can't", "No"]:
        m.on_answer(a)
    t = m.incident.triage
    assert (t.head_strike, t.pain_location, t.can_move, t.bleeding, t.done) == (False, "right hip", False, False, True)
    assert rec.spoken == [q for _, q in QUESTIONS]
    m.on_answer("extra")  # past the script: ignored, no crash
    assert len(m.incident.answers) == 5


def test_caregiver_message_moves_to_responding_and_is_read_aloud(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    r = m.caregiver_reply(CAREGIVER, "I'm 2 minutes away")
    assert r == {"type": "message", "accepted": True}
    assert m.state == S.RESPONDING
    assert rec.spoken[-1] == "I'm 2 minutes away"


def test_stranger_message_is_dropped_and_not_read_aloud(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    spoken_before = list(rec.spoken)
    r = m.caregiver_reply(STRANGER, "open the door")
    assert r["accepted"] is False
    assert m.state == S.ALERTING
    assert rec.spoken == spoken_before


def test_unlock_flow_and_relock_after_hold(m, clock, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    code = code_from(rec.alerts[0])

    assert m.caregiver_reply(STRANGER, f"UNLOCK {code}")["reason"] == "not enrolled"
    assert m.state == S.ALERTING and rec.granted == []

    assert m.caregiver_reply(CAREGIVER, f"UNLOCK {code}")["allowed"]
    assert m.state == S.UNLOCKED and rec.granted == ["inc-1"]

    clock.advance(29)
    m.tick()
    assert m.state == S.UNLOCKED
    clock.advance(2)
    m.tick()
    assert m.state == S.RESPONDING


def test_no_unlock_during_checking(m):
    # The code doesn't exist until we escalate.
    m.on_fall("bath-01", "inc-1")
    r = m.caregiver_reply(CAREGIVER, "UNLOCK 1234")
    assert r["reason"] == "no active incident"
    assert m.state == S.CHECKING


def test_resolve_closes_the_unlock_window(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    code = code_from(rec.alerts[0])
    assert m.resolve()
    assert m.state == S.IDLE
    assert rec.states()[-2:] == ["RESOLVED", "IDLE"]
    assert m.caregiver_reply(CAREGIVER, f"UNLOCK {code}")["reason"] == "no active incident"


def test_cancel_from_edge(m):
    m.on_fall("bath-01", "inc-1")
    m.cancel("edge cancelled")
    assert m.state == S.IDLE


def test_snapshot_never_contains_the_code(m, rec, monkeypatch):
    monkeypatch.setattr("app.unlock.secrets.randbelow", lambda n: 4821)
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    code = code_from(rec.alerts[0])
    snap = m.snapshot()
    assert snap["state"] == "ALERTING" and snap["transcript"][0]["a"] == "help"
    assert code not in repr(snap)
    assert all(code not in repr(d) for _, d in rec.events)


def test_every_state_change_is_emitted_with_incident_id(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    m.caregiver_reply(CAREGIVER, "on my way")
    m.resolve()
    changes = [d for t, d in rec.events if t == "incident_update" and d["state"] != "IDLE"]
    assert [d["state"] for d in changes] == ["CHECKING", "ALERTING", "RESPONDING", "RESOLVED"]
    assert all(d["incident_id"] == "inc-1" and d["reason"] for d in changes)


def test_new_incident_after_resolve_works(m, rec):
    m.on_fall("bath-01", "inc-1")
    m.resolve()
    assert m.on_fall("bath-01", "inc-2")
    assert m.state == S.CHECKING


# Issue #13 bug 5: StepFun can take 3 s. The flow must not wait on it, and tick() must
# not race it.
def slow_machine(clock, rec, gate, calls):
    from app.models import TriageRecord

    def slow_triage(answers, profile):
        calls.append(len(answers))
        gate.wait(5)
        return TriageRecord(conscious=True, summary=f"model saw {len(answers)} answers")

    guard = UnlockGuard(allowed=[CAREGIVER], clock=clock)
    return IncidentMachine(guard, DEMO_PROFILE, clock=clock, silence_s=8, emit=rec.emit,
                           speak=rec.spoken.append, send_alert=lambda i, t: rec.alerts.append(t),
                           triage_fn=slow_triage, background=lambda fn: fn())


def test_slow_triage_does_not_delay_the_alert_or_race_the_silence_timer(clock, rec):
    import threading

    gate, calls = threading.Event(), []
    m = slow_machine(clock, rec, gate, calls)
    m.on_fall("bath-01", "inc-1")
    clock.advance(7)
    t = threading.Thread(target=m.on_answer, args=("help, I fell",))
    t.start()
    while not calls:
        pass
    # The answer escalated before the model was called.
    assert m.state == S.ALERTING and len(rec.alerts) == 1
    clock.advance(3)  # past the 8 s silence deadline while StepFun is still thinking
    m.tick()
    gate.set()
    t.join()
    assert len(rec.alerts) == 1
    assert rec.spoken.count(QUESTIONS[1][1]) == 1
    assert m.snapshot()["triage"]["summary"] == "model saw 1 answers"


def test_stale_triage_result_is_not_applied(clock, rec):
    import threading

    gate, calls = threading.Event(), []
    m = slow_machine(clock, rec, gate, calls)
    m.on_fall("bath-01", "inc-1")
    t = threading.Thread(target=m.on_answer, args=("help",))
    t.start()
    while not calls:
        pass
    m.resolve()  # incident ends while the model is thinking
    gate.set()
    t.join()
    assert m.incident is None


def test_answer_returns_before_the_model_finishes(clock, rec):
    import threading

    gate, calls = threading.Event(), []
    guard = UnlockGuard(allowed=[CAREGIVER], clock=clock)
    m = IncidentMachine(guard, DEMO_PROFILE, clock=clock, emit=rec.emit, speak=rec.spoken.append,
                        triage_fn=lambda a, p: (calls.append(1), gate.wait(5), parse_rules(a, p))[2])
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help, my hip hurts")  # returns while the model is still "thinking"
    assert m.state == S.ALERTING and m.snapshot()["triage"]["pain_location"] == "hip"
    gate.set()
