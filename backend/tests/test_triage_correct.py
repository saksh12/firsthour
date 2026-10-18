"""The operator corrects a misread triage field (POST /triage/correct). Corrections win over the
rules and step-5-preview, survive later refinements, and die with the incident."""

import json
import logging

import pytest

from app.main import create_app
from conftest import LaptopClient
from test_api import TS, lan_client, post_event, settings
from test_stepfun import _model_app, _wait_for


def _open(c):
    post_event(c)
    c.post("/person/answer", json={"text": "I need help"})


@pytest.mark.parametrize("body", [
    {"field": "on_blood_thinner", "value": False},  # from the profile, never corrected
    {"field": "summary", "value": "fine"},
    {"field": "next_question", "value": None},
    {"field": "done", "value": True},
    {"field": "nope", "value": True},
    {"field": "head_strike", "value": "yes"},        # yes/no fields take booleans
    {"field": "bleeding", "value": 1},
    {"field": "pain_location", "value": True},       # pain takes a string
    {"field": "pain_location", "value": "x" * 41},
    {"field": "head_strike"},                        # value is required (null means unknown)
    {"field": "head_strike", "value": True, "by": "someone"},
])
def test_bad_corrections_are_refused(body):
    c = LaptopClient(create_app(settings()))
    _open(c)
    assert c.post("/triage/correct", json=body).status_code == 422
    assert c.get("/incident").json()["corrections"] == {}


def test_no_incident_is_409():
    c = LaptopClient(create_app(settings()))
    r = c.post("/triage/correct", json={"field": "head_strike", "value": False})
    assert r.status_code == 409


def test_only_the_laptop_may_correct():
    lan = lan_client()
    laptop = LaptopClient(lan.app)
    _open(laptop)
    assert lan.post("/triage/correct", json={"field": "head_strike", "value": False}).status_code == 403
    assert laptop.get("/incident").json()["corrections"] == {}


def test_a_correction_beats_the_rules_and_is_audited(caplog):
    caplog.set_level(logging.INFO, logger="firsthour.incident")
    c = LaptopClient(create_app(settings()))
    _open(c)
    snap = c.post("/person/answer", json={"text": "I hit my head"}).json()
    assert snap["rules"]["head_strike"] is True and snap["triage"]["head_strike"] is True

    snap = c.post("/triage/correct", json={"field": "head_strike", "value": False}).json()
    assert snap["triage"]["head_strike"] is False
    assert snap["rules"]["head_strike"] is True  # the rules' own reading is still shown
    assert snap["corrections"] == {"head_strike": False}
    assert "No head strike." in snap["triage"]["summary"] and "Hit head" not in snap["triage"]["summary"]
    assert snap["triage"]["on_blood_thinner"] is True  # still from the profile
    assert snap["model"]["added"] == []  # the operator changed it, not the model

    ev = [e.data for e in c.app.state.events if e.type == "triage_update"][-1]
    assert ev["corrections"] == {"head_strike": False} and ev["triage"]["head_strike"] is False
    lines = [r.getMessage() for r in caplog.records if r.name == "firsthour.incident"]
    assert any(line.startswith("triage corrected field=head_strike value=False by=operator incident_id=")
               for line in lines)

    # Later answers are parsed by the rules again, and the correction still wins.
    snap = c.post("/person/answer", json={"text": "my right hip"}).json()
    assert snap["triage"]["head_strike"] is False and snap["triage"]["pain_location"] == "right hip"


def test_pain_location_takes_text_and_empty_means_unknown():
    c = LaptopClient(create_app(settings()))
    _open(c)
    snap = c.post("/triage/correct", json={"field": "pain_location", "value": "  left   wrist "}).json()
    assert snap["triage"]["pain_location"] == "left wrist" and "Left wrist pain." in snap["triage"]["summary"]
    snap = c.post("/triage/correct", json={"field": "pain_location", "value": ""}).json()
    assert snap["triage"]["pain_location"] is None and snap["corrections"] == {"pain_location": None}


def test_a_correction_beats_the_model_and_survives_its_next_result():
    model = {"conscious": True, "head_strike": None, "pain_location": None, "can_move": None,
             "bleeding": True, "summary": "Needs help. Bleeding."}

    def transport(url, headers, body, timeout):
        content = json.dumps(model) if body.get("response_format") else "ok"
        return {"choices": [{"message": {"content": content}}]}

    c = _model_app(transport)
    post_event(c)
    c.post("/person/answer", json={"text": "help, there's something on the floor"})
    done = _wait_for(c, lambda e: e.type == "triage_update" and e.data["model"]["status"] == "ok")
    assert done["triage"]["bleeding"] is True and done["model"]["added"] == ["bleeding", "summary"]

    snap = c.post("/triage/correct", json={"field": "bleeding", "value": False}).json()
    assert snap["triage"]["bleeding"] is False and snap["triage"]["summary"] != model["summary"]
    assert "No bleeding." in snap["triage"]["summary"]
    assert snap["model"]["overruled"] == ["bleeding"] and snap["model"]["added"] == []

    c.post("/person/answer", json={"text": "no"})  # the refinement runs again on two answers
    again = _wait_for(c, lambda e: e.type == "triage_update" and e.data["model"]["status"] == "ok"
                      and e.data["model"]["answers_seen"] == 2)
    assert again["model"]["record"]["bleeding"] is True  # the model still says so
    assert again["triage"]["bleeding"] is False and again["corrections"] == {"bleeding": False}
    assert c.get("/incident").json()["triage"]["bleeding"] is False


def test_a_new_incident_starts_with_no_corrections():
    c = LaptopClient(create_app(settings()))
    _open(c)
    c.post("/triage/correct", json={"field": "can_move", "value": False})
    c.post("/incident/resolve")
    post_event(c, ts=TS + 60_000)
    c.post("/person/answer", json={"text": "I need help"})
    snap = c.get("/incident").json()
    assert snap["incident_id"] == f"bath-01-{TS + 60_000}"
    assert snap["corrections"] == {} and snap["triage"]["can_move"] is None


def test_the_websocket_snapshot_carries_corrections():
    c = LaptopClient(create_app(settings()))
    _open(c)
    c.post("/triage/correct", json={"field": "conscious", "value": False})
    with c.websocket_connect("/ws/dashboard") as ws:
        d = ws.receive_json()["data"]
    assert d["snapshot"] is True and d["corrections"] == {"conscious": False}
    assert d["triage"]["conscious"] is False
