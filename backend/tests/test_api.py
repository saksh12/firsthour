"""End to end over HTTP: signed fall event -> caregiver UNLOCK -> Pi polls -> Pi verifies."""

import json
import re
import time

import pytest

from app.main import create_app
from conftest import CAREGIVER, SECRET, STRANGER, LaptopClient, signed_poll
from edge.security import CommandVerifier, sign_event

TS = 1_760_000_000_000


def settings(**overrides) -> dict:
    s = {"STUB_EDGE": True, "SHARED_SECRET": SECRET, "CAREGIVER_NUMBERS": CAREGIVER,
         "INCIDENT_WINDOW_SECONDS": 600}
    s.update(overrides)
    return s


@pytest.fixture
def client() -> LaptopClient:
    return LaptopClient(create_app(settings()))


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
    c = LaptopClient(create_app(settings(STUB_EDGE=False)))
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


def escalate(client) -> str:
    """Fall, the person asks for help, return the incident's door code (it is texted only once a
    caregiver commits, so read it from the machine to keep the state at ALERTING)."""
    assert post_event(client).status_code == 200
    client.post("/person/answer", json={"text": "Help, I slipped and can't move my leg"})
    return client.app.state.machine.incident.code


def test_fall_opens_checking_with_no_alert_yet(client):
    r = post_event(client).json()
    assert r["state"] == "CHECKING" and r["incident_id"] == f"bath-01-{TS}"
    assert client.get("/comms/stub/outbox").json() == {"messages": []}


def test_full_unlock_path_and_stranger_refused(client):
    code = escalate(client)
    assert client.get("/incident").json()["state"] == "ALERTING"

    stranger = client.post("/comms/inbound", json={"sender": STRANGER, "text": f"UNLOCK {code}"}).json()
    assert stranger == {"type": "unlock_attempt", "allowed": False, "reason": "not enrolled"}
    assert signed_poll(client).json() == {"commands": []}

    ok = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()
    assert ok["allowed"] is True
    assert client.get("/incident").json()["state"] == "UNLOCKED"

    cmds = signed_poll(client).json()["commands"]
    assert [c["action"] for c in cmds] == ["unlock"]
    # The Pi independently accepts it, and only once.
    pi = CommandVerifier(SECRET)
    assert pi.check(cmds[0], local_incident_id=cmds[0]["incident_id"]) == (True, "ok")
    assert pi.check(cmds[0], local_incident_id=cmds[0]["incident_id"]) == (False, "replay")
    # Commands are handed out once.
    assert signed_poll(client).json() == {"commands": []}


def test_im_fine_cancels_and_no_alert_is_sent(client):
    post_event(client)
    snap = client.post("/person/answer", json={"text": "I'm fine"}).json()
    assert snap["state"] == "IDLE"
    assert client.get("/comms/stub/outbox").json() == {"messages": []}


def test_replayed_fall_event_keeps_the_same_incident(client):
    post_event(client)
    r = post_event(client).json()
    assert r["incident_id"] == f"bath-01-{TS}"


def test_new_local_incident_supersedes_a_stale_one(client):
    # Issue #13 bug 2: the Pi opened a new incident, so the backend's open one is stale.
    old_code = escalate(client)
    r = post_event(client, ts=TS + 5000).json()
    assert r == {"ok": True, "incident_id": f"bath-01-{TS + 5000}", "state": "CHECKING"}
    stale = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {old_code}"}).json()
    assert stale["reason"] == "no active incident"


def test_cancelled_event_closes_incident(client):
    code = escalate(client)
    post_event(client, "cancelled", ts=TS + 1000)
    r = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()
    assert r["reason"] == "no active incident"


def test_resolve_endpoint(client):
    code = escalate(client)
    assert client.post("/incident/resolve").json()["resolved"] is True
    r = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()
    assert r["reason"] == "no active incident"


def test_caregiver_message_is_read_aloud(client):
    escalate(client)
    r = client.post("/comms/inbound", json={"sender": CAREGIVER, "text": "Hang in there, I love you"}).json()
    assert r == {"type": "message", "accepted": True}
    assert client.app.state.spoken[-1] == "Hang in there, I love you"
    assert client.get("/incident").json()["state"] == "RESPONDING"
    assert signed_poll(client).json() == {"commands": []}


def test_incident_snapshot_and_events_never_contain_the_code(client, monkeypatch):
    # Pin the code: a random one could appear by chance inside a timestamp.
    monkeypatch.setattr("app.unlock.secrets.randbelow", lambda n: 4821)
    code = escalate(client)
    assert code == "4821"
    snap = client.get("/incident").json()
    assert snap["triage"]["can_move"] is False and snap["transcript"]
    assert code not in json.dumps(snap)
    assert all(code not in e.model_dump_json() for e in client.app.state.events)


