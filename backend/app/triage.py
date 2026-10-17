"""Triage (IMPLEMENTATION.md section 11). Rule-based path first.

The rules only structure what the person said. They never diagnose or advise,
and they never guess: anything not said stays None.

StepFun path (section 11.3/11.4): `StepFun.triage` asks step-5-preview to structure
the answers, with a hard 3 s deadline, and falls back to `parse_rules` on stub mode,
missing key, timeout, HTTP error, bad JSON or a record that fails validation.
`StepFun.rewrite` turns a caregiver reply into one calm sentence, falling back to the
original text. The question script, next_question/done and on_blood_thinner never
come from the model.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import TriageRecord

log = logging.getLogger("firsthour.triage")

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


# --- StepFun (step-5-preview) ----------------------------------------------------

TRIAGE_SYSTEM = """You turn short spoken answers from a person who may have fallen into a JSON record.
Rules:
- Output ONLY a JSON object with keys: conscious, head_strike, pain_location, can_move, bleeding, summary.
- conscious, head_strike, can_move, bleeding are true, false or null. pain_location is a short body part or null.
- Use null when the answers do not say. Never guess or invent.
- Do not give medical advice or diagnoses.
- summary: one plain sentence for a paramedic, using only what was said."""

REWRITE_SYSTEM = (
    "Rewrite this message as one calm, short sentence (max 20 words) to be read aloud "
    "to someone who has fallen. Do not add information. Output only the sentence."
)

MAX_REWRITE_WORDS = 20

# Transport: (url, headers, body_dict, timeout_s) -> parsed JSON response. Injectable for tests.
Transport = Callable[[str, dict, dict, float], dict]


def urllib_transport(url: str, headers: dict, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class ModelTriage(BaseModel):
    """What we accept from the model. Anything else (wrong types, missing summary) is a failure."""

    model_config = ConfigDict(extra="ignore", strict=True)
    conscious: Optional[bool] = None
    head_strike: Optional[bool] = None
    pain_location: Optional[str] = Field(default=None, max_length=40)
    can_move: Optional[bool] = None
    bleeding: Optional[bool] = None
    summary: str = Field(min_length=1, max_length=300)


class StepFunError(Exception):
    pass


def transcript_text(answers: list[tuple[str, str]]) -> str:
    return "\n".join(f"Q: {QUESTION_TEXT[k]}\nA: {a}" for k, a in answers)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t


class StepFun:
    def __init__(self, *, api_key: str, base_url: str, model: str = "step-5-preview",
                 stub: bool = True, timeout_s: float = 3.0, transport: Transport = urllib_transport):
        self.enabled = not stub and bool(api_key) and bool(base_url)
        if not stub and not self.enabled:
            log.warning("STUB_STEPFUN=0 but STEPFUN_API_KEY/STEPFUN_BASE_URL missing; using rules only")
        self.api_key, self.model = api_key, model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.timeout_s = timeout_s
        self._transport = transport
        # The deadline is enforced here, not by the socket: a slow-trickling server can't hold us.
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stepfun")

    def _chat(self, system: str, user: str, *, json_mode: bool, max_tokens: int) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            # TODO(verify): StepFun docs for a low-reasoning-effort parameter on step-5-preview.
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}  # TODO(verify) supported on our key
        headers = {"Authorization": f"Bearer {self.api_key}"}
        fut = self._pool.submit(self._transport, self.url, headers, body, self.timeout_s)
        try:
            data = fut.result(timeout=self.timeout_s)
        except FutureTimeout:
            raise StepFunError("timeout")
        except urllib.error.HTTPError as e:
            raise StepFunError(f"http {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise StepFunError(f"network: {e}")
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise StepFunError("unexpected response shape")

    def triage(self, answers: list[tuple[str, str]], profile: dict) -> TriageRecord:
        rules = parse_rules(answers, profile)
        if not self.enabled or not answers:
            return rules
        t0 = time.monotonic()
        try:
            content = self._chat(TRIAGE_SYSTEM, transcript_text(answers), json_mode=True, max_tokens=300)
            try:
                raw = json.loads(_strip_fences(content))
            except ValueError:
                raise StepFunError("bad json")
            if not isinstance(raw, dict):
                raise StepFunError("bad json")
            try:
                m = ModelTriage.model_validate(raw)
            except ValidationError as e:
                raise StepFunError(f"invalid record ({e.error_count()} errors)")
        except StepFunError as e:
            self._log("triage", t0, False, str(e))
            return rules
        self._log("triage", t0, True, None)
        return TriageRecord(
            conscious=m.conscious, head_strike=m.head_strike, pain_location=m.pain_location,
            can_move=m.can_move, bleeding=m.bleeding, summary=m.summary,
            # Never from the model:
            on_blood_thinner=profile.get("on_blood_thinner"),
            next_question=rules.next_question, done=rules.done,
        )

    def rewrite(self, text: str) -> str:
        if not self.enabled or not text.strip():
            return text
        t0 = time.monotonic()
        try:
            out = self._chat(REWRITE_SYSTEM, text, json_mode=False, max_tokens=60).strip().strip('"')
            if not out:
                raise StepFunError("empty")
            if len(out.split()) > MAX_REWRITE_WORDS:
                raise StepFunError("too long")
            # "Do not add information": a number the caregiver didn't write is a red flag.
            if set(re.findall(r"\d+", out)) - set(re.findall(r"\d+", text)):
                raise StepFunError("added numbers")
        except StepFunError as e:
            self._log("rewrite", t0, False, str(e))
            return text
        self._log("rewrite", t0, True, None)
        return out

    def _log(self, call: str, t0: float, ok: bool, fallback_reason: Optional[str]) -> None:
        ms = int((time.monotonic() - t0) * 1000)
        if ok:
            log.info("stepfun call=%s model=%s ms=%d ok=True", call, self.model, ms)
        else:
            log.warning("stepfun call=%s model=%s ms=%d ok=False fallback=rules reason=%s",
                        call, self.model, ms, fallback_reason)
