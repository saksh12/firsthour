"""Sentry (IMPLEMENTATION.md section 12). Owner: D.

Products used beyond error monitoring:
- Tracing: one trace per incident ("fall_incident"), with a span per stage:
  sensor_event, voice_check, stepfun_triage, photon_send, unlock_decision, command_to_pi.
  (speech_to_text gets a span once STT is wired; nothing to time yet.)
- Logs: every `logging` record at INFO+ (state changes, unlock attempts with reasons,
  StepFun latency) is sent as a Sentry log.
- Uptime Monitoring: point a monitor at the tunneled GET/HEAD /health, which is 503
  when the Pi hasn't sent a heartbeat in 30 s (STUB_EDGE=0).

No DSN = every method is a cheap no-op. Privacy: send_default_pii=False, request
bodies are never attached (a caregiver's reply can contain the unlock code), a
neutral server_name, and every log/event is scrubbed of "UNLOCK <4 digits>" and
phone numbers (last 4 digits kept so the audit trail is still readable).

SDK calls checked against sentry-sdk 2.71.0 (pinned in requirements.txt).
"""

import logging
import re
import threading
from contextlib import contextmanager
from typing import Any, Callable, Optional

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

log = logging.getLogger("firsthour.telemetry")

# Polled every second by the Pi, or hit by the uptime monitor: tracing them is noise.
UNTRACED_PATHS = {"/health", "/edge/commands", "/edge/event"}
_CODE = re.compile(r"\bUNLOCK\s+\d{4}\b", re.IGNORECASE)
_PHONE = re.compile(r"\+\d{6,14}(\d{4})\b")


def _redact(text: str) -> str:
    return _PHONE.sub(r"+***\1", _CODE.sub("UNLOCK ****", text))


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, dict) and isinstance(value.get("value"), str):
        return {**value, "value": _redact(value["value"])}
    return value


def _before_send_log(record: dict, _hint: dict) -> dict:
    if isinstance(record.get("body"), str):
        record["body"] = _redact(record["body"])
    # The raw format arguments travel too (sentry.message.parameter.N), so scrub every attribute.
    attrs = record.get("attributes")
    if isinstance(attrs, dict):
        record["attributes"] = {k: _scrub(v) for k, v in attrs.items()}
    return record


def _before_send(event: dict, _hint: dict) -> dict:
    msg = event.get("logentry") or {}
    for key in ("message", "formatted"):
        if isinstance(msg.get(key), str):
            msg[key] = _redact(msg[key])
    return event


def _traces_sampler(ctx: dict) -> float:
    scope = ctx.get("asgi_scope") or {}
    if scope.get("path") in UNTRACED_PATHS:
        return 0.0
    return 1.0  # hackathon volume: keep every incident and request


class Telemetry:
    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._lock = threading.Lock()
        self._tx: dict[str, Any] = {}        # incident_id -> Transaction
        self._open: dict[tuple, Any] = {}    # (incident_id, key) -> Span

    @classmethod
    def init(cls, dsn: str, environment: str = "hackathon", transport: Any = None) -> "Telemetry":
        if not dsn:
            log.info("sentry disabled (no SENTRY_DSN)")
            return cls(False)
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            send_default_pii=False,
            max_request_body_size="never",
            server_name="firsthour-backend",
            traces_sampler=_traces_sampler,
            enable_logs=True,
            integrations=[LoggingIntegration(level=logging.INFO, event_level=logging.ERROR,
                                             sentry_logs_level=logging.INFO)],
            before_send=_before_send,
            before_send_log=_before_send_log,
            **({"transport": transport} if transport is not None else {}),
        )
        # The Logs product is the audit trail: make sure INFO records exist even if the
        # host's logging config (or pytest) leaves the root logger at WARNING.
        logging.getLogger("firsthour").setLevel(logging.INFO)
        log.info("sentry enabled environment=%s", environment)
        return cls(True)

    # --- incident trace ---------------------------------------------------------

    def incident_opened(self, incident_id: str, device_id: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            if incident_id in self._tx:
                return
            tx = sentry_sdk.start_transaction(op="incident", name="fall_incident")
            tx.set_tag("incident_id", incident_id)
            tx.set_tag("device_id", device_id)
            self._tx[incident_id] = tx

    def incident_closed(self, incident_id: str, final_state: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            for key in [k for k in self._open if k[0] == incident_id]:
                self._open.pop(key).finish()
            tx = self._tx.pop(incident_id, None)
        if tx is not None:
            tx.set_tag("final_state", final_state)
            tx.set_data("final_state", final_state)
            tx.finish()

    def start(self, incident_id: Optional[str], key: str, op: str, **data: Any) -> None:
        """Open a span that a later call to `finish` closes (e.g. voice_check)."""
        if not self.enabled or not incident_id:
            return
        with self._lock:
            tx = self._tx.get(incident_id)
            if tx is None or (incident_id, key) in self._open:
                return
            span = tx.start_child(op=op, name=op)
            for k, v in data.items():
                span.set_data(k, v)
            self._open[(incident_id, key)] = span

    def finish(self, incident_id: Optional[str], key: str, **data: Any) -> None:
        if not self.enabled or not incident_id:
            return
        with self._lock:
            span = self._open.pop((incident_id, key), None)
        if span is not None:
            for k, v in data.items():
                span.set_data(k, v)
            span.finish()

    @contextmanager
    def span(self, incident_id: Optional[str], op: str, **data: Any):
        """Time a block as a child span of the incident's trace."""
        if not self.enabled or not incident_id:
            yield None
            return
        with self._lock:
            tx = self._tx.get(incident_id)
        if tx is None:
            yield None
            return
        span = tx.start_child(op=op, name=op)
        for k, v in data.items():
            span.set_data(k, v)
        try:
            yield span
        except Exception:
            span.set_status("internal_error")
            raise
        finally:
            span.finish()

    def wrap(self, op: str, fn: Callable, incident_id: Callable[[], Optional[str]]) -> Callable:
        """Wrap a stage function so each call becomes a span on the current incident."""
        def wrapped(*args, **kwargs):
            with self.span(incident_id(), op):
                return fn(*args, **kwargs)
        return wrapped

    # --- the dashboard event stream drives the rest ---------------------------------

    def on_event(self, type_: str, data: dict, device_id: str = "") -> None:
        if not self.enabled:
            return
        inc = data.get("incident_id")
        if type_ == "incident_update":
            state = data.get("state")
            if state == "CHECKING" and data.get("prev") in (None, "IDLE"):
                self.incident_opened(inc, device_id)
                self.start(inc, "voice_check", "voice_check")
            elif state in ("ALERTING", "CANCELLED"):
                self.finish(inc, "voice_check", outcome=data.get("reason"))
            if state in ("CANCELLED", "RESOLVED"):
                self.incident_closed(inc, state)
        elif type_ == "unlock_attempt":
            # No sender number in the span: it's personal data.
            with self.span(inc, "unlock_decision", allowed=data.get("allowed"), reason=data.get("reason")):
                pass

    @staticmethod
    def flush(timeout: float = 2.0) -> None:
        client = sentry_sdk.get_client()
        if client.is_active():
            client.flush(timeout=timeout)

