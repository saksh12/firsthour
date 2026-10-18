# TODOS: FirstHour

Claim a task by putting your role (and name) on the line, e.g. `- [ ] (B · Sam) ...`. Tick it only when its acceptance check passes. Phases and acceptance tests are in `IMPLEMENTATION.md` section 7. Never delete someone else's item.

## Phase 0: Setup and verification (until 3:00 PM Sat)

- [ ] (All) Agree on roles; put names on Q1–Q10 in `IMPLEMENTATION.md` section 2
- [ ] (A) Collect hardware + craft supplies; record accelerometer chip, Grove shield (Q7)
- [ ] (A) QNX table: board image, flashing, `oss.qnx.com` AI module, audio capture (Q2). Answered from QNX's own sources for the **Pi 5** (see #22 comment): `whisper.cpp` and `onnxruntime` are prebuilt apks, audio is USB-only, Python 3.14, no HAM. Still to check on the board: Arduino shows up in `ls /dev/ser*`
- [x] (B) StepFun key works with one call; record the matching base URL (Q4): the `.ai` host
- [x] (B) FastAPI skeleton: `/health`, `models.py` contracts, `STUB_*` switches
- [x] (C) Photon table: outbound calls? sender number on inbound? credentials (Q3). Calls run on an **SMS number** (not iMessage); inbound carries `sentBy.handle`; keys work
- [x] (C) ElevenLabs key works with one TTS call; cloning / STT on our tier? (Q5): free tier, no cloning, stock voice
- [x] (D) Sentry project + DSN; read the challenge text
- [ ] (D) Pick Paper or Base44 (Q8)

**Done when:** `/health` OK, one StepFun and one ElevenLabs call work, Q2/Q3/Q4/Q7 answered.

## Phase 1: Vertical slice with stubs (3:00 to 6:00 PM)

