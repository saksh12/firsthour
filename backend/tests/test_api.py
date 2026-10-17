"""End to end over HTTP: signed fall event -> caregiver UNLOCK -> Pi polls -> Pi verifies."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import CAREGIVER, SECRET, STRANGER
from edge.security import CommandVerifier, sign_event

TS = 1_760_000_000_000


def settings(**overrides) -> dict:
    s = {"STUB_EDGE": True, "SHARED_SECRET": SECRET, "CAREGIVER_NUMBERS": CAREGIVER,
         "INCIDENT_WINDOW_SECONDS": 600}
    s.update(overrides)
    return s


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(settings()))


def post_event(client, type_="fall_suspected", secret=SECRET, ts=TS):
    body = json.dumps({
        "device_id": "bath-01", "ts": ts, "type": type_,
        "sensors": {"sound_peak": 812, "accel_delta_g": 1.9, "dist_cm": 28},
        "local_incident_id": f"bath-01-{ts}",
    }).encode()
    return client.post("/edge/event", content=body,
                       headers={"X-Signature": sign_event(secret, ts, body), "Content-Type": "application/json"})


def test_health_ok_in_stub_mode(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["edge"] == "stubbed"


def test_health_503_without_heartbeat_when_edge_is_live():
    c = TestClient(create_app(settings(STUB_EDGE=False)))
    assert c.get("/health").status_code == 503
    post_event(c, "heartbeat")
    assert c.get("/health").status_code == 200


def test_event_with_bad_signature_rejected(client):
    r = post_event(client, secret="wrong")
    assert r.status_code == 401
    assert client.app.state.guard.active_incident_id is None


def test_unsigned_event_rejected(client):
    r = client.post("/edge/event", json={"device_id": "bath-01", "ts": TS, "type": "fall_suspected"})
    assert r.status_code == 401


def test_full_unlock_path_and_stranger_refused(client):
    r = post_event(client)
    assert r.status_code == 200 and r.json()["incident_id"] == f"bath-01-{TS}"
    code = client.app.state.alert_code

    stranger = client.post("/comms/inbound", json={"sender": STRANGER, "text": f"UNLOCK {code}"}).json()
    assert stranger == {"type": "unlock_attempt", "allowed": False, "reason": "not enrolled"}
    assert client.get("/edge/commands", params={"device_id": "bath-01"}).json() == {"commands": []}

    ok = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()
    assert ok["allowed"] is True

    cmds = client.get("/edge/commands", params={"device_id": "bath-01"}).json()["commands"]
    assert [c["action"] for c in cmds] == ["unlock"]
    # The Pi independently accepts it, and only once.
    pi = CommandVerifier(SECRET)
    assert pi.check(cmds[0], local_incident_open=True) == (True, "ok")
    assert pi.check(cmds[0], local_incident_open=True) == (False, "replay")
    # Commands are handed out once.
    assert client.get("/edge/commands", params={"device_id": "bath-01"}).json() == {"commands": []}


def test_second_fall_event_keeps_the_same_incident_and_code(client):
    post_event(client)
    code = client.app.state.alert_code
    r = post_event(client, ts=TS + 5000)
    assert r.json()["incident_id"] == f"bath-01-{TS}"
    assert client.app.state.alert_code == code


def test_cancelled_event_closes_incident(client):
    post_event(client)
    code = client.app.state.alert_code
    post_event(client, "cancelled", ts=TS + 1000)
    r = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()
    assert r["reason"] == "no active incident"


def test_non_unlock_reply_is_a_message(client):
    post_event(client)
    r = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": "I'm 2 minutes away"}).json()
    assert r["type"] == "message"
    assert client.get("/edge/commands", params={"device_id": "bath-01"}).json() == {"commands": []}


def test_missing_secret_refuses_to_start():
    with pytest.raises(RuntimeError):
        create_app(settings(SHARED_SECRET=""))
