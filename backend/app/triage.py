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
    "head", "jawline", "jaw", "face", "neck", "collarbone", "shoulder", "arm", "elbow", "wrist",
    "hand", "finger", "chest", "rib", "back", "spine", "tailbone", "hip", "pelvis", "side",
    "stomach", "leg", "thigh", "knee", "shin", "ankle", "foot", "toe",
]

_UNSURE = re.compile(
    r"\b(don't know|dont know|not sure|no idea|i think so|maybe"
    r"|(can't|cant|cannot|can not|don't|dont|do not) (remember|tell|say|recall))\b"
)
_YES_WORD = re.compile(r"\b(yes|yeah|yep|yup)\b")
_NO_WORD = re.compile(r"\b(no|nope|nah)\b")
# A short direct answer: "I did", "I can't", "I'm not". Only counts at the start.
# "I'm okay" to "Did you hit your head?" is not a yes, so "I am" counts only on its own.
_LEAD_YES = re.compile(r"^(i did|i do|i can|a little|a bit)\b|^(i am|i'm)\W*$")  # checked after _LEAD_NO
_LEAD_NO = re.compile(r"^(i didn't|i didnt|i did not|i don't|i dont|i do not|i can't|i cant|i cannot"
                      r"|i can not|i'm not|im not|i am not|not really|never)\b")
_NEG = re.compile(r"\b(no|not|never|didn't|didnt|don't|dont|can't|cant|cannot|isn't|wasn't|haven't)\b")
# A negation only answers a question if the answer is about that question's subject:
# "can't move my leg" says nothing about the head.
_TOPIC = {
    "head": re.compile(r"\b(head|hit|bump|bumped|bang|banged)\b"),
    "move": re.compile(r"\b(move|moving|get up|stand|legs?)\b"),
    "bleeding": re.compile(r"\b(bleed|bleeding|blood)\b"),
}
_FINE = re.compile(r"\b(i'm|im|i am)\s+(fine|okay|ok|alright|all right)\b|^(fine|okay|ok)$")
# Anything that means "not fine" even when "I'm fine" also appears.
_TROUBLE = re.compile(r"\b(help|hurt|hurts|pain|can't|cant|cannot|not|but|fell|bleed|bleeding|blood|stuck|dizzy)\b")
_CANT_MOVE = re.compile(r"\b(can't|cant|cannot|unable to|not able to)\s+(move|get up|stand)\b")
_HIT_HEAD = re.compile(r"\b(hit|banged|bumped|smacked)\s+my\s+head\b")
_NOT_HIT_HEAD = re.compile(r"\b(not|didn't|didnt|did not|never|no)\b[^.]*\b(hit|banged|bumped|smacked)\s+my\s+head\b")
_BLEEDING = re.compile(r"\b(bleeding|blood)\b(?!\s+thinner)")
_NOT_BLEEDING = re.compile(r"\b(not|no|isn't|wasn't)\b[^.]*\b(bleeding|blood)\b")
_PART = re.compile(r"\b(?:(left|right)\s+)?(" + "|".join(BODY_PARTS) + r")s?\b")


def _norm(text: str) -> str:
    return text.replace("’", "'").strip().lower()


def is_clear_fine(text: str) -> bool:
    """Only an unambiguous 'I'm fine' cancels an incident. Silence or anything unclear escalates."""
    t = _norm(text)
    return bool(_FINE.search(t)) and not _TROUBLE.search(t)


