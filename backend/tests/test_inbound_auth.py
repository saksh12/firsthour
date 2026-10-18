"""Caregiver inbound contract (IMPLEMENTATION.md section 6.6): the gateway's
{from, body, ...} shape and the X-Signature/X-Timestamp authentication on
POST /comms/inbound.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient
from conftest import LaptopClient

from app.main import create_app
from app.security import INBOUND_MAX_AGE_MS, sign_event
from conftest import CAREGIVER, SECRET, STRANGER, signed_poll
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


TS = 1_760_000_000_000


@pytest.fixture
def client() -> TestClient:
    return LaptopClient(create_app(settings(STUB_PHOTON=False)))


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
    c = LaptopClient(create_app(settings()))  # STUB_PHOTON=1 keeps the outbox readable
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
    c = LaptopClient(create_app(settings()))
    r = c.post("/comms/inbound", json={"sender": STRANGER, "text": "UNLOCK 0000"})
    assert r.status_code == 200
    assert r.json() == {"type": "unlock_attempt", "allowed": False, "reason": "no active incident"}


def test_bad_signature_rejected_even_in_stub_mode():
    """The stub exception covers *unsigned* requests, not forged ones."""
    c = LaptopClient(create_app(settings()))
    r = post_inbound(c, GATEWAY_PAYLOAD, secret="wrong-secret")
    assert r.status_code == 401


# --- malformed bodies: 422, never an unhandled 500 ----------------------------


def test_signed_body_missing_body_field_is_422(client):
    r = post_inbound(client, {"from": CAREGIVER, "platform": "imessage"})
    assert r.status_code == 422


def test_from_as_number_is_422(client):
    r = post_inbound(client, {**GATEWAY_PAYLOAD, "from": 123})
    assert r.status_code == 422


def test_non_json_body_is_422(client):
    body = b"this is not json"
    ts = now_ms()
    r = client.post("/comms/inbound", content=body, headers={
        "Content-Type": "application/json",
        "X-Signature": sign_event(SECRET, ts, body),
        "X-Timestamp": str(ts),
    })
    assert r.status_code == 422


def test_unsigned_empty_body_is_422_in_stub_mode():
    c = LaptopClient(create_app(settings()))
    assert c.post("/comms/inbound", json={}).status_code == 422


def test_malformed_edge_event_is_422(client):
    """Same bug class on /edge/event: validly signed but invalid schema."""
    body = json.dumps({"ts": TS}).encode()  # missing device_id, type
    r = client.post("/edge/event", content=body, headers={
        "Content-Type": "application/json",
        "X-Signature": sign_event(SECRET, TS, body),
    })
    assert r.status_code == 422


def send_raw(client, body: bytes, ts: int):
    """Resend the identical captured request (same body, same signature)."""
    return client.post("/comms/inbound", content=body, headers={
        "Content-Type": "application/json",
        "X-Signature": sign_event(SECRET, ts, body),
        "X-Timestamp": str(ts),
    })


# --- replay inside the 30 s window --------------------------------------------


def test_replayed_signed_reply_is_409_and_does_not_lock_out():
    c = LaptopClient(create_app(settings()))
    code = escalate(c)
    body = json.dumps({**GATEWAY_PAYLOAD, "body": "UNLOCK 0000"}).encode()
    ts = now_ms()
    assert send_raw(c, body, ts).status_code == 200  # first delivery: bad code
    for _ in range(3):
        r = send_raw(c, body, ts)
        assert r.status_code == 409 and r.json()["detail"] == "replay"
    # The replays did not burn the 3-try lockout: the real reply still works.
    ok = post_inbound(c, {**GATEWAY_PAYLOAD, "body": f"UNLOCK {code}", "messageId": "msg_2"})
    assert ok.status_code == 200 and ok.json()["allowed"] is True


def test_replayed_valid_unlock_is_409_and_queues_one_command():
    c = LaptopClient(create_app(settings()))
    code = escalate(c)
    body = json.dumps({**GATEWAY_PAYLOAD, "body": f"UNLOCK {code}"}).encode()
    ts = now_ms()
    assert send_raw(c, body, ts).json()["allowed"] is True
    assert send_raw(c, body, ts).status_code == 409
    cmds = signed_poll(c).json()["commands"]
    assert [x["action"] for x in cmds] == ["unlock"]  # no second unlock queued


def test_same_message_id_resigned_is_replay():
    """A re-sign (new ts, new signature) of the same Photon message is deduped."""
    c = LaptopClient(create_app(settings()))
    assert post_inbound(c, GATEWAY_PAYLOAD).status_code == 200
    r = post_inbound(c, GATEWAY_PAYLOAD, ts=now_ms() + 1)
    assert r.status_code == 409


def test_unsigned_stub_requests_are_not_deduped():
    c = LaptopClient(create_app(settings()))
    for _ in range(2):
        r = c.post("/comms/inbound", json={"sender": STRANGER, "text": "UNLOCK 0000"})
        assert r.status_code == 200


def test_replay_cache_prunes_old_entries():
    from app.security import ReplayCache

    cache = ReplayCache()
    t0 = now_ms()
    assert not cache.is_replay("sig1", t0)
    assert cache.is_replay("sig1", t0 + 1000)
    # Entries older than 2x the window are dropped on the next check.
    cache._seen["sig1"] = t0 - 3 * INBOUND_MAX_AGE_MS
    assert not cache.is_replay("sig2", t0 + 1000)
    assert "sig1" not in cache._seen
