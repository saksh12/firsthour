# FirstHour

A camera-free fall-response node for a bathroom. A Raspberry Pi (QNX) and an Arduino hear a fall, check on the person by voice, text a caregiver a triage card, and let help in through a guarded door unlock. It stays safe with the network gone.

- [`AGENTS.md`](AGENTS.md): rules and contracts (read first)
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md): full build plan
- [`TODOS.md`](TODOS.md): task board
- [`docs/mission-control.html`](docs/mission-control.html): interactive concept of the demo (simulated data). Share it with `scripts/share_concept.sh`, which prints a temporary public link

## Status

Last updated Sat Oct 17, 5:25 PM.

| Status | Meaning |
| --- | --- |
| ✅ | Done and on `main` |
| 🔶 | Code exists, never run with real keys or hardware |
| ⬜ | Not started |

### Demo moments

| Moment | What judges see | Status | What's missing |
| --- | --- | --- | --- |
| M1 The thud | Clock starts, sensors spike | 🔶 Works with the fake Arduino | Arduino sketch, real sensors, calibration |
| M2 The voice | "Are you okay?" spoken, triage follows | ⬜ | No audio plays yet (ElevenLabs), no speech input (typed box only) |
| M3 The text | Caregiver phone gets the triage card | ✅ Simulator page · 🔶 real Photon | Caregiver texts the agent line once, then a live test |
| M4 The door | `UNLOCK <code>` opens the latch, a stranger is refused | ✅ Unlock rules, signing, door shows the Pi's confirmation | Arduino sketch (simulated latch, no servo) |
| M5 Offline | Wi-Fi off: alarm and clock keep going | 🔶 Covered by tests | Real hardware run |

### By part

| Part | What it does | Status | Notes |
| --- | --- | --- | --- |
| `backend/` state machine | `IDLE → CHECKING → ALERTING → RESPONDING → UNLOCKED → RESOLVED` | ✅ | Escalation never waits on StepFun |
| `backend/` unlock rules | Allowlist, one-time code, 3 tries, signed commands | ✅ | Full test suite |
| `backend/` triage | Rule-based answers → triage record | ✅ | Never invents a "no" the person didn't say |
| `backend/` StepFun | `step-5-preview` triage + reply rewrite, rule fallback | 🔶 Key works | Measured 5-7 s per call, so the 3 s timeout always falls back; timeout decision pending |
| `backend/` Sentry | One trace per incident, logs, uptime `/health` | ✅ Traces and logs arrive | Uptime monitor on a tunnel still to set up |
| `backend/` voice | Speak the fixed questions and caregiver replies | ⬜ | ElevenLabs key and stock voice work; nothing plays audio yet |
| `edge/` sensor node | Two-sensor fusion, local alarm + clock, offline log + replay, verifies commands | ✅ | Obeys only its own incident |
| `edge/` on the Pi | Same code on QNX over USB serial | ⬜ | Gate 1 at 7:00 PM: QNX or Linux |
| `arduino/hub.ino` | Sensors, simulated latch (LED), buzzer, display | ⬜ | Need accelerometer chip and display type |
| `comms-gateway/` | Photon 2.0: alert out, caregiver reply in | ✅ stub · 🔶 live path | Keys work; caregiver texts the line once first |
| `dashboard/` | Live incident page: state, clock, triage + transcript, unlock log | ✅ | Door shows the Pi's confirmation |
| Caregiver simulator | Stand-in phone for the demo | ✅ | `dashboard/caregiver-simulator.html` |
| `scripts/simulate_fall.py` | Whole story end to end on stubs | ✅ | Passes |
| `scripts/demo_reset.sh` | One-command reset between runs | ⬜ | |
| Calibration | Thresholds from ~20 trials per action | ⬜ | Tool in review (#15); placeholder numbers in `edge/config.py` |
| Handoff sheet page | Paramedic summary | ⬜ | |
| Docs | `docs/sentry-story.md` started; demo script, Devpost draft | 🔶 / ⬜ | |
| Paper or Base44 | Pick one sponsor for the dashboard | ⬜ | Decision was due 3:00 PM |

### Decided

- **No NFC reader and no servo** at the event. NFC is dropped. The latch is simulated: the Pi verifies the signed command and tells the Arduino to open; the Mac shows the door only after the Pi confirms.

### Keys in `.env` (checked with real calls)

| Key | Status |
| --- | --- |
| `SHARED_SECRET` | ✅ Generated (same value goes on the Pi) |
| `SENTRY_DSN` | ✅ Traces and logs arrive |
| `STEPFUN_API_KEY`, `STEPFUN_BASE_URL` | ✅ Key is on the `.ai` host |
| `PHOTON_TOKEN`, `PHOTON_PROJECT_ID`, `PHOTON_AGENT_HANDLE` | ✅ Read-only call works |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | ✅ Free tier (no cloning); stock voice "Sarah" |
| `CAREGIVER_NUMBERS` | ✅ Real phone; it must text the agent line once |
| `SERIAL_PORT` | ⬜ When the Arduino arrives |

## Run it locally (no hardware)

All from the repo root, one terminal each:

```bash
cp .env.example .env                                    # set SHARED_SECRET at least
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8000   # 0.0.0.0 so the Pi can reach it
python3 -m http.server 5173 -d dashboard                # dashboard at http://localhost:5173
python3 scripts/simulate_fall.py                        # whole story on stubs
python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin   # edge node, type f + Enter for a fall
(cd backend && .venv/bin/python -m pytest -q)           # tests
```