- [x] (B) Unlock test suite (section 9.4) written first
- [x] (B) `incident.py` state machine
- [x] (B) `POST /edge/event`, `GET /edge/commands`
- [x] (B) `triage.py` rule-based fallback, then StepFun path
- [x] (B) `unlock.py` + `security.py` signing
- [x] (C) `voice.py` with cached fixed phrases (#19)
- [ ] (C) Speech input chosen (ElevenLabs STT → sponsor provider → Web Speech API): Web Speech push-to-talk in review (#28), needs a real-mic run in Chrome
- [x] (C) Caregiver phone simulator page
- [ ] (D) Live incident page: state badge, clock, triage card + transcript, event log, device status
- [x] (D) WebSocket events wired
- [x] (B) `scripts/simulate_fall.py`

**Done when:** the 6:00 PM test in section 7 passes end to end on stubs.

## Phase 2: Hardware bring-up (3:00 to 7:00 PM, parallel)

- [x] (A · Codex) BOX-3 Mac USB bring-up: flashed, clap/tilt readings observed, BOOT emits `B`, `U`/`L`/`A1`/`A0` acknowledge
- [x] (A · Codex) BOX-3 QNX bring-up: 127 sensor samples; `L`/`A1`/`A0`/`U`/`L` acknowledged through raw USB; 332 tests pass
- [x] (A · Codex) Simulated latch: `ACK,U`, BOX-3 screen shows `LATCH OPEN`, then automatically `LATCH LOCKED` after the hold (no servo or latch LED)
- [x] (A · Codex) BOX-3 microphone readings noted on Mac USB; clap/tilt capture peaked at 16013 (not calibrated)
- [x] (A · Codex) BOX-3 accelerometer readings noted on Mac USB; near 1 g at rest, all axes change on tilt
- [ ] (A) Ultrasonic readings noted
- [x] (A · Codex) BOX-3 display + audible alarm by command; operator verified timer text, beep start/stop, latch status, and automatic relock (`edge/box3-display-alarm`)
- [x] (A · Codex) QNX: SSH shell + BOX-3 sensor samples through pyusb/libusb (no USB-serial driver)
- [x] (A) `sensor_node.py` fusion, tested with `sim_sensors.py`

## Gate 1: QNX decision (7:00 PM)

- [ ] (All) Four questions answered; QNX or Linux decided and recorded here: ______ (Pi 5 facts from QNX's sources are in #22; the deciding test is the Arduino over USB serial on QNX)

## Phase 3: Real integrations (7:00 to 10:30 PM)

- [ ] (A+B) `STUB_EDGE=0`: signed events + verified commands; wrong secret rejected
- [ ] (B) `STUB_STEPFUN=0`, latency logged
- [ ] (C) `STUB_ELEVENLABS=0`, phrases cached, consented voice or stock
- [ ] (C) Photon gateway: text out, reply in (90-minute cap, then simulator)
- [ ] (D) Sentry tracing spans + uptime monitor on tunneled `/health`
- [x] (A · Codex) Local BOX-3 alarm + `TIME DOWN` with test backend paused; operator confirmed alarm, ticking clock, and locked latch; incident replayed on reconnect

## Phase 4: Hardening (10:30 PM to 1:30 AM)

- [ ] (B) All 14 security cases pass
- [ ] (A) Offline test + reconnect replay
- [ ] (A) Calibration done, thresholds committed in `edge/config.py`
- [ ] (A) False-alarm check: __ of 20 non-falls triggered the voice check
- [ ] (D) Dashboard polish, handoff sheet, phone view
- [ ] (D) `docs/sentry-story.md` with one real finding
- [x] (C) `scripts/demo_reset.sh` resets everything in one command (#25)
- [ ] (All) Full demo 3 times in a row, no manual fixes

## Phases 5–8: Soak, freeze, submit

- [ ] Soak test (one fall every 10 minutes)
- [ ] Tag `demo-ready` at 8:30 AM
- [ ] Rehearse demo 5 times, under 2 minutes
- [ ] Backup video recorded
- [ ] Devpost submitted by 11:00 AM, screenshot saved
- [ ] Sponsor-specific forms (Q10)

## Found in the PR review (#13)

- [ ] (A) `arduino/hub/hub.ino`: no PR adds it yet (IMPLEMENTATION.md 8.3 is the starting point)
- [ ] (A) Pi clock must be NTP-synced (or set by hand): the Pi has no RTC, and every command is refused as `stale` if its clock is >10 s off the laptop's
- [x] (C) `voice.py`: cached ElevenLabs audio for the fixed script (`triage.QUESTIONS`), wired into `main.speak`; #3's version is not wired and its phrases drift from 11.2 (#19)
- [ ] (C) Speech input for the person's answers: Chrome Web Speech push-to-talk + Auto-listen on the dashboard, checked with a stubbed recognizer; tick after a real-mic run on the demo laptop
- [ ] (C) Caregiver texts the Photon agent line once before the demo (shared lines can't text first); ask the table about a dedicated line. Since #27 an alert blocked this way is shown on the dashboard and sent on their first text
- [x] (B) `GET /edge/commands` is unauthenticated: anyone on the LAN can drain the queue (commands are signed, so this is denial, not forgery)
- [x] (A) Edge HTTP shares the serial loop: a blackholed backend can delay serial handling by up to `HTTP_TIMEOUT_SECONDS` once per backoff
- [ ] (B) A late answer to Q1 after the silence escalation is filed under Q2 (now recorded as unknown, not as a wrong "no")

## Issue #21: judge-visible bugs (branch `backend/review-21-fixes`)

- [x] (B · Claude) 1. One-time unlock code is consumed on the first grant
- [x] (A · Claude) 2. `local_unlock` only after `ACK,U`; `unlock_failed` on timeout
- [x] (B · Claude) 3. Signed `GET /edge/commands` (backend verifies, Pi signs)
- [x] (C · Claude) 4. Gateway marks a reply seen only after the forward succeeds
- [x] (A · Claude) 5. Edge network calls off the sensor loop
- [x] (A · Claude) 6. Local alarm outlives the unlock window
- [x] (B · Claude) 7. One StepFun refinement in flight per incident
- [x] (B · Claude) 8. `scripts/demo_reset.sh` + signed `reset` command
- [x] (B · Claude) `POST /incident/resolve` has no auth: anyone on the LAN can end an incident, which sends `alarm_off` to the Pi. Gate it like `/demo/reset` or bind the backend to localhost + the Pi's IP

## Issue #26: bugs after #17/#19 (PR #27, merged)

- [x] (B · Claude) Caregiver rewrite has its own StepFun pool (never queues behind triage)
- [x] (B · Claude) 8 s silence timer starts when question 1 finished playing (12 s grace if the voice never reports); the escalation log line says how it started. Measured: question played 6.7 s after queueing, then 8.2 s of silence
- [x] (B · Claude) "This is an automated assistant." spoken before question 1
- [x] (B · Claude) Voice queue flushed when an incident ends
- [x] (B · Claude) Alert refused by the shared line (409) is shown on the dashboard and resent on the caregiver's first text

## Other done since #25

- [x] (B · Claude) pytest never reports to the real Sentry project (#29): local runs were filing issues and emails
- [x] (C) Photon SMS number + Voice profile connected to the ElevenLabs agent "FirstHour Caregiver Line" over SIP/TLS; inbound and outbound test calls passed on a real phone. Backend still has to place the call (#23 item 3)
- [ ] (C) Rotate the Photon outbound SIP password (it appeared in a setup tool log) and update the ElevenLabs trunk

- [ ] (C/D) Speech Auto-listen starts on a backend "question played" event instead of a fixed 3 s delay: the mic can hear the laptop say "…say I'm fine…", and `is_clear_fine("say I'm fine")` is True, so it could cancel an incident

## Proposed

<!-- Ideas that aren't in IMPLEMENTATION.md go here. Don't build them without team agreement. -->
