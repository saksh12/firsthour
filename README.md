# FirstHour

A camera-free fall-response node for a bathroom. A Raspberry Pi (QNX) and an Arduino hear a fall, check on the person by voice, text a caregiver a triage card, and let help in through a guarded door unlock. It stays safe with the network gone.

- [`AGENTS.md`](AGENTS.md): rules and contracts (read first)
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md): full build plan
- [`TODOS.md`](TODOS.md): task board

## Status

Last updated Sat Oct 17, 8:20 PM.

| Status | Meaning |
| --- | --- |
| ✅ | Done and on `main` |
| 🔶 | Code exists, never run with real keys or hardware |
| ⬜ | Not started |

### Demo moments

| Moment | What judges see | Status | What's missing |
| --- | --- | --- | --- |
| M1 The thud | Clock starts, sensors spike | 🔶 Works with the fake Arduino | Arduino sketch, real sensors, calibration |
| M2 The voice | "Are you okay?" spoken, triage follows | 🔶 Audio pipeline works · 🔶 speech input | Says "This is an automated assistant" first; the 8 s silence timer starts when the question has finished playing (checked with macOS `say`). Needs a run with the real ElevenLabs key. Speech input (Chrome push-to-talk + optional Auto-listen) is on the dashboard, checked only with a stubbed recognizer: never run with a real microphone yet. **Keep Auto-listen off** until it starts after the question finishes playing (it can hear the laptop say "…I'm fine…"). Typed box stays as the fallback |
| M3 The text | Caregiver phone gets the triage card | ✅ Simulator page · 🔶 real Photon | Caregiver texts the agent line once, then a live test |
| M4 The door | `UNLOCK <code>` opens the latch, a stranger is refused | ✅ Unlock rules, signing, door shows the Pi's confirmation | Arduino sketch (simulated latch, no servo) |
| M5 Offline | Wi-Fi off: alarm and clock keep going | 🔶 Covered by tests | Real hardware run |

### By part

