"""FastAPI skeleton: /health, the edge contract, and caregiver inbound.

TODO(phase 1): the incident state machine (incident.py), triage, voice, comms and the
dashboard WebSocket plug in here. For now a fall_suspected event opens the unlock window
directly.
"""

import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .models import CommandsResponse, EdgeEvent, InboundMessage
from .security import make_command, verify_event
from .unlock import UnlockGuard, is_unlock_attempt

log = logging.getLogger("firsthour.api")

HEARTBEAT_STALE_S = 30
DEFAULT_SECRET = "change-me-before-demo"


def load_settings() -> dict:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    return {
        "STUB_EDGE": os.getenv("STUB_EDGE", "1") == "1",
        "SHARED_SECRET": os.environ.get("SHARED_SECRET", ""),
        "CAREGIVER_NUMBERS": os.getenv("CAREGIVER_NUMBERS", ""),
        "INCIDENT_WINDOW_SECONDS": int(os.getenv("INCIDENT_WINDOW_SECONDS", "600")),
    }


def create_app(settings: Optional[dict] = None) -> FastAPI:
    s = settings or load_settings()
    secret = s["SHARED_SECRET"]
    if not secret:
        raise RuntimeError("SHARED_SECRET is not set; copy .env.example to .env")
    if secret == DEFAULT_SECRET:
        log.warning("SHARED_SECRET is still the example value; change it before the demo")

    app = FastAPI(title="FirstHour backend")
    queues: dict[str, list] = defaultdict(list)
    last_heartbeat: dict[str, float] = {}
    state = {"device_id": None}

    def queue_unlock(incident_id: str) -> None:
        device = state["device_id"]
        if device is None:
            log.error("unlock granted but no device known incident_id=%s", incident_id)
            return
        cmd = make_command(secret, "unlock", incident_id)
        queues[device].append(cmd)
        log.info("command queued action=unlock incident_id=%s device_id=%s id=%s", incident_id, device, cmd.id)

    guard = UnlockGuard(
        allowed=s["CAREGIVER_NUMBERS"],
        window_s=s["INCIDENT_WINDOW_SECONDS"],
        on_grant=queue_unlock,
    )
    app.state.guard = guard
    app.state.queues = queues
    app.state.alert_code = None  # read by comms.py when it formats the alert; never returned by an API

    @app.get("/health")
    def health():
        now = time.time()
        devices = {d: round(now - t, 1) for d, t in last_heartbeat.items()}
        if s["STUB_EDGE"]:
            return {"ok": True, "edge": "stubbed", "heartbeat_age_s": devices}
        alive = any(age <= HEARTBEAT_STALE_S for age in devices.values())
        body = {"ok": alive, "edge": "live", "heartbeat_age_s": devices}
        return JSONResponse(body, status_code=200 if alive else 503)

    @app.post("/edge/event")
    async def edge_event(request: Request):
        raw = await request.body()
        ok, reason = verify_event(secret, raw, request.headers.get("X-Signature"))
        if not ok:
            log.warning("edge event rejected reason=%s", reason)
            raise HTTPException(status_code=401, detail=reason)
        event = EdgeEvent.model_validate_json(raw)
        last_heartbeat[event.device_id] = time.time()
        log.info("edge event type=%s device_id=%s local_incident_id=%s",
                 event.type, event.device_id, event.local_incident_id)

        incident_id = guard.active_incident_id
        if event.type == "fall_suspected" and incident_id is None:
            incident_id = event.local_incident_id or f"{event.device_id}-{event.ts}"
            state["device_id"] = event.device_id
            app.state.alert_code = guard.open_incident(incident_id)
        elif event.type == "cancelled":
            guard.close_incident()
            app.state.alert_code = None
            incident_id = None
        return {"ok": True, "incident_id": incident_id}

    @app.get("/edge/commands", response_model=CommandsResponse)
    def edge_commands(device_id: str):
        # Commands are signed and single-use, so handing them out once is enough.
        pending, queues[device_id] = queues[device_id], []
        return CommandsResponse(commands=pending)

    @app.post("/comms/inbound")
    def comms_inbound(msg: InboundMessage):
        """Caregiver reply from Photon or the simulator page."""
        if is_unlock_attempt(msg.text):
            result = guard.try_unlock(msg.sender, msg.text)
            return {"type": "unlock_attempt", "allowed": result.allowed, "reason": result.reason}
        # TODO(phase 1, C): rewrite to one calm sentence (triage.py) and read it aloud (voice.py).
        log.info("caregiver message sender=%s incident_id=%s", msg.sender, guard.active_incident_id)
        return {"type": "message", "queued_for_voice": False}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = create_app()
