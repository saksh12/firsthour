# FirstHour: Devpost draft

Draft text for the Devpost page. Every claim points at code on this branch or at a measured run. Anything not verified is marked `TODO(verify)` or `TODO(measure)`. Delete a marked sentence rather than guess.

**Read this first:** FirstHour is a hackathon prototype, **not a medical device**, and it has not been tested clinically. Margaret, her medications and every phone number are fictional demo data (`backend/app/profile.py`). Nobody fell for real: fall detection was tuned on bottle drops on a table (`edge/calibration/README.md`).

---

## Inspiration

Many falls at home happen in the bathroom. That is where people are least likely to carry a phone, and where nobody wants a camera. A fall pendant only helps if the person wears it and can press it. We wanted a device that notices the fall, asks the person how they are, tells someone who can help, and lets that person in. It also had to stay safe when the Wi-Fi drops.

## What it does

FirstHour is a camera-free fall-response node for a bathroom. An ESP32-S3-BOX-3 senses, a Raspberry Pi 5 on QNX 8 decides, and a laptop handles AI and messaging. It covers five moments:

1. **The thud.** The BOX-3 streams its microphone peak and accelerometer 20 times a second (`arduino/hub/hub.ino`). The Pi opens an incident only when a sound spike and an acceleration spike agree within 400 ms (`FUSION_WINDOW_MS` in `edge/config.py`). Before any network call, the Pi turns the alarm on and the BOX-3 screen shows `TIME DOWN mm:ss` (`edge/sensor_node.py`).
2. **The voice.** The laptop says "This is an automated assistant." and then "Are you okay? Say 'I'm fine' or 'I need help.'" from a fixed five-question script (`QUESTIONS` in `backend/app/triage.py`). The fixed lines are pre-generated ElevenLabs audio. The person answers out loud. The laptop records the answer and the Pi transcribes it with whisper.cpp from QNX's open-source repository. Only a clear "I'm fine" cancels. Silence escalates.
3. **The text.** The caregiver gets an iMessage triage card through Photon: time down, what the person said, and "on a blood thinner" from the profile, never from a model (`format_alert` in `backend/app/comms.py`). The first alert asks "How soon can you be here?" and carries no door code. A clear "on my way" or an ETA claims the incident, is read aloud to the person, and gets the one-time door code texted back to that caregiver only (`backend/app/coordinator.py`). Questions get a texted answer built from incident facts. Other messages are rewritten into one calm sentence and read aloud.
4. **The door.** `UNLOCK <code>` opens the door only when every unlock rule passes: an open incident, an enrolled number, the one-time 4-digit code for this incident, fewer than 3 wrong tries, and a signed, fresh, single-use command. The Pi then checks on its own that the command names its own open incident (`backend/app/unlock.py`, `backend/app/security.py`, `edge/security.py`). The latch is **simulated**: we had no servo. The BOX-3 shows `LATCH OPEN`, answers `ACK,U` and relocks itself after 30 s (`HOLD_MS` in `hub.ino`). The dashboard shows the door open only after the Pi reports that acknowledgement.
5. **Offline.** The Pi's safety process keeps the alarm and the clock running with no network. Events wait in a local outbox and replay on reconnect. Offline, nothing unlocks the door.

After the five questions, an ElevenLabs companion agent keeps the person company and decides what is worth texting to the caregiver. If no caregiver replies at all within 20 s (`VOICE_CALL_AFTER_SECONDS` in `.env.example`), the backend places a voice call through an ElevenLabs agent on a Photon phone number.

A live dashboard (`dashboard/index.html`) shows the state, the clock, the triage record next to the person's exact words, the conversation, the caregivers panel and every unlock attempt with its reason. A caregiver-phone simulator (`dashboard/caregiver-simulator.html`) posts to the same endpoint as Photon.

## How we built it

