"""Door unlock rules (IMPLEMENTATION.md section 9). The important file.

Never add a bypass, debug unlock or hardcoded code here. Test through
tests/test_unlock.py instead.
"""

import hmac
import logging
import re
import secrets
import time
from typing import Callable, Iterable, Optional, Union

from .models import UnlockResult

log = logging.getLogger("firsthour.unlock")

MAX_TRIES = 3
_UNLOCK = re.compile(r"^UNLOCK(?:\s+(\S+))?(\s+.*)?$")


def is_unlock_attempt(text: str) -> bool:
    """Caregiver grammar: a reply whose first word is UNLOCK is an attempt; anything else is read aloud."""
    words = text.strip().split()
    return bool(words) and words[0].upper() == "UNLOCK"


def _parse_list(value: Union[str, Iterable[str]]) -> set[str]:
    items = value.split(",") if isinstance(value, str) else value
    return {i.strip() for i in items if i and i.strip()}


class UnlockGuard:
    """Holds the one open incident and decides every unlock attempt.

    `on_grant(incident_id)` is called only when every rule passes; the caller
    queues the signed command for the Pi, which re-checks on its side.
    """

    def __init__(
        self,
        allowed: Union[str, Iterable[str]],
        window_s: int = 600,
        clock: Callable[[], float] = time.time,
        on_grant: Optional[Callable[[str], None]] = None,
    ):
        self.allowed = _parse_list(allowed)
        self.window_s = window_s
        self._clock = clock
        self._on_grant = on_grant
        self._incident: Optional[dict] = None
        self.audit: list[UnlockResult] = []

    @property
    def active_incident_id(self) -> Optional[str]:
        inc = self._incident
        if inc and self._clock() <= inc["expires"]:
            return inc["id"]
        return None

    def open_incident(self, incident_id: str) -> str:
        """Start a fresh incident and return its one-time code (goes into the alert text)."""
        self._incident = {
            "id": incident_id,
            "code": f"{secrets.randbelow(10000):04d}",
            "expires": self._clock() + self.window_s,
            "tries": 0,
        }
        log.info("incident opened incident_id=%s", incident_id)
        return self._incident["code"]

    def close_incident(self) -> None:
        if self._incident:
            log.info("incident closed incident_id=%s", self._incident["id"])
        self._incident = None

    def try_unlock(self, sender: str, text: str) -> UnlockResult:
        sender = sender.strip()
        inc = self._incident
        if not inc or self._clock() > inc["expires"]:
            return self._decide(False, "no active incident", sender, None)
        # Allowlist before the try counter, so a stranger can't lock the caregiver out.
        if sender not in self.allowed:
            return self._decide(False, "not enrolled", sender, inc["id"])
        if inc["tries"] >= MAX_TRIES:
            return self._decide(False, "locked out", sender, inc["id"])

        m = _UNLOCK.match(text.strip().upper())
        code = m.group(1) if m and not m.group(2) else None
        if not code or not re.fullmatch(r"\d{4}", code) or not hmac.compare_digest(code, inc["code"]):
            inc["tries"] += 1
            return self._decide(False, "bad code", sender, inc["id"])

        result = self._decide(True, "granted", sender, inc["id"])
        if self._on_grant:
            self._on_grant(inc["id"])
        return result

    def _decide(self, allowed: bool, reason: str, sender: str, incident_id: Optional[str]) -> UnlockResult:
        result = UnlockResult(
            allowed=allowed, reason=reason, sender=sender,
            incident_id=incident_id, ts=int(self._clock() * 1000),
        )
        self.audit.append(result)
        # Never log the code or the raw text: the text may contain the code.
        log.info(
            "unlock_attempt allowed=%s reason=%s sender=%s incident_id=%s",
            allowed, reason, sender, incident_id,
        )
        return result
