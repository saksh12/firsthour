"""Voice escalation over HTTP: placing the call, the signed post-call webhook, the read-only tool."""

import hashlib
import hmac
import json
import re
import time

import pytest

from app.calls import VoiceCalls, verify_postcall_signature
from app.main import create_app
from conftest import CAREGIVER, SECRET, LaptopClient
from test_api import escalate, settings

WEBHOOK = "whsec-test"
TOOL = "tool-token"


def sign(raw: bytes, secret=WEBHOOK, t=None) -> str:
    t = int(time.time()) if t is None else t
    return f"t={t},v0=" + hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()


@pytest.fixture
def client():
    return LaptopClient(create_app(settings(
        ELEVENLABS_WEBHOOK_SECRET=WEBHOOK, VOICE_TOOL_TOKEN=TOOL, VOICE_CALL_AFTER_SECONDS=0,
        CAREGIVER_NAMES=f"{CAREGIVER}:Sarah")))


def postcall(conv_id, turns, incident_id=None):
    return json.dumps({"type": "post_call_transcription", "event_timestamp": int(time.time()), "data": {
        "conversation_id": conv_id, "agent_id": "a",
        "transcript": [{"role": "agent", "message": "How many minutes?"}] +
                      [{"role": "user", "message": t} for t in turns],
        "analysis": {"transcript_summary": "Caregiver will come."},
        "conversation_initiation_client_data": {"dynamic_variables": {"incident_id": incident_id}},
    }}).encode()


def test_signature_check():
    raw = b'{"a":1}'
    assert verify_postcall_signature(WEBHOOK, raw, sign(raw)) == (True, "ok")
    assert verify_postcall_signature(WEBHOOK, raw, sign(raw, "other"))[1] == "bad signature"
    assert verify_postcall_signature(WEBHOOK, raw, sign(raw, t=int(time.time()) - 3600))[1] == "stale signature"
    assert verify_postcall_signature(WEBHOOK, raw, None)[1] == "missing signature"
    assert verify_postcall_signature("", raw, sign(raw))[1] == "webhook secret not configured"


def test_real_call_request_shape():
    sent = []
    calls = VoiceCalls(stub=False, api_key="k", agent_id="ag", phone_number_id="ph",
                       transport=lambda url, h, body, t: sent.append((url, h, body)) or
                       {"success": True, "conversation_id": "conv-1"})
    assert calls.place("+1310", {"incident_id": "i"}) == {"conversation_id": "conv-1", "stubbed": False}
    url, headers, body = sent[0]
    assert url == "https://api.elevenlabs.io/v1/convai/sip-trunk/outbound-call"
    assert headers == {"xi-api-key": "k"}
    assert body == {"agent_id": "ag", "agent_phone_number_id": "ph", "to_number": "+1310",
                    "conversation_initiation_client_data": {"dynamic_variables": {"incident_id": "i"}}}


def test_missing_keys_stay_stubbed():
    assert VoiceCalls(stub=False, api_key="k").real is False


def test_silence_places_stub_call_then_webhook_carries_eta_back(client):
    code = escalate(client)
    client.app.state.machine.tick()
    log = client.app.state.calls.log
    assert [c["to"] for c in log] == [CAREGIVER]
    assert code not in json.dumps(log)
    conv = log[0]["conversation_id"]
    raw = postcall(conv, ["yes", "I'll be there in 5 minutes"])
    r = client.post("/voice/postcall", content=raw, headers={"ElevenLabs-Signature": sign(raw)})
    assert r.status_code == 200 and r.json()["status"] == "arriving"
    assert client.app.state.spoken[-2] == "Sarah is about 5 minutes away."
    texts = client.get("/comms/stub/outbox").json()["messages"]
    assert texts[-1]["to"] == [CAREGIVER] and texts[-1]["text"].startswith("You said 5 minutes on the call.")
    assert client.get("/incident").json()["state"] == "RESPONDING"


