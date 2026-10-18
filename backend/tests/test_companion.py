"""Conversation after the alert: re-asking missed questions, the closing line, and the
ElevenLabs companion's laptop-only routes (relay to the caregiver, facts, session)."""

import pytest

from app import main as main_mod
from app.incident import CLOSING_LINE, RELAY_MAX, REASK_LINE, IncidentMachine
from app.main import create_app
from app.models import IncidentState as S
from app.profile import DEMO_PROFILE
from app.triage import QUESTIONS
from app.unlock import UnlockGuard
from conftest import CAREGIVER, SECRET, LaptopClient

OTHER = "+15550000002"


class Rec:
    def __init__(self):
        self.events, self.spoken, self.texts = [], [], []

    def emit(self, t, d):
        self.events.append((t, d))


@pytest.fixture
def rec():
    return Rec()


def machine(clock, rec, reask=True):
    guard = UnlockGuard(allowed=[CAREGIVER, OTHER], clock=clock)
    return IncidentMachine(guard, DEMO_PROFILE, clock=clock, silence_s=8, emit=rec.emit,
                           speak=rec.spoken.append, call_after_s=None, reask=reask,
                           send_text=lambda inc, to, text, kind: rec.texts.append((to, text, kind)))


@pytest.fixture
def m(clock, rec):
    return machine(clock, rec)


def alerted(m):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("I need help")  # escalates, now on question 2
    assert m.state == S.ALERTING and m.incident.question_index == 1


def test_missed_answer_after_the_alert_is_asked_again_then_skipped(m, clock, rec):
    alerted(m)
    m.line_played(QUESTIONS[1][1])
    clock.advance(8.1)
    m.tick()
    assert rec.spoken[-2:] == [REASK_LINE, QUESTIONS[1][1]]
    assert m.incident.question_index == 1
    m.line_played(QUESTIONS[1][1])
    clock.advance(8.1)
    m.tick()
    assert m.incident.question_index == 2  # left unknown, never guessed
    assert rec.spoken[-1] == QUESTIONS[2][1]
    assert m.incident.triage.head_strike is None


def test_failed_speech_to_text_does_not_stall_the_script(m, clock, rec):
    alerted(m)
    m.line_played(QUESTIONS[1][1])
    m.hold_for_speech(20)  # a clip is with the Pi
    clock.advance(9)
    m.tick()
    assert rec.spoken[-1] == QUESTIONS[1][1] and REASK_LINE not in rec.spoken  # held
    m.release_speech_hold()  # the Pi timed out
    m.tick()
    assert rec.spoken[-2:] == [REASK_LINE, QUESTIONS[1][1]]


def test_an_answer_resets_the_miss_count(m, clock, rec):
    alerted(m)
    m.line_played(QUESTIONS[1][1])
    clock.advance(8.1)
    m.tick()
    m.on_answer("no")
    assert m.incident.question_index == 2 and m.incident.misses == 0


def test_no_reask_without_a_listening_mic(clock, rec):
    m = machine(clock, rec, reask=False)
    alerted(m)
    m.line_played(QUESTIONS[1][1])
    clock.advance(60)
    m.tick()
    assert REASK_LINE not in rec.spoken and m.incident.question_index == 1


def test_caregiver_update_is_followed_by_the_pending_question(m, rec):
    alerted(m)
    m.caregiver_reply(CAREGIVER, "on my way, 10 minutes")
    assert rec.spoken[-1] == QUESTIONS[1][1]
    assert rec.spoken[-2] == "Your caregiver is about 10 minutes away."


def test_finished_script_closes_and_hands_over_once(m, rec):
    alerted(m)
    for a in ["no", "my hip", "yes", "no"]:
        m.on_answer(a)
    assert rec.spoken[-1] == CLOSING_LINE
    assert [d for t, d in rec.events if t == "script_done"] == [{"incident_id": "inc-1"}]
    m.caregiver_reply(CAREGIVER, "on my way")
    assert rec.spoken[-1] != CLOSING_LINE and rec.spoken.count(CLOSING_LINE) == 1


