"""StepFun triage + rewrite (IMPLEMENTATION.md sections 11.3-11.5). No network: fake transport."""

import json
import logging
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.profile import DEMO_PROFILE
from app.triage import QUESTIONS, StepFun, parse_rules
from conftest import CAREGIVER
from test_api import post_event, settings

ANSWERS = [("ok", "Help, I slipped"), ("head", "no"), ("pain", "my right hip")]
GOOD = {"conscious": True, "head_strike": False, "pain_location": "right hip", "can_move": None,
        "bleeding": None, "summary": "Conscious. Right hip pain. No head strike."}


class FakeTransport:
    def __init__(self, content=None, raise_=None, delay=0.0, raw=None):
        self.content, self.raise_, self.delay, self.raw = content, raise_, delay, raw
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.delay:
            time.sleep(self.delay)
        if self.raise_:
            raise self.raise_
        if self.raw is not None:
            return self.raw
        content = self.content if isinstance(self.content, str) else json.dumps(self.content)
        return {"choices": [{"message": {"content": content}}]}


def sf(transport, **kw):
    args = dict(api_key="k-test", base_url="https://api.stepfun.example/v1/", stub=False,
                timeout_s=0.5, transport=transport)
    args.update(kw)
    return StepFun(**args)


# --- triage ------------------------------------------------------------------

def test_good_model_output_is_used():
    rec = sf(FakeTransport(GOOD)).triage(ANSWERS, DEMO_PROFILE)
    assert rec.pain_location == "right hip" and rec.head_strike is False
    assert rec.summary == GOOD["summary"]


def test_profile_and_script_fields_never_come_from_the_model():
    lying = {**GOOD, "on_blood_thinner": False, "next_question": "Do you take aspirin?", "done": True}
    rec = sf(FakeTransport(lying)).triage(ANSWERS, DEMO_PROFILE)
    assert rec.on_blood_thinner is True                    # profile
    assert rec.next_question == QUESTIONS[3][1]           # fixed script
    assert rec.done is False


def test_request_shape():
    t = FakeTransport(GOOD)
    sf(t).triage(ANSWERS, DEMO_PROFILE)
    url, headers, body, timeout = t.calls[0]
    assert url == "https://api.stepfun.example/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k-test"
    assert body["model"] == "step-5-preview"
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "low"
    # Reasoning tokens count against max_tokens: a small budget returns an empty answer.
    assert body["max_tokens"] >= 1000
    assert body["temperature"] <= 0.2
    user = body["messages"][1]["content"]
    assert "Q: Did you hit your head?\nA: no" in user
    assert "Never guess" in body["messages"][0]["content"]


def test_fenced_json_is_accepted():
    rec = sf(FakeTransport("```json\n" + json.dumps(GOOD) + "\n```")).triage(ANSWERS, DEMO_PROFILE)
    assert rec.summary == GOOD["summary"]


@pytest.mark.parametrize("content", [
    "not json at all",
    "[1, 2, 3]",
    json.dumps({**GOOD, "conscious": "yes"}),            # wrong type
    json.dumps({**GOOD, "summary": ""}),                 # empty summary
    json.dumps({k: v for k, v in GOOD.items() if k != "summary"}),
    json.dumps({**GOOD, "summary": "x" * 301}),
    json.dumps({**GOOD, "pain_location": "y" * 41}),
])
def test_bad_output_falls_back_to_rules(content):
    rec = sf(FakeTransport(content)).triage(ANSWERS, DEMO_PROFILE)
    assert rec == parse_rules(ANSWERS, DEMO_PROFILE)


@pytest.mark.parametrize("err", [
    urllib.error.HTTPError("u", 401, "unauthorized", {}, None),
    urllib.error.URLError("dns"),
    OSError("reset"),
])
def test_transport_errors_fall_back(err):
    rec = sf(FakeTransport(raise_=err)).triage(ANSWERS, DEMO_PROFILE)
    assert rec == parse_rules(ANSWERS, DEMO_PROFILE)


def test_unexpected_response_shape_falls_back():
    rec = sf(FakeTransport(raw={"error": "quota"})).triage(ANSWERS, DEMO_PROFILE)
    assert rec == parse_rules(ANSWERS, DEMO_PROFILE)


def test_hard_deadline_even_if_the_server_hangs():
    t0 = time.monotonic()
    rec = sf(FakeTransport(GOOD, delay=2.0), triage_timeout_s=0.2).triage(ANSWERS, DEMO_PROFILE)
    assert time.monotonic() - t0 < 1.0
    assert rec == parse_rules(ANSWERS, DEMO_PROFILE)


def test_stub_mode_never_calls_the_model():
    t = FakeTransport(GOOD)
    rec = sf(t, stub=True).triage(ANSWERS, DEMO_PROFILE)
    assert t.calls == [] and rec == parse_rules(ANSWERS, DEMO_PROFILE)