def test_webhook_needs_a_valid_signature_and_never_unlocks(client):
    code = escalate(client)
    client.app.state.machine.tick()
    conv = client.app.state.calls.log[0]["conversation_id"]
    raw = postcall(conv, [f"UNLOCK {code}"])
    assert client.post("/voice/postcall", content=raw).status_code == 401
    assert client.post("/voice/postcall", content=raw,
                       headers={"ElevenLabs-Signature": sign(raw, "nope")}).status_code == 401
    assert client.post("/voice/postcall", content=raw, headers={"ElevenLabs-Signature": sign(raw)}).status_code == 200
    assert client.get("/incident").json()["state"] == "ALERTING"
    assert client.app.state.guard.audit == []


def test_inbound_call_by_caller_id_records_no_commitment(client):
    escalate(client)
    inc = client.get("/incident").json()["incident_id"]
    raw = postcall("someone-elses-call", ["on my way"], incident_id=inc)
    r = client.post("/voice/postcall", content=raw, headers={"ElevenLabs-Signature": sign(raw)}).json()
    assert r["accepted"] is True and r["commitment"] is None


def test_voice_tool_is_token_gated_and_has_no_code(client):
    assert client.get("/voice/incident").status_code == 401
    auth = {"Authorization": f"Bearer {TOOL}"}
    assert client.get("/voice/incident", headers=auth).json()["active"] is False
    code = escalate(client)
    body = client.get("/voice/incident", headers=auth).json()
    assert body["active"] and body["patient"] == "Margaret"
    assert code not in json.dumps(body) and CAREGIVER not in json.dumps(body)


def test_voice_paths_reachable_through_tunnel_with_auth_only(client):
    proxied = {"X-Forwarded-For": "1.2.3.4"}
    assert client.get("/voice/incident", headers=proxied).status_code == 401
    assert client.get("/voice/incident", headers={**proxied, "Authorization": f"Bearer {TOOL}"}).status_code == 200
    assert client.post("/voice/postcall", content=b"{}", headers=proxied).status_code == 401
    assert client.get("/incident", headers=proxied).status_code == 404


def test_voice_tool_disabled_without_token():
    c = LaptopClient(create_app(settings()))
    assert c.get("/voice/incident", headers={"Authorization": "Bearer "}).status_code == 401
    raw = b"{}"
    assert c.post("/voice/postcall", content=raw, headers={"ElevenLabs-Signature": sign(raw)}).status_code == 401


def test_question_answered_in_thread_over_http(client):
    escalate(client)
    r = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": "is she still talking?"}).json()
    assert r == {"type": "question", "accepted": True}
    last = client.get("/comms/stub/outbox").json()["messages"][-1]
    assert last["kind"] == "answer" and last["text"].startswith("Margaret has been down")
    types = [e.type for e in client.app.state.events]
    assert "delivery_status" in types


# --- the finished call fetched from ElevenLabs when the webhook does not arrive (dead tunnel) ---

def conversation(conv_id, turns, status="done", incident_id=None):
    """GET /v1/convai/conversations/{id} body, the same model the webhook wraps in `data`."""
    return {"conversation_id": conv_id, "agent_id": "a", "status": status,
            "transcript": [{"role": "agent", "message": "How many minutes?"}] +
                          [{"role": "user", "message": t} for t in turns],
            "analysis": {"transcript_summary": "Caregiver will come."},
            "conversation_initiation_client_data": {"dynamic_variables": {"incident_id": incident_id}}}


