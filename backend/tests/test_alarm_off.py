"""When the backend incident ends (CANCELLED or RESOLVED), the Pi gets a signed alarm_off."""

import json

import pytest

from app.incident import IncidentMachine
from app.main import create_app
from app.models import IncidentState as S
from app.profile import DEMO_PROFILE
from app.unlock import UnlockGuard
from conftest import CAREGIVER, SECRET, LaptopClient, signed_poll
from edge.security import CommandVerifier
from test_api import TS, post_event, settings

# --- machine hook ----------------------------------------------------------------


@pytest.fixture
def closes():
    return []


@pytest.fixture
def m(clock, closes):
    guard = UnlockGuard(allowed=[CAREGIVER], clock=clock)
    return IncidentMachine(guard, DEMO_PROFILE, clock=clock,
                           on_close=lambda inc, final: closes.append((inc.id, inc.device_id, final)))


def test_on_close_fires_on_cancel(m, closes):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("I'm fine")
    assert closes == [("inc-1", "bath-01", S.CANCELLED)]


def test_on_close_fires_on_resolve(m, closes):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    m.resolve()
    assert closes == [("inc-1", "bath-01", S.RESOLVED)]


def test_on_close_fires_on_edge_cancel(m, closes):
    m.on_fall("bath-01", "inc-1")
    m.cancel("cancelled on device")
    assert closes == [("inc-1", "bath-01", S.CANCELLED)]


def test_on_close_does_not_fire_for_other_transitions(m, closes, clock):
    m.on_fall("bath-01", "inc-1")
    m.on_answer("help")
    m.caregiver_reply(CAREGIVER, "on my way")
    clock.advance(60)
    m.tick()
    assert closes == []


def test_resolve_with_no_incident_does_nothing(m, closes):
    assert not m.resolve()
    assert closes == []


# --- over HTTP, checked by the Pi's verifier ---------------------------------------


@pytest.fixture
def client():
    return LaptopClient(create_app(settings()))


def poll(client):
    return signed_poll(client).json()["commands"]


def escalate(client):
    post_event(client)
    client.post("/person/answer", json={"text": "help"})
    return client.app.state.machine.incident.code


def test_resolve_sends_signed_alarm_off_the_pi_accepts(client):
    escalate(client)
    client.post("/incident/resolve")
    cmds = poll(client)
    assert [c["action"] for c in cmds] == ["alarm_off"]
    assert cmds[0]["incident_id"] == f"bath-01-{TS}"
    pi = CommandVerifier(SECRET)
    assert pi.check(cmds[0], local_incident_id=cmds[0]["incident_id"]) == (True, "ok")


def test_im_fine_sends_alarm_off(client):
    post_event(client)
    client.post("/person/answer", json={"text": "I'm fine"})
    assert [c["action"] for c in poll(client)] == ["alarm_off"]


def test_edge_cancelled_event_sends_alarm_off(client):
    post_event(client)
    post_event(client, "cancelled", ts=TS + 1000)
    assert [c["action"] for c in poll(client)] == ["alarm_off"]


def test_alarm_off_signed_with_wrong_secret_is_refused_by_pi(client):
    post_event(client)
    client.post("/incident/resolve")
    cmd = poll(client)[0]
    assert CommandVerifier("other-secret").check(cmd, local_incident_id=cmd["incident_id"]) == (False, "bad signature")


def test_resolve_drops_an_unlock_the_pi_has_not_picked_up(client):
    code = escalate(client)
    assert client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"}).json()["allowed"]
    client.post("/incident/resolve")  # before the Pi polled
    assert [c["action"] for c in poll(client)] == ["alarm_off"]


def test_unlock_already_picked_up_is_unaffected(client):
    code = escalate(client)
    client.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"})
    assert [c["action"] for c in poll(client)] == ["unlock"]
    client.post("/incident/resolve")
    assert [c["action"] for c in poll(client)] == ["alarm_off"]


def test_no_alarm_off_while_incident_is_still_open(client):
    escalate(client)
    client.post("/comms/inbound", json={"sender": CAREGIVER, "text": "on my way"})
    assert poll(client) == []


def test_alarm_off_never_carries_the_code(client, monkeypatch):
    monkeypatch.setattr("app.unlock.secrets.randbelow", lambda n: 4821)
    escalate(client)
    client.post("/incident/resolve")
    assert "4821" not in json.dumps(poll(client))