def test_stub_outbox_is_gone_when_photon_is_real():
    c = LaptopClient(create_app(settings(STUB_PHOTON=False)))
    assert c.get("/comms/stub/outbox").status_code == 404


def test_real_photon_inbound_only_trusts_the_gateway():
    c = LaptopClient(create_app(settings(STUB_PHOTON=False)))
    msg = {"sender": CAREGIVER, "text": "on my way"}
    assert c.post("/comms/inbound", json=msg).status_code == 401
    assert c.post("/comms/inbound", json=msg, headers={"Authorization": "Bearer nope"}).status_code == 401
    ts = int(time.time() * 1000)
    body = json.dumps(msg).encode()
    ok = c.post("/comms/inbound", content=body,
                headers={"X-Timestamp": str(ts), "X-Signature": sign_event(SECRET, ts, body)})
    assert ok.status_code == 200 and ok.json()["reason"] == "no active incident"


def test_real_photon_alert_goes_to_the_gateway(monkeypatch):
    import threading

    sent, done = [], threading.Event()

    def fake_transport(url, headers, body, timeout):
        sent.append((url, body))
        done.set()
        return {"id": "msg_1", "stubbed": False}

    monkeypatch.setattr("app.main.urllib_transport", fake_transport)
    c = LaptopClient(create_app(settings(STUB_PHOTON=False, COMMS_GATEWAY_URL="http://gw:8787/")))
    post_event(c)
    c.post("/person/answer", json={"text": "help"})
    assert done.wait(2)
    url, body = sent[0]
    assert url == "http://gw:8787/send" and body["to"] == CAREGIVER
    assert "How soon can you be here?" in body["body"] and not re.search(r"UNLOCK \d{4}", body["body"])


def test_door_code_goes_only_to_the_committed_caregiver_and_never_to_the_dashboard():
    c = LaptopClient(create_app(settings()))
    post_event(c)
    c.post("/person/answer", json={"text": "help"})
    code = c.app.state.machine.incident.code
    assert not any(f"UNLOCK {code}" in m["text"] for m in c.get("/comms/stub/outbox").json()["messages"])
    c.post("/comms/inbound", json={"sender": CAREGIVER, "text": "on my way, 5 min"})
    inbox = c.get("/comms/stub/outbox").json()["messages"]
    assert inbox[-1]["to"] == [CAREGIVER] and f"reply UNLOCK {code}." in inbox[-1]["text"]
    outs = [e.data for e in c.app.state.events if e.type == "message_out" and e.data.get("kind") == "code"]
    assert outs and all("text" not in d for d in outs)


def test_alert_blocked_by_shared_line_is_sent_once_the_caregiver_texts(monkeypatch):
    """#26: Photon's shared line returns 409 until the caregiver has texted it once."""
    import io
    import threading
    import urllib.error

    sent, attempt = [], threading.Event()

    def fake_transport(url, headers, body, timeout):
        sent.append(body)
        attempt.set()
        if len(sent) == 1:
            raise urllib.error.HTTPError(url, 409, "no conversation", {}, io.BytesIO(b""))
        return {"id": "msg_2"}

    monkeypatch.setattr("app.main.urllib_transport", fake_transport)
    c = LaptopClient(create_app(settings(STUB_PHOTON=False)))
    post_event(c)
    c.post("/person/answer", json={"text": "help"})
    assert attempt.wait(2)
    for _ in range(100):  # the dashboard hears that it didn't go out
        if any(e.type == "message_out" and e.data.get("delivered") is False for e in c.app.state.events):
            break
        time.sleep(0.01)
    else:
        pytest.fail("no undelivered alert event")

    attempt.clear()
    body = json.dumps({"sender": CAREGIVER, "text": "hi, what happened?"}).encode()
    ts = int(time.time() * 1000)
    c.post("/comms/inbound", content=body,
           headers={"X-Timestamp": str(ts), "X-Signature": sign_event(SECRET, ts, body)})
    assert attempt.wait(2)
    assert sent[1]["to"] == CAREGIVER and sent[1]["body"] == sent[0]["body"]


def test_help_button_escalates(client):
    post_event(client)
    post_event(client, "help_button", ts=TS + 500)
    assert client.get("/incident").json()["state"] == "ALERTING"


def test_missing_secret_refuses_to_start():
    with pytest.raises(RuntimeError):
        create_app(settings(SHARED_SECRET=""))