- **Sensor hub (`arduino/hub/hub.ino`):** Arduino-ESP32 firmware on the ESP32-S3-BOX-3. It drives the board's own IMU, microphones, LCD and speaker, so there is no wiring. Details in `docs/espressif.md`.
- **Edge (`edge/`):** Python on QNX 8 on a Raspberry Pi 5. QNX has no USB-serial driver for the BOX-3, so the Pi reads it over raw USB with pyusb and libusb, in a restartable child process. `edge/supervisor.py` runs the safety process at QNX priority 21, the network uplink at 10 and `whisper-server` at 8, and restarts any that die. The uplink signs nothing: every signature and command check happens in the safety process (`edge/link.py`, `edge/uplink.py`).
- **Backend (`backend/`):** FastAPI on the laptop. Incident state machine (`incident.py`), rule-based triage with a StepFun refinement (`triage.py`), caregiver coordination (`coordinator.py`), unlock rules and HMAC signing (`unlock.py`, `security.py`), cached ElevenLabs speech (`voice.py`), voice calls and the companion token (`calls.py`), Sentry tracing and logs (`telemetry.py`).
- **Comms gateway (`comms-gateway/`):** Node and TypeScript on Photon's 2.0 beta SDK (`@photon-ai/api` pinned to `0.5.0`). Alerts go out; replies are polled every 2 s and forwarded to the backend with an HMAC signature.
- **Dashboard (`dashboard/`):** static HTML and JavaScript with no build step.
- **Tests:** 476 pytest cases (backend and edge) and 23 vitest cases (gateway), measured Oct 18.

## Challenges we ran into

- **No USB-serial driver on QNX.** The Pi 5 image has no driver for the BOX-3's USB serial port. We talk to it over raw USB with pyusb and the `libusb` apk, which only works as root on this image (`edge/README.md`).
- **A board reset could crash the reader.** A physical reset of the BOX-3 aborted the native libusb code before Python could catch anything. We moved USB access into a child process that the safety process restarts, so the incident, clock and alarm survive (`edge/README.md`, "Recover after a BOX reset").
- **No microphone on the Pi.** The USB webcam's microphone failed in QNX `io-snd`. The microphone moved to the laptop: the dashboard records each answer, the backend queues a signed `transcribe` command, and the Pi fetches the clip once with a signed request and transcribes it.
- **whisper.cpp stalled on 4 threads.** On the 4-core Pi 5, 4 threads took 272 ms per decoded token and 3 threads took 9.5 ms (`edge/README.md`). We run 3.
- **StepFun latency varies a lot.** `step-5-preview` triage took 5-38 s on Saturday and 8-22 s on Sunday morning. The caregiver-reply rewrite went from 1-1.8 s on Saturday evening to 6-12 s on Sunday morning. We kept the model off the critical path: an instant rule-based record drives escalation and the alert, and StepFun refines it in the background.
- **Photon gives each caregiver a different shared line.** Our gateway only accepted replies to one number and silently dropped real replies. It now accepts replies on any of the project's lines (`comms-gateway/README.md`).
- **Privacy leaks found through observability.** The Sentry FastAPI integration attaches request bodies even with `send_default_pii=False`, which would have sent `UNLOCK <code>` replies out. Phone numbers also leaked through Sentry Logs parameters. Both are fixed and tested (`docs/sentry-story.md`, `backend/tests/test_telemetry.py`).
- **A live stress run on Sunday found five more bugs.** A redelivered fall could reopen a cancelled incident; a `nan` sensor value could poison the accelerometer baseline; one device's help button could escalate another device's incident. All fixed with tests (`TODOS.md`, "Live stress test").

## Accomplishments that we're proud of