def test_relay_goes_to_everyone_until_someone_claims(m, rec):
    alerted(m)
    assert m.companion_relay("my hip really hurts now", "urgent")["sent"]
    to, text, kind = rec.texts[-1]
    assert to == sorted([CAREGIVER, OTHER]) and kind == "relay"
    assert text == 'URGENT: FirstHour: Margaret says: "my hip really hurts now"'
    m.caregiver_reply(OTHER, "on my way")
    m.companion_relay("I feel dizzy")
    assert rec.texts[-1][0] == [OTHER]


def test_relay_is_bounded_and_never_carries_the_code(m, rec):
    assert not m.companion_relay("hello")["sent"]  # no incident
    m.on_fall("bath-01", "inc-1")
    assert not m.companion_relay("hello")["sent"]  # still checking: no alert yet
    m.on_answer("help")
    assert m.companion_relay(f"the code is {m.incident.code}") == {"sent": False, "reason": "refused"}
    assert not m.companion_relay("   ")["sent"]
    for _ in range(RELAY_MAX):
        assert m.companion_relay("still here")["sent"]
    assert m.companion_relay("again") == {"sent": False, "reason": "relay limit reached"}


def app_client(**overrides) -> LaptopClient:
    s = {"STUB_EDGE": True, "SHARED_SECRET": SECRET, "CAREGIVER_NUMBERS": CAREGIVER,
         "INCIDENT_WINDOW_SECONDS": 600}
    s.update(overrides)
    return LaptopClient(create_app(s))


def test_companion_is_off_unless_configured():
    c = app_client(STUB_ELEVENLABS=True, ELEVENLABS_COMPANION_AGENT_ID="agent_x", ELEVENLABS_API_KEY="k")
    assert c.get("/companion/session").json() == {"enabled": False}
    c = app_client(STUB_ELEVENLABS=False, ELEVENLABS_API_KEY="k")
    assert c.get("/companion/session").json() == {"enabled": False}


def test_companion_session_mints_a_token_only_for_an_alerted_incident(monkeypatch):
    seen = []
    monkeypatch.setattr(main_mod, "companion_token", lambda key, agent, base: seen.append(agent) or "tok")
    c = app_client(STUB_ELEVENLABS=False, ELEVENLABS_COMPANION_AGENT_ID="agent_x", ELEVENLABS_API_KEY="k")
    assert c.get("/companion/session").status_code == 409
    c.app.state.machine.on_fall("bath-01", "inc-1")
    assert c.get("/companion/session").status_code == 409  # still the fixed check
    c.app.state.machine.on_answer("help")
    body = c.get("/companion/session").json()
    assert body == {"enabled": True, "token": "tok", "variables": {"patient_name": "Margaret", "room": "Bathroom"}}
    assert seen == ["agent_x"]


def test_companion_tools_are_laptop_only_and_code_free():
    c = app_client()
    machine = c.app.state.machine
    machine.on_fall("bath-01", "inc-1")
    machine.on_answer("help")
    facts = c.get("/companion/facts").json()
    assert facts["active"] and machine.incident.code not in str(facts)
    r = c.post("/companion/relay", json={"message": "my hip hurts", "urgency": "normal"})
    assert r.json()["sent"]
    assert c.post("/companion/relay", json={"message": "x", "urgency": "panic"}).status_code == 422
    for path in ("/companion/facts", "/companion/session"):
        assert c.get(path, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404
    assert c.post("/companion/relay", json={"message": "x"},
                  headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404


def test_finishing_the_script_through_the_app_reaches_the_dashboard():
    c = app_client()
    machine = c.app.state.machine
    machine.on_fall("bath-01", "inc-1")
    for a in ["help", "no", "my hip", "yes", "no"]:
        assert c.post("/person/answer", json={"text": a}).status_code == 200
    assert any(e.type == "script_done" for e in c.app.state.events)
    assert c.app.state.spoken[-1] == CLOSING_LINE
