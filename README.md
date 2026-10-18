# FirstHour

A camera-free fall-response node for a bathroom. A Raspberry Pi (QNX) and an ESP32-S3-BOX-3 sensor hub hear a fall, check on the person by voice, text a caregiver a triage card, and let help in through a guarded door unlock. It stays safe with the network gone.

- [`AGENTS.md`](AGENTS.md): rules and contracts (read first)
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md): full build plan
- [`TODOS.md`](TODOS.md): task board

## Status

Last updated Sun Oct 18, 3:30 AM PDT (after #37).

| Status | Meaning |
| --- | --- |
| ✅ | Done and on `main` |
| ✅ live | Also run on the real hardware (QNX Pi 5 + ESP32-S3-BOX-3) or with real keys |
| 🔶 | Code exists, never run with real keys or hardware |
| ⬜ | Not started |

The sensor hub is an **ESP32-S3-BOX-3**, not an Arduino: `arduino/hub/hub.ino` runs on it, and the Pi reads it over raw USB (`sensor_node.py --usb`, pyusb + libusb, QNX has no USB-serial driver). Mac, Pi and BOX-3 connection steps are in [`connect.md`](connect.md); the latest handoff notes are in [`HANDOFF.md`](HANDOFF.md).

### Demo moments

| Moment | What judges see | Status | What's missing |
| --- | --- | --- | --- |
| M1 The thud | Clock starts, sensors spike | ✅ live on the BOX-3 | `TIME DOWN mm:ss` on the BOX-3 screen and an audible alarm, verified by the operator. Thresholds are **provisional**, from 19 bottle drops on one table (`edge/calibration/`); non-fall checks and the full calibration set are still to do |
| M2 The voice | "Are you okay?" spoken, triage follows | ✅ live | Real ElevenLabs stock voice plays every fixed line. The person answers out loud: AirPods on the laptop record each answer, and the Pi transcribes it with `whisper.cpp` (QNX AI module, `PI_STT=1`). Live runs on the real Pi passed the help path and the "I'm fine" cancel. The silence timer starts after the question finishes playing. Typed box stays as the fallback |
| M3 The text | Caregiver phone gets the triage card | ✅ Simulator page · ✅ real Photon | Live Oct 18: alert delivered, a real reply reached the backend 3.5 s after the phone sent it, the door-code text followed 0.3 s later. Each caregiver gets their own shared Photon line, so they text the agent line once first (PR #39) |
| M4 The door | `UNLOCK <code>` opens the latch, a stranger is refused | ✅ live on the BOX-3 | On the real Pi: a stranger with the right code was refused, the caregiver was accepted, the Pi verified the signed command, the BOX-3 answered `ACK,U`, showed `LATCH OPEN` and relocked by itself. Run with the simulator page, not real Photon |
| M5 Offline | Wi-Fi off: alarm and clock keep going | ✅ live (backend paused) | With the backend paused, the BOX-3 kept the alarm and clock going and the latch locked; the incident replayed on reconnect. Wi-Fi itself has not been unplugged yet |

### By part

| Part | What it does | Status | Notes |
| --- | --- | --- | --- |
| `backend/` state machine | `IDLE → CHECKING → ALERTING → RESPONDING → UNLOCKED → RESOLVED` | ✅ | Escalation never waits on StepFun |
| `backend/` unlock rules | Allowlist, one-time code, 3 tries, signed commands | ✅ live | Full test suite; checked on the real Pi (M4) |
| `backend/` triage | Rule-based answers → triage record | ✅ | Never invents a "no" the person didn't say. Dashboard shows "Unclear" for an answer the rules couldn't read, "Not said" for no answer. Hedged amounts like "a little, not very much" are not read yet |
| `backend/` StepFun | `step-5-preview` triage + reply rewrite, rule fallback | ✅ live | Runs in the background (45 s, `max_tokens` 5000, `reasoning_effort` low) after the instant rule record. Rules always win; the model can only add a worrying answer (head strike, can't move, bleeding), never a reassuring one. Late results still add their worrying findings. Measured Oct 18: triage 5-38 s (longest on unclear answers); rewrite 2.3 s early on, 6-12 s by morning (deadline now 10 s, Q&A 12 s) |
| `backend/` Sentry | One trace per incident, logs, uptime `/health` | ✅ Traces and logs arrive | Uptime monitor on a tunnel still to set up |
| `backend/` voice | Speak the fixed questions and caregiver replies | ✅ live | Cached ElevenLabs TTS + one playback worker; afplay→say→log fallback; reports each finished line (silence timer) and drops leftover lines when an incident ends. Real stock voice played in three knock runs (`STUB_ELEVENLABS=0`) |
| `edge/` sensor node | Two-sensor fusion, local alarm + clock, offline log + replay, verifies commands | ✅ | Obeys only its own incident |
| `edge/` process split | `supervisor.py`: safety (QNX priority 21), uplink (10), whisper-server (8), restarted if they die | ✅ live | #22 step 1, PR #38. `pidin` on the Pi shows the priorities. `kill -9` of the uplink: back in 1.6-1.8 s, alarm untouched. `kill -9` of the safety process, idle and mid-incident: resumed the same incident and reclaimed the hub in 1.8-3.9 s. The single-process reader stays the default in `connect.md` |
| `edge/` on the Pi | Same code on QNX 8 (Pi 5), BOX-3 over raw USB | ✅ live | Runs detached from SSH (`connect.md`). Raw USB lives in a restartable child process: a BOX-3 reset or USB abort keeps the incident, clock and alarm (verified with SIGKILL and physical RST). SIGTERM stops everything cleanly |
| QNX AI module | `whisper.cpp` (oss.qnx.com apk, `ggml-base.en`) transcribes the person's answers on the Pi | ✅ live | `PI_STT=1`. Mic on the laptop (the Brio's USB mic fails in QNX `io-snd`); the Pi fetches each clip with a signed request, once. Resident `whisper-server` on loopback, below the reader's priority. Measured whisper 2.7-3.1 s per clip, upload to answer applied 3.8-4.3 s (model comparison in `edge/README.md`). The all-silent recording path is unit-tested only |
| `arduino/hub/hub.ino` (BOX-3) | IMU, mic peak, screen, alarm, simulated latch | ✅ live | `TIME DOWN`, `LATCH OPEN`/`LATCH LOCKED` with automatic relock, pulsed speaker alarm. No ultrasonic sensor (`distCm` is `-1`) |
| `comms-gateway/` | Photon 2.0: alert out, caregiver reply in | ✅ stub · 🔶 live path | Keys work; caregiver texts the line once first (a blocked alert is resent on their first text). Gateway replies are HMAC-signed with replay protection (#14). Coordinator work in PR #36 |
| Phone calls | Photon SMS number → ElevenLabs agent over SIP | ✅ live | The backend placed two real escalation calls Oct 18 (04:24, 04:58); the agent collected an ETA and refused to unlock. The post-call webhook carried the first one back into the thread; the second was lost to a flapping tunnel, so the backend now also polls ElevenLabs for the finished call. `scripts/voice_tunnel_setup.py` repoints the webhook and tool at a fresh tunnel |
| `dashboard/` | Live incident page: state, clock, triage + transcript, conversation, unlock log | ✅ live | Door shows the Pi's confirmation. Conversation panel shows each spoken line and what the Pi heard |
| Speech input | The person answers out loud | ✅ live (Pi) · 🔶 Chrome | With `PI_STT=1` the dashboard records after each question (8 s cap, stops early on quiet) and the Pi transcribes. Without it, Chrome Web Speech push-to-talk remains, checked only with a stubbed recognizer; keep its Auto-listen off (it can hear the laptop say "…I'm fine…") |
| Caregiver simulator | Stand-in phone for the demo | ✅ | `dashboard/caregiver-simulator.html` |
| `scripts/simulate_fall.py` | Whole story end to end on stubs | ✅ | Passes; `--silent` prints how long escalation took |
| `scripts/demo_reset.sh` | One-command reset between runs | ✅ live | Backend to IDLE + signed `reset` to the Pi (latch locked, alarm off, `READY`) |
| Calibration | Thresholds from ~20 trials per action | 🔶 Provisional | 19 bottle drops recorded on the BOX-3 (`edge/calibrate.py`); demo values live only in the Pi's `.env`. Non-fall trials and repo defaults still to do |
| Handoff sheet page | Paramedic summary | ⬜ | |
| Docs | `docs/sentry-story.md` started; demo script and Devpost draft in PR #31 | 🔶 | |
| Paper or Base44 | Pick one sponsor for the dashboard | ⬜ | Decision was due 3:00 PM |

Tests: `backend/.venv/bin/python -m pytest -q backend/tests edge/` gives **463 passed** (Oct 18, #38).

### Still open

- A full live voice round trip in one go (call, ETA on the call, code text, `UNLOCK`) on the demo laptop with the Pi attached.
- Non-fall checks and full calibration; unplugging Wi-Fi for M5; an all-silent Pi speech run.
- Sentry uptime monitor on the tunnel; handoff sheet page; Paper or Base44.
- Rotate `PHOTON_SIP_OUTBOUND_PASSWORD`.

### Decided

- **Speech input is transcribed on the Pi** by `whisper.cpp` (`PI_STT=1`, `ggml-base.en`). The laptop only records; clips stay in backend memory until the Pi fetches them once, and the words are never logged. Chrome Web Speech remains as the fallback when `PI_STT=0` (Chrome sends that audio to Google's service).
- **The question script stays fixed.** Real-time conversation is for the caregiver (ElevenLabs agent over Photon, #23), not the person on the floor.
- **No NFC reader and no servo** at the event. NFC is dropped. The latch is simulated on the BOX-3 screen: the Pi verifies the signed command and tells the hub to open; the Mac shows the door only after the Pi confirms.
- **Start demo runs with a knock**, not BOOT: BOOT sends `help_button`, which skips the first question.

### Changed in #32 to #37

- **#32-#33:** BOX-3 firmware: sensors, screen (`TIME DOWN`, latch status), pulsed alarm, simulated latch with automatic relock. The Pi reads it over raw USB.
- **#34:** BOX-3 calibration recordings (19 bottle drops) and `edge/calibrate.py` USB recording.
- **#35:** USB isolated in a restartable child process, so a BOX-3 reset can't take down the safety process.
- **#37:** QNX AI module (`whisper.cpp` on the Pi), live StepFun with the "worrying only" merge, live ElevenLabs, dashboard Conversation panel and "Unclear" label, SIGTERM stops the reader and whisper-server cleanly.
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
| `SERIAL_PORT` | Not needed: the BOX-3 is read over raw USB (`--usb`) |
| `PI_STT`, `WHISPER_MODEL` | ✅ `PI_STT=1` on the backend; `WHISPER_MODEL` set on the Pi at launch (`connect.md`) |

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
