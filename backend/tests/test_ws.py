"""Dashboard WebSocket (IMPLEMENTATION.md section 6.7)."""

import json


from app.main import create_app
from conftest import CAREGIVER, STRANGER, LaptopClient
from test_api import post_event, settings

CONTRACT_TYPES = {"incident_update", "triage_update", "message_in", "message_out", "unlock_attempt", "device_status"}


def read_until(ws, type_, pred=lambda d: True, limit=50):
    """Read events until one of `type_` matches; return it and everything seen."""
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == type_ and pred(msg["data"]):
            return msg, seen
    raise AssertionError(f"no {type_} in {[m['type'] for m in seen]}")


def test_first_message_is_a_snapshot():
    with LaptopClient(create_app(settings())) as c, c.websocket_connect("/ws/dashboard") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "incident_update", "data": {"snapshot": True, "pi_stt": False, "state": "IDLE",
                                                         "incident_id": None}}


def test_snapshot_mid_incident_has_clock_and_transcript():
    with LaptopClient(create_app(settings())) as c:
        post_event(c)
        c.post("/person/answer", json={"text": "help"})
        with c.websocket_connect("/ws/dashboard") as ws:
            d = ws.receive_json()["data"]
            assert d["state"] == "ALERTING" and d["opened_at_ms"] > 0
            assert d["transcript"][0]["a"] == "help"


def test_full_incident_streams_contract_events_and_never_the_code(monkeypatch):
    monkeypatch.setattr("app.unlock.secrets.randbelow", lambda n: 4821)
    with LaptopClient(create_app(settings())) as c, c.websocket_connect("/ws/dashboard") as ws:
        ws.receive_json()  # snapshot
        post_event(c)
        read_until(ws, "incident_update", lambda d: d["state"] == "CHECKING")

        c.post("/person/answer", json={"text": "Help, I slipped and can't move my leg"})
        tri, _ = read_until(ws, "triage_update")
        assert tri["data"]["triage"]["can_move"] is False
        read_until(ws, "message_out", lambda d: d["kind"] == "alert")

        c.post("/comms/inbound", json={"sender": STRANGER, "text": "UNLOCK 4821"})
        refused, _ = read_until(ws, "unlock_attempt")
        assert refused["data"]["allowed"] is False and refused["data"]["reason"] == "not enrolled"

        c.post("/comms/inbound", json={"sender": CAREGIVER, "text": "I'm 2 minutes away"})
        read_until(ws, "message_in", lambda d: d["text"] == "I'm 2 minutes away")

        c.post("/comms/inbound", json={"sender": CAREGIVER, "text": "UNLOCK 4821"})
        granted, _ = read_until(ws, "unlock_attempt")
        assert granted["data"]["allowed"] is True
        _, seen = read_until(ws, "incident_update", lambda d: d["state"] == "UNLOCKED")

        c.post("/incident/resolve")
        _, more = read_until(ws, "incident_update", lambda d: d["state"] == "IDLE")

        everything = [tri, refused, granted, *seen, *more]
        assert {m["type"] for m in everything} <= CONTRACT_TYPES
        assert all("4821" not in json.dumps(m) for m in everything)


def test_device_status_event_on_edge_event():
    with LaptopClient(create_app(settings())) as c, c.websocket_connect("/ws/dashboard") as ws:
        ws.receive_json()
        post_event(c, "help_button")
        msg, _ = read_until(ws, "device_status")
        assert msg["data"] == {"device_id": "bath-01", "event": "help_button"}


def test_two_dashboards_both_get_events():
    with LaptopClient(create_app(settings())) as c, \
            c.websocket_connect("/ws/dashboard") as a, c.websocket_connect("/ws/dashboard") as b:
        a.receive_json(), b.receive_json()
        post_event(c)
        for ws in (a, b):
            read_until(ws, "incident_update", lambda d: d["state"] == "CHECKING")


def test_disconnect_does_not_break_the_incident_flow():
    with LaptopClient(create_app(settings())) as c:
        with c.websocket_connect("/ws/dashboard") as ws:
            ws.receive_json()
        post_event(c)
        r = c.post("/person/answer", json={"text": "help"})
        assert r.status_code == 200 and r.json()["state"] == "ALERTING"
