"""Incident state machine (IMPLEMENTATION.md section 6.4).

Rules that must not change:
- Silence is not "fine". Only a clear "I'm fine" cancels.
- The unlock window (and its one-time code) opens only when we escalate, so a
  cancelled check never produces a usable code.
- Only enrolled caregivers move the state or get read aloud. A stranger's text
  is logged and dropped.
- Caregiver coordination (issue #23) is deterministic: regexes decide who claimed the
  incident and their ETA, timers decide when to call. The door code goes only to a
  caregiver who commits, never in the first alert. A model only words Q&A answers,
  from incident facts that never include the unlock code. Nothing heard on a call
  is ever parsed as an unlock.
- Nothing slow runs under the lock: tick() runs on the event loop, so StepFun calls
  happen outside it and their result is applied only if it is still current.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .comms import format_alert
from .coordinator import (Commitment, claimer_reply, incident_facts, is_question, parse_commitment,
                          spoken_line, stand_by_text, status_answer)
from .models import IncidentState as S
from .models import StepFunTriageView, TriageRecord
from .triage import QUESTIONS, TriageOutcome, apply_corrections, carry_forward, is_clear_fine, parse_rules
from .unlock import UnlockGuard, is_unlock_attempt
from .voice import ASSISTANT_LINE

log = logging.getLogger("firsthour.incident")

REASK_LINE = "Sorry, I didn't catch that."
MAX_TRIES = 2  # asks per question after the alert; then it stays unknown and the script moves on
TRIAGE_FIELDS = ("conscious", "head_strike", "pain_location", "can_move", "bleeding")
CLOSING_LINE = "Thank you. That's all my questions."
RELAY_MAX = 6  # companion relays per incident, so a looping agent can't flood the caregiver
RELAY_MAX_CHARS = 300

Emit = Callable[[str, dict], None]


@dataclass
class Incident:
    id: str
    device_id: str
    opened_at: float
    state: S = S.IDLE
    answers: list[tuple[str, str]] = field(default_factory=list)
    question_index: int = 0
    triage: TriageRecord = field(default_factory=TriageRecord)
    unlocked_until: Optional[float] = None
    refining: bool = False  # a StepFun refinement is in flight
    model_result: Optional[TriageOutcome] = None  # the last finished refinement, for the dashboard
    model_seen: int = 0  # answers that refinement saw
    alerted_at: Optional[float] = None
    responders: dict[str, dict] = field(default_factory=dict)  # number -> status, eta_min, name
    claimed_by: Optional[str] = None  # first clear "on my way"
    calls: list[str] = field(default_factory=list)  # numbers called, in order
    last_call_at: Optional[float] = None
    call_notes: list[str] = field(default_factory=list)
    code: str = field(default="", repr=False)  # texted only to a caregiver who commits; never in snapshot/facts
    misses: int = 0  # unanswered asks of the current question (after the alert)
    script_done: bool = False
    companion_ready: bool = False
    relays: int = 0
    corrections: dict[str, Any] = field(default_factory=dict)  # field -> value set by the operator


def _noop(*_args) -> None:
    pass


def _template_answer(_question: str, facts: dict) -> tuple[str, str]:
    return "answer", status_answer(facts)


def _in_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


def _answered(inc: Incident) -> list[str]:
    """Question keys she said something to: an empty field there is unclear, not unsaid."""
    return [k for k, a in inc.answers if a.strip()]


class IncidentMachine:
    def __init__(
        self,
        guard: UnlockGuard,
        profile: dict,
        clock: Callable[[], float] = time.time,
        silence_s: float = 8,
        playback_grace_s: float = 0,
        hold_s: float = 30,
        emit: Emit = _noop,
        speak: Callable[[str], None] = _noop,
        send_alert: Callable[[str, str], None] = _noop,
        on_close: Callable[[Incident, S], None] = _noop,
        triage_fn: Callable[[list, dict], TriageRecord | TriageOutcome] = parse_rules,
        rewrite_fn: Callable[[str], str] = lambda text: text,
        background: Callable[[Callable[[], None]], None] = _in_thread,
        send_text: Callable[[str, list[str], str, str], None] = _noop,
        place_call: Callable[[str, str, dict], None] = _noop,
        answer_fn: Callable[[str, dict], tuple[str, str]] = _template_answer,
        call_order: Optional[list[str]] = None,
        names: Optional[dict[str, str]] = None,
        call_after_s: Optional[float] = 20,
        reask: bool = False,
        model: Optional[str] = None,
    ):
        self.guard = guard
        self.profile = profile
        self._clock = clock
        self.silence_s = silence_s
        # The silence timer starts when the voice reports the question finished playing
        # (line_played). If that report never comes, it starts this long after queueing,
        # so a stuck speaker can delay escalation but never stop it.
        self.playback_grace_s = playback_grace_s
        self.hold_s = hold_s
        self._emit = emit
        self._speak = speak
        self._send_alert = send_alert
        self._on_close = on_close
        self._triage_fn = triage_fn  # StepFun with rule fallback, or rules only
        self.model = model  # name of the model refining triage in the background; None = rules only
        self._rewrite_fn = rewrite_fn
        self._background = background  # runs the StepFun refinement; tests pass a synchronous one
        self._send_text = send_text  # (incident_id, to, text, kind); never the code
        self._place_call = place_call  # (incident_id, number, variables); must not block
        self._answer_fn = answer_fn  # StepFun Q&A with templated fallback
        # Called in this order when nobody claims: primary first, then the backups.
        self.call_order = [n for n in (call_order or sorted(guard.allowed)) if n in guard.allowed]
        self.names = names or {}
        self.call_after_s = call_after_s  # None: no voice escalation
        # After the alert, an unanswered question is asked again, then skipped. Only with a
        # listening mic (PI_STT): with typed answers the operator sets the pace.
        self.reask = reask
        self.incident: Optional[Incident] = None
        # Ids of incidents this machine closed, newest last. Outbox delivery from the Pi is
        # at-least-once (seen Oct 18: a fall_suspected delivered again after its uplink
        # process restarted), so a closed id must never open a second incident.
        self._closed: dict[str, float] = {}
        self._question_asked_at = 0.0
        self._question_heard_at: Optional[float] = None
        # Speech was recorded and the Pi is transcribing it: hold the silence escalation
        # until this time. Bounded, so a lost transcript delays escalation but never stops it.
        self._speech_hold_until: Optional[float] = None
        # Routes call in from worker threads and tick() from the event loop.
        self._lock = threading.RLock()

    # --- read ---------------------------------------------------------------

    @property
    def state(self) -> S:
        return self.incident.state if self.incident else S.IDLE

    def snapshot(self) -> dict:
        """For the dashboard. Never includes the unlock code."""
        with self._lock:
            return self._snapshot()

    def _snapshot(self) -> dict:
        inc = self.incident
        if not inc:
            return {"state": S.IDLE.value, "incident_id": None}
        return {
            "state": inc.state.value,
            "incident_id": inc.id,
            "device_id": inc.device_id,
            "opened_at_ms": int(inc.opened_at * 1000),  # dashboard runs the clock locally
            "down_s": round(self._clock() - inc.opened_at, 1),
            "current_question": self._current_question(),
            "question_index": inc.question_index,
            "companion_ready": inc.companion_ready,
            "transcript": [{"q": dict(QUESTIONS)[k], "a": a} for k, a in inc.answers],
            "answered": _answered(inc),
            "triage": inc.triage.model_dump(),
            **self._triage_sources(inc),
            "responders": self._responders(inc),
            "calls": len(inc.calls),
        }

    def facts(self) -> Optional[dict]:
        """Read-only incident facts for Q&A and the voice agent. No unlock code."""
        with self._lock:
            return self._facts(self.incident) if self.incident else None

    def was_closed(self, incident_id: str) -> bool:
        """True for an incident this machine ended (cancelled, resolved or superseded)."""
        with self._lock:
            return incident_id in self._closed

    # --- inputs -------------------------------------------------------------

    def on_fall(self, device_id: str, incident_id: str, ts_ms: Optional[int] = None) -> bool:
        """ts_ms: when the Pi detected the fall, so a fall replayed from its offline
        outbox keeps its real time down. Ignored if the Pi's clock looks wrong."""
        with self._lock:
            if incident_id in self._closed:
                log.info("fall ignored, incident already closed (delivered again) incident_id=%s", incident_id)
                return False
            inc = self.incident
            if inc and (inc.id == incident_id or inc.device_id != device_id):
                log.info("fall ignored, incident already open incident_id=%s", inc.id)
                return False
            if inc:
                # The Pi opens a new incident only after closing its last one, so ours is
                # stale. The Pi decides; follow it (its alarm_off for the old id is ignored).
                self._finish(S.RESOLVED, f"superseded by {incident_id}")
            now = self._clock()
            opened_at = now
            if ts_ms is not None:
                t = ts_ms / 1000
                if now - self.guard.window_s <= t <= now + 5:
                    opened_at = t
                else:
                    log.warning("edge ts out of range, using receive time incident_id=%s skew_s=%.0f",
                                incident_id, t - now)
            self.incident = Incident(id=incident_id, device_id=device_id, opened_at=opened_at)
            self.incident.triage = parse_rules([], self.profile)
            self._set(S.CHECKING, "fall_suspected")
            self._speak(ASSISTANT_LINE)  # the voice says what it is before it asks anything
            self._ask()
            return True

    def on_answer(self, text: str, question_index: Optional[int] = None) -> bool:
        """question_index: the question a recorded clip answered. A transcript that comes
        back after the script moved on is dropped, never filed under the next question."""
        with self._lock:
            inc = self.incident
            if not inc or inc.state not in (S.CHECKING, S.ALERTING, S.RESPONDING, S.UNLOCKED):
                log.info("answer ignored, no open incident")
                return False
            if inc.question_index >= len(QUESTIONS):
                log.info("answer after script finished incident_id=%s", inc.id)
                return False
            if question_index is not None and question_index != inc.question_index:
                log.info("late answer dropped: recorded for question %d, now at %d incident_id=%s",
                         question_index, inc.question_index, inc.id)
                return False
            self._speech_hold_until = None
            inc.misses = 0
            key = QUESTIONS[inc.question_index][0]
            inc.answers.append((key, text))

            if inc.state == S.CHECKING and is_clear_fine(text):
                self._finish(S.CANCELLED, "person said fine")
                return True

            inc.question_index += 1
            answers = list(inc.answers)
            # One StepFun call per incident at a time: answers come every 2-10 s and a call
            # takes 5-38 s, so the running one picks up whatever arrived meanwhile.
            start, inc.refining = not inc.refining, True
            # Escalate on the instant rule-based record: the alert never waits on StepFun.
            inc.triage = apply_corrections(parse_rules(answers, self.profile), inc.corrections)
            self._emit_triage(inc)
            if inc.state == S.CHECKING:
                self._escalate("person needs help" if text.strip() else "no clear answer")
            self._ask()
            self._maybe_script_done(inc)

        # StepFun takes seconds; the caller (and the person's next answer) never waits on it.
        if start:
            self._background(lambda: self._refine(inc))
        return True

    def hold_for_speech(self, seconds: float) -> None:
        """A clip with sound in it is being transcribed on the Pi: don't call it silence yet."""
        with self._lock:
            if self.incident and self._current_question():
                self._speech_hold_until = self._clock() + seconds
                log.info("silence timer held up to %.0f s for speech-to-text incident_id=%s",
                         seconds, self.incident.id)

    def release_speech_hold(self) -> None:
        """The transcript came back with no words: silence is silence again."""
        with self._lock:
            self._speech_hold_until = None

    def _refine(self, inc: Incident) -> None:
        try:
            while True:
                with self._lock:
                    answers = list(inc.answers)
                out = self._triage_fn(answers, self.profile)  # StepFun: up to 45 s, then rules
                if not isinstance(out, TriageOutcome):
                    out = TriageOutcome(out)
                triage = out.record
                with self._lock:
                    if self.incident is not inc:
                        return  # the incident ended while the model was thinking
                    # A finished model call is news for the dashboard even if the record is unchanged.
                    shown = self.model is not None
                    if shown:
                        inc.model_result, inc.model_seen = out, len(answers)
                    if len(inc.answers) != len(answers):
                        # Stale, and with live speech nearly every call is: keep what it learned,
                        # then run once more on the latest answers.
                        carried = apply_corrections(
                            carry_forward(triage, parse_rules(list(inc.answers), self.profile)), inc.corrections)
                        if carried != inc.triage or shown:
                            inc.triage = carried
                            self._emit_triage(inc)
                        continue
                    inc.refining = False
                    triage = apply_corrections(triage, inc.corrections)  # read under the lock
                    if triage != inc.triage or shown:
                        inc.triage = triage
                        self._emit_triage(inc)
                    return
        except Exception:  # the rule record stands; never wedge later refinements
            log.exception("triage refinement failed incident_id=%s", inc.id)
            with self._lock:
                inc.refining = False
                if self.model is not None and self.incident is inc:
                    inc.model_result = TriageOutcome(inc.triage, "error", reason="refinement failed")
                    self._emit_triage(inc)

    def on_help_button(self, device_id: Optional[str] = None) -> None:
        """device_id: the node whose button was pressed; another node's button never
        escalates this incident."""
        with self._lock:
            inc = self.incident
            if inc and device_id is not None and inc.device_id != device_id:
                log.info("help button from another device ignored device_id=%s incident_id=%s", device_id, inc.id)
                return
            if self.incident and self.incident.state == S.CHECKING:
                self.incident.question_index = max(self.incident.question_index, 1)
                self._escalate("help button")
                self._ask()

    def caregiver_reply(self, sender: str, text: str) -> dict:
        sender = sender.strip()
        with self._lock:
            if is_unlock_attempt(text):
                # Always through the guard, even with no incident, so every attempt is logged.
                result = self.guard.try_unlock(sender, text)
                self._emit("unlock_attempt", result.model_dump())
                if result.allowed and self.incident:
                    self.incident.unlocked_until = self._clock() + self.hold_s
                    self._set(S.UNLOCKED, "unlock granted")
                return {"type": "unlock_attempt", "allowed": result.allowed, "reason": result.reason}

            if sender not in self.guard.allowed:
                log.info("message from non-enrolled sender dropped sender=%s", sender)
                return {"type": "message", "accepted": False, "reason": "not enrolled"}
            inc = self.incident
            if not inc:
                log.info("caregiver message with no incident sender=%s", sender)
                return {"type": "message", "accepted": False, "reason": "no active incident"}

            self._acknowledge(inc, sender)
            commitment = parse_commitment(text)
            if commitment:
                self._emit("message_in", {"sender": sender, "text": text, "spoken": None,
                                          "incident_id": inc.id})
                return self._commit(inc, sender, commitment)
            facts = self._facts(inc) if is_question(text) else None

        if facts is not None:
            kind, answer = self._answer_fn(text, facts)  # StepFun: a few s, then the template
            if kind == "answer":
                with self._lock:
                    if self.incident is not inc:
                        return {"type": "message", "accepted": False, "reason": "no active incident"}
                    self._emit("message_in", {"sender": sender, "text": text, "spoken": None,
                                              "question": True, "incident_id": inc.id})
                    self._send_text(inc.id, [sender], answer, "answer")
                    return {"type": "question", "accepted": True}

        spoken = self._rewrite_fn(text)  # StepFun: up to its rewrite deadline (10 s), then the original text

        with self._lock:
            if self.incident is not inc:
                log.info("caregiver message arrived as the incident closed incident_id=%s", inc.id)
                return {"type": "message", "accepted": False, "reason": "no active incident"}
            # Both versions go to the dashboard, so the operator can see what the AI changed.
            self._emit("message_in", {"sender": sender, "text": text, "spoken": spoken,
                                      "incident_id": inc.id})
            if inc.state == S.ALERTING:
                self._set(S.RESPONDING, "caregiver replied")
            self._speak(spoken)
            self._resume_script(inc)
            return {"type": "message", "accepted": True}

    def record_call_result(self, incident_id: Optional[str], number: Optional[str],
                           user_turns: list[str], summary: str) -> dict:
        """A finished call (post-call webhook). Only a call we placed to an enrolled number can
        record a commitment; caller ID alone never does. Nothing said is parsed as an unlock."""
        with self._lock:
            inc = self.incident
            if not inc or inc.id != incident_id:
                return {"accepted": False, "reason": "no matching incident"}
            if summary:
                inc.call_notes.append(summary)
            self._emit("call_update", {"incident_id": inc.id, "status": "completed",
                                       "name": self.names.get(number or ""), "summary": summary})
            commitment = None
            if number in self.guard.allowed:
                for turn in user_turns:
                    commitment = parse_commitment(turn) or commitment
            if not commitment:
                return {"accepted": True, "commitment": None}
            self._acknowledge(inc, number)
            return self._commit(inc, number, commitment, via_call=True)

    def resolve(self, by: str = "caregiver") -> bool:
        with self._lock:
            if not self.incident:
                return False
            self._finish(S.RESOLVED, f"resolved by {by}")
            return True

    def correct(self, field_name: str, value: Any, by: str = "operator") -> bool:
        """The operator fixes a misread field. It wins over the rules and the model for the rest
        of this incident, including later refinements. False with no open incident."""
        if field_name not in TRIAGE_FIELDS:
            raise ValueError(f"not a correctable field: {field_name}")
        with self._lock:
            inc = self.incident
            if not inc:
                return False
            inc.corrections[field_name] = value
            inc.triage = apply_corrections(inc.triage, inc.corrections)
            log.info("triage corrected field=%s value=%r by=%s incident_id=%s", field_name, value, by, inc.id)
            self._emit_triage(inc)
            return True

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self.incident:
                self._finish(S.CANCELLED, reason)

    def line_played(self, text: str) -> None:
        """The voice finished playing `text`. Starts the silence timer for the current question."""
        with self._lock:
            inc = self.incident
            if inc and inc.script_done and text == CLOSING_LINE and not inc.companion_ready:
                inc.companion_ready = True
                self._emit("script_done", {"incident_id": inc.id})
                return
            if not inc or text != self._current_question():
                return
            if self._question_heard_at is None:
                self._question_heard_at = self._clock()
            # The dashboard starts recording the answer only now, so it never hears the question.
            self._emit("question_played", {"incident_id": inc.id, "question_index": inc.question_index,
                                           "question": text})

    def tick(self) -> None:
        """Called a few times per second by the backend loop."""
        with self._lock:
            inc = self.incident
            if not inc:
                return
            now = self._clock()
            heard_at = self._question_heard_at
            if heard_at is None:
                heard_at = self._question_asked_at + self.playback_grace_s
            held = self._speech_hold_until is not None and now < self._speech_hold_until
            if inc.state == S.CHECKING and now - heard_at >= self.silence_s and not held:
                if self._speech_hold_until is not None:
                    log.warning("speech-to-text gave no answer in time; treating it as silence incident_id=%s",
                                inc.id)
                    self._speech_hold_until = None
                if self._question_heard_at is not None:
                    log.info("silence timer: question finished playing %.1f s after it was queued, "
                             "then %.1f s of silence before escalating (timer started at playback end) "
                             "incident_id=%s", self._question_heard_at - self._question_asked_at,
                             now - self._question_heard_at, inc.id)
                else:
                    log.warning("silence timer: voice never reported the question finished; started after "
                                "the %g s grace, escalating %.1f s after queueing incident_id=%s",
                                self.playback_grace_s, now - self._question_asked_at, inc.id)
                inc.question_index = max(inc.question_index, 1)
                self._escalate(f"no answer for {self.silence_s:g} s")
                self._ask()
            elif inc.state == S.UNLOCKED and inc.unlocked_until and now >= inc.unlocked_until:
                inc.unlocked_until = None
                self._set(S.RESPONDING, "unlock hold ended")
            if (self.reask and inc.state != S.CHECKING and self._current_question()
                    and now - heard_at >= self.silence_s and not held):
                self._missed(inc)
            self._maybe_call(inc, now)

    # --- internals ----------------------------------------------------------

    def _current_question(self) -> Optional[str]:
        inc = self.incident
        if not inc or inc.question_index >= len(QUESTIONS):
            return None
        return QUESTIONS[inc.question_index][1]

    def _ask(self) -> None:
        q = self._current_question()
        self._speech_hold_until = None
        if q:
            self._question_asked_at, self._question_heard_at = self._clock(), None
            self._speak(q)

    def _missed(self, inc: Incident) -> None:
        """No usable answer (silence, or speech-to-text failed or timed out): ask once more,
        then leave it unknown and move on, so the conversation never just stops."""
        self._speech_hold_until = None
        inc.misses += 1
        if inc.misses < MAX_TRIES:
            log.info("no answer to question %d, asking again incident_id=%s", inc.question_index, inc.id)
            self._speak(REASK_LINE)
        else:
            log.info("no answer to question %d after %d asks, moving on incident_id=%s",
                     inc.question_index, inc.misses, inc.id)
            inc.misses = 0
            inc.question_index += 1
            self._emit_triage(inc)
        self._ask()
        self._maybe_script_done(inc)

    def _resume_script(self, inc: Incident) -> None:
        """After a caregiver update is read aloud, ask the pending question again so she
        knows the conversation is still going."""
        if inc.state != S.CHECKING and self._current_question():
            self._ask()

    def _maybe_script_done(self, inc: Incident) -> None:
        if inc.script_done or inc.state == S.CHECKING or self._current_question():
            return
        inc.script_done = True
        self._speak(CLOSING_LINE)
        log.info("question script finished incident_id=%s", inc.id)
        # line_played hands over after the closing audio, not while it is still speaking.

    def companion_relay(self, message: str, urgency: str = "normal") -> dict:
        """The companion agent judged something she said worth texting: to whoever claimed the
        incident, else every caregiver. Text only; it can never unlock or change the triage."""
        message = " ".join(message.split())[:RELAY_MAX_CHARS]
        with self._lock:
            inc = self.incident
            if not inc or inc.state == S.CHECKING:
                return {"sent": False, "reason": "no alerted incident"}
            if not inc.companion_ready:
                return {"sent": False, "reason": "fixed questions still finishing"}
            if not message:
                return {"sent": False, "reason": "empty message"}
            if inc.code and inc.code in message:
                log.warning("companion relay refused: contains the door code incident_id=%s", inc.id)
                return {"sent": False, "reason": "refused"}
            if inc.relays >= RELAY_MAX:
                return {"sent": False, "reason": "relay limit reached"}
            inc.relays += 1
            to = [inc.claimed_by] if inc.claimed_by else sorted(self.guard.allowed)
            prefix = "URGENT: " if urgency == "urgent" else ""
            text = f'{prefix}FirstHour: {self.profile["name"]} says: "{message}"'
            log.info("companion relay #%d urgency=%s to=%s incident_id=%s", inc.relays, urgency, to, inc.id)
            self._send_text(inc.id, to, text, "relay")
            return {"sent": True, "to": "claimer" if inc.claimed_by else "all caregivers"}

    def _emit_triage(self, inc: Incident) -> None:
        self._emit("triage_update", {"incident_id": inc.id, "triage": inc.triage.model_dump(),
                                     **self._triage_sources(inc),
                                     "transcript": [a for _, a in inc.answers],
                                     "answered": _answered(inc),
                                     "current_question": self._current_question()})

    def _triage_sources(self, inc: Incident) -> dict:
        """The rules record and the model's own record side by side, so the operator can see
        what each one understood and which fields of the record in use came from the model."""
        rules = parse_rules(list(inc.answers), self.profile)
        used = inc.triage
        r = inc.model_result
        record = r.model_record if r else None
        # A corrected field came from the operator, not the model.
        added = [f for f in TRIAGE_FIELDS
                 if f not in inc.corrections and getattr(used, f) != getattr(rules, f)]
        # A record with a changed field gets a templated summary, so "summary" means the model wrote it.
        if record and used.summary == record.get("summary") and used.summary != rules.summary:
            added.append("summary")
        if self.model is None:
            status = "off"
        elif inc.refining:
            status = "pending"
        elif r is None:
            status = "waiting"
        else:
            status = r.status
        view = StepFunTriageView(
            name=self.model, status=status, ms=r.ms if r else None, deadline_s=r.deadline_s if r else None,
            reason=r.reason if r else None, record=record, answers_seen=inc.model_seen if r else 0,
            added=added,
            overruled=[f for f in TRIAGE_FIELDS
                       if record and record.get(f) is not None and record.get(f) != getattr(used, f)],
        )
        return {"rules": rules.model_dump(), "model": view.model_dump(), "corrections": dict(inc.corrections)}

    def _responders(self, inc: Incident) -> list[dict]:
        return [{"number": n, **r, "assigned": n == inc.claimed_by} for n, r in inc.responders.items()]

    def _facts(self, inc: Incident) -> dict:
        return incident_facts(
            profile=self.profile, state=inc.state.value, down_s=self._clock() - inc.opened_at,
            triage=inc.triage, transcript=[{"q": dict(QUESTIONS)[k], "a": a} for k, a in inc.answers],
            responders=list(inc.responders.values()), call_notes=list(inc.call_notes))

    def _acknowledge(self, inc: Incident, sender: str) -> None:
        if sender not in inc.responders:
            inc.responders[sender] = {"name": self.names.get(sender), "status": "acknowledged",
                                      "eta_min": None}
            self._emit("delivery_status", {"to": [sender], "incident_id": inc.id, "status": "acknowledged"})

    def _commit(self, inc: Incident, sender: str, c: Commitment, via_call: bool = False) -> dict:
        r = inc.responders[sender]
        r["status"] = c.status
        if c.eta_min is not None:
            r["eta_min"] = c.eta_min
        first = inc.claimed_by is None
        if first:
            inc.claimed_by = sender
        self._emit("responder_update", {"incident_id": inc.id, "number": sender, **r,
                                        "assigned": inc.claimed_by == sender, "via": "call" if via_call else "text"})
        if inc.state == S.ALERTING:
            self._set(S.RESPONDING, "caregiver claimed" if first else "caregiver replied")
        self._speak(spoken_line(r["name"], Commitment(c.status, r["eta_min"])))
        self._resume_script(inc)
        # kind "code": the dashboard is told a code text went out, never its text.
        self._send_text(inc.id, [sender], claimer_reply(Commitment(c.status, r["eta_min"]), inc.code, via_call),
                        "code" if inc.code else "ack")
        if first:
            self._emit("delivery_status", {"to": [sender], "incident_id": inc.id, "status": "responder_assigned"})
            others = sorted(self.guard.allowed - {sender})
            if others:
                self._send_text(inc.id, others, stand_by_text(r["name"]), "stand_by")
        return {"type": "commitment", "accepted": True, "status": c.status, "eta_min": r["eta_min"],
                "assigned": inc.claimed_by == sender}

    def _maybe_call(self, inc: Incident, now: float) -> None:
        """Voice escalation: no caregiver replied at all within call_after_s of the alert (or of the
        last call), or the person hit their head. Any reply means someone is engaged, so it stops.
        One call per number, primary first."""
        if (self.call_after_s is None or inc.alerted_at is None or inc.responders
                or inc.state not in (S.ALERTING, S.RESPONDING) or len(inc.calls) >= len(self.call_order)):
            return
        since = inc.last_call_at if inc.last_call_at is not None else inc.alerted_at
        head = inc.triage.head_strike is True and not inc.calls
        if not head and now - since < self.call_after_s:
            return
        number = self.call_order[len(inc.calls)]
        inc.calls.append(number)
        inc.last_call_at = now
        reason = "head strike" if head else f"no reply for {self.call_after_s:g} s"
        log.info("voice escalation call #%d reason=%s incident_id=%s", len(inc.calls), reason, inc.id)
        self._emit("call_update", {"incident_id": inc.id, "status": "dialing", "reason": reason,
                                   "name": self.names.get(number), "attempt": len(inc.calls)})
        self._place_call(inc.id, number, self._call_variables(inc))

    def _call_variables(self, inc: Incident) -> dict:
        f = self._facts(inc)
        return {
            "incident_id": inc.id,
            "patient_name": f["patient"],
            "room": f["room"],
            "time_down": f["time_down"],
            "triage_summary": f["triage_summary"],
            "on_blood_thinner": "yes" if f["on_blood_thinner"] else "no",
            "last_answer": f["last_answer"] or "",
            "eta_question": "How many minutes until you can be at the door?",
        }

    def _escalate(self, reason: str) -> None:
        inc = self.incident
        inc.code = self.guard.open_incident(inc.id)
        inc.alerted_at = self._clock()
        self._set(S.ALERTING, reason)
        self._send_alert(inc.id, format_alert(self.profile, self._clock() - inc.opened_at, inc.triage))

    def _finish(self, final: S, reason: str) -> None:
        self._set(final, reason)
        self.guard.close_incident()
        self._closed[self.incident.id] = self._clock()
        while len(self._closed) > 50:
            del self._closed[next(iter(self._closed))]
        # Tell the Pi before forgetting the incident: its buzzer and clock stay up
        # until this alarm_off (or a demo reset), never on a timer.
        self._on_close(self.incident, final)
        self.incident = None
        self._emit("incident_update", {"state": S.IDLE.value, "incident_id": None, "reason": "reset"})
        log.info("state -> IDLE")

    def _set(self, state: S, reason: str) -> None:
        inc = self.incident
        prev = inc.state
        inc.state = state
        log.info("state %s -> %s reason=%s incident_id=%s", prev.value, state.value, reason, inc.id)
        self._emit("incident_update", {"state": state.value, "prev": prev.value,
                                       "reason": reason, "incident_id": inc.id,
                                       "opened_at_ms": int(inc.opened_at * 1000),
                                       "current_question": self._current_question()})
