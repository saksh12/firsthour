"""Caregiver coordination (issue #23): who is coming, how soon, and Q&A in the thread.

Every state change here is deterministic. A model may write the wording of an
answer, never decide who claimed the incident, when to call, or anything about
the door. The unlock code never enters this module.
"""

import re
from dataclasses import dataclass
from typing import Optional

from .triage import summarize

# "On my way" claims the incident. A hedge ("I might come", "maybe") never does.
_CLAIM = re.compile(
    r"\b(on my way|omw|i'?m coming|i am coming|coming now|coming over|heading (over|there|home)"
    r"|leaving now|be there|i'?ll come|i will come|i'?ll go|i got (it|this)|i'?m going)\b")
_ARRIVED = re.compile(r"\b(i'?m here|i am here|i'?ve arrived|arrived|at the door|i'?m outside|i am outside)\b")
_ETA = re.compile(r"\b(\d{1,3})\s*(m|min|mins|minute|minutes)\b")
_HEDGE = re.compile(r"\b(might|maybe|may|perhaps|possibly|not sure|try to|if i can|could)\b")
_NEGATIVE = re.compile(r"\b(can'?t|cannot|won'?t|not coming|unable|no way)\b")
_QUESTION_START = re.compile(
    r"^(is|are|was|were|did|does|do|has|have|can|could|will|would|what|when|where|who|why|how)\b")

STATUSES = ("acknowledged", "accepted", "arriving", "arrived")


@dataclass(frozen=True)
class Commitment:
    status: str  # accepted | arriving | arrived
    eta_min: Optional[int] = None


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("\u2019", "'").split())


def parse_commitment(text: str) -> Optional[Commitment]:
    """A clear claim, ETA or arrival, else None. Hedged or negative replies never count."""
    t = _norm(text)
    if not t or _HEDGE.search(t) or _NEGATIVE.search(t):
        return None
    if _ARRIVED.search(t):
        return Commitment("arrived")
    eta = _ETA.search(t)
    if eta and (_CLAIM.search(t) or re.search(r"\b(away|out|there in|eta)\b", t)):
        return Commitment("arriving", min(int(eta.group(1)), 180))
    if _CLAIM.search(t):
        return Commitment("accepted")
    return None


def is_question(text: str) -> bool:
    """Deterministic fallback for question vs message: a '?' or a question word first."""
    t = _norm(text)
    return "?" in t or bool(_QUESTION_START.match(t))


def parse_names(value: str) -> dict[str, str]:
    """CAREGIVER_NAMES: "+13105550101:Sarah,+13105550102:Tom"."""
    out = {}
    for item in value.split(","):
        number, sep, name = item.partition(":")
        if sep and number.strip() and name.strip():
            out[number.strip()] = name.strip()
    return out


def parse_order(value: str) -> list[str]:
    """CAREGIVER_NUMBERS in written order: the first is called first, then the backups."""
    seen, out = set(), []
    for n in (i.strip() for i in value.split(",")):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def spoken_line(name: Optional[str], c: Commitment) -> str:
    """Read to the person on the floor. Fixed templates, no model."""
    who = name or "Your caregiver"
    if c.status == "arrived":
        return f"{who} is at the door."
    if c.status == "arriving" and c.eta_min is not None:
        unit = "minute" if c.eta_min == 1 else "minutes"
        return f"{who} is about {c.eta_min} {unit} away."
    return f"{who} is on the way."


def claimer_reply(c: Commitment, via_call: bool = False) -> str:
    """Texted back to the caregiver who committed. Never contains the code."""
    if c.status == "arrived":
        return "Thanks. Reply UNLOCK with the code from the alert to open the door."
    said = f"You said {c.eta_min} minutes" if c.eta_min is not None else "You're on the way"
    if via_call:
        said += " on the call"
    return f"{said}. We told them. Reply UNLOCK with the code from the alert at the door."


def stand_by_text(name: Optional[str]) -> str:
    return f"{name or 'Another caregiver'} is on the way. Please stand by; we'll text if that changes."


def status_answer(facts: dict) -> str:
    """Templated answer when the model is off or fails: only facts, no advice."""
    parts = [f"{facts['patient']} has been down {facts['time_down']}.", facts["triage_summary"]]
    if facts.get("last_answer"):
        parts.append(f'Last thing {facts["pronoun"]} said: "{facts["last_answer"]}".')
    coming = [r for r in facts.get("responders", []) if r["status"] in ("accepted", "arriving", "arrived")]
    if coming:
        r = coming[0]
        parts.append(spoken_line(r["name"], Commitment(r["status"], r.get("eta_min"))))
    else:
        parts.append("Nobody has said they are on the way yet.")
    return " ".join(p for p in parts if p)


def incident_facts(*, profile: dict, state: str, down_s: float, triage, transcript: list[dict],
                   responders: list[dict], call_notes: list[str]) -> dict:
    """The only incident data Q&A and the voice agent ever see. No unlock code, no numbers."""
    s = max(0, int(down_s))
    return {
        "patient": profile["name"],
        "pronoun": "she" if profile.get("pronoun_object") == "her" else
                   "he" if profile.get("pronoun_object") == "him" else "they",
        "room": profile.get("room", ""),
        "state": state,
        "time_down": f"{s // 60} min {s % 60:02d} s",
        "triage_summary": summarize(triage, include_meds=False),
        "triage": {k: v for k, v in triage.model_dump().items() if k not in ("next_question", "done")},
        "on_blood_thinner": bool(profile.get("on_blood_thinner")),
        "transcript": transcript,
        "last_answer": transcript[-1]["a"] if transcript else None,
        "responders": [{k: r[k] for k in ("name", "status", "eta_min")} for r in responders],
        "call_notes": call_notes,
        "unlock_policy": "Voice can never unlock the door. The caregiver texts UNLOCK with the code from the alert.",
    }
