"""Triage (IMPLEMENTATION.md section 11). Rule-based path first.

The rules only structure what the person said. They never diagnose or advise,
and they never guess: anything not said stays None.

TODO(phase 3, B): StepFun `parse_answers` path with a 3 s timeout that falls back
to `parse_rules` on timeout, bad JSON or validation failure.
"""

import re
from typing import Optional

from .models import TriageRecord

# The fixed question script (section 11.2). Never generate questions.
QUESTIONS: list[tuple[str, str]] = [
    ("ok", "Are you okay? Say 'I'm fine' or 'I need help.'"),
    ("head", "Did you hit your head?"),
    ("pain", "Where does it hurt?"),
    ("move", "Can you move your legs?"),
    ("bleeding", "Are you bleeding anywhere?"),
]
QUESTION_TEXT = dict(QUESTIONS)

BODY_PARTS = [
    "head", "neck", "shoulder", "arm", "elbow", "wrist", "hand", "chest",
    "back", "hip", "leg", "knee", "ankle", "foot", "side", "stomach",
]

_UNSURE = re.compile(r"\b(don't know|dont know|not sure|no idea|i think so|maybe)\b")
_NO = re.compile(r"\b(no|nope|nah|not|didn't|didnt|don't|dont|can't|cant|cannot|isn't|haven't|wasn't)\b")
_YES = re.compile(r"\b(yes|yeah|yep|yup|i did|i am|i can|a little|a bit)\b")
_FINE = re.compile(r"\b(i'm|im|i am)\s+(fine|okay|ok|alright|all right)\b|^(fine|okay|ok)$")
# Anything that means "not fine" even when "I'm fine" also appears.
_TROUBLE = re.compile(r"\b(help|hurt|hurts|pain|can't|cant|cannot|not|but|fell|bleed|bleeding|blood|stuck|dizzy)\b")
_CANT_MOVE = re.compile(r"\b(can't|cant|cannot|unable to|not able to)\s+(move|get up|stand)\b")
_HIT_HEAD = re.compile(r"\b(hit|banged|bumped|smacked)\s+my\s+head\b")
_BLEEDING = re.compile(r"\b(bleeding|blood)\b")
_NOT_BLEEDING = re.compile(r"\b(not|no|isn't|wasn't)\b[^.]*\b(bleeding|blood)\b")
_PART = re.compile(r"\b(?:(left|right)\s+)?(" + "|".join(BODY_PARTS) + r")s?\b")


def _norm(text: str) -> str:
    return text.replace("’", "'").strip().lower()


def is_clear_fine(text: str) -> bool:
    """Only an unambiguous 'I'm fine' cancels an incident. Silence or anything unclear escalates."""
    t = _norm(text)
    return bool(_FINE.search(t)) and not _TROUBLE.search(t)


def yes_no(text: str) -> Optional[bool]:
    t = _norm(text)
    if not t or _UNSURE.search(t):
        return None
    if _NO.search(t):
        return False
    if _YES.search(t):
        return True
    return None


def find_body_part(text: str) -> Optional[str]:
    m = _PART.search(_norm(text))
    if not m:
        return None
    side, part = m.groups()
    return f"{side} {part}" if side else part


def parse_rules(answers: list[tuple[str, str]], profile: dict) -> TriageRecord:
    """answers: (question_key, what the person said), in order."""
    by_key = {k: a for k, a in answers}
    said = [a for _, a in answers if a and a.strip()]
    everything = " . ".join(_norm(a) for a in said)

    head = yes_no(by_key["head"]) if "head" in by_key else None
    if head is None and _HIT_HEAD.search(everything):
        head = True

    move = yes_no(by_key["move"]) if "move" in by_key else None
    if move is None and _CANT_MOVE.search(everything):
        move = False

    bleeding = yes_no(by_key["bleeding"]) if "bleeding" in by_key else None
    if bleeding is None and _BLEEDING.search(everything):
        bleeding = not _NOT_BLEEDING.search(everything)

    pain = None
    if "pain" in by_key:
        pain = find_body_part(by_key["pain"])
    if pain is None:
        for a in said:
            t = _norm(a)
            if re.search(r"\b(hurt|hurts|pain|sore|can't move|cant move)\b", t):
                pain = find_body_part(t)
                if pain:
                    break

    rec = TriageRecord(
        conscious=True if said else None,  # silence is unknown, never "unconscious"
        head_strike=head,
        pain_location=pain,
        can_move=move,
        bleeding=bleeding,
        on_blood_thinner=profile.get("on_blood_thinner"),
    )
    asked = {k for k, _ in answers}
    remaining = [q for k, q in QUESTIONS if k not in asked]
    rec.next_question = remaining[0] if remaining else None
    rec.done = not remaining
    rec.summary = summarize(rec)
    return rec


def summarize(rec: TriageRecord, include_meds: bool = True) -> str:
    """Templated, one line, only what was said."""
    parts = []
    if rec.conscious:
        parts.append("Conscious.")
    else:
        parts.append("No answer yet.")
    if rec.pain_location:
        parts.append(f"{rec.pain_location.capitalize()} pain.")
    if rec.can_move is False:
        parts.append("Cannot move.")
    elif rec.can_move is True:
        parts.append("Can move.")
    if rec.head_strike is True:
        parts.append("Hit head.")
    elif rec.head_strike is False:
        parts.append("No head strike.")
    if rec.bleeding is True:
        parts.append("Bleeding.")
    elif rec.bleeding is False:
        parts.append("No bleeding.")
    if include_meds and rec.on_blood_thinner:
        parts.append("On a blood thinner.")
    return " ".join(parts)
