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
    assert client.app.state.spoken[-1] == "Sarah is about 5 minutes away."
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