def test_result_fetches_the_conversation_and_waits_while_it_runs():
    seen = []
    state = {"status": "in-progress"}
    calls = VoiceCalls(stub=False, api_key="k", agent_id="ag", phone_number_id="ph",
                       fetch=lambda url, h, t: seen.append((url, h)) or conversation("conv 1", ["ten"], state["status"]))
    assert calls.result("conv 1") is None
    assert seen[0] == ("https://api.elevenlabs.io/v1/convai/conversations/conv%201", {"xi-api-key": "k"})
    state["status"] = "done"
    r = calls.result("conv 1")
    assert r == {"conversation_id": "conv 1", "incident_id": None, "user_turns": ["ten"],
                 "summary": "Caregiver will come.", "status": "done"}
    assert VoiceCalls(stub=True).result("conv 1") is None  # stub calls have nothing to fetch


def arm_poll(client, fetch):
    """A stub call was placed; make the app treat it as real for the result poll."""
    calls = client.app.state.calls
    calls.real, calls._fetch = True, fetch
    return calls.log[0]["conversation_id"]


def test_poll_carries_the_call_back_when_the_webhook_never_comes(client):
    escalate(client)
    client.app.state.machine.tick()
    conv = arm_poll(client, lambda url, h, t: conversation(conv, ["I'll be there in 5 minutes"]))
    inc = client.get("/incident").json()["incident_id"]
    r = client.app.state.poll_call_result(conv, inc, sleep=lambda _s: None)
    assert r["status"] == "arriving"
    assert client.get("/incident").json()["state"] == "RESPONDING"
    assert client.app.state.spoken[-2] == "Sarah is about 5 minutes away."
    texts = client.get("/comms/stub/outbox").json()["messages"]
    assert texts[-1]["text"].startswith("You said 5 minutes on the call.")
    # The webhook arriving late for the same call changes nothing and texts no second code.
    raw = postcall(conv, ["I'll be there in 5 minutes"])
    r = client.post("/voice/postcall", content=raw, headers={"ElevenLabs-Signature": sign(raw)}).json()
    assert r == {"ok": True, "accepted": False, "reason": "already recorded"}
    assert client.get("/comms/stub/outbox").json()["messages"] == texts


def test_poll_stops_once_the_webhook_recorded_the_call(client):
    escalate(client)
    client.app.state.machine.tick()
    fetched = []
    conv = arm_poll(client, lambda url, h, t: fetched.append(url) or conversation(conv, ["five"]))
    inc = client.get("/incident").json()["incident_id"]
    raw = postcall(conv, ["I'll be there in 5 minutes"])
    assert client.post("/voice/postcall", content=raw, headers={"ElevenLabs-Signature": sign(raw)}).status_code == 200
    assert client.app.state.poll_call_result(conv, inc, sleep=lambda _s: None) is None
    assert fetched == []


def test_poll_stops_when_the_incident_is_over_and_retries_after_an_error(client):
    escalate(client)
    client.app.state.machine.tick()
    attempts = []

    def flaky(url, h, t):
        attempts.append(url)
        if len(attempts) == 1:
            raise OSError("tunnel-less network blip")
        return conversation(conv, [], status="in-progress")

    conv = arm_poll(client, flaky)
    inc = client.get("/incident").json()["incident_id"]
    ticks = {"n": 0}

    def sleep(_s):
        ticks["n"] += 1
        if ticks["n"] == 3:  # the operator resolves while the call is still running
            client.post("/incident/resolve")

    assert client.app.state.poll_call_result(conv, inc, sleep=sleep) is None
    assert len(attempts) == 2  # error, then still running, then the incident was gone
    assert client.get("/incident").json()["state"] == "IDLE"


def test_poll_reports_a_call_that_failed_on_the_provider_side(client):
    escalate(client)
    client.app.state.machine.tick()
    conv = arm_poll(client, lambda url, h, t: conversation(conv, [], status="failed"))
    inc = client.get("/incident").json()["incident_id"]
    assert client.app.state.poll_call_result(conv, inc, sleep=lambda _s: None) is None
    updates = [e.data for e in client.app.state.events if e.type == "call_update"]
    assert updates[-1]["status"] == "failed" and updates[-1]["incident_id"] == inc
    assert client.get("/incident").json()["state"] == "ALERTING"
