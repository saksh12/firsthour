# AGENTS.md: FirstHour

Every agent and human on this repo reads this first. `IMPLEMENTATION.md` is the full build plan and `TODOS.md` is the task board. Work from them, update them, and do not drift from the contracts below.

## What we are building

FirstHour is a **camera-free fall-response node** for a bathroom. A Raspberry Pi running QNX plus an Arduino sensor hub hear a fall, check on the person by voice, text a caregiver a triage card, and let help in through a guarded door unlock. It must stay safe with the network gone.

| # | Moment | What judges see and hear | Trigger |
| --- | --- | --- | --- |
| M1 | **The thud** | Display flips to `TIME DOWN 00:01`. Terminal shows both sensors spiking | Sound peak + accelerometer spike agree within 400 ms |
| M2 | **The voice** | Calm synthetic voice: "Are you okay? Say 'I'm fine' or 'I need help.'" Short scripted triage follows | `fall_suspected` event reaches the backend |
| M3 | **The text** | Caregiver phone buzzes with a triage card. Their reply is read aloud to the person on the floor | Any answer other than a clear "I'm fine", or 8 s of silence |
| M4 | **The door** | Caregiver replies `UNLOCK <code>`, the simulated latch opens on the Mac (see "No servo" below). A stranger's text is refused | Every unlock rule passes (see below) |
| M5 | **The offline test** | Wi-Fi unplugged: box still alarms, clock still counts; the door stays locked (safe default) and the Pi replays its log on reconnect | Local incident open on the Pi |

The pitch rests on **local safety, cloud convenience**: detection, alarm, signature checks and the final unlock decision live on the Pi. The cloud can ask; **the Pi decides**. Never move safety logic off the Pi.

Hackathon: LA Hacks 2026, UCLA. Hacking runs **Sat Oct 17 to Sun Oct 18, 12:00 PM**. Code freeze **Sun 8:30 AM** (tag `demo-ready`). Submit on Devpost by **11:00 AM**.

## Sponsor targets and their hard requirements

Every sponsor below has a real job in the pipeline. If a change would break one of these requirements, stop and flag it.

| Sponsor | Requirement to qualify | Our usage |
| --- | --- | --- |
| **Main track**: Beyond the Digital | Intelligence in the physical world with embedded AI or hardware | The QNX node, sensors, simulated door latch |
| **QNX** | Product runs on **QNX OS** and includes **one open-source AI module from `oss.qnx.com`** | `edge/` on the Pi. Module: **`whisper.cpp`** from `repo.oss.qnx.com` (`apk add whisper.cpp`, model `ggml-base.en`) transcribes the person's spoken answers on the Pi (`IMPLEMENTATION.md` 6.8). **Gate 1 at 7:00 PM Sat**: if QNX isn't booting and reading serial, run `edge/` on Linux and drop this prize |
| **Espressif**: Best Use of Espressif | Build with Espressif hardware (confirm the exact rule on the tracks page) | `arduino/hub/hub.ino` on the ESP32-S3-BOX-3: ICM-42607-P IMU, ES7210 mics in a FreeRTOS I2S task, ES8311 speaker alarm, LovyanGFX LCD, simulated latch with auto relock, the serial protocol. Write-up in `docs/espressif.md` |
| **StepFun**: Get It Done with Step 5 Preview | App **must call `step-5-preview` at runtime**. Explain what every other model does. Judged on task completion, reliability, user understanding and control | Turns spoken answers into the triage record, writes the handoff summary, rewrites caregiver replies into one calm sentence |
| **Photon**: Messaging/Calling Agent | Build on **Photon 2.0 beta** | `comms-gateway/`: alert text out, caregiver reply in (live Oct 18). Voice: the backend calls the caregiver through an ElevenLabs agent on the Photon SMS number over SIP (real calls placed Oct 18) |
| **Sentry**: Best Use of Sentry | **≥2 products beyond error monitoring**, and show how they shaped the build | Tracing (one trace per incident), Uptime Monitoring (`/health` via tunnel), Logs (unlock audit trail), Session Replay optional |
| **MLH**: Best Use of ElevenLabs | Use ElevenLabs | TTS for every fixed phrase (pre-cached); an Agents voice call to a silent caregiver; after the fixed script, a companion agent that talks with the person and decides what to relay to the caregiver (IMPLEMENTATION.md 6.9) |
| **Paper** *or* **Base44** (pick one) | Paper: design craft. Base44: real problem, usable product | Skipped (decided Sun). Not entered |
| **Cognition**: Best Use of Devin (optional) | Build with Devin | Not entered |

