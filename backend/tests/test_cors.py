"""CORS for the dashboard and caregiver simulator pages (served from another port)."""

import pytest

from app.main import create_app, parse_origins
from conftest import LaptopClient
from test_api import settings

DASH = "http://localhost:5173"
EVIL = "https://evil.example"


def preflight(c, origin, path="/person/answer"):
    return c.options(path, headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })


@pytest.fixture
def client():
    return LaptopClient(create_app(settings()))  # default origins


def test_preflight_from_dashboard_origin_allowed(client):
    r = preflight(client, DASH)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASH
    assert "POST" in r.headers["access-control-allow-methods"]


@pytest.mark.parametrize("path", ["/person/answer", "/incident/resolve", "/comms/inbound"])
def test_all_page_actions_allowed(client, path):
    assert preflight(client, DASH, path).headers.get("access-control-allow-origin") == DASH


def test_127_alias_allowed_by_default(client):
    assert preflight(client, "http://127.0.0.1:5173").headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


def test_other_origin_gets_no_cors_headers(client):
    r = preflight(client, EVIL)
    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


def test_simple_post_from_dashboard_carries_header(client):
    r = client.post("/person/answer", json={"text": "hi"}, headers={"Origin": DASH})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASH


def test_no_credentials_allowed(client):
    assert "access-control-allow-credentials" not in preflight(client, DASH).headers


def test_custom_origin_list_from_settings():
    lan = "http://192.168.1.20:5173"
    c = LaptopClient(create_app(settings(CORS_ORIGINS=f"{lan}/, {DASH}")))
    assert preflight(c, lan).headers.get("access-control-allow-origin") == lan
    assert preflight(c, DASH).headers.get("access-control-allow-origin") == DASH


def test_empty_origin_list_disables_cors():
    c = LaptopClient(create_app(settings(CORS_ORIGINS="")))
    assert "access-control-allow-origin" not in preflight(c, DASH).headers


@pytest.mark.parametrize("value", ["*", "http://localhost:5173,*", "null"])
def test_wildcard_and_null_are_refused(value):
    with pytest.raises(RuntimeError):
        parse_origins(value)


def test_websocket_still_works_with_cors_on(client):
    with client, client.websocket_connect("/ws/dashboard", headers={"Origin": DASH}) as ws:
        assert ws.receive_json()["type"] == "incident_update"