@pytest.mark.parametrize("kw", [{"api_key": ""}, {"base_url": ""}])
def test_missing_key_or_url_means_rules_only(kw):
    t = FakeTransport(GOOD)
    s = sf(t, **kw)
    assert not s.enabled
    s.triage(ANSWERS, DEMO_PROFILE)
    assert t.calls == []


def test_no_answers_skips_the_call():
    t = FakeTransport(GOOD)
    sf(t).triage([], DEMO_PROFILE)
    assert t.calls == []


def test_latency_is_logged_for_success_and_fallback(caplog):
    caplog.set_level(logging.INFO, logger="firsthour.triage")
    sf(FakeTransport(GOOD)).triage(ANSWERS, DEMO_PROFILE)
    sf(FakeTransport("nope")).triage(ANSWERS, DEMO_PROFILE)
    lines = [r.getMessage() for r in caplog.records if r.name == "firsthour.triage"]
    assert "call=triage" in lines[0] and "ok=True" in lines[0] and "ms=" in lines[0]
    assert "ok=False" in lines[1] and "fallback=rules" in lines[1] and "bad json" in lines[1]


def test_transcript_never_logged(caplog):
    caplog.set_level(logging.DEBUG, logger="firsthour.triage")
    sf(FakeTransport("nope")).triage(ANSWERS, DEMO_PROFILE)
    assert all("right hip" not in r.getMessage() for r in caplog.records)


# --- rewrite -------------------------------------------------------------------

def test_rewrite_and_triage_have_separate_deadlines():
    t = FakeTransport(GOOD)
    s = sf(t, timeout_s=3.0, triage_timeout_s=15.0)
    s.triage(ANSWERS, DEMO_PROFILE)
    s.rewrite("omw")
    assert [c[3] for c in t.calls] == [15.0, 3.0]
    assert "Answer immediately" in t.calls[1][2]["messages"][0]["content"]


def test_rewrite_good():
    s = sf(FakeTransport("Your daughter is two minutes away."))
    assert s.rewrite("im like 2 min away hang on!!") == "Your daughter is two minutes away."


def test_rewrite_request_is_plain_text():
    t = FakeTransport("Help is coming.")
    sf(t).rewrite("on my way")
    body = t.calls[0][2]
    assert "response_format" not in body
    assert "Do not add information" in body["messages"][0]["content"]


@pytest.mark.parametrize("out", [
    "",
    " ".join(["word"] * 21),                    # too long
    "Help arrives in 5 minutes.",               # added a number
])
def test_rewrite_falls_back_to_original(out):
    assert sf(FakeTransport(out)).rewrite("on my way") == "on my way"


def test_rewrite_keeps_numbers_that_were_there():
    assert sf(FakeTransport("Help is 2 minutes away.")).rewrite("2 min away") == "Help is 2 minutes away."


def test_rewrite_error_and_stub_return_original():
    assert sf(FakeTransport(raise_=OSError("x"))).rewrite("on my way") == "on my way"
    assert sf(FakeTransport("Calm."), stub=True).rewrite("on my way") == "on my way"


# --- wired into the app -------------------------------------------------------------

def test_app_uses_stepfun_for_triage_and_reply():
    def transport(url, headers, body, timeout):
        system = body["messages"][0]["content"]
        content = json.dumps(GOOD) if "JSON record" in system else "Help is on the way."
        return {"choices": [{"message": {"content": content}}]}

    c = TestClient(create_app(settings(STUB_STEPFUN=False, STEPFUN_API_KEY="k", STEPFUN_BASE_URL="https://x/v1",
                                       STEPFUN_TRANSPORT=transport)))
    post_event(c)
    snap = c.post("/person/answer", json={"text": "help"}).json()
    assert snap["state"] == "ALERTING"  # the rule-based record escalates; StepFun refines it after
    for _ in range(100):
        snap = c.get("/incident").json()
        if snap["triage"]["summary"] == GOOD["summary"]:
            break
        time.sleep(0.01)
    assert snap["triage"]["summary"] == GOOD["summary"]
    assert snap["triage"]["on_blood_thinner"] is True
    c.post("/comms/inbound", json={"sender": CAREGIVER, "text": "omw"})
    assert c.app.state.spoken[-1] == "Help is on the way."
    ev = [e for e in c.app.state.events if e.type == "message_in"][-1]
    assert ev.data["text"] == "omw" and ev.data["spoken"] == "Help is on the way."


def test_app_default_is_rules_only():
    c = TestClient(create_app(settings()))
    post_event(c)
    snap = c.post("/person/answer", json={"text": "help, I can't move"}).json()
    assert snap["triage"]["can_move"] is False
