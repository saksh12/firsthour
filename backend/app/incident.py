"""Incident state machine (IMPLEMENTATION.md section 6.4).

Rules that must not change:
- Silence is not "fine". Only a clear "I'm fine" cancels.
- The unlock window (and its one-time code) opens only when we escalate, so a
  cancelled check never produces a usable code.
- Only enrolled caregivers move the state or get read aloud. A stranger's text
  is logged and dropped.
"""

import logging
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
    ):
        self.guard = guard
        self.profile = profile
        self._clock = clock
        self.silence_s = silence_s
        self.hold_s = hold_s
        self._emit = emit
        self._speak = speak
        self._send_alert = send_alert
        self.incident: Optional[Incident] = None
        self._question_asked_at = 0.0

    # --- read ---------------------------------------------------------------

    @property
    def state(self) -> S:
        return self.incident.state if self.incident else S.IDLE

    def snapshot(self) -> dict:
        """For the dashboard. Never includes the unlock code."""
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

    def on_fall(self, device_id: str, incident_id: str) -> bool:
        if self.incident:
            log.info("fall ignored, incident already open incident_id=%s", self.incident.id)
            return False
        now = self._clock()
        self.incident = Incident(id=incident_id, device_id=device_id, opened_at=now)
        self.incident.triage = parse_rules([], self.profile)
        self._set(S.CHECKING, "fall_suspected")
        self._ask()
        return True

    def on_answer(self, text: str) -> None:
        inc = self.incident
        if not inc or inc.state not in (S.CHECKING, S.ALERTING, S.RESPONDING, S.UNLOCKED):
            log.info("answer ignored, no open incident")
            return
        if inc.question_index >= len(QUESTIONS):
            log.info("answer after script finished incident_id=%s", inc.id)
            return
        key = QUESTIONS[inc.question_index][0]

        if inc.state == S.CHECKING and is_clear_fine(text):
            inc.answers.append((key, text))
            self._finish(S.CANCELLED, "person said fine")
            return

        inc.answers.append((key, text))
        inc.question_index += 1
        self._update_triage()
        if inc.state == S.CHECKING:
            self._escalate("person needs help" if text.strip() else "no clear answer")
        self._ask()

    def on_help_button(self) -> None:
        if self.incident and self.incident.state == S.CHECKING:
            self.incident.question_index = max(self.incident.question_index, 1)
            self._escalate("help button")
            self._ask()

    def caregiver_reply(self, sender: str, text: str) -> dict:
        sender = sender.strip()
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
        if not self.incident:
            log.info("caregiver message with no incident sender=%s", sender)
            return {"type": "message", "accepted": False, "reason": "no active incident"}

        self._emit("message_in", {"sender": sender, "text": text, "incident_id": self.incident.id})
        if self.incident.state == S.ALERTING:
            self._set(S.RESPONDING, "caregiver replied")
        # TODO(phase 3, B/C): StepFun rewrite to one calm sentence, falling back to the original.
        self._speak(text)
        return {"type": "message", "accepted": True}

    def resolve(self, by: str = "caregiver") -> bool:
        if not self.incident:
            return False
        self._finish(S.RESOLVED, f"resolved by {by}")
        return True

    def cancel(self, reason: str) -> None:
        if self.incident:
            self._finish(S.CANCELLED, reason)

    def tick(self) -> None:
        """Called a few times per second by the backend loop."""
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

    def _update_triage(self) -> None:
        inc = self.incident
        inc.triage = parse_rules(inc.answers, self.profile)
        self._emit("triage_update", {"incident_id": inc.id, "triage": inc.triage.model_dump(),
                                     "transcript": [a for _, a in inc.answers]})

    def _escalate(self, reason: str) -> None:
        inc = self.incident
        code = self.guard.open_incident(inc.id)
        self._set(S.ALERTING, reason)
        self._send_alert(inc.id, format_alert(self.profile, self._clock() - inc.opened_at, inc.triage, code))

    def _finish(self, final: S, reason: str) -> None:
        self._set(final, reason)
        self.guard.close_incident()
        self.incident = None
        self._emit("incident_update", {"state": S.IDLE.value, "incident_id": None, "reason": "reset"})
        log.info("state -> IDLE")

    def _set(self, state: S, reason: str) -> None:
        inc = self.incident
        prev = inc.state
        inc.state = state
        log.info("state %s -> %s reason=%s incident_id=%s", prev.value, state.value, reason, inc.id)
        self._emit("incident_update", {"state": state.value, "prev": prev.value,
                                       "reason": reason, "incident_id": inc.id})
