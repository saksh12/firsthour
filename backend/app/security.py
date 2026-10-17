"""Signing for the edge contracts (IMPLEMENTATION.md sections 6.2 and 6.3).

edge/security.py is the Pi-side mirror of this file. Keep the message formats identical.
"""

import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from .models import Command


def _hmac_hex(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_command(secret: str, action: str, incident_id: str, ts: int, nonce: str) -> str:
    return _hmac_hex(secret, f"{action}|{incident_id}|{ts}|{nonce}".encode())


def make_command(secret: str, action: str, incident_id: str, now_ms: Optional[int] = None) -> Command:
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    nonce = secrets.token_hex(8)
    return Command(
        id=f"cmd-{secrets.token_hex(3)}",
        action=action,
        incident_id=incident_id,
        ts=ts,
        nonce=nonce,
        sig=sign_command(secret, action, incident_id, ts, nonce),
    )


def sign_event(secret: str, ts: int, raw_body: bytes) -> str:
    return _hmac_hex(secret, f"{ts}.".encode() + raw_body)


def verify_event(secret: str, raw_body: bytes, signature: Optional[str]) -> tuple[bool, str]:
    """Check X-Signature on POST /edge/event.

    No freshness check here on purpose: the Pi replays its offline log after a
    reconnect, so old events are expected. Commands to the Pi are what must be fresh.
    """
    if not signature:
        return False, "missing signature"
    try:
        ts = json.loads(raw_body)["ts"]
    except (ValueError, KeyError, TypeError):
        return False, "malformed"
    if not hmac.compare_digest(sign_event(secret, ts, raw_body), signature):
        return False, "bad signature"
    return True, "ok"


INBOUND_MAX_AGE_MS = 30_000


def verify_inbound(secret: str, raw_body: bytes, signature: Optional[str],
                   ts_header: Optional[str]) -> tuple[bool, str]:
    """Check X-Signature + X-Timestamp on POST /comms/inbound (section 6.6).

    Same scheme as /edge/event: hex(HMAC_SHA256(secret, ts + "." + raw_body)),
    but the timestamp arrives in the X-Timestamp header and must be fresh:
    unlike the Pi's replayed log, a caregiver reply is live, so a stale one
    is a replay or a broken client.
    """
    if not signature:
        return False, "missing signature"
    try:
        ts = int(ts_header or "")
    except ValueError:
        return False, "missing timestamp" if not ts_header else "malformed timestamp"
    if not hmac.compare_digest(sign_event(secret, ts, raw_body), signature):
        return False, "bad signature"
    if abs(time.time() * 1000 - ts) > INBOUND_MAX_AGE_MS:
        return False, "stale"
    return True, "ok"
