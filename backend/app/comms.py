"""Caregiver alert formatting (IMPLEMENTATION.md section 6.6).

Owner: C (Comms). Only the alert template lives here so far.
TODO(phase 1/3, C): send through comms-gateway when STUB_PHOTON=0.
"""

from .models import TriageRecord
from .triage import summarize


def format_down(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def format_alert(profile: dict, down_s: float, triage: TriageRecord) -> str:
    """The first text goes to every caregiver, so it carries no door code: the code is
    texted only to a caregiver who says they're coming (coordinator.claimer_reply)."""
    lines = [
        f"FirstHour: {profile['name']} has had a fall in the {profile['room'].lower()}. "
        f"Down {format_down(down_s)}.",
        summarize(triage, include_meds=False),
    ]
    if profile.get("on_blood_thinner"):
        lines.append("NOTE: on a blood thinner.")
    lines.append("How soon can you be here? Reply with an ETA like \"10 min\" or ON MY WAY, "
                 "and we'll text you the door code.")
    lines.append(f"Or ask a question, or send a message and we'll read it to {profile.get('pronoun_object', 'them')}.")
    return "\n".join(lines)
