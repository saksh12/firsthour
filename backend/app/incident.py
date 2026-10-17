"""Incident state machine (IMPLEMENTATION.md section 6.4).

Rules that must not change:
- Silence is not "fine". Only a clear "I'm fine" cancels.
- The unlock window (and its one-time code) opens only when we escalate, so a
  cancelled check never produces a usable code.
- Only enrolled caregivers move the state or get read aloud. A stranger's text
  is logged and dropped.
- Nothing slow runs under the lock: tick() runs on the event loop, so StepFun calls
  happen outside it and their result is applied only if it is still current.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .comms import format_alert
from .models import IncidentState as S
from .models import TriageRecord
from .triage import QUESTIONS, is_clear_fine, parse_rules
from .unlock import UnlockGuard, is_unlock_attempt

log = logging.getLogger("firsthour.incident")

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


def _noop(*_args) -> None:
    pass


class IncidentMachine:
    def __init__(
        self,
        guard: UnlockGuard,
        profile: dict,
        clock: Callable[[], float] = time.time,
        silence_s: float = 8,
        hold_s: float = 30,
        emit: Emit = _noop,
        speak: Callable[[str], None] = _noop,
        send_alert: Callable[[str, str], None] = _noop,
        on_close: Callable[[Incident, S], None] = _noop,
        triage_fn: Callable[[list, dict], TriageRecord] = parse_rules,
        rewrite_fn: Callable[[str], str] = lambda text: text,
    ):
        self.guard = guard
        self.profile = profile
        self._clock = clock
        self.silence_s = silence_s
        self.hold_s = hold_s
        self._emit = emit
        self._speak = speak
        self._send_alert = send_alert
        self._on_close = on_close
        self._triage_fn = triage_fn  # StepFun with rule fallback, or rules only
        self._rewrite_fn = rewrite_fn
        self.incident: Optional[Incident] = None
        self._question_asked_at = 0.0
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
            "transcript": [{"q": dict(QUESTIONS)[k], "a": a} for k, a in inc.answers],
            "triage": inc.triage.model_dump(),
        }

    # --- inputs -------------------------------------------------------------

    def on_fall(self, device_id: str, incident_id: str, ts_ms: Optional[int] = None) -> bool:
        """ts_ms: when the Pi detected the fall, so a fall replayed from its offline
        outbox keeps its real time down. Ignored if the Pi's clock looks wrong."""
        with self._lock:
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
            self._ask()
            return True

    def on_answer(self, text: str) -> None:
        with self._lock:
            inc = self.incident
            if not inc or inc.state not in (S.CHECKING, S.ALERTING, S.RESPONDING, S.UNLOCKED):
                log.info("answer ignored, no open incident")
                return
            if inc.question_index >= len(QUESTIONS):
                log.info("answer after script finished incident_id=%s", inc.id)
                return
            key = QUESTIONS[inc.question_index][0]
            inc.answers.append((key, text))

            if inc.state == S.CHECKING and is_clear_fine(text):
                self._finish(S.CANCELLED, "person said fine")
                return

            inc.question_index += 1
            answers = list(inc.answers)
            # Escalate on the instant rule-based record: the alert never waits on StepFun.
            inc.triage = parse_rules(answers, self.profile)
            self._emit_triage(inc)
            if inc.state == S.CHECKING:
                self._escalate("person needs help" if text.strip() else "no clear answer")
            self._ask()

        triage = self._triage_fn(answers, self.profile)  # StepFun: up to 3 s, then rules

        with self._lock:
            # A newer answer (or the end of the incident) makes this result stale.
            if self.incident is inc and len(inc.answers) == len(answers) and triage != inc.triage:
                inc.triage = triage
                self._emit_triage(inc)

    def on_help_button(self) -> None:
        with self._lock:
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

        spoken = self._rewrite_fn(text)  # StepFun: up to 3 s, then the original text

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
            return {"type": "message", "accepted": True}

    def resolve(self, by: str = "caregiver") -> bool:
        with self._lock:
            if not self.incident:
                return False
            self._finish(S.RESOLVED, f"resolved by {by}")
            return True

    def cancel(self, reason: str) -> None:
        with self._lock:
            if self.incident:
                self._finish(S.CANCELLED, reason)

    def tick(self) -> None:
        """Called a few times per second by the backend loop."""
        with self._lock:
            inc = self.incident
            if not inc:
                return
            now = self._clock()
            if inc.state == S.CHECKING and now - self._question_asked_at >= self.silence_s:
                inc.question_index = max(inc.question_index, 1)
                self._escalate(f"no answer for {self.silence_s:g} s")
                self._ask()
            elif inc.state == S.UNLOCKED and inc.unlocked_until and now >= inc.unlocked_until:
                inc.unlocked_until = None
                self._set(S.RESPONDING, "unlock hold ended")

    # --- internals ----------------------------------------------------------

    def _current_question(self) -> Optional[str]:
        inc = self.incident
        if not inc or inc.question_index >= len(QUESTIONS):
            return None
        return QUESTIONS[inc.question_index][1]

    def _ask(self) -> None:
        q = self._current_question()
        if q:
            self._question_asked_at = self._clock()
            self._speak(q)

    def _emit_triage(self, inc: Incident) -> None:
        self._emit("triage_update", {"incident_id": inc.id, "triage": inc.triage.model_dump(),
                                     "transcript": [a for _, a in inc.answers],
                                     "current_question": self._current_question()})

    def _escalate(self, reason: str) -> None:
        inc = self.incident
        code = self.guard.open_incident(inc.id)
        self._set(S.ALERTING, reason)
        self._send_alert(inc.id, format_alert(self.profile, self._clock() - inc.opened_at, inc.triage, code))

    def _finish(self, final: S, reason: str) -> None:
        self._set(final, reason)
        self.guard.close_incident()
        # Tell the Pi before forgetting the incident, so its buzzer, clock and
        # offline NFC unlock don't stay live until the local window runs out.
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
