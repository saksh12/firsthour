"""Caregiver comms: alert formatting + inbound parsing.

Role C (Comms and voice). This module is the logic layer behind the
``/comms/inbound`` route and the outbound caregiver alert. It talks to the
Photon ``comms-gateway`` when ``STUB_PHOTON=0``; otherwise it stays fully
offline so the stub slice runs with no network and no keys.

Contracts (IMPLEMENTATION.md section 6.6):
  * Outbound alert text template.
  * Inbound grammar: ``UNLOCK <4 digits>`` unlocks if the unlock rules pass;
    anything else is a message to read aloud to the person.

Nothing here decides whether an unlock is *allowed* -- that is B's
``unlock.py`` / ``security.py``. This module only recognises the sender and
parses the text. Unlock authorisation stays with the backend.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal, Optional

# --------------------------------------------------------------------------
# Config (read straight from env; see .env.example)
# --------------------------------------------------------------------------


def _stub_photon() -> bool:
    return os.environ.get("STUB_PHOTON", "1") != "0"


def caregiver_numbers() -> list[str]:
    """Allowlist of caregiver phone numbers, comma separated in env."""
    raw = os.environ.get("CAREGIVER_NUMBERS", "")
    return [n.strip() for n in raw.split(",") if n.strip()]


def _norm_number(num: str) -> str:
    """Keep digits and a leading '+', drop spaces/dashes/parens so that
    '+1 (310) 555-0101' and '+13105550101' compare equal."""
    num = num.strip()
    plus = num.startswith("+")
    digits = re.sub(r"\D", "", num)
    return ("+" if plus else "") + digits


def is_caregiver(sender: str, allowlist: Optional[list[str]] = None) -> bool:
    """True if ``sender`` is on the caregiver allowlist (format-insensitive)."""
    if allowlist is None:
        allowlist = caregiver_numbers()
    want = _norm_number(sender)
    return any(_norm_number(a) == want for a in allowlist)


# --------------------------------------------------------------------------
# Outbound alert (section 6.6)
# --------------------------------------------------------------------------


@dataclass
class AlertContext:
    """Everything the outbound alert text needs. Built by the backend from
    the incident + triage record; kept local so this module does not depend
    on B's models.py."""

    name: str
    location: str
    down_seconds: int
    triage_summary: str
    on_blood_thinner: bool
    unlock_code: str


def format_down_time(seconds: int) -> str:
    """Seconds -> ``MM:SS`` (minutes can exceed 99 and just keep counting)."""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def format_alert(ctx: AlertContext) -> str:
    """Render the caregiver alert exactly in the section 6.6 shape."""
    lines = [
        f"FALL ALERT: {ctx.name} ({ctx.location}). Down {format_down_time(ctx.down_seconds)}.",
        ctx.triage_summary.strip(),
    ]
    if ctx.on_blood_thinner:
        lines.append("NOTE: on a blood thinner.")
    lines.append(f"Reply UNLOCK {ctx.unlock_code} to open the front door.")
    lines.append("Or reply with a message and we'll read it to her.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Inbound parsing (section 6.6)
# --------------------------------------------------------------------------

# "UNLOCK 4821": case-insensitive, any run of whitespace, exactly 4 digits.
_UNLOCK_RE = re.compile(r"^\s*unlock\s+(\d{4})\s*$", re.IGNORECASE)

# One short calm sentence, max 20 words, when we read a caregiver note aloud.
_MAX_SPOKEN_WORDS = 20


@dataclass
class InboundMessage:
    kind: Literal["unlock", "message"]
    code: Optional[str] = None  # set when kind == "unlock"
    text: Optional[str] = None  # set when kind == "message"


def parse_inbound(body: str) -> InboundMessage:
    """Classify a caregiver reply.

    ``UNLOCK 4821`` (exactly 4 digits) -> an unlock request carrying the code.
    Anything else -> a message to read aloud to the person.
    """
    body = (body or "").strip()
    m = _UNLOCK_RE.match(body)
    if m:
        return InboundMessage(kind="unlock", code=m.group(1))
    return InboundMessage(kind="message", text=body)


def spoken_form(text: str) -> str:
    """Trim a caregiver note to one short line for the voice to read
    (<= 20 words). The calm rewrite itself is StepFun's job in triage.py;
    this is only the hard length guard so a long text never plays in full."""
    words = (text or "").split()
    if len(words) <= _MAX_SPOKEN_WORDS:
        return " ".join(words)
    return " ".join(words[:_MAX_SPOKEN_WORDS]) + "..."


# --------------------------------------------------------------------------
# Sending (stub by default; real path posts to comms-gateway)
# --------------------------------------------------------------------------


@dataclass
class SentAlert:
    to: str
    body: str
    stubbed: bool
    # populated with the gateway's message id on the real path
    message_id: Optional[str] = None


# In stub mode, every outbound alert is recorded here so the caregiver
# simulator / tests / dashboard can see what *would* have gone out.
_outbox: list[SentAlert] = field(default_factory=list)  # type: ignore[assignment]
_outbox = []


def outbox() -> list[SentAlert]:
    return list(_outbox)


def clear_outbox() -> None:
    _outbox.clear()


async def send_alert(to: str, body: str) -> SentAlert:
    """Send one SMS/iMessage to a caregiver.

    STUB_PHOTON=1 (default): record it in the outbox and return, no network.
    STUB_PHOTON=0: POST to the Photon comms-gateway, which owns the carrier
    link. We never call the Photon SDK from Python directly -- the gateway
    (Node + TS) is the single place that talks to Photon.
    """
    if _stub_photon():
        sent = SentAlert(to=to, body=body, stubbed=True)
        _outbox.append(sent)
        return sent

    import httpx  # lazy: only needed on the real path

    gateway = os.environ.get("COMMS_GATEWAY_URL", "http://localhost:8787")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{gateway}/send", json={"to": to, "body": body})
        resp.raise_for_status()
        data = resp.json()
    sent = SentAlert(to=to, body=body, stubbed=False, message_id=data.get("id"))
    _outbox.append(sent)
    return sent
