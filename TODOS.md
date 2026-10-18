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

- [x] (C/D · Droid) Enable the post-script companion and refine both saved agent prompts (`docs/voice-prompts.md`). Verified: 432 backend tests, 7 frontend tests, stub fall-to-unlock, browser off/retry state, SDK import, and private-agent token minting. Live backend restarted with companion enabled.
- [ ] (C/D) Live AirPods companion and selective Photon relay after all five questions. Blocked at the latest check by absent Pi heartbeats; no real test call or text placed during companion enablement.
- [ ] (A+B) `STUB_EDGE=0`: signed events + verified commands; wrong secret rejected
- [x] (B) `STUB_STEPFUN=0`, latency logged (Droid, Oct 18: every call logs `stepfun call=<kind> model=step-5-preview ms=<n> ok=<bool>` and reaches the dashboard as `stepfun_call`)
- [x] (C · Droid) `STUB_ELEVENLABS=0`, phrases cached, consented voice or stock: stock voice, 6 fixed phrases cached on the port-8002 test backend (`edge/box3-voice`); played live through three knock runs on the real Pi
- [x] (A · Droid) QNX AI module (#22, `edge/box3-voice`): `whisper.cpp` on the Pi transcribes the person's answers (laptop mic → signed clip fetch → `speech_text`). Live AirPods runs: help path and "I'm fine" cancel. tiny.en: whisper 1.16-1.55 s per clip, upload to answer applied 1.6-2.4 s; base.en (demo model, more accurate): 2.7-3.1 s and 3.8-4.3 s
- [ ] (A) Live check of an all-silent recording with Pi speech-to-text (unit-tested only)
- [ ] (B) Rules don't read hedged amounts like "a little, not very much" (moving); StepFun may fill it, but only when its reply lands before the incident ends
- [x] (B · Droid) StepFun live on the port-8002 test backend (`edge/box3-voice`): rules win, the model only adds worrying answers, stale results carry forward. Measured 5-38 s on `low`; 45 s timeout, `max_tokens` 5000
- [x] (A · Droid) Split the Pi safety node into separate QNX processes (#22 step 1), branch `edge/qnx-process-split`: `supervisor.py` runs safety (prio 21), uplink (10), whisper-server (8); Unix-socket link, uplink signs nothing, restarted safety resumes its incident. On the Pi: priorities in `pidin`, `kill -9` of each process idle and during a knock incident, signed `alarm_off` and `reset` accepted afterwards
- [ ] (C) Photon gateway: text out, reply in (90-minute cap, then simulator)
- [ ] (D) Sentry tracing spans + uptime monitor on tunneled `/health`
- [x] (A · Codex) Local BOX-3 alarm + `TIME DOWN` with test backend paused; operator confirmed alarm, ticking clock, and locked latch; incident replayed on reconnect

## Phase 4: Hardening (10:30 PM to 1:30 AM)

- [ ] (B) All 14 security cases pass
- [ ] (A) Offline test + reconnect replay
- [ ] (A) Calibration done, thresholds committed in `edge/config.py`
- [x] (A · Codex) Record face-up BOX-3 bottle impacts on the same table: 19 clean trials, box-fall trial excluded; provisional Pi-only demo sensitivity selected from replay (`edge/box3-calibration`). Independent non-fall checks and human-fall validation remain pending.
- [x] (A · Codex) Isolate QNX native USB aborts from the safety process (`edge/box3-usb-recovery`): helper SIGKILL and physical BOX RST recover with the same parent PID; operator also confirmed alarm/timer resume during an open incident, then signed reset clears it. 347 tests pass; unlocks are never replayed.
- [x] (A · Codex) Consolidate verified Mac, QNX Pi 5 and BOX-3 connection/reset instructions in root `connect.md`, including limits for agents running off the local network. Local links and shell syntax checked.
- [ ] (A) Improve fusion diagnostics: current logs/event sensors show the sample at detection, which can have low acceleration or sound when the other sensor crossed earlier within 400 ms. Log the matched peaks and timing to make calibration evidence clear.
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
- [ ] (C) Caregiver texts the Photon agent line once before the demo (shared lines can't text first); ask the table about a dedicated line. Since #27 an alert blocked this way is shown on the dashboard and sent on their first text. **Oct 18:** Photon gives each caregiver its own shared line (`+1...4622` for the first phone, `+1...8199` for the second); the gateway now accepts replies on any of the project's lines, so `PHOTON_AGENT_HANDLE` is only the number to tell them to text
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
- [ ] (C, Devin for Manya) #23 caregiver coordinator: durable inbound cursor, multi-caregiver claim/ETA, Q&A, honest delivery states, ElevenLabs escalation call + signed post-call webhook. Tick after the Mac + Pi + BOX-3 check in docs/manual-test-caregiver-coordinator.md. Not in it: Socket.IO swap, JOIN enrollment, voice note
- [x] (C) Photon SMS number + Voice profile connected to the ElevenLabs agent "FirstHour Caregiver Line" over SIP/TLS; inbound and outbound test calls passed on a real phone. Backend still has to place the call (#23 item 3)
- [ ] (C) Rotate the Photon outbound SIP password (it appeared in a setup tool log) and update the ElevenLabs trunk

- [ ] (C/D) Speech Auto-listen starts on a backend "question played" event instead of a fixed 3 s delay: the mic can hear the laptop say "…say I'm fine…", and `is_clear_fine("say I'm fine")` is True, so it could cancel an incident

## Live stress test, Sun morning (Droid)

Stack with every stub off (`STUB_*=0`, real Sentry) on spare ports; the Pi-side run used `sim_sensors` + `sensor_node --stdin` + `supervisor --stdin`.

- [x] (C · Droid) Gateway dropped every real caregiver reply: it only accepted replies to `PHOTON_AGENT_HANDLE`, but Photon assigns a shared line per caregiver. Fixed; a real reply reached the backend 3.5 s after it was sent
- [x] (B · Droid) A `fall_suspected` delivered twice (Pi outbox after an uplink restart) reopened a cancelled incident and would have texted a second alert. Closed ids are now remembered and ignored
- [x] (B · Droid) Pi still alarming for a closed incident (its safety process restarted and refused the `alarm_off` as signed before the restart) had no way out but a restart. The backend now re-sends a fresh `alarm_off` when a heartbeat still names a closed incident
- [x] (A · Droid) `nan`/`inf` in a sensor line poisoned the accelerometer baseline for good (no falls, or every clap a fall) and an `inf` sound crashed the node. Non-finite samples are dropped
- [x] (B · Droid) A help button on one device escalated another device's incident
- [x] (B · Droid) StepFun rewrite/Q&A deadlines raised to 10 s / 12 s (`STEPFUN_*_TIMEOUT_S`): step-5-preview reasoned 6-12 s on every call Sunday morning, so both always fell back
- [ ] (B) Only one incident at a time across devices: a second device's fall is ignored while another device's incident is open, and that node then alarms with no backend incident. Fine for the one-box demo; note it in the pitch
- [ ] (A) `edge/link.py` drops a message already received when the peer closes the socket; delivery stays at-least-once (the backend dedupes), but the uplink could hand over cleanly
- [ ] (C) Photon filters inbound texts that look like one-time codes (`listFilteredVerificationCodes`): a real `UNLOCK 1234` reply from a phone is still to be tried through the live line

## Issue #24 (branch `dashboard/stepfun-visible`)

- [x] (D · Droid) Make StepFun visible: the Triage panel shows Rules, step-5-preview and the record in use side by side (model fields it added marked "+ model", answers the rules kept crossed out), a badge with the call's measured latency, "model: pending…" while it runs, or "fallback: rules (timeout 45 s)" / the rejection reason. A StepFun calls panel logs every triage, reply rewrite and caregiver Q&A call with ms and outcome; caregiver rows say whether the rewrite was used or fell back. Checked with pytest, `node --test`, and a headless browser against a fake StepFun server, then against live step-5-preview Oct 18 (:8041, `STUB_STEPFUN=0`): `model.status` went waiting → pending → ok (12.9 s, deadline 45 s, 5 answers); the model returned `null` for two hedged answers the rules read ("I think I hit my head a little", "Not really"), so the rules record stayed and both records show the gap; `stepfun_call` events arrived for rewrite (6.6 s), triage (8.9 s) and Q&A (8.0 s), all `ok`; the first WebSocket snapshot carried `stepfun.deadlines_s` 45/10/12
- [ ] (B/D) Let the operator correct a misread triage field: no backend endpoint can carry it yet (`/person/answer` only appends the next answer)

## Proposed

<!-- Ideas that aren't in IMPLEMENTATION.md go here. Don't build them without team agreement. -->