Read each sponsor's "View Additional Information" page for extra rules or forms. Requirements can change.

## Hardware and platform facts (assumed; confirm and update this section)

Nothing here is verified yet. Whoever confirms a fact replaces the `TODO(verify)` with what they saw and where.

- **The Pi never reads sensors directly.** The Grove sound sensor is analog, and Grove/GPIO drivers on QNX are a time sink. The Arduino reads everything and talks to the Pi over **USB serial at 115200 baud**.
- **The Pi polls the backend; it never accepts inbound connections.** No port or firewall problems on venue Wi-Fi.
- **Venue Wi-Fi may isolate clients.** Have a phone hotspot set up and tested before you need it.
- QNX board image, flashing steps, Python runtime on QNX, and serial device path: `TODO(verify)` at the QNX table.
- Accelerometer chip, Grove shield, display type: `TODO(verify)`, read the module labels.
- **No NFC reader and no servo at the event** (decided Sat). NFC is removed from the code and the contracts. The latch is simulated, see below.
- Photon: inbound replies **do** carry the sender's number (`sentBy.handle`). A **shared** line can't send the first message, so the caregiver texts the agent line once before the demo. Each caregiver gets their **own** shared line, so the gateway accepts replies on any of the project's lines. A dedicated line is available (not taken). Outbound calls run on the project's SMS number through SIP to ElevenLabs; the backend placed real calls Oct 18.
- StepFun base URL depends on the key: `https://api.stepfun.com/v1` or `https://api.stepfun.ai/v1`. **Never hardcode either.**
- ElevenLabs voice cloning and realtime STT on our tier: `TODO(verify)`. Fallback is a stock voice and browser Web Speech API.
- **Speech-to-text on the QNX Pi** (verified Oct 18): the `whisper.cpp` apk runs `whisper-cli` and `whisper-server` on the Pi 5. The Logitech Brio's USB mic fails in QNX `io-snd`, so the mic is on the laptop: the dashboard records the answer and the Pi fetches the clip (signed) and transcribes it. Use 3 whisper threads on the 4-core Pi, not 4 (measured 9.5 vs 272 ms per decoded token).

### No servo: the simulated latch

There is no servo motor, so the door latch is simulated, and the security chain stays exactly as real as with a servo:

1. The backend grants an unlock and queues a **signed** `unlock` command (all unlock rules below).
2. The Pi verifies it (signature, freshness, nonce, **its own** open incident) and sends `U` to the Arduino.
3. The Arduino "opens the latch": it lights the latch LED (if wired), answers `ACK,U` and relocks itself after `UNLOCK_HOLD_SECONDS`.
4. On `ACK,U` the Pi reports `local_unlock` to the backend, and **the Mac shows the door opening** (dashboard door panel). No ACK within `UNLOCK_ACK_TIMEOUT_S`: the Pi reports `unlock_failed` and the door panel says so.

The Mac only ever shows what the Pi reports, never the backend's grant: until the Pi confirms, the dashboard says "Unlocking…". Never fake the opening on the Mac from the grant alone.

Docs: `docs.photon.codes/docs/llms.txt` (Photon index) · `oss.qnx.com` (QNX modules) · StepFun, ElevenLabs and Sentry developer docs for exact parameter names.

## Architecture