def test_unsigned_or_replayed_poll_is_refused_and_leaves_the_unlock_queued(client):
    code = escalate(client)
    assert client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()["allowed"]
    # Someone else on the Wi-Fi polls first: no signature, then a forged one.
    r = client.get("/edge/commands", params={"device_id": "bath-01"})
    assert r.status_code == 401
    ts = int(time.time() * 1000)
    r = client.get("/edge/commands", params={"device_id": "bath-01", "ts": ts}, headers={"X-Signature": "00" * 32})
    assert r.status_code == 401
    # The Pi's signed poll still gets the unlock.
    r = signed_poll(client)
    assert [c["action"] for c in r.json()["commands"]] == ["unlock"]
    # Replaying that exact poll is refused.
    replay = client.get(str(r.request.url), headers={"X-Signature": r.request.headers["X-Signature"]})
    assert replay.status_code == 401 and replay.json()["detail"] == "replay"


def reset(client, token=SECRET):
    return client.post("/demo/reset", headers={"Authorization": f"Bearer {token}"})


def test_demo_reset_needs_the_token(client):
    escalate(client)
    assert client.post("/demo/reset").status_code == 401
    assert reset(client, token="wrong").status_code == 401
    assert client.get("/incident").json()["state"] == "ALERTING"
    assert signed_poll(client).json() == {"commands": []}


def test_three_demo_runs_with_reset_between_all_end_clean(client):
    pi = CommandVerifier(SECRET)
    for run in range(3):
        ts = int(time.time() * 1000) + run
        assert post_event(client, ts=ts).status_code == 200
        client.post("/person/answer", json={"text": "help"})
        code = client.app.state.machine.incident.code
        assert client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()["allowed"]
        local = f"bath-01-{ts}"
        unlock = signed_poll(client).json()["commands"]
        assert [c["action"] for c in unlock] == ["unlock"] and pi.check(unlock[0], local) == (True, "ok")

        r = reset(client).json()
        assert r["state"] == "IDLE" and r["devices"] == ["bath-01"]
        assert client.app.state.guard.active_incident_id is None
        cmds = signed_poll(client).json()["commands"]
        # alarm_off closes the Pi's incident first, so the reset that follows is obeyed.
        assert [c["action"] for c in cmds] == ["alarm_off", "reset"]
        assert pi.check(cmds[0], local) == (True, "ok")
        assert pi.check(cmds[1], None) == (True, "ok")
        assert client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()["reason"] == "no active incident"


def test_unlock_failed_from_the_pi_reaches_the_dashboard(client):
    escalate(client)
    assert post_event(client, "unlock_failed").status_code == 200
    ev = list(client.app.state.events)[-1]
    assert ev.type == "device_status" and ev.data == {"device_id": "bath-01", "event": "unlock_failed"}


# --- who may press the buttons ------------------------------------------------------

def lan_client(**overrides):
    from fastapi.testclient import TestClient

    return TestClient(create_app(settings(**overrides)), client=("192.168.1.50", 50000))


def test_operator_actions_refused_from_another_device_on_the_wifi():
    lan = lan_client()
    app = lan.app
    laptop = LaptopClient(app)
    escalate(laptop)
    assert lan.post("/incident/resolve").status_code == 403
    assert lan.post("/person/answer", json={"text": "I'm fine"}).status_code == 403
    assert lan.get("/comms/stub/outbox").status_code == 403  # the alert carries the code
    assert lan.post("/comms/inbound", json={"sender": CAREGIVER, "text": "UNLOCK 0000"}).status_code == 403
    assert laptop.get("/incident").json()["state"] == "ALERTING"
    # Watching still works, and so does the Pi.
    assert lan.get("/incident").status_code == 200
    assert signed_poll(lan).status_code == 200
    assert post_event(lan, "heartbeat").status_code == 200


def test_tunnel_reaches_health_only(client):
    via = {"X-Forwarded-For": "203.0.113.7", "Cf-Connecting-IP": "203.0.113.7"}
    assert client.get("/health", headers=via).status_code == 200
    for method, path in [("POST", "/incident/resolve"), ("GET", "/comms/stub/outbox"), ("GET", "/incident"),
                         ("POST", "/demo/reset"), ("GET", "/edge/commands")]:
        assert client.request(method, path, headers=via).status_code == 404, path
    # Each proxy header on its own is enough.
    assert client.post("/incident/resolve", headers={"X-Forwarded-For": "203.0.113.7"}).status_code == 404


def test_dashboard_socket_refused_through_the_tunnel(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/dashboard", headers={"X-Forwarded-For": "203.0.113.7"}) as ws:
            ws.receive_json()