- **The Pi decides.** Detection, alarm, clock, signature checks and the final unlock decision run on the Pi. The cloud can ask; it cannot open the door.
- **The QNX process split works on the real Pi.** `pidin` shows the safety process at 21, the uplink at 10 and `whisper-server` at 8. A killed uplink reached the backend again 1.6-1.8 s after the kill. A killed safety process resumed the same incident and reclaimed the hub in 1.8-3.9 s (`IMPLEMENTATION.md` 6.10).
- **The unlock chain ran end to end on the hardware.** On the real Pi, a stranger with the right code was refused, the enrolled caregiver was accepted, the Pi verified the signed command, and the BOX-3 answered `ACK,U`, showed `LATCH OPEN` and relocked itself (`README.md`, M4).
- **Live iMessage on Oct 18.** The alert was delivered to a real phone, a real reply reached the backend 3.5 s after it was sent, and the door-code text was accepted by Photon 0.3 s after the claim. A real `UNLOCK` reply from the phone came through Photon and was correctly refused because the 600 s unlock window had passed.
- **The AI never decides anything that matters.** The question script is fixed, blood thinners come from the profile, unknowns stay `null`, the model can add only worrying findings, and the raw transcript sits next to the record.
- **Honest status everywhere.** The door says "Unlocking..." until the Pi confirms. "Sent" means Photon accepted the text, never "delivered".

## What we learned

- Put the model next to the critical path, not in it. Rules first, model in the background, rules win.
- A real-time OS shows its value when you can kill the network process during an incident and the alarm does not blink.
- Writing tests that capture exactly what an SDK sends finds privacy bugs that reading the docs does not.
- Live services behave differently from their docs and from yesterday. Measure on the day and keep every fallback.

## What's next

- Full calibration: about 20 trials each of falls, sits, drops, door slams, footsteps and claps, then a false-alarm count. Today's thresholds are provisional, from 19 bottle drops on one table, and no non-fall checks have run (`edge/calibration/README.md`).
- Unplug the Wi-Fi for the offline test. So far the backend was paused and the uplink was killed; the Wi-Fi itself was not disconnected.
- Play the first question and run the silence deadline on the Pi itself, so the first check works with the laptop gone.
- Make the latch a QNX resource manager that only the safety process may write.
- A paramedic handoff sheet page (not built).
- Test with clinicians and older adults before calling it anything more than a prototype.

## Built with

Python, FastAPI, Uvicorn, Pydantic, pytest, TypeScript, Node.js, Vitest, HTML, JavaScript, WebSockets, QNX 8, Raspberry Pi 5, Espressif ESP32-S3-BOX-3, Arduino-ESP32, FreeRTOS, LovyanGFX, pyusb, libusb, whisper.cpp, StepFun `step-5-preview`, ElevenLabs (text to speech and Agents), Photon 2.0 beta (`@photon-ai/api`), Sentry, cloudflared.

---

## Prizes and tracks

| Prize | Entering | Notes |
| --- | --- | --- |
| Main track: Beyond the Digital | Yes | Hardware and edge AI in a physical safety device |
| Applied Intelligence track | Yes, **pending organizer confirmation that it can be combined** with Beyond the Digital | See `docs/prize-admin.md` |
| QNX | Yes | QNX 8 on the Pi 5, whisper.cpp from oss.qnx.com |
| Espressif: Best Use of Espressif | Yes | `docs/espressif.md` |
| StepFun: Get It Done with Step 5 Preview | Yes | |
| Photon: Messaging/Calling Agent | Yes | |
| Sentry: Best Use of Sentry | Yes | |
| MLH: Best Use of ElevenLabs | Yes | |
| MLH: GoDaddy domain | Yes | TODO: register `<domain>` and link it |
| Cognition: Devin | Not entered | |
| Paper | Not entered | |
| Base44 | Not entered | |

## Sponsor tools and exactly how we use them

### Main track: Beyond the Digital

FirstHour is intelligence in a physical room. A BOX-3 hears and feels the fall, a QNX Pi decides on its own and sounds the alarm, a simulated latch opens only after the Pi verifies a signed command, and the person talks to it out loud. Files: `arduino/hub/hub.ino`, `edge/`, `backend/app/unlock.py`.

### Applied Intelligence track