| Part | What it does | Status | Notes |
| --- | --- | --- | --- |
| `backend/` state machine | `IDLE → CHECKING → ALERTING → RESPONDING → UNLOCKED → RESOLVED` | ✅ | Escalation never waits on StepFun |
| `backend/` unlock rules | Allowlist, one-time code, 3 tries, signed commands | ✅ | Full test suite |
| `backend/` triage | Rule-based answers → triage record | ✅ | Never invents a "no" the person didn't say |
| `backend/` StepFun | `step-5-preview` triage + reply rewrite, rule fallback | 🔶 Key works | Triage runs in the background (15 s, one call at a time per incident) after the instant rule record; the reply rewrite has its own pool and a 3 s deadline |
| `backend/` Sentry | One trace per incident, logs, uptime `/health` | ✅ Traces and logs arrive | Uptime monitor on a tunnel still to set up |
| `backend/` voice | Speak the fixed questions and caregiver replies | 🔶 | Cached ElevenLabs TTS + one playback worker; afplay→say→log fallback; reports each finished line (silence timer) and drops leftover lines when an incident ends; real-key run pending |
| `edge/` sensor node | Two-sensor fusion, local alarm + clock, offline log + replay, verifies commands | ✅ | Obeys only its own incident |
| `edge/` on the Pi | Same code on QNX over USB serial | ⬜ | Pi 5. QNX's sources say: `whisper.cpp`/`onnxruntime` prebuilt, USB audio only, no HAM, and Arduino USB-serial may not auto-load (test `ls /dev/ser*` first). Details in #22 |
| `arduino/hub.ino` | Sensors, simulated latch (LED), buzzer, display | ⬜ | Need accelerometer chip and display type |
| `comms-gateway/` | Photon 2.0: alert out, caregiver reply in | ✅ stub · 🔶 live path | Keys work; caregiver texts the line once first (a blocked alert is resent on their first text). Gateway replies are HMAC-signed with replay protection (#14) |
| Phone calls | Photon SMS number → ElevenLabs agent over SIP | 🔶 | Test calls in and out passed; the backend doesn't place the escalation call yet (#23) |
| `dashboard/` | Live incident page: state, clock, triage + transcript, unlock log | ✅ | Door shows the Pi's confirmation |
| Speech input | The person answers out loud (Chrome Web Speech API on the dashboard) | 🔶 | Hold the mic button or Space; optional Auto-listen (3 s after each question, gives up after 8 s). Posts to `/person/answer` like the typed box. Verified with a stubbed recognizer only; real-mic test on the demo laptop pending |
| Caregiver simulator | Stand-in phone for the demo | ✅ | `dashboard/caregiver-simulator.html` |
| `scripts/simulate_fall.py` | Whole story end to end on stubs | ✅ | Passes; `--silent` prints how long escalation took |
| `scripts/demo_reset.sh` | One-command reset between runs | ✅ | Backend to IDLE + signed `reset` to the Pi; 3 back-to-back `simulate_fall.py` runs pass |
| Calibration | Thresholds from ~20 trials per action | ⬜ | `edge/calibrate.py` record + analyze (#15); placeholder numbers in `edge/config.py` until real trials |
| Handoff sheet page | Paramedic summary | ⬜ | |
| Docs | `docs/sentry-story.md` started; demo script, Devpost draft | 🔶 / ⬜ | |
| Paper or Base44 | Pick one sponsor for the dashboard | ⬜ | Decision was due 3:00 PM |

### Decided

- **Speech input is the browser's Web Speech API** in Chrome on the laptop (`dashboard/index.html`). Privacy: Chrome's speech recognition sends audio to Google's service; nothing is recorded or stored by FirstHour. Other browsers show "Speech input needs Chrome" and keep the typed box.

- **No NFC reader and no servo** at the event. NFC is dropped. The latch is simulated: the Pi verifies the signed command and tells the Arduino to open; the Mac shows the door only after the Pi confirms.

### Changed in #25: what you need to do

#25 fixed the review bugs in #21. Four of the changes affect how you work:

- **Pi: pull before the next hardware test.** Command polls are now signed (`GET /edge/commands?device_id=…&ts=…` with `X-Signature`). An old `sensor_node.py` gets 401 on every poll and never receives a command. The Pi's clock must be within 10 s of the laptop's: compare `date -u` on both. If the edge log says `command poll rejected status=401`, the clock is off or `SHARED_SECRET` differs.
- **Arduino: answer `ACK,U` to `U`.** The sketch in IMPLEMENTATION.md 8.2 already does. The Pi reports the door open only after that ACK. With no ACK within 2 s (`UNLOCK_ACK_TIMEOUT_SECONDS`) it reports `unlock_failed`, and the dashboard door says "Unlock failed". On the laptop, `sensor_node.py --stdin` fakes the ACK; add `--no-ack` to see the failure.
- **Dashboard: buttons work only on the laptop.** Resolve, the typed answer box and the caregiver simulator (stub mode) are refused (403) from any other device; a phone can still watch. Through the Sentry tunnel only `/health` answers. Open the pages on the laptop at `http://localhost:5173`.
- **Between demo runs: `bash scripts/demo_reset.sh`.** It puts the backend back in IDLE and sends the Pi a signed reset (latch locked, alarm off, fall cooldown cleared, `READY`). The Pi obeys it only with no local incident open, so the backend first sends `alarm_off` for the open incident.

Other changes:
- The unlock code works once.
- The local alarm no longer gives up after 10 minutes; only unlocks expire.
- The Pi's network calls no longer stall the sensors.
- A caregiver reply survives a gateway forward failing once.
- StepFun runs one triage call at a time.

### Changed in #27 and #29

- The voice says "This is an automated assistant." before "Are you okay?".
- The 8 s silence timer starts when the question has finished playing, so with sound on, silence escalates about 15 s after the fall. The backend log line `silence timer: …` shows the split.
- Lines still queued when an incident ends are dropped.
- If Photon refuses an alert because the caregiver hasn't texted the line yet, the dashboard says so and the alert goes out on their first text.
- `pytest` no longer sends issues to the real Sentry project. If you got Sentry emails from test runs, that was the cause.

### Keys in `.env` (checked with real calls)

| Key | Status |
| --- | --- |
| `SHARED_SECRET` | ✅ Generated (same value goes on the Pi) |
| `SENTRY_DSN` | ✅ Traces and logs arrive |
| `STEPFUN_API_KEY`, `STEPFUN_BASE_URL` | ✅ Key is on the `.ai` host |
| `PHOTON_TOKEN`, `PHOTON_PROJECT_ID`, `PHOTON_AGENT_HANDLE` | ✅ Read-only call works |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | ✅ Free tier (no cloning); stock voice "Sarah" |
| `CAREGIVER_NUMBERS` | ✅ Real phone; it must text the agent line once |
| `PHOTON_SMS_NUMBER`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` | ✅ Test calls both ways passed (Anmol's `.env`; added to `.env.example` with the code that reads them) |
| `PHOTON_SIP_OUTBOUND_PASSWORD` | ⚠️ Rotate it: it appeared in a setup tool log |
| `SERIAL_PORT` | ⬜ When the Arduino arrives |

## Run it locally (no hardware)

All from the repo root, one terminal each:

```bash
cp .env.example .env                                    # set SHARED_SECRET at least
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8000   # 0.0.0.0 so the Pi can reach it
python3 -m http.server 5173 -d dashboard                # dashboard at http://localhost:5173
python3 scripts/simulate_fall.py                        # whole story on stubs
python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin   # edge node, type f + Enter for a fall (--no-ack: dead latch)
bash scripts/demo_reset.sh                              # between demo runs
(cd backend && .venv/bin/python -m pytest -q)           # tests
```
