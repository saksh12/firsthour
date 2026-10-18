"""Pydantic schemas for every contract in IMPLEMENTATION.md section 6.

If you change a model here, change the contract there in the same commit.
All timestamps are UTC epoch milliseconds.
"""

from enum import Enum
from typing import Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

# --- 6.2 Edge to backend -----------------------------------------------------

# speech_text: the Pi's whisper.cpp transcript of a clip it fetched for a transcribe command.
EdgeEventType = Literal[
    "fall_suspected", "heartbeat", "help_button", "local_unlock", "unlock_failed", "cancelled",
    "speech_text",
]


class SensorReading(BaseModel):
    sound_peak: Optional[int] = None
    accel_delta_g: Optional[float] = None
    dist_cm: Optional[int] = None


class SpeechResult(BaseModel):
    """What the Pi heard in one clip. Empty text means no words (silence stays silence)."""

    clip_id: str
    text: str = ""
    stt_ms: Optional[int] = None    # whisper.cpp wall time on the Pi
    audio_ms: Optional[int] = None  # clip length
    error: Optional[str] = None     # set when the Pi could not transcribe


class EdgeEvent(BaseModel):
    device_id: str
    ts: int
    type: EdgeEventType
    sensors: SensorReading = Field(default_factory=SensorReading)
    local_incident_id: Optional[str] = None
    speech: Optional[SpeechResult] = None  # only on speech_text


# --- 6.3 Backend to edge -----------------------------------------------------

# reset: demo reset. The Pi obeys it only with no local incident open (incident_id is "").
# transcribe: fetch clip <id> from GET /edge/audio/<id> and post a speech_text event.
CommandAction = Literal["unlock", "lock", "alarm_on", "alarm_off", "display", "reset", "transcribe"]


class Command(BaseModel):
    id: str
    action: CommandAction
    incident_id: str
    ts: int
    nonce: str
    sig: str


class CommandsResponse(BaseModel):
    commands: list[Command]


# --- 6.4 Incident state machine ----------------------------------------------


class IncidentState(str, Enum):
    IDLE = "IDLE"
    CHECKING = "CHECKING"
    ALERTING = "ALERTING"
    RESPONDING = "RESPONDING"
    UNLOCKED = "UNLOCKED"
    CANCELLED = "CANCELLED"
    RESOLVED = "RESOLVED"


# --- 6.5 Triage record -------------------------------------------------------


class TriageRecord(BaseModel):
    # None means "the person did not say". Never fill these with a guess.
    conscious: Optional[bool] = None
    head_strike: Optional[bool] = None
    pain_location: Optional[str] = None
    can_move: Optional[bool] = None
    bleeding: Optional[bool] = None
    on_blood_thinner: Optional[bool] = None  # from the patient profile, never the model
    summary: str = ""
    next_question: Optional[str] = None
    done: bool = False


# Fields the operator may correct. on_blood_thinner comes from the profile; summary,
# next_question and done follow from the record and the fixed script.
CorrectableField = Literal["conscious", "head_strike", "pain_location", "can_move", "bleeding"]


class TriageCorrection(BaseModel):
    """POST /triage/correct: the operator sets one field of the record in use.

    Yes/no fields take true, false or null (unknown). pain_location takes a short string;
    empty or null means unknown."""

    model_config = ConfigDict(extra="forbid")
    field: CorrectableField
    value: Union[StrictBool, StrictStr, None]

    @model_validator(mode="after")
    def _type_matches_field(self) -> "TriageCorrection":
        if self.field == "pain_location":
            if isinstance(self.value, bool):
                raise ValueError("pain_location takes a string or null")
            if isinstance(self.value, str):
                v = " ".join(self.value.split())
                if len(v) > 40:
                    raise ValueError("pain_location is at most 40 characters")
                self.value = v or None
        elif isinstance(self.value, str):
            raise ValueError(f"{self.field} takes true, false or null")
        return self


# --- 6.6 Caregiver messages --------------------------------------------------


class InboundMessage(BaseModel):
    """A caregiver reply, from the comms-gateway or the simulator page (same endpoint).

    The gateway forwards Photon received-messages as
    ``{from, body, platform, spaceId, senderId, messageId}``; the simulator page
    posts ``{sender, text}``. Both name pairs are accepted and the extra gateway
    fields are ignored.
    """

    sender: str = Field(validation_alias=AliasChoices("sender", "from"))
    text: str = Field(validation_alias=AliasChoices("text", "body"))


class UnlockResult(BaseModel):
    allowed: bool
    reason: str
    sender: str
    incident_id: Optional[str] = None
    ts: int


# --- 6.7 Dashboard live events -----------------------------------------------

DashboardEventType = Literal[
    "incident_update", "triage_update", "message_in", "message_out", "unlock_attempt", "device_status",
    "question_played", "speech_heard", "voice_line",
    "script_done",  # the fixed questions are over; the dashboard starts the companion agent
    # issue #23: honest delivery states, who is coming, voice escalation
    "delivery_status", "responder_update", "call_update",
    "stepfun_call",  # issue #24: one per step-5-preview call (triage, rewrite, answer), with latency and outcome
]

# What a finished StepFun call came to. "invalid": bad JSON, a record that failed validation, or a
# rewrite/answer that broke a rule (too long, added numbers). "error": HTTP or network failure.
StepFunOutcome = Literal["ok", "timeout", "invalid", "error"]


class StepFunCall(BaseModel):
    """stepfun_call: one runtime call. Never carries the prompt, the reply text or a phone number."""

    kind: Literal["triage", "rewrite", "answer"]
    model: str
    ms: int
    outcome: StepFunOutcome
    fallback: Optional[str] = None  # what was used instead: "rules", "original text", "template"
    reason: Optional[str] = None
    deadline_s: float
    incident_id: Optional[str] = None


class StepFunTriageView(BaseModel):
    """triage_update.model: step-5-preview's own triage record, next to the rules record.

    status: "off" (rules only), "waiting" (no answer yet), "pending" (a call is running),
    or the last finished call's outcome. record/ms/answers_seen describe that last finished
    call; while pending they still show it. added: fields where the record in use differs from
    the rules (the model added them; never a field the operator corrected). overruled: fields where the model said something the
    record in use does not (the rules kept their answer)."""

    name: Optional[str] = None
    status: Literal["off", "waiting", "pending", "ok", "timeout", "invalid", "error"]
    ms: Optional[int] = None
    deadline_s: Optional[float] = None
    reason: Optional[str] = None
    record: Optional[dict] = None
    answers_seen: int = 0
    added: list[str] = Field(default_factory=list)
    overruled: list[str] = Field(default_factory=list)


class DashboardEvent(BaseModel):
    type: DashboardEventType
    data: dict
