"""Caregiver inbound contract (IMPLEMENTATION.md section 6.6): the gateway's
{from, body, ...} shape and the X-Signature/X-Timestamp authentication on
POST /comms/inbound.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.security import INBOUND_MAX_AGE_MS, sign_event
from conftest import CAREGIVER, SECRET, STRANGER
from test_api import escalate, settings


def now_ms() -> int:
    return int(time.time() * 1000)


def post_inbound(client, payload, secret=SECRET, ts=None, sign=True):
    body = json.dumps(payload).encode()
    ts = now_ms() if ts is None else ts
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Signature"] = sign_event(secret, ts, body)
        headers["X-Timestamp"] = str(ts)
    return client.post("/comms/inbound", content=body, headers=headers)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(settings(STUB_PHOTON=False)))


GATEWAY_PAYLOAD = {
    "from": CAREGIVER,
    "body": "UNLOCK 0000",
    "platform": "imessage",
    "spaceId": "space_1",
    "senderId": "u_agent",
    "messageId": "msg_1",
}


def test_gateway_shape_accepted_and_extras_ignored(client):
    """{from, body, platform, spaceId, senderId, messageId} maps to sender/text."""
    r = post_inbound(client, GATEWAY_PAYLOAD)
    assert r.status_code == 200
    # The reply is evaluated as an unlock attempt, not dropped as a 422.
    assert r.json() == {"type": "unlock_attempt", "allowed": False, "reason": "no active incident"}


def test_sender_text_shape_still_works(client):
    r = post_inbound(client, {"sender": STRANGER, "text": "UNLOCK 0000"})
    assert r.status_code == 200
    assert r.json() == {"type": "unlock_attempt", "allowed": False, "reason": "no active incident"}


def test_signed_unlock_reaches_the_guard():
    """Full path: signed gateway-shaped body, real unlock."""
    c = TestClient(create_app(settings()))  # STUB_PHOTON=1 keeps the outbox readable
    code = escalate(c)
    r = post_inbound(c, {**GATEWAY_PAYLOAD, "body": f"UNLOCK {code}"})
    assert r.status_code == 200
    assert r.json()["allowed"] is True
    assert c.get("/incident").json()["state"] == "UNLOCKED"


def test_valid_signature_accepted_when_photon_is_live(client):
    assert post_inbound(client, GATEWAY_PAYLOAD).status_code == 200


def test_bad_signature_rejected(client):
    r = post_inbound(client, GATEWAY_PAYLOAD, secret="wrong-secret")
    assert r.status_code == 401
    assert r.json()["detail"] == "bad signature"


def test_stale_timestamp_rejected(client):
    stale = now_ms() - INBOUND_MAX_AGE_MS - 1000
    r = post_inbound(client, GATEWAY_PAYLOAD, ts=stale)
    assert r.status_code == 401
    assert r.json()["detail"] == "stale"


def test_future_timestamp_rejected(client):
    future = now_ms() + INBOUND_MAX_AGE_MS + 1000
    assert post_inbound(client, GATEWAY_PAYLOAD, ts=future).status_code == 401


def test_missing_signature_rejected_when_photon_is_live(client):
    r = post_inbound(client, GATEWAY_PAYLOAD, sign=False)
    assert r.status_code == 401
    assert r.json()["detail"] == "missing signature"


def test_missing_timestamp_rejected(client):
    body = json.dumps(GATEWAY_PAYLOAD).encode()
    r = client.post("/comms/inbound", content=body, headers={
        "Content-Type": "application/json",
        "X-Signature": sign_event(SECRET, now_ms(), body),
    })
    assert r.status_code == 401


def test_unsigned_allowed_in_stub_mode():
    """STUB_PHOTON=1: the dashboard and simulator pages post unsigned."""
    c = TestClient(create_app(settings()))
    r = c.post("/comms/inbound", json={"sender": STRANGER, "text": "UNLOCK 0000"})
    assert r.status_code == 200
    assert r.json() == {"type": "unlock_attempt", "allowed": False, "reason": "no active incident"}


def test_bad_signature_rejected_even_in_stub_mode():
    """The stub exception covers *unsigned* requests, not forged ones."""
    c = TestClient(create_app(settings()))
    r = post_inbound(c, GATEWAY_PAYLOAD, secret="wrong-secret")
    assert r.status_code == 401