Pending organizer confirmation that it can be combined with Beyond the Digital. Our case: AI is applied to a narrow real-world job (understanding a spoken answer, structuring triage, wording messages, keeping company), and deterministic rules keep control of every safety decision. Files: `backend/app/triage.py`, `backend/app/coordinator.py`, `edge/sensor_node.py`.

### QNX

- **OS:** QNX 8 on a Raspberry Pi 5. `uname` on our board reported `QNX qnxpi 8.0.0 ... RaspberryPi5 aarch64le` on the 8.0.5 apk image (issue #22). Python 3.14 in a venv (`connect.md`).
- **Open-source AI module from oss.qnx.com:** `whisper.cpp`, installed with `apk add whisper.cpp`. It transcribes the person's spoken answers on the Pi. The Pi keeps one `whisper-server` on `127.0.0.1:8090` at QNX priority 8 with 3 threads, and falls back to `whisper-cli` (`edge/README.md`, `edge/sensor_node.py`). The demo model is `ggml-base.en`: measured on the Pi with live clips up to 8 s, whisper took 2.7-3.1 s per clip, and clip upload to answer applied took 3.8-4.3 s. `ggml-tiny.en` is also on the Pi: 1.16-1.55 s per clip (`edge/README.md`). The words are never logged.
- **Processes and priorities:** `edge/supervisor.py` starts each child with `on -p` so `pidin` shows real priorities: safety at 21 (sensors, fusion, incident, alarm, clock, signatures, command checks, latch), uplink at 10 (HTTP, outbox, clip fetch; signs nothing), `whisper-server` at 8. They talk over a Unix socket (`edge/link.py`). The safety process never blocks on the link. HAM is not on the image, so the supervisor is the restarter. A restarted safety process resumes its open incident and refuses commands signed before it started.
- **Measured on the Pi, Oct 18:** a killed uplink was back at the backend in 1.6-1.8 s; a killed safety process resumed its incident and reclaimed the hub in 1.8-3.9 s (`IMPLEMENTATION.md` 6.10).
- **Raw USB on QNX:** pyusb 1.3.1 and the `libusb` apk, USB in a restartable child so a board reset cannot take down the safety process (`edge/README.md`).

### Espressif: Best Use of Espressif

The ESP32-S3-BOX-3 is the whole sensor hub, using only the board's own parts (`arduino/hub/hub.ino`, `arduino/hub/README.md`, full write-up in `docs/espressif.md`):

- **IMU:** ICM-42607-P at I2C `0x68`, set to plus or minus 16 g at 400 Hz, low-noise mode, gyro off, read at 2048 LSB/g.
- **Microphones:** the ES7210 dual-mic ADC at I2C `0x40`, configured with Espressif's `es7210_config_codec()` register sequence: standard I2S, 16 bit, 16 kHz from a 4.096 MHz MCLK, 30 dB gain, 2.87 V mic bias. A FreeRTOS task reads I2S and keeps the peak in an atomic, so a blocking read never stalls the serial protocol.
- **Speaker alarm:** the ES8311 codec at I2C `0x18`, initialized from Espressif's driver sequence, with a second FreeRTOS task playing a pulsed tone when the Pi sends `A1`.
- **LCD:** LovyanGFX 1.2.33 drives the ILI9342 panel over SPI. It shows `TIME DOWN mm:ss`, `LATCH OPEN` or `LATCH LOCKED`, and the alarm status.
- **Simulated latch:** `U` opens it and answers `ACK,U`; it relocks itself after 30 s. Compile-time `static_assert`s check the timer boundary and `millis()` rollover.
- **Help button:** BOOT (GPIO 0) sends `B`.
- **Serial protocol:** `S,<ms>,<soundPeak>,<ax>,<ay>,<az>,-1` at 20 Hz, `B`, `ACK,<cmd>`; commands `U`, `L`, `A1`, `A0`, `D,<text>`.

### StepFun: Get It Done with Step 5 Preview

- **Where:** `backend/app/triage.py` (`StepFun`). OpenAI-compatible `POST {STEPFUN_BASE_URL}/chat/completions` over stdlib `urllib`, model from `STEPFUN_MODEL` (default `step-5-preview`), strict JSON, `reasoning_effort` `low`, validated with pydantic.
- **It is called at runtime for three jobs:**
  1. **Triage structuring:** turns the person's spoken answers into a `TriageRecord` (conscious, head strike, pain location, can move, bleeding). Runs in the background after the instant rule-based record, with a 45 s deadline and `max_tokens` 5000.
  2. **Caregiver-reply rewrite:** turns a caregiver's text into one calm sentence of 20 words or fewer, read aloud to the person. Deadline 10 s, then the original text is read.
  3. **Caregiver Q&A:** words the texted answer to a caregiver's question from incident facts only. Deadline 12 s, then a templated status.
- **Task completion and reliability:** the rules always win. Where the rules found nothing, the model may only add a worrying finding (`head_strike=true`, `can_move=false`, `bleeding=true`), never a reassuring one (`merge_model`). On timeout, bad JSON or validation failure, the rule record and templates stay. The incident never waits on the model.
- **Measured Oct 18 (backend logs):** triage 8-22 s this morning (5-38 s on Saturday), reply rewrite 6-12 s (1-1.8 s on Saturday evening), caregiver Q&A 7.4 s. We are upfront: on Sunday morning the rewrite and Q&A often take most of their deadline, and the fallback is what keeps the conversation moving.
- **User understanding and control:** the dashboard shows the raw transcript next to the record, labels a question "Unclear" when the rules could not read the answer and "Not said" when there was no answer, and the operator can type a correction.
- **What it never does:** diagnose, advise, write questions, set `on_blood_thinner`, decide escalation, or touch the door.

**What every other model does:**

- **whisper.cpp on the Pi (QNX):** speech to text for the person's answers. `ggml-base.en` is the demo model in `edge/README.md`; `ggml-tiny.en` is installed as the faster option. It runs on the device, so the first check does not depend on a cloud speech service.
- **ElevenLabs text to speech:** speaks every fixed line (the assistant line, the five questions, closing and status lines). Audio is generated once and cached, so "Are you okay?" plays with no model call.
- **ElevenLabs Agents, two of them:** the caregiver call agent ("FirstHour Caregiver Line") phones a silent caregiver over Photon SIP, and the companion agent ("FirstHour companion") talks with the person after the script and decides what to relay. Neither can unlock the door or see the code (`docs/voice-prompts.md`).
- **Chrome Web Speech API:** the fallback speech recognizer in the dashboard when Pi speech-to-text is off (`PI_STT=0`). Chrome sends that audio to Google's service.
- **The deterministic rules engine:** not a model. Regex rules in `triage.py` and `coordinator.py` read "I'm fine", help, ETAs and claims. They always win, drive escalation and the alert, and decide who gets the door code.

### Photon: Messaging/Calling Agent

- **Built on Photon 2.0 beta:** `comms-gateway/` (`src/photon.ts`, `src/server.ts`, `src/messages.ts`, `src/state.ts`) with `@photon-ai/api` 0.5.0. The backend calls the gateway's `/send` (`backend/app/main.py`).
- **iMessage alert out, replies in:** verified live on Oct 18 with every stub off. ALERTING to Photon accepting the alert took about 0.3 s. A real reply reached `RESPONDING` 3.5 s after the phone sent it (the gateway polls every 2 s). The door-code text was accepted 0.3 s after the claim. A real `UNLOCK 4101` from the phone was not filtered by Photon and was correctly refused because the 600 s unlock window had passed (backend logs and Photon message timestamps).
- **Caregiver coordinator (`backend/app/coordinator.py`):** the alert asks for an ETA and carries no code. A clear "on my way", ETA or "I'm here" claims the incident; hedges like "I might come" never do. The claim is read aloud to the person, other caregivers get a stand-by text, and the one-time code goes only to the caregiver who is coming. Questions are answered by text from incident facts. Delivery status is honest: "sent" means Photon accepted it, never "delivered".
- **Shared lines:** Photon's shared iMessage mode gives each caregiver their own shared line, and a shared line cannot text first. Each caregiver texts the agent line once before the demo. A blocked alert is resent on their first text. A dedicated line is available on the project.
- **Durable inbound:** the gateway's cursor and conversations survive a restart (`GATEWAY_STATE_FILE`), and every forward is HMAC-signed with replay protection.
- **Voice escalation:** if no caregiver replies within `VOICE_CALL_AFTER_SECONDS` (20), the backend places a call through ElevenLabs' outbound SIP API to a Photon SMS number with a voice profile (`backend/app/calls.py`). The call agent gets incident facts, never the code, and nothing said on a call is an unlock. Inbound and outbound test calls between that number and a real phone passed (`TODOS.md`). A call placed by the backend during a live incident: `TODO(verify)`.

### Sentry: Best Use of Sentry

All in `backend/app/telemetry.py`; the story is in `docs/sentry-story.md`.

- **Tracing:** one `fall_incident` transaction per incident, with spans `sensor_event`, `voice_check`, `speech_to_text`, `stepfun_triage`, `voice`, `photon_send`, `voice_call`, `unlock_decision` and `command_to_pi`. Traces arrive in our project.
- **Logs:** every INFO and higher log line, including every unlock attempt with its reason and every StepFun call's latency. This is the unlock audit trail. Phone numbers are masked as `+***0101` and codes as `UNLOCK ****`.
- **Uptime Monitoring:** `GET /health` returns 503 when the Pi has been silent for 30 s, and the tunnel reaches only `/health` and the two voice webhook routes. The monitor on the tunnel URL is still to be created (`docs/prize-admin.md`).
- **Session Replay (conditional):** only if the `backend/sentry-products` branch lands, with all text masked. Not on this branch. Delete this line if it does not merge.
- **How it shaped the build:** the two privacy fixes in Challenges. The slowest-stage finding from a real trace is `TODO(measure)` in `docs/sentry-story.md`.

### MLH: Best Use of ElevenLabs

Three uses, all live code:

1. **Pre-cached text to speech** for the fixed script and every fixed phrase (`backend/app/voice.py`): `POST /v1/text-to-speech/{voice_id}` with `eleven_turbo_v2_5`, cached on disk, played by one ordered worker. Stock voice "Sarah", labeled as an automated assistant; no cloning. Measured Oct 18: fall event to "Are you okay?" played took 4.7-4.8 s on a fresh backend (line generated, not cached) and 0.7 s for a cached line (backend logs).
2. **Caregiver call agent** "FirstHour Caregiver Line" over Photon SIP (`backend/app/calls.py`, `docs/voice-prompts.md`): asks a silent caregiver whether they can come and how soon, answers from incident facts through a tool, and posts back through an HMAC-signed post-call webhook. A commitment on the call gets the door code by text. Live backend-placed call: `TODO(verify)`.
3. **Companion agent** "FirstHour companion" after the fixed script (`dashboard/index.html`, `backend/app/calls.py` `companion_token`, IMPLEMENTATION.md 6.9): talks with the person through the laptop microphone, reads incident facts, and decides what to relay to the caregiver (at most 6 relays per incident, never the code). Live run with a real microphone: `TODO(verify)` (`TODOS.md`).

### MLH: GoDaddy domain

TODO: register `<domain>` and link it to the Devpost page or the repository.

### Not entered

- **Cognition (Devin):** not entered.
- **Paper:** not entered.
- **Base44:** not entered.

---

## Claims checklist before pasting into Devpost

- No accuracy, false-alarm, outcome or security-guarantee claims: check the banned list in `AGENTS.md`, "Honest claims only".
- No false-alarm rate: non-fall checks have not run.
- No end-to-end latency until it is read off a real Sentry trace (`TODO(measure)`).
- The latch is simulated, not a real lock or motor.
- Quote no prize amounts.
