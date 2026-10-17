"""Pi-side checks (IMPLEMENTATION.md section 9.3). Stdlib only: this runs on QNX.

The Pi is the final gatekeeper for the door. Never add a bypass here.
backend/app/security.py signs with the same formats.
"""

import hashlib
import hmac
import logging
import time

log = logging.getLogger("firsthour.edge.security")

MAX_AGE_MS = 10_000
_FIELDS = ("action", "incident_id", "ts", "nonce", "sig")


def _hmac_hex(secret, msg):
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_event(secret, ts, raw_body):
    """X-Signature for POST /edge/event: hex(HMAC_SHA256(secret, ts + "." + raw_body))."""
    return _hmac_hex(secret, f"{ts}.".encode() + raw_body)


class CommandVerifier:
    """Checks signature, freshness, nonce and (for unlock) the local incident."""

    def __init__(self, secret, max_age_ms=MAX_AGE_MS, clock=time.time):
        self._secret = secret
        self._max_age_ms = max_age_ms
        self._clock = clock
        self._seen = {}  # nonce -> ts; pruned so it can't grow forever on a long soak test

    def check(self, cmd, local_incident_open):
        ok, reason = self._check(cmd, local_incident_open)
        log.info("command action=%s incident_id=%s accepted=%s reason=%s",
                 cmd.get("action"), cmd.get("incident_id"), ok, reason)
        return ok, reason

    def _check(self, cmd, local_incident_open):
        if not all(k in cmd for k in _FIELDS) or not isinstance(cmd["ts"], int):
            return False, "malformed"
        msg = f'{cmd["action"]}|{cmd["incident_id"]}|{cmd["ts"]}|{cmd["nonce"]}'.encode()
        if not hmac.compare_digest(_hmac_hex(self._secret, msg), str(cmd["sig"])):
            return False, "bad signature"
        now_ms = int(self._clock() * 1000)
        if abs(now_ms - cmd["ts"]) > self._max_age_ms:
            return False, "stale"
        self._prune(now_ms)
        if cmd["nonce"] in self._seen:
            return False, "replay"
        if cmd["action"] == "unlock" and not local_incident_open:
            # Don't record the nonce: the same command may be retried once the incident opens.
            return False, "no local incident"
        self._seen[cmd["nonce"]] = cmd["ts"]
        return True, "ok"

    def _prune(self, now_ms):
        # Anything older than 2x the window is already rejected as stale, so forget it.
        cutoff = now_ms - 2 * self._max_age_ms
        for nonce in [n for n, ts in self._seen.items() if ts < cutoff]:
            del self._seen[nonce]


def _norm_uid(uid):
    return uid.replace(":", "").replace(" ", "").upper()


def nfc_unlock_allowed(uid, allowed_uids, local_incident_open):
    """Offline unlock: an enrolled tag works only while a local incident is open."""
    if isinstance(allowed_uids, str):
        allowed_uids = allowed_uids.split(",")
    allowed = {_norm_uid(u) for u in allowed_uids if u and u.strip()}
    if not local_incident_open:
        ok, reason = False, "no local incident"
    elif not uid or _norm_uid(uid) not in allowed:
        ok, reason = False, "unknown tag"
    else:
        ok, reason = True, "ok"
    log.info("nfc_tap uid=%s accepted=%s reason=%s", _norm_uid(uid or ""), ok, reason)
    return ok, reason