```text
 FLOOR / BATHROOM                       PI ON QNX (edge/)                       LAPTOP (backend/)                 OUTSIDE WORLD
┌───────────────────────┐ USB serial ┌──────────────────────────────┐  HTTP   ┌──────────────────────────────┐
│ arduino/hub           │◀──────────▶│ sensor_node.py               │────────▶│ FastAPI                       │──▶ StepFun step-5-preview
│ • sound (A0)          │  lines     │ • fusion (sound + accel)     │ events  │ • incident state machine      │──▶ ElevenLabs TTS / STT
│ • accelerometer (I2C) │            │ • local incident + clock     │◀────────│ • triage (StepFun + rules)    │──▶ Sentry
│ • ultrasonic (D7)     │            │ • buzzer + TIME DOWN display │ polled  │ • voice (cached audio)        │
│ • latch LED (sim)     │            │ • verifies SIGNED commands   │ signed  │ • unlock rules + signing      │
│                       │            │ • reports local_unlock       │ cmds    │ • /health (503 if Pi silent)  │
│ • buzzer / LED / LCD  │            │ • local log, replay on recon │         └───────┬──────────────┬────────┘
└───────────────────────┘            └──────────────────────────────┘                 │              │ ws
                                                                       ┌─────────────▼──────┐ ┌─────▼──────────────────┐
                                                                       │ comms-gateway/     │ │ dashboard/             │
                                                                       │ Node + Photon SDK  │ │ live incident, clock,  │
                                                                       │ alert out, reply in│ │ triage + transcript,   │
                                                                       └─────────┬──────────┘ │ handoff sheet,         │
                                                                                 ▼            │ caregiver simulator    │
                                                                         caregiver's phone    └────────────────────────┘
```

