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


# Commands that act on one incident. The Pi only obeys them for its own open incident,
# so a stale backend incident can never unlock the door or silence a newer fall.
INCIDENT_ACTIONS = ("unlock", "alarm_off")


class CommandVerifier:
    """Checks signature, freshness, nonce and, for incident actions, the local incident."""

    def __init__(self, secret, max_age_ms=MAX_AGE_MS, clock=time.time):
        self._secret = secret
        self._max_age_ms = max_age_ms
        self._clock = clock
        self._seen = {}  # nonce -> ts; pruned so it can't grow forever on a long soak test

    def check(self, cmd, local_incident_id):
        """local_incident_id: the Pi's open incident, or None."""
        ok, reason = self._check(cmd, local_incident_id)
        log.info("command action=%s incident_id=%s accepted=%s reason=%s",
                 cmd.get("action"), cmd.get("incident_id"), ok, reason)
        return ok, reason

    def _check(self, cmd, local_incident_id):
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
        if cmd["action"] in INCIDENT_ACTIONS:
            # Don't record the nonce: the same command may be retried once the incident opens.
            if local_incident_id is None:
                return False, "no local incident"
            if cmd["incident_id"] != local_incident_id:
                return False, "other incident"
        self._seen[cmd["nonce"]] = cmd["ts"]
        return True, "ok"

    def _prune(self, now_ms):
        # Anything older than 2x the window is already rejected as stale, so forget it.
        cutoff = now_ms - 2 * self._max_age_ms
        for nonce in [n for n, ts in self._seen.items() if ts < cutoff]:
            del self._seen[nonce]

