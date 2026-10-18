"""Caregiver coordination (issue #23): claims, ETAs, backups, Q&A, voice escalation."""

import re

import pytest

from app.coordinator import Commitment, incident_facts, is_question, parse_commitment, parse_names, parse_order
from app.incident import IncidentMachine
from app.models import IncidentState as S
from app.profile import DEMO_PROFILE
from app.unlock import UnlockGuard
from conftest import CAREGIVER, STRANGER

BACKUP = "+13105550102"


@pytest.mark.parametrize("text,expected", [
    ("On my way!", Commitment("accepted")),
    ("omw", Commitment("accepted")),
    ("I\u2019m coming", Commitment("accepted")),
    ("On my way, 10 min", Commitment("arriving", 10)),
    ("I'm 5 minutes away", Commitment("arriving", 5)),
    ("I'm here", Commitment("arrived")),
    ("at the door", Commitment("arrived")),
    ("I might come", None),
    ("maybe on my way in 10", None),
    ("I can't come, at work", None),
    ("is she still talking?", None),
    ("tell her I love her", None),
    ("UNLOCK 1234", None),
])
def test_parse_commitment(text, expected):
    assert parse_commitment(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("is she still talking?", True), ("Did she hit her head", True), ("What happened", True),
    ("tell her I love her", False), ("hang in there", False),
])
def test_is_question_fallback(text, expected):
    assert is_question(text) is expected


def test_config_parsers_keep_order_and_names():
    assert parse_order(f" {CAREGIVER}, {BACKUP},{CAREGIVER}") == [CAREGIVER, BACKUP]
    assert parse_names(f"{CAREGIVER}:Sarah, {BACKUP}:Tom,bad") == {CAREGIVER: "Sarah", BACKUP: "Tom"}


class Rec:
    def __init__(self):
        self.events, self.spoken, self.alerts, self.texts, self.calls, self.granted = [], [], [], [], [], []
        self.answers = []

    def of(self, type_):
        return [d for t, d in self.events if t == type_]


@pytest.fixture
def rec():
    return Rec()


def machine(clock, rec, **kw):
    guard = UnlockGuard(allowed=[CAREGIVER, BACKUP], clock=clock, on_grant=rec.granted.append)
    reply = kw.pop("_answer", ("answer", "She is talking and says her hip hurts."))

    def answer(q, facts):
        rec.answers.append((q, facts))
        return reply

    return IncidentMachine(
        guard, DEMO_PROFILE, clock=clock, silence_s=8, hold_s=30,
        emit=lambda t, d: rec.events.append((t, d)), speak=rec.spoken.append,
        send_alert=lambda inc_id, text: rec.alerts.append(text),
        send_text=lambda inc_id, to, text, kind: rec.texts.append((tuple(to), kind, text)),
        place_call=lambda inc_id, number, variables: rec.calls.append((number, variables)),
        call_order=[CAREGIVER, BACKUP], names={CAREGIVER: "Sarah", BACKUP: "Tom"},
        **{"answer_fn": answer, **kw},
    )


@pytest.fixture
def m(clock, rec):
    mm = machine(clock, rec)
    mm.on_fall("bath-01", "inc-1")
    mm.on_answer("help, my hip")
    assert mm.state == S.ALERTING
    return mm


def code_of(rec) -> str:
    return re.search(r"UNLOCK (\d{4})", rec.alerts[-1]).group(1)


def test_first_clear_claim_wins_and_others_stand_by(m, rec):
    r = m.caregiver_reply(CAREGIVER, "on my way, 10 min")
    assert r == {"type": "commitment", "accepted": True, "status": "arriving", "eta_min": 10, "assigned": True}
    assert m.state == S.RESPONDING
    assert rec.spoken[-1] == "Sarah is about 10 minutes away."
    assert ((BACKUP,), "stand_by", "Sarah is on the way. Please stand by; we'll text if that changes.") in rec.texts
    ack = [t for t in rec.texts if t[1] == "ack"][0]
    assert ack[0] == (CAREGIVER,) and "You said 10 minutes" in ack[2]
    assert [d["status"] for d in rec.of("delivery_status")] == ["acknowledged", "responder_assigned"]
    # A second "on my way" is recorded but doesn't take over.
    r2 = m.caregiver_reply(BACKUP, "I'm coming too")
    assert r2["assigned"] is False
    snap = {x["name"]: x for x in m.snapshot()["responders"]}
    assert snap["Sarah"]["assigned"] and not snap["Tom"]["assigned"]


def test_eta_then_arrived_updates_the_same_responder(m, rec):
    m.caregiver_reply(CAREGIVER, "omw")
    m.caregiver_reply(CAREGIVER, "I'm here")
    assert rec.spoken[-1] == "Sarah is at the door."
    assert [d["status"] for d in rec.of("responder_update")] == ["accepted", "arrived"]


def test_hedge_is_not_a_claim_and_is_read_aloud(m, rec):
    r = m.caregiver_reply(CAREGIVER, "I might come")
    assert r == {"type": "message", "accepted": True}
    assert rec.spoken[-1] == "I might come"
    assert m.incident.claimed_by is None and rec.of("responder_update") == []