Key decisions (already made; don't relitigate):

1. **Arduino = sensors and actuators. Pi = safety brain. Laptop = AI and comms.** The Pi never needs StepFun, ElevenLabs, Photon or Sentry to stay safe.
2. **The backend never touches hardware.** It queues signed commands. The Pi verifies and acts.
3. **The Pi polls `GET /edge/commands` every second.** No inbound connections to the Pi.
4. **Two sensors must agree** (sound + accelerometer within 400 ms). Ultrasonic only adds confidence; never require it.
5. **Silence escalates.** Only a clear "I'm fine" cancels an incident.
6. **The question script is fixed.** The first "Are you okay?" is pre-recorded and plays with no model call. After the alert, an unanswered question (silence, or speech-to-text failed) is asked once more, then left unknown. Only **after** the five questions does an ElevenLabs companion agent talk freely with her (6.9); it never replaces or changes the triage script.
7. **`on_blood_thinner` and medications come from the patient profile**, never from a model.
8. **The caregiver simulator page is a first-class path**, not a hack. It posts to the same inbound endpoint Photon uses.

## Repo layout

```text
AGENTS.md  IMPLEMENTATION.md  TODOS.md  .env.example
arduino/hub/hub.ino            sensors, simulated latch (LED + relock timer), buzzer, serial protocol
edge/                          runs on the Pi (QNX), plain Python, no heavy deps
  sensor_node.py               serial reader, fusion, local incident, event posting, command polling
  supervisor.py                QNX process split: safety (prio 21), uplink (10), whisper-server (8); restarts them
  uplink.py                    HTTP to the backend for the split mode; signs nothing
  link.py                      Unix-socket link between the safety process and the uplink
  security.py                  HMAC verify, freshness, nonce cache
  config.py                    thresholds (from calibration data) + env
  sim_sensors.py               fake serial feed for laptop testing
backend/                       FastAPI (Python 3.11+)
  app/main.py                  routes: /edge/event, /edge/commands, /health, /comms/inbound, /ws
  app/models.py                pydantic schemas for every contract below
  app/incident.py              state machine
  app/triage.py                StepFun + rule-based fallback
  app/voice.py                 ElevenLabs + audio cache + stub
  app/comms.py                 alert formatting, talks to comms-gateway
  app/unlock.py                unlock rules (the important file)
  app/security.py              signing
  app/telemetry.py             sentry_sdk init + span helpers
  tests/                       pytest: unlock suite, state machine, triage fallback
comms-gateway/                 Node + TypeScript, Photon SDK wrapper
dashboard/                     web UI: live incident, handoff sheet, caregiver simulator
scripts/                       simulate_fall.py, send_test_alert.py, demo_reset.sh
docs/                          sentry-story.md, demo-script.md, devpost-draft.md
```

Grow a file before you split it. Do not add packages, layers or abstractions that this layout doesn't need.

## Wire protocol (the contract between the parts)

`IMPLEMENTATION.md` section 6 has the full version. If you change a contract, update both files in the same commit and tell the team. All timestamps are **UTC epoch milliseconds**.

**Arduino → Pi** (serial, 115200, `\n` terminated)

| Line | Meaning |
| --- | --- |
| `S,<ms>,<soundPeak>,<ax>,<ay>,<az>,<distCm>` | Sensor sample, 20 per second |
| `B` | Help button pressed (if wired) |
| `ACK,<cmd>` | Reply to a command |

**Pi → Arduino**: `U` (unlock, auto relock after hold), `L` (lock now), `A1` / `A0` (buzzer on/off), `D,<text>` (display text).

**Pi → backend**: `POST /edge/event`

| Field | Notes |
| --- | --- |
| `device_id` | e.g. `bath-01` |
| `ts` | epoch ms |
| `type` | `fall_suspected` \| `heartbeat` \| `help_button` \| `local_unlock` (the Arduino answered `ACK,U`) \| `unlock_failed` (no `ACK,U` within `UNLOCK_ACK_TIMEOUT_S`) \| `cancelled` \| `speech_text` (transcript of a `transcribe` clip) |
| `sensors` | `{sound_peak, accel_delta_g, dist_cm}` |
| `local_incident_id` | `<device_id>-<ts>` |
| `speech` | only on `speech_text`: `{clip_id, text, stt_ms, audio_ms, error}`; empty text or an error counts as no answer |

Header `X-Signature: hex(HMAC_SHA256(SHARED_SECRET, ts + "." + raw_body))`.

**Backend → Pi**: Pi polls `GET /edge/commands?device_id=bath-01&ts=<ms>` every 1 s, header `X-Signature: hex(HMAC_SHA256(SHARED_SECRET, ts + "." + device_id))`. A missing, bad, stale (> 10 s) or replayed (`ts` not newer than the last accepted poll) signature gets 401 and leaves the queue alone. Response `{"commands": [...]}`, each with `id, action, incident_id, ts, nonce, sig`. `action` is `unlock` \| `lock` \| `alarm_on` \| `alarm_off` \| `display` \| `reset` (demo reset: lock, alarm off, clear the fusion cooldown, `READY`; obeyed only with no local incident open; `incident_id` is `""`) \| `transcribe` (own open incident only: fetch `GET /edge/audio/<id>?device_id&ts`, signed `hex(HMAC_SHA256(SHARED_SECRET, ts + "." + device_id + "." + clip_id))`, served once, then post `speech_text`). `sig = hex(HMAC_SHA256(SHARED_SECRET, "action|incident_id|ts|nonce"))`.

**Incident states**: `IDLE` → `CHECKING` → (`CANCELLED` on "I'm fine" | `ALERTING` on help or 8 s silence) → `RESPONDING` on any caregiver reply → `UNLOCKED` on accepted unlock → `RESOLVED` → `IDLE`.

**Triage record** (StepFun output, pydantic-validated): `conscious, head_strike, pain_location, can_move, bleeding, on_blood_thinner, summary, next_question, done`. Unknown = `null`, never a guess.

**Caregiver reply grammar**: `UNLOCK <4 digits>` attempts an unlock. The first alert asks "How soon can you be here?" and carries **no code**. A clear "on my way" / ETA / "I'm here" (regex, `backend/app/coordinator.py`; hedges like "I might come" never count) claims the incident, is read aloud from a fixed template, and gets the one-time door code texted back to that caregiver only (also after a voice call where they committed). No reply at all within `VOICE_CALL_AFTER_SECONDS` places a voice call; any reply stops it. A question (StepFun, `?` fallback) is answered by text from incident facts only, never the code. Anything else is a message, rewritten to one calm sentence (≤ 20 words) and read aloud, then the pending question is asked again. Nothing said on a voice call is ever an unlock. The companion agent can text the caregiver what she said (`relay`, to the claimer, else everyone; at most 6 per incident, never the code).

**Backend → dashboard** (WebSocket): `incident_update`, `triage_update`, `message_in`, `message_out`, `unlock_attempt {result, reason}`, `device_status`, `question_played`, `speech_heard`, `voice_line`, `script_done` (the five questions and closing audio are over: the dashboard starts the companion), `delivery_status {status: queued|sent|blocked|failed|acknowledged|responder_assigned}`, `responder_update`, `call_update` (issue #23; "sent" = Photon API accepted, never "delivered"). Snapshots carry `companion_ready` for reconnects. Companion tokens and relays are refused before this handoff. The closing line makes no connection or delivery claim; the dashboard shows connection failures and a retry button. Agent prompts are in `docs/voice-prompts.md`. A companion tool's `sent:true` means queued, not confirmed Photon acceptance.

## Unlock rules (non-negotiable; judges will poke at this)

All must be true, every time. Code lives in `backend/app/unlock.py` and `edge/security.py`.

1. An incident is open (confirmed fall within `INCIDENT_WINDOW_SECONDS`).
2. Sender is on the `CAREGIVER_NUMBERS` allowlist.
3. Reply contains the **one-time 4-digit code** made for this incident (compared with `hmac.compare_digest`). It dies when the incident ends.
4. Fewer than 3 wrong tries this incident.
5. The command is signed, fresh (< 10 s), and its nonce is unseen.
6. The Pi independently confirms the command names **its own open incident** before opening the latch.
7. Every attempt is logged, allowed or refused, with the reason.
8. Auto relock after `UNLOCK_HOLD_SECONDS`.

Offline, nothing unlocks the door: it stays locked until the backend is reachable again.

**Never add** a debug unlock, a hardcoded code, a bypass flag, or a "skip signature in dev" path. To test, use the pytest suite or the `STUB_*` switches. The full test matrix (11 cases) is `IMPLEMENTATION.md` section 9.4 and must stay green.

## Fall detection rules (`edge/`)

- Fusion: suspected fall when sound peak and accelerometer delta both cross their thresholds within `WINDOW_MS = 400`.
- 30-second cooldown after each event, so one fall can't trigger twice.
- **Thresholds come from calibration data**, not guesses: ~20 trials each of `fall_beanbag`, `sit_mat`, `bottle_drop`, `door_slam`, `footsteps`, `clap`, logged to CSV with labels. Commit the final numbers in `edge/config.py`. Recheck in the demo room.
- The local incident and alarm have no timeout: they stay up until a signed `alarm_off` (a clear "I'm fine", a resolve) or a demo `reset`. Only unlocks expire with the window.
- HTTP runs on its own thread; the sensor loop never waits on the network. With `edge/supervisor.py` it runs in a separate, lower-priority uplink process that holds no signing role (`IMPLEMENTATION.md` 6.10).
- On `fall_suspected` the Pi, **before any network call**: opens a local incident, starts the clock, turns the buzzer on, shows `TIME DOWN mm:ss`. Then it tries to POST. Unreachable backend → keep going locally, log to file, replay when the network returns.

## Latency (measure everything in Sentry; quote only measured numbers)

| Path | Rule |
| --- | --- |
| Thud → `TIME DOWN` on display | Local only, no network in path |
| Fall event → "Are you okay?" plays | Pre-cached audio, no model or TTS call in path |
| Spoken answer → transcript on the Pi (`PI_STT=1`) | Off the sensor loop, whisper at lower QNX priority than the reader, 10 s timeout. Measured live with `base.en` and clips up to 8 s: whisper 2.7-3.1 s, clip upload to answer applied 3.8-4.3 s (`tiny.en`: 1.16-1.55 s and 1.6-2.4 s) |
| StepFun triage call | Runs in the background after the instant rule-based record (which drives escalation and the alert). Hard timeout **45 s**, then the rule record stays. Measured 5-38 s on `low` (the lowest level); unclear answers reason longest, up to 3593 tokens, so `max_tokens` is 5000 |
| StepFun reply rewrite | Hard timeout **10 s** (`STEPFUN_REWRITE_TIMEOUT_S`), then the original text. Measured 1-1.8 s Oct 17 with the "answer immediately" prompt; Oct 18 morning the model reasoned 800-1200 tokens regardless, 6-12 s (no off switch; the flash models are no faster) |
| StepFun caregiver Q&A answer | Hard timeout **12 s** (`STEPFUN_ANSWER_TIMEOUT_S`), then the templated status. Texted, so it can wait longer than speech. Measured 7.4 s Oct 18 |
| Caregiver reply → backend (live Photon, polled every 2 s) | Measured Oct 18: 3.5 s from the phone's send to `RESPONDING`; the door-code text was accepted by Photon 0.3 s later |
| Unlock reply → latch opens (Pi `local_unlock`) | ≤ ~1 s poll + verify; measure it |
| Fall → caregiver text delivered | Measure it |

One Sentry trace per incident, spans: `sensor_event` → `voice_check` → `speech_to_text` → `stepfun_triage` → `photon_send` → `unlock_decision` → `command_to_pi`. The demo-close screenshot is this trace with the **real** end-to-end number.

## StepFun usage (`backend/app/triage.py`)

- OpenAI-compatible `POST {STEPFUN_BASE_URL}/chat/completions` over stdlib `urllib` (no SDK dependency), model from `STEPFUN_MODEL` (default `step-5-preview`).
- Every call asks for **strict JSON** (`response_format={"type": "json_object"}`, low temperature) and is validated with pydantic.
- The model can add detail and worrying answers, never reassurance: a clear rule answer always wins, and where the rules found nothing only `head_strike=true`, `can_move=false` or `bleeding=true` is taken from the model. If any field is overridden, the templated summary replaces the model's (`merge_model` in `triage.py`). Measured Oct 18: the model read "not exactly" (bleeding) as "denies bleeding".
- On timeout, bad JSON or validation failure: **fall back to rule-based keyword parsing** and a templated summary, and log it. The incident flow never waits on or crashes from the model.
- Functions: `parse_answers(transcript, profile) → TriageRecord` · `rewrite_reply(text) → str (≤ 20 words, no added info)` · handoff summary.
- StepFun **does not** diagnose, advise, or improvise questions. If you are tempted to let it, stop.
- `reasoning_effort` accepts `low`, `medium` or `high` (StepFun docs, checked Oct 18); triage uses `low`, the lowest. Log latency on every call.
- The dashboard shows the **raw transcript next to the record**, so users can see and correct what the AI understood.

## Safety and ethics rules (non-negotiable)

- **No cameras, no video code.** The webcam is used only as a microphone.
- **No real personal data.** Margaret, her medications and every phone number are fictional demo data.
- **Clone only a teammate's voice, with clear consent**, labeled as an assistant ("this is an assistant, not your daughter"). Otherwise use a stock voice.
- **Nobody falls for real.** Beanbag drop plus a controlled sit onto a crash mat.
- **Honest claims only.** Never write "99% accurate", "zero false alarms", "detects every fall", "clinically proven", "saves lives", "unhackable", or any latency or accuracy number you didn't measure, in code, comments, docs or UI. It is a prototype, not a medical device.
- Sentry: `send_default_pii=False`, no audio in Sentry, mask text in Session Replay.
- Secrets live only in `.env` (gitignored). Commit `.env.example` with every key name and no real values.

## Environment: one shared `.env` at the repo root

- `.env.example` (committed) lists every key. Copy to `.env` and fill it in. **No per-package `.env` files.**
- Stub switches: `STUB_EDGE`, `STUB_STEPFUN`, `STUB_ELEVENLABS`, `STUB_PHOTON` (1 = fake, 0 = real). Default to 1 while building. **Every new integration must honor its switch** and have a tested fallback.
- `backend/` reads config only through `os.environ` (loaded from `../.env`). `edge/` reads it through `edge/config.py`.
- Browser code (`dashboard/`) never sees secrets. If it uses Vite, only `VITE_*` keys are exposed, so **secrets never get a `VITE_` prefix**.
- Add a new key to `.env.example` in the same commit that first reads it.

## Dev tooling

| Tool | Used for | Check |
| --- | --- | --- |
| git, gh | branches, PRs (`gh auth login` once) | `gh auth status` |
| Python 3.11+ | `backend/`, `edge/`, `scripts/` (venv + pip per `IMPLEMENTATION.md`) | `python3 --version` |
| Node ≥ 22 + npm | `comms-gateway/` (Photon's SDK needs 22) | `node --version` |
| Arduino IDE or `arduino-cli` | flashing `arduino/hub/hub.ino` | `arduino-cli version` |
| cloudflared or ngrok | public `/health` for Sentry Uptime | `cloudflared --version` |

Keep `edge/` dependencies to the Python stdlib plus `pyserial` (and `requests` if QNX has it). QNX may not have pip for everything, so check before adding anything there.

If a tool is missing and the install needs a human (sudo password, `gh auth login`), stop and tell the user the exact command to run. Don't silently switch to a different toolchain.

## Commands

```bash
# one-time
cp .env.example .env                          # fill in keys, never commit

# backend
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
pytest -q                                     # must pass before merging

# comms gateway
cd comms-gateway && npm install && npm run dev

# dashboard (static pages; the caregiver simulator is dashboard/caregiver-simulator.html).
# Operator buttons (resolve, typed answers, the stub caregiver phone) work only in a browser on the laptop;
# other devices can watch. Through the tunnel only /health answers.
python3 -m http.server 5173 -d dashboard

# edge on the laptop, no hardware
python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin   # type f + Enter for a fall, b for the help button
                                              # stdin fakes the latch's ACK,U; --no-ack simulates a dead latch

# fake a fall end to end (stubs)
python3 scripts/simulate_fall.py

# reset between demo runs (IDLE, latch locked, display READY)
bash scripts/demo_reset.sh
```

## Working rules for agents

- **Read `IMPLEMENTATION.md` for your task's phase and acceptance test before coding.** Prefer the smallest change that passes it.
- **Stay inside your role's folder** (A: `arduino/`, `edge/` · B: `backend/` core · C: `comms-gateway/`, `backend/app/voice.py`, `comms.py` · D: `dashboard/`, `docs/`, `telemetry.py`). Ask before touching anything else.
- **Test before every PR and every push to `main`.** `cd backend && pytest -q` must be green, and the unlock suite must pass for any change touching `unlock.py`, `security.py`, `incident.py` or `edge/`. Every change adds or extends tests for its logic. Paste the result summary into the PR body. Hardware-only behavior (latch LED, sensors, QNX) can't be unit-tested, so state exactly what you checked on the real hardware.
- **Keep logic testable without hardware.** Fusion, nonce/freshness checks, the state machine, unlock rules and triage fallback are pure functions over timestamps and strings.
- **Every slice of work happens on a named branch**: `<role>/<short-topic>` (e.g. `edge/fusion`, `backend/unlock`). Open a PR into `main` with `gh pr create`. Push straight to `main` only for tiny changes (typo, TODOS tick, one-line config). Rebase on `main` before merging; never overwrite someone else's work.
- **Claim a task in `TODOS.md` before you start** (put your role on the line), and tick it only when its acceptance check passes. Add discovered work as new unchecked items. Never delete someone else's item.
- **Keep the demo working after every merge.** The vertical slice (`simulate_fall.py` through every stub) must work by **6:00 PM Sat**, and `main` must be demo-able from then on. Replace stubs one at a time and rerun the Phase 1 acceptance test after each.
- **Every external service has a stub and a fallback**, and every demo beat has a manual trigger (simulator page, typed answer box, "force fall" hotkey). The real pipeline stays the default path.
- **Never invent sponsor API calls from memory.** Check the sponsor's docs (Photon `llms.txt`, StepFun, ElevenLabs, Sentry, QNX). Mark anything you couldn't verify with `TODO(verify)` and link the doc you used.
- **Blocked or unclear contract?** (QNX audio, Photon sender number, voice calls.) Stop and say what you need. Don't guess.
- Log every state change, every unlock attempt with its reason, and every external call's latency, keyed by incident id.
- Python: type hints on public functions, pydantic for every contract, `ruff format` if installed. TypeScript: strict, no `any` on contract types. Arduino: no `delay()` in `loop()`; use `millis()`. Comments explain why, not what.
- Tell the team before adding a dependency, and pin its version.
- After the **8:30 AM freeze**: bug fixes only, each tested three times.
- Do not add: cameras or video, a login system, cloud hosting for the backend, ML fall models that replace the two-sensor rule, LLM-generated triage questions (the companion agent after the script is the one exception, 6.9), both Paper and Base44, or anything else not in `IMPLEMENTATION.md`. If you think something is missing, add it to `TODOS.md` under "Proposed" and keep going.
- **Never weaken a rule in "Unlock rules" or "Safety and ethics rules".**

## Escalation

Blocked for more than 20 minutes? Post in the team channel: what you tried, what you saw, what you need. Hardware questions go to the hardware lab volunteers or the relevant sponsor table.
