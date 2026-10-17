# TODOS: FirstHour

Claim a task by putting your role (and name) on the line, e.g. `- [ ] (B · Sam) ...`. Tick it only when its acceptance check passes. Phases and acceptance tests are in `IMPLEMENTATION.md` section 7. Never delete someone else's item.

## Phase 0: Setup and verification (until 3:00 PM Sat)

- [ ] (All) Agree on roles; put names on Q1–Q10 in `IMPLEMENTATION.md` section 2
- [ ] (A) Collect hardware + craft supplies; record accelerometer chip, NFC board, Grove shield (Q7)
- [ ] (A) QNX table: board image, flashing, `oss.qnx.com` AI module, audio capture (Q2)
- [ ] (B) StepFun key works with one call; record the matching base URL (Q4)
- [ ] (B) FastAPI skeleton: `/health`, `models.py` contracts, `STUB_*` switches
- [ ] (C) Photon table: outbound calls? sender number on inbound? credentials (Q3)
- [ ] (C) ElevenLabs key works with one TTS call; cloning / STT on our tier? (Q5)
- [ ] (D) Sentry project + DSN; read the challenge text
- [ ] (D) Pick Paper or Base44 (Q8)

**Done when:** `/health` OK, one StepFun and one ElevenLabs call work, Q2/Q3/Q4/Q7 answered.

## Phase 1: Vertical slice with stubs (3:00 to 6:00 PM)

- [ ] (B) Unlock test suite (section 9.4) written first
- [ ] (B) `incident.py` state machine
- [ ] (B) `POST /edge/event`, `GET /edge/commands`
- [ ] (B) `triage.py` rule-based fallback, then StepFun path
- [ ] (B) `unlock.py` + `security.py` signing
- [ ] (C) `voice.py` with cached fixed phrases
- [ ] (C) Speech input chosen (ElevenLabs STT → sponsor provider → Web Speech API)
- [ ] (C) Caregiver phone simulator page
- [ ] (D) Live incident page: state badge, clock, triage card + transcript, event log, device status
- [ ] (D) WebSocket events wired
- [ ] (B) `scripts/simulate_fall.py`

**Done when:** the 6:00 PM test in section 7 passes end to end on stubs.

## Phase 2: Hardware bring-up (3:00 to 7:00 PM, parallel)

- [ ] (A) Servo test + tune `LOCK_DEG` / `UNLOCK_DEG`
- [ ] (A) Lock prop, 10 unlocks in a row
- [ ] (A) Sound sensor readings noted
- [ ] (A) Accelerometer readings noted
- [ ] (A) Ultrasonic readings noted
- [ ] (A) NFC UIDs recorded
- [ ] (A) Buzzer + display by command
- [ ] (A) QNX: shell + read Arduino serial from a script
- [ ] (A) `sensor_node.py` fusion, tested with `sim_sensors.py`

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

## Proposed

<!-- Ideas that aren't in IMPLEMENTATION.md go here. Don't build them without team agreement. -->
