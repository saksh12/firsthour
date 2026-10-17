"""Pydantic schemas for every contract in IMPLEMENTATION.md section 6.

If you change a model here, change the contract there in the same commit.
All timestamps are UTC epoch milliseconds.
"""

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

# --- 6.2 Edge to backend -----------------------------------------------------

EdgeEventType = Literal[
    "fall_suspected", "heartbeat", "help_button", "local_unlock", "cancelled"
]


class SensorReading(BaseModel):
    sound_peak: Optional[int] = None
    accel_delta_g: Optional[float] = None
    dist_cm: Optional[int] = None


class EdgeEvent(BaseModel):
    device_id: str
    ts: int
    type: EdgeEventType
    sensors: SensorReading = Field(default_factory=SensorReading)
    local_incident_id: Optional[str] = None


# --- 6.3 Backend to edge -----------------------------------------------------

CommandAction = Literal["unlock", "lock", "alarm_on", "alarm_off", "display"]


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


# --- 6.6 Caregiver messages --------------------------------------------------


class InboundMessage(BaseModel):
    """A caregiver reply, from Photon or the simulator page (same endpoint)."""

    sender: str
    text: str


class UnlockResult(BaseModel):
    allowed: bool
    reason: str
    sender: str
    incident_id: Optional[str] = None
    ts: int


# --- 6.7 Dashboard live events -----------------------------------------------

DashboardEventType = Literal[
    "incident_update", "triage_update", "message_in", "message_out", "unlock_attempt", "device_status"
]


class DashboardEvent(BaseModel):
    type: DashboardEventType
    data: dict
