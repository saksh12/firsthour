"""Sentry telemetry. A capturing transport records exactly what would be sent; no network."""

import json
import logging
import re

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport

from app.main import create_app
from app.telemetry import Telemetry
from conftest import CAREGIVER, STRANGER, LaptopClient, signed_poll
from test_api import TS, post_event, settings

FAKE_DSN = "https://public@o0.ingest.example.invalid/1"


class Capture(Transport):
    def __init__(self):
        super().__init__()
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)

    def items(self, type_):
        out = []
        for env in self.envelopes:
            for item in env.items:
                if item.type == type_:
                    out.append(item.payload.json)
        return out

    def raw(self) -> str:
        return "\n".join(json.dumps(i.payload.json) for e in self.envelopes for i in e.items
                         if i.payload.json is not None)


@pytest.fixture
def cap():
    c = Capture()
    yield c
    sentry_sdk.get_client().close()
    sentry_sdk.init()  # back to a disabled client for the other tests


def app_with(cap, **kw):
    return LaptopClient(create_app(settings(SENTRY_DSN=FAKE_DSN, SENTRY_TRANSPORT=cap, **kw)))


def flush():
    sentry_sdk.flush(timeout=2)


def code_of(c):
    text = c.get("/comms/stub/outbox").json()["messages"][-1]["text"]
    return re.search(r"UNLOCK (\d{4})", text).group(1)


def test_no_dsn_is_a_noop():
    t = Telemetry.init("")
    assert not t.enabled
    t.incident_opened("i", "d")
    with t.span("i", "x"):
        pass
    t.on_event("incident_update", {"state": "CHECKING", "prev": "IDLE", "incident_id": "i"})
    t.incident_closed("i", "RESOLVED")


def test_client_options_are_privacy_safe(cap):
    app_with(cap)
    opts = sentry_sdk.get_client().options
    assert opts["send_default_pii"] is False
    assert opts["max_request_body_size"] == "never"
    assert opts["server_name"] == "firsthour-backend"
    assert opts["enable_logs"] is True


def test_full_incident_is_one_trace_with_every_stage(cap, monkeypatch):
    monkeypatch.setattr("app.unlock.secrets.randbelow", lambda n: 4821)
    c = app_with(cap)
    post_event(c)
    c.post("/person/answer", json={"text": "Help, I slipped and can't move my leg"})
    code = code_of(c)
    c.post("/comms/inbound", json={"sender": STRANGER, "text": f"UNLOCK {code}"})
    c.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code}"})
    signed_poll(c)
    c.post("/incident/resolve")
    flush()

    txs = [t for t in cap.items("transaction") if t["transaction"] == "fall_incident"]
    assert len(txs) == 1
    tx = txs[0]
    assert tx["tags"]["incident_id"] == f"bath-01-{TS}"
    assert tx["tags"]["final_state"] == "RESOLVED"
    ops = [s["op"] for s in tx["spans"]]
    for stage in ["sensor_event", "voice_check", "stepfun_triage", "photon_send", "command_to_pi"]:
        assert stage in ops, (stage, ops)
    decisions = [s for s in tx["spans"] if s["op"] == "unlock_decision"]
    assert [d["data"]["reason"] for d in decisions] == ["not enrolled", "granted"]
    voice = next(s for s in tx["spans"] if s["op"] == "voice_check")
    assert voice["data"]["outcome"] == "person needs help"
    sensor = next(s for s in tx["spans"] if s["op"] == "sensor_event")
    assert sensor["data"]["sound_peak"] == 812 and "edge_to_backend_ms" in sensor["data"]

    # Nothing sent to Sentry (traces, request transactions, logs) carries the code or a phone number.
    everything = cap.raw()
    # As a token: float timestamps and hex ids contain "4821" by chance (seen in CI: 1792282806.4548218).
    leak = re.search(r"(?<![\w.])4821(?![\w.])", everything)
    assert not leak, everything[max(0, leak.start() - 120):leak.end() + 40]
    assert CAREGIVER not in everything and STRANGER not in everything
    assert "+***0101" in everything                # still readable as "the caregiver"


def test_cancel_path_closes_the_trace(cap):
    c = app_with(cap)
    post_event(c)
    c.post("/person/answer", json={"text": "I'm fine"})
    flush()
    tx = next(t for t in cap.items("transaction") if t["transaction"] == "fall_incident")
    assert tx["tags"]["final_state"] == "CANCELLED"
    voice = next(s for s in tx["spans"] if s["op"] == "voice_check")
    assert voice["data"]["outcome"] == "person said fine"


def test_unpicked_command_span_is_closed_with_the_incident(cap):
    c = app_with(cap)
    post_event(c)
    c.post("/person/answer", json={"text": "help"})
    c.post("/comms/inbound", json={"sender": CAREGIVER, "text": f"UNLOCK {code_of(c)}"})
    c.post("/incident/resolve")  # before the Pi polled
    flush()
    tx = next(t for t in cap.items("transaction") if t["transaction"] == "fall_incident")
    assert any(s["op"] == "command_to_pi" for s in tx["spans"])


def test_polling_and_health_are_not_traced(cap):
    c = app_with(cap)
    for _ in range(3):
        c.get("/health")
        signed_poll(c)
    post_event(c, "heartbeat")
    flush()
    names = [t["transaction"] for t in cap.items("transaction")]
    assert not any(n in ("/health", "/edge/commands", "/edge/event") for n in names), names


def test_state_changes_and_unlock_attempts_go_out_as_logs(cap):
    c = app_with(cap)
    post_event(c)
    c.post("/person/answer", json={"text": "help"})
    c.post("/comms/inbound", json={"sender": STRANGER, "text": "UNLOCK 0000"})
    flush()
    bodies = [i["body"] for batch in cap.items("log") for i in batch["items"]]
    assert any("state CHECKING -> ALERTING" in b for b in bodies)
    assert any("unlock_attempt allowed=False reason=not enrolled sender=+***9999" in b for b in bodies)


def test_unlock_code_is_redacted_from_any_log(cap):
    app_with(cap)
    logging.getLogger("firsthour.test").warning("caregiver texted UNLOCK 1234 by mistake")
    flush()
    bodies = [i["body"] for batch in cap.items("log") for i in batch["items"]]
    hit = [b for b in bodies if "caregiver texted" in b]
    assert hit and "1234" not in hit[0] and "UNLOCK ****" in hit[0]


def test_health_supports_head_and_is_not_cached():
    c = LaptopClient(create_app(settings()))
    r = c.head("/health")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"


def test_health_is_503_when_pi_silent_and_edge_live():
    c = LaptopClient(create_app(settings(STUB_EDGE=False)))
    assert c.head("/health").status_code == 503
    post_event(c, "heartbeat")
    assert c.get("/health").status_code == 200


def test_tests_never_read_the_real_sentry_dsn_from_env():
    """The module-level app in app.main loads the root .env; a real DSN there used to turn
    every log.error a test provoked into a Sentry issue email."""
    from app.main import load_settings

    assert load_settings()["SENTRY_DSN"] == ""
