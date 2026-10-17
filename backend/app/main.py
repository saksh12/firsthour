"""FastAPI app: /health, the edge contract, the person's answers, and caregiver inbound.

TODO(phase 1): dashboard WebSocket (section 6.7) streams `app.state.events`;
voice.py replaces `speak`; comms-gateway replaces the stub outbox.
"""

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .incident import IncidentMachine
from .models import CommandsResponse, DashboardEvent, EdgeEvent, InboundMessage
from .profile import DEMO_PROFILE
from .security import make_command, verify_event
from .unlock import UnlockGuard

log = logging.getLogger("firsthour.api")

HEARTBEAT_STALE_S = 30
TICK_S = 0.25
DEFAULT_SECRET = "change-me-before-demo"


class PersonAnswer(BaseModel):
    text: str


def load_settings() -> dict:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    return {
        "STUB_EDGE": os.getenv("STUB_EDGE", "1") == "1",
        "STUB_PHOTON": os.getenv("STUB_PHOTON", "1") == "1",
        "SHARED_SECRET": os.environ.get("SHARED_SECRET", ""),
        "CAREGIVER_NUMBERS": os.getenv("CAREGIVER_NUMBERS", ""),
        "INCIDENT_WINDOW_SECONDS": int(os.getenv("INCIDENT_WINDOW_SECONDS", "600")),
        "UNLOCK_HOLD_SECONDS": int(os.getenv("UNLOCK_HOLD_SECONDS", "30")),
    }


def create_app(settings: Optional[dict] = None) -> FastAPI:
    s = {"STUB_PHOTON": True, "UNLOCK_HOLD_SECONDS": 30, **(settings or load_settings())}
    secret = s["SHARED_SECRET"]
    if not secret:
        raise RuntimeError("SHARED_SECRET is not set; copy .env.example to .env")
    if secret == DEFAULT_SECRET:
        log.warning("SHARED_SECRET is still the example value; change it before the demo")

    queues: dict[str, list] = defaultdict(list)
    last_heartbeat: dict[str, float] = {}
    events: deque = deque(maxlen=500)
    outbox: list[dict] = []
    spoken: deque = deque(maxlen=100)

    def emit(type_: str, data: dict) -> None:
        events.append(DashboardEvent(type=type_, data=data))

    def speak(text: str) -> None:
        # TODO(C): voice.py plays cached ElevenLabs audio here.
        spoken.append(text)
        log.info("say %r", text)

    def send_alert(incident_id: str, text: str) -> None:
        to = sorted(guard.allowed)
        if s["STUB_PHOTON"]:
            outbox.append({"to": to, "incident_id": incident_id, "text": text, "ts": int(time.time() * 1000)})
        else:
            # TODO(C): POST to comms-gateway. Until then the simulator page is the path.
            log.error("STUB_PHOTON=0 but comms-gateway is not wired yet incident_id=%s", incident_id)
        # The dashboard gets that an alert went out, not its text: the text carries the code.
        emit("message_out", {"to": to, "incident_id": incident_id, "kind": "alert"})
        log.info("alert sent incident_id=%s to=%s stub=%s", incident_id, to, s["STUB_PHOTON"])

    def queue_unlock(incident_id: str) -> None:
        inc = machine.incident
        if not inc or inc.id != incident_id:
            log.error("unlock granted but incident not open in machine incident_id=%s", incident_id)
            return
        cmd = make_command(secret, "unlock", incident_id)
        queues[inc.device_id].append(cmd)
        log.info("command queued action=unlock incident_id=%s device_id=%s id=%s",
                 incident_id, inc.device_id, cmd.id)

    guard = UnlockGuard(allowed=s["CAREGIVER_NUMBERS"], window_s=s["INCIDENT_WINDOW_SECONDS"],
                        on_grant=queue_unlock)
    machine = IncidentMachine(guard, DEMO_PROFILE, hold_s=s["UNLOCK_HOLD_SECONDS"],
                              emit=emit, speak=speak, send_alert=send_alert)

    @asynccontextmanager
    async def lifespan(_app):
        async def ticker():
            while True:
                try:
                    machine.tick()
                except Exception:  # never let the loop die mid-incident
                    log.exception("tick failed")
                await asyncio.sleep(TICK_S)

        task = asyncio.create_task(ticker())
        yield
        task.cancel()

    app = FastAPI(title="FirstHour backend", lifespan=lifespan)
    app.state.guard, app.state.machine = guard, machine
    app.state.queues, app.state.events, app.state.outbox, app.state.spoken = queues, events, outbox, spoken

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
        if event.type != "heartbeat":
            log.info("edge event type=%s device_id=%s local_incident_id=%s",
                     event.type, event.device_id, event.local_incident_id)
            emit("device_status", {"device_id": event.device_id, "event": event.type})

        if event.type == "fall_suspected":
            machine.on_fall(event.device_id, event.local_incident_id or f"{event.device_id}-{event.ts}")
        elif event.type == "help_button":
            machine.on_help_button()
        elif event.type == "cancelled":
            machine.cancel("cancelled on device")
        # nfc_tap / local_unlock: the Pi already decided; we only log and show them.
        inc = machine.incident
        return {"ok": True, "incident_id": inc.id if inc else None, "state": machine.state.value}

    @app.get("/edge/commands", response_model=CommandsResponse)
    def edge_commands(device_id: str):
        # Commands are signed and single-use, so handing them out once is enough.
        pending, queues[device_id] = queues[device_id], []
        return CommandsResponse(commands=pending)

    @app.post("/person/answer")
    def person_answer(ans: PersonAnswer):
        """What the person on the floor said (speech-to-text, or the operator's typed fallback)."""
        machine.on_answer(ans.text)
        return machine.snapshot()

    @app.post("/comms/inbound")
    def comms_inbound(msg: InboundMessage):
        """Caregiver reply from Photon or the simulator page (same endpoint)."""
        return machine.caregiver_reply(msg.sender, msg.text)

    @app.get("/incident")
    def incident():
        return machine.snapshot()

    @app.post("/incident/resolve")
    def resolve():
        return {"resolved": machine.resolve("operator"), **machine.snapshot()}

    @app.get("/comms/stub/outbox")
    def stub_outbox():
        """The simulated caregiver phone's inbox. Exists only while STUB_PHOTON=1:
        with real Photon, the alert (and its code) only ever reaches the enrolled phone."""
        if not s["STUB_PHOTON"]:
            raise HTTPException(status_code=404)
        return {"messages": outbox[-20:]}

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
app = create_app()