def yes_no(text: str, topic: Optional[str] = None) -> Optional[bool]:
    """The answer to a yes/no question, or None when it isn't a clear one.

    The leading word decides ("Yes, not too hard" is a yes). A yes and a no in the
    same answer, "can't remember", or a negation about something else is unknown.
    """
    t = _norm(text)
    if not t or _UNSURE.search(t):
        return None
    has_yes, has_no = bool(_YES_WORD.search(t)), bool(_NO_WORD.search(t))
    if has_yes and has_no:
        return None
    first = re.sub(r"^\W+", "", t)
    if _NO_WORD.match(first) or _LEAD_NO.match(first):
        return False
    if _YES_WORD.match(first) or _LEAD_YES.match(first):
        return True
    if topic in _TOPIC and _TOPIC[topic].search(t) and _NEG.search(t):
        return False
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

    head = yes_no(by_key["head"], "head") if "head" in by_key else None
    if head is None and _HIT_HEAD.search(everything):
        head = not _NOT_HIT_HEAD.search(everything)

    move = yes_no(by_key["move"], "move") if "move" in by_key else None
    if move is None and _CANT_MOVE.search(everything):
        move = False

    bleeding = yes_no(by_key["bleeding"], "bleeding") if "bleeding" in by_key else None
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
- A hedge or partial answer ("not exactly", "sort of", "kind of") is null, not false.
- Do not give medical advice or diagnoses.
- summary: one plain sentence for a paramedic, using only what was said."""

REWRITE_SYSTEM = (
    "Rewrite this message as one calm, short sentence (max 20 words) to be read aloud "
    "to someone who has fallen. Do not add information. Output only the sentence."
    # Measured Oct 17: without this, step-5-preview reasons for 900+ tokens (6-12 s);
    # with it, ~20 tokens in 1-1.8 s. Undocumented, so the 3 s fallback still guards it.
    " Answer immediately. Do not think step by step."
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


# The answer that means "worse" for each yes/no field. Measured Oct 18: step-5-preview turned
# "not exactly" to "Are you bleeding anywhere?" into bleeding=false, "denies bleeding".
_WORRYING = {"head_strike": True, "can_move": False, "bleeding": True}


def merge_model(rules: TriageRecord, m: "ModelTriage") -> tuple[TriageRecord, list[str]]:
    """The model may add detail and worrying answers, never a reassuring one the rules didn't hear.

    A clear rule answer always wins. Where the rules found nothing, only the worrying value is
    taken from the model. If any field differs from the model's, its summary (which says what the
    model thought) is replaced by the templated one. Returns (record, fields the rules kept)."""
    fields, kept = {}, []
    for f, worrying in _WORRYING.items():
        r, v = getattr(rules, f), getattr(m, f)
        fields[f] = r if r is not None else (v if v == worrying else None)
        if fields[f] != v:
            kept.append(f)
    fields["pain_location"] = m.pain_location or rules.pain_location
    if fields["pain_location"] != m.pain_location:
        kept.append("pain_location")
    if rules.conscious != m.conscious:  # answering at all is the only evidence either way
        kept.append("conscious")
    rec = rules.model_copy(update=fields)  # profile and script fields never come from the model
    rec.summary = summarize(rec) if kept else m.summary
    return rec, kept


def carry_forward(older: TriageRecord, rules: TriageRecord) -> TriageRecord:
    """A refinement that finished after newer answers arrived: keep what it learned under the
    same rules as merge_model, on top of the rule record for the latest answers. Its summary
    predates those answers, so the templated one is used."""
    fields = {f: getattr(rules, f) if getattr(rules, f) is not None
              else (getattr(older, f) if getattr(older, f) == worrying else None)
              for f, worrying in _WORRYING.items()}
    fields["pain_location"] = older.pain_location or rules.pain_location
    rec = rules.model_copy(update=fields)
    rec.summary = summarize(rec)
    return rec


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
                 stub: bool = True, timeout_s: float = 3.0, triage_timeout_s: float = 45.0,
                 transport: Transport = urllib_transport):
        self.enabled = not stub and bool(api_key) and bool(base_url)
        if not stub and not self.enabled:
            log.warning("STUB_STEPFUN=0 but STEPFUN_API_KEY/STEPFUN_BASE_URL missing; using rules only")
        self.api_key, self.model = api_key, model
        self.url = base_url.rstrip("/") + "/chat/completions"
        # Rewrite is spoken to the person, so it keeps a tight deadline. Triage runs in the
        # background after the instant rule-based record (measured 5-38 s on low effort, the
        # lowest level; it reasons through extraction whatever the prompt says, longest on
        # unclear answers).
        self.timeout_s, self.triage_timeout_s = timeout_s, triage_timeout_s
        self._transport = transport
        # The deadline is enforced here, not by the socket: a slow-trickling server can't hold us.
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stepfun")
        # Rewrite is spoken to the person, so it never queues behind 10 s triage calls.
        self._rewrite_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stepfun-rewrite")

    def _chat(self, system: str, user: str, *, json_mode: bool, max_tokens: int, timeout_s: float,
              pool: Optional[ThreadPoolExecutor] = None) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
            # step-5-preview reasons before it answers; low is the fastest documented level.
            # https://platform.stepfun.ai/docs/en/guides/models/step-5-preview
            "reasoning_effort": "low",
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}  # verified on our key, Oct 17
        headers = {"Authorization": f"Bearer {self.api_key}"}
        fut = (pool or self._pool).submit(self._transport, self.url, headers, body, timeout_s)
        try:
            data = fut.result(timeout=timeout_s)
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
            # Reasoning counts against max_tokens. Measured Oct 18: unclear answers took up to
            # 3593 tokens; at 2000 the answer came back empty (finish_reason=length).
            content = self._chat(TRIAGE_SYSTEM, transcript_text(answers), json_mode=True, max_tokens=5000,
                                 timeout_s=self.triage_timeout_s)
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
        rec, kept = merge_model(rules, m)
        if kept:
            log.info("stepfun triage: rules kept for fields=%s", ",".join(kept))
        return rec

    def rewrite(self, text: str) -> str:
        if not self.enabled or not text.strip():
            return text
        t0 = time.monotonic()
        try:
            out = self._chat(REWRITE_SYSTEM, text, json_mode=False, max_tokens=1200,
                             timeout_s=self.timeout_s, pool=self._rewrite_pool).strip().strip('"')
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