def test_question_is_answered_in_thread_not_read_aloud(m, rec):
    spoken_before = list(rec.spoken)
    r = m.caregiver_reply(CAREGIVER, "is she still talking?")
    assert r == {"type": "question", "accepted": True}
    assert rec.texts[-1] == ((CAREGIVER,), "answer", "She is talking and says her hip hurts.")
    assert rec.spoken == spoken_before
    q, facts = rec.answers[-1]
    assert q == "is she still talking?" and facts["patient"] == "Margaret"
    # The model never sees the code or a phone number.
    blob = repr(facts)
    assert code_of(rec) not in blob and CAREGIVER not in blob and BACKUP not in blob


def test_model_can_route_a_question_shaped_message_to_read_aloud(clock, rec):
    mm = machine(clock, rec, _answer=("message", ""))
    mm.on_fall("bath-01", "inc-1")
    mm.on_answer("help")
    r = mm.caregiver_reply(CAREGIVER, "Can you tell her I love her?")
    assert r == {"type": "message", "accepted": True}
    assert rec.spoken[-1] == "Can you tell her I love her?"


def test_template_answer_is_the_default(clock, rec):
    guard = UnlockGuard(allowed=[CAREGIVER], clock=clock)
    texts = []
    mm = IncidentMachine(guard, DEMO_PROFILE, clock=clock,
                         send_text=lambda i, to, text, kind: texts.append(text))
    mm.on_fall("bath-01", "inc-1")
    mm.on_answer("help, my hip hurts")
    mm.caregiver_reply(CAREGIVER, "how is she")
    assert texts[-1].startswith("Margaret has been down 0 min")
    assert "Nobody has said they are on the way yet." in texts[-1]


def test_stranger_cannot_claim_or_ask(m, rec):
    assert m.caregiver_reply(STRANGER, "on my way")["accepted"] is False
    assert m.caregiver_reply(STRANGER, "is she ok?")["accepted"] is False
    assert rec.texts == [] and m.incident.claimed_by is None


def test_unlock_still_only_through_the_guard(m, rec):
    m.caregiver_reply(CAREGIVER, "omw")
    r = m.caregiver_reply(CAREGIVER, f"UNLOCK {code_of(rec)}")
    assert r["allowed"] is True and rec.granted == ["inc-1"]


def test_no_claim_calls_primary_then_backup(m, rec, clock):
    clock.advance(19)
    m.tick()
    assert rec.calls == []
    clock.advance(1)
    m.tick()
    assert [n for n, _ in rec.calls] == [CAREGIVER]
    v = rec.calls[0][1]
    assert v["incident_id"] == "inc-1" and v["on_blood_thinner"] == "yes" and v["eta_question"]
    assert code_of(rec) not in repr(v)
    m.caregiver_reply(CAREGIVER, "what happened?")  # a question is not a claim
    clock.advance(20)
    m.tick()
    assert [n for n, _ in rec.calls] == [CAREGIVER, BACKUP]
    clock.advance(60)
    m.tick()
    assert len(rec.calls) == 2  # one call per number


def test_claim_stops_voice_escalation(m, rec, clock):
    m.caregiver_reply(BACKUP, "on my way")
    clock.advance(60)
    m.tick()
    assert rec.calls == []


def test_head_strike_calls_immediately(clock, rec):
    mm = machine(clock, rec)
    mm.on_fall("bath-01", "inc-1")
    mm.on_answer("help, I hit my head")
    assert mm.incident.triage.head_strike is True
    mm.tick()
    assert [n for n, _ in rec.calls] == [CAREGIVER]
    assert rec.of("call_update")[-1]["reason"] == "head strike"


def test_call_after_none_disables_calls(clock, rec):
    mm = machine(clock, rec, call_after_s=None)
    mm.on_fall("bath-01", "inc-1")
    mm.on_answer("help, I hit my head")
    clock.advance(120)
    mm.tick()
    assert rec.calls == []


def test_call_result_records_commitment_and_texts_back(m, rec):
    r = m.record_call_result("inc-1", CAREGIVER, ["Oh no.", "I'll be there in 5 minutes"], "Sarah will come.")
    assert r["status"] == "arriving" and r["eta_min"] == 5 and r["assigned"]
    assert rec.spoken[-1] == "Sarah is about 5 minutes away."
    ack = [t for t in rec.texts if t[1] == "ack"][-1]
    assert ack[0] == (CAREGIVER,) and ack[2].startswith("You said 5 minutes on the call.")
    assert "UNLOCK with the code" in ack[2] and code_of(rec) not in ack[2]
    assert m.facts()["call_notes"] == ["Sarah will come."]


def test_spoken_unlock_on_a_call_never_unlocks(m, rec):
    code = code_of(rec)
    m.record_call_result("inc-1", CAREGIVER, [f"UNLOCK {code}", f"unlock the door, the code is {code}"], "")
    assert rec.granted == [] and m.state == S.ALERTING
    assert m.guard.audit == []  # not even counted as an attempt


def test_unplaced_call_or_wrong_incident_records_no_commitment(m, rec):
    assert m.record_call_result("inc-1", None, ["on my way"], "caller")["commitment"] is None
    assert m.record_call_result("inc-1", STRANGER, ["on my way"], "")["commitment"] is None
    assert m.record_call_result("inc-old", CAREGIVER, ["on my way"], "")["accepted"] is False
    assert m.incident.claimed_by is None


def test_facts_never_include_code(m, rec):
    f = incident_facts(profile=DEMO_PROFILE, state="ALERTING", down_s=75, triage=m.incident.triage,
                       transcript=[], responders=[], call_notes=[])
    assert f["time_down"] == "1 min 15 s" and "Voice can never unlock" in f["unlock_policy"]
    assert code_of(rec) not in repr(m.facts())
