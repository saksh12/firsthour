# TODOS: FirstHour

Claim a task by putting your role (and name) on the line, e.g. `- [ ] (B · Sam) ...`. Tick it only when its acceptance check passes. Phases and acceptance tests are in `IMPLEMENTATION.md` section 7. Never delete someone else's item.

## Phase 0: Setup and verification (until 3:00 PM Sat)

- [ ] (All) Agree on roles; put names on Q1–Q10 in `IMPLEMENTATION.md` section 2
- [ ] (A) Collect hardware + craft supplies; record accelerometer chip, Grove shield (Q7)
- [ ] (A) QNX table: board image, flashing, `oss.qnx.com` AI module, audio capture (Q2)
- [ ] (B) StepFun key works with one call; record the matching base URL (Q4)
- [ ] (B) FastAPI skeleton: `/health`, `models.py` contracts, `STUB_*` switches
- [ ] (C) Photon table: outbound calls? sender number on inbound? credentials (Q3)
- [ ] (C) ElevenLabs key works with one TTS call; cloning / STT on our tier? (Q5)
- [ ] (D) Sentry project + DSN; read the challenge text
- [ ] (D) Pick Paper or Base44 (Q8)

**Done when:** `/health` OK, one StepFun and one ElevenLabs call work, Q2/Q3/Q4/Q7 answered.

## Phase 1: Vertical slice with stubs (3:00 to 6:00 PM)

- [x] (B) Unlock test suite (section 9.4) written first
- [x] (B) `incident.py` state machine
- [x] (B) `POST /edge/event`, `GET /edge/commands`
- [x] (B) `triage.py` rule-based fallback, then StepFun path
- [x] (B) `unlock.py` + `security.py` signing
- [ ] (C) `voice.py` with cached fixed phrases
- [ ] (C) Speech input chosen (ElevenLabs STT → sponsor provider → Web Speech API)
- [x] (C) Caregiver phone simulator page
- [ ] (D) Live incident page: state badge, clock, triage card + transcript, event log, device status
- [x] (D) WebSocket events wired
- [x] (B) `scripts/simulate_fall.py`

**Done when:** the 6:00 PM test in section 7 passes end to end on stubs.

## Phase 2: Hardware bring-up (3:00 to 7:00 PM, parallel)

- [ ] (A) Simulated latch: `U` lights the latch LED, `ACK,U`, relocks after the hold time (no servo)
- [ ] (A) Sound sensor readings noted
- [ ] (A) Accelerometer readings noted
- [ ] (A) Ultrasonic readings noted
- [ ] (A) Buzzer + display by command
- [ ] (A) QNX: shell + read Arduino serial from a script
- [x] (A) `sensor_node.py` fusion, tested with `sim_sensors.py`

## Gate 1: QNX decision (7:00 PM)

- [ ] (All) Four questions answered; QNX or Linux decided and recorded here: ______

## Phase 3: Real integrations (7:00 to 10:30 PM)

- [ ] (A+B) `STUB_EDGE=0`: signed events + verified commands; wrong secret rejected
- [ ] (B) `STUB_STEPFUN=0`, latency logged
- [ ] (C) `STUB_ELEVENLABS=0`, phrases cached, consented voice or stock
- [ ] (C) Photon gateway: text out, reply in (90-minute cap, then simulator)
- [ ] (D) Sentry tracing spans + uptime monitor on tunneled `/health`
- [ ] (A) Local alarm + `TIME DOWN` with backend unreachable

## Phase 4: Hardening (10:30 PM to 1:30 AM)

- [ ] (B) All 14 security cases pass
- [ ] (A) Offline test + reconnect replay
- [ ] (A) Calibration done, thresholds committed in `edge/config.py`
- [ ] (A) False-alarm check: __ of 20 non-falls triggered the voice check
- [ ] (D) Dashboard polish, handoff sheet, phone view
- [ ] (D) `docs/sentry-story.md` with one real finding
- [ ] (C) `scripts/demo_reset.sh` resets everything in one command
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
- [ ] (C) `voice.py`: cached ElevenLabs audio for the fixed script (`triage.QUESTIONS`), wired into `main.speak`; #3's version is not wired and its phrases drift from 11.2
- [ ] (C) Speech input for the person's answers (today only the typed box)
- [ ] (C) Caregiver texts the Photon agent line once before the demo (shared lines can't text first); ask the table about a dedicated line
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
- [ ] (B) `POST /incident/resolve` has no auth: anyone on the LAN can end an incident, which sends `alarm_off` to the Pi. Gate it like `/demo/reset` or bind the backend to localhost + the Pi's IP

## Proposed

<!-- Ideas that aren't in IMPLEMENTATION.md go here. Don't build them without team agreement. -->
