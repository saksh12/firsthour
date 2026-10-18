# FirstHour: Devpost draft

Draft text for the Devpost page. Every claim here points to code on `main` or to a PR. Anything not yet run with real hardware or real keys is marked. Fill the `TODO(measure)` items with numbers from a real run, or delete the sentence.

**Read this first:** FirstHour is a hackathon prototype, **not a medical device**, and it hasn't been tested clinically. Margaret, her medications and every phone number are fictional demo data (`backend/app/profile.py`).

---

## Inspiration

A lot of falls at home happen in the bathroom. That's where people are least likely to have a phone with them, and where nobody wants a camera. Fall pendants only help if the person is wearing one and can press it. We wanted a device that notices the fall, asks the person how they are, tells someone who can help, and lets that person in. It also had to stay safe when the Wi-Fi drops.

## What it does

FirstHour is a camera-free fall-response node for a bathroom. It covers five moments:

1. **The thud.** The edge node opens an incident only when a sound spike and an accelerometer spike agree within 400 ms (`edge/sensor_node.py`, `FUSION_WINDOW_MS` in `edge/config.py`). The incident opens on the device before any network call: buzzer on, `TIME DOWN` clock running.
2. **The voice.** The laptop says "This is an automated assistant." and then asks "Are you okay? Say 'I'm fine' or 'I need help.'" from a fixed five-question script (`backend/app/triage.py` `QUESTIONS`). Only a clear "I'm fine" cancels. Silence for 8 s after a question has played escalates (`backend/app/incident.py`).
3. **The text.** The caregiver gets a triage card: time down, what the person said, structured into a record, plus "on a blood thinner" from the profile, never from a model (`backend/app/comms.py` `format_alert`). Anything the caregiver texts back is rewritten into one calm sentence and read aloud to the person on the floor.
4. **The door.** `UNLOCK <code>` opens the door only when every unlock rule passes: an open incident, an enrolled number, a one-time 4-digit code made for this incident, fewer than 3 wrong tries, and a signed, fresh, single-use command. The Pi then checks independently that the command names its own open incident (`backend/app/unlock.py`, `backend/app/security.py`, `edge/security.py`). The latch is **simulated**, because we had no servo. The Pi sends `U` to the Arduino, and the dashboard shows the door open only after the Pi reports the latch confirmed (`dashboard/index.html`).
5. **Offline.** With the backend unreachable, the device keeps the alarm and the clock running, logs events to a local outbox, and replays them when the network comes back. Offline, nothing unlocks the door (`edge/sensor_node.py`).

A live dashboard (`dashboard/index.html`) shows the state, the clock, the triage card next to the person's exact words, and every unlock attempt with its reason. A caregiver-phone simulator (`dashboard/caregiver-simulator.html`) posts to the same endpoint as the real messaging path.

## How we built it

- **Edge (`edge/`)**: plain Python, stdlib only, so it can run on QNX. It handles two-sensor fusion, the local incident and alarm, a signed event outbox with replay, signed command polling (the Pi never accepts inbound connections), and command verification. `edge/sim_sensors.py` is a fake Arduino for testing on a laptop.
- **Backend (`backend/`)**: FastAPI. It has the incident state machine (`incident.py`), rule-based triage with a StepFun refinement (`triage.py`), unlock rules and HMAC signing (`unlock.py`, `security.py`), cached TTS (`voice.py`), Sentry tracing and logs (`telemetry.py`), and the dashboard WebSocket.
- **Comms gateway (`comms-gateway/`)**: Node + TypeScript on Photon's 2.0 beta SDK. Alerts go out and caregiver replies come back in.
- **Dashboard (`dashboard/`)**: static HTML/JS, no build step.
- **Tests**: `cd backend && pytest -q` gives 286 passed, and `cd comms-gateway && npm test` gives 14 passed (both run on `main` while writing this). CI runs both on every push (`.github/workflows/`).
- **Arduino (`arduino/hub/`)**: `TODO`. No sketch is on `main` yet. The serial protocol is in `AGENTS.md`, and the edge node runs against `sim_sensors.py` until the sketch lands.

## Challenges we ran into

- **StepFun is slow on triage.** On our key, `step-5-preview` triage took 4.8 to 6.0 s (PR #16) and then 5 to 13 s (PR #17), so a 3 s timeout always fell back. We moved triage off the critical path (PR #17). An instant rule-based record drives escalation and the alert, and StepFun refines the dashboard card in the background with a 15 s cap. The caregiver-reply rewrite went from 6 to 12 s to **1.0 to 1.8 s** with an "answer immediately" prompt (PR #17).
- **Privacy leaks we found through observability.** The Sentry FastAPI integration attaches request bodies even with `send_default_pii=False`, which would have sent `UNLOCK <code>` replies out. Phone numbers also leaked through Sentry Logs' raw format arguments. Both are fixed and covered by tests (`docs/sentry-story.md`, `backend/tests/test_telemetry.py`).
- **Messaging on a shared line.** A shared Photon iMessage line can't send the first message, so the caregiver has to text the agent line once before the demo. If an alert was blocked, the backend resends it as soon as the caregiver texts (`backend/app/main.py`, `comms-gateway/src/photon.ts`).
- **Hardware we didn't get.** We had no NFC reader and no servo, so we dropped NFC and simulated the latch, while keeping the signature chain and the Pi-side checks exactly as strict (PR #16).
- **Review bugs.** A multi-model review found real problems: the one-time code could be reused, the door could show open without the latch confirming, and command polls weren't signed. We fixed them in PRs #21/#25 and #27.

## Accomplishments that we're proud of

- The safety logic lives on the device. Detection, alarm, signature checks and the final unlock decision run on the edge node, and the cloud can only ask.
- The unlock path refuses a stranger, a wrong code, a replayed command, and a command for another incident. Each of these has a test (`backend/tests/test_unlock.py`, `test_edge_security.py`).
- The AI never decides anything that matters. The question script is fixed, blood thinners come from the profile, unknowns stay `null`, and the raw transcript sits next to the record so people can check what it understood.
- One Sentry trace per incident, with the unlock code and phone numbers scrubbed.

## What we learned

- Put the model next to the critical path, not in it. A rules-first record with a model refinement in the background kept the demo fast and still used StepFun on every incident.
- Writing tests that capture exactly what an SDK sends finds privacy bugs that reading the docs doesn't.
- "Honest by default" UI is more work but worth it. The door says "Unlocking…" until the device confirms, and "Unlock failed" if it doesn't.

## What's next

- Run the edge on QNX on the Pi, with the open-source AI module from `oss.qnx.com` (not done yet, see below).
- Write the Arduino sketch and calibrate the thresholds with ~20 trials per action (tool in PR #15), then measure the false-alarm rate (`TODO(measure)`).
- Speech input with a real microphone (PR #28), and later a help button and tap input for people who can't speak.
- A paramedic handoff sheet page (not built).
- Test with clinicians and older adults before calling it anything more than a prototype.

## Built with

Python, FastAPI, Uvicorn, Pydantic, pytest, TypeScript, Node.js, Vitest, HTML/JavaScript, WebSockets, StepFun `step-5-preview`, ElevenLabs, Photon 2.0 beta (`@photon-ai/api`), Sentry, Raspberry Pi, Arduino, Devin. Add QNX only if it ran on the Pi.

---

## Sponsor tools and exactly how we use them

### QNX

- **Plan:** `edge/` runs on a Raspberry Pi on QNX and reads the Arduino over USB serial. The code is stdlib-only Python for that reason (`edge/config.py`, `edge/security.py`, `edge/sensor_node.py`).
- **Status: not yet run on QNX.** The serial device path and pyserial on the QNX image are still `TODO(verify)` in `edge/config.py` and `edge/sensor_node.py`. The `oss.qnx.com` AI module is `TODO(verify)` in `AGENTS.md`, and the Gate 1 decision in `TODOS.md` is blank. Unless QNX boots on the Pi and runs this code with a named module, **don't enter this prize** and say "the edge runs on Linux".

### StepFun: `step-5-preview`

- **Where:** `backend/app/triage.py` (`StepFun`). It's an OpenAI-compatible `POST {STEPFUN_BASE_URL}/chat/completions` over stdlib `urllib`, with the model from `STEPFUN_MODEL` (default `step-5-preview`) and `reasoning_effort: "low"`.
- **What it does at runtime:**
  1. **Triage**: it turns the person's spoken answers into a pydantic-validated `TriageRecord` with strict JSON and a 15 s cap. It runs in the background after the instant rule-based record (`parse_rules`), and a stale result is dropped.
  2. **Reply rewrite**: it turns a caregiver's text into one calm sentence of 20 words or fewer, with no new numbers and a 3 s cap. Otherwise the original text is read.
- **What it never does:** diagnose, advise, write questions, set `on_blood_thinner`, or decide escalation or unlocks.
- **Other models in the pipeline:** ElevenLabs (text to speech only), and Chrome's built-in speech recognition (speech to text, PR #28, if merged). The rule-based parser is code, not a model.
- **Measured** (PR #17, and the `AGENTS.md` latency table): triage 5 to 13 s on `low` (lands in about 10 s live), rewrite 1.0 to 1.8 s, `POST /person/answer` about 3 ms. The README Status row ("5-7 s, 3 s timeout always falls back") is from before #17.
- **User control:** the dashboard shows the raw transcript next to the record, and the typed box lets the operator correct an answer.

### Photon: 2.0 beta

- **Where:** `comms-gateway/` (`src/photon.ts`, `src/server.ts`, `src/messages.ts`), using `@photon-ai/api` pinned to `0.5.0` (`comms-gateway/package.json`). The backend calls the gateway's `/send` in `backend/app/main.py`.
- **What it does:** sends the caregiver triage alert over iMessage and polls for replies every 2 s, which it forwards to `POST /comms/inbound`.
- **Status:** a read-only call with our keys works (README "Keys", PR #16). A full live alert-out / reply-in run is still pending (README Status M3: 🔶). Voice calls are **not used**. The demo fallback is `dashboard/caregiver-simulator.html`, which posts to the same inbound endpoint.

### Sentry

Products used, all in `backend/app/telemetry.py` (`sentry-sdk==2.71.0` in `backend/requirements.txt`), with the story in `docs/sentry-story.md`:

- **Tracing**: one `fall_incident` transaction per incident, with spans `sensor_event`, `voice_check`, `voice`, `stepfun_triage`, `photon_send`, `unlock_decision` and `command_to_pi`. Traces arrive in our project (README "Keys").
- **Logs**: every INFO+ line goes out, including every unlock attempt with its reason and StepFun latency. Phone numbers are masked as `+***0101` and codes as `UNLOCK ****`. Logs arrive in our project (README "Keys").
- **Uptime Monitoring**: `GET /health` returns 503 when the Pi has been silent for 30 s, and the tunnel reaches `/health` only (`backend/app/main.py`). **The monitor itself isn't set up yet** (README Status: "Uptime monitor on a tunnel still to set up").
- **Session Replay: not used.** The dashboard has no Sentry browser SDK.
- **How it shaped the build:** the two privacy fixes above. The "slowest stage" finding is `TODO(measure)` in `docs/sentry-story.md`.

### ElevenLabs (MLH)

- **Where:** `backend/app/voice.py`. It calls `POST /v1/text-to-speech/{voice_id}` with model `eleven_turbo_v2_5`, caches the audio on disk, and prewarms the five fixed questions and the assistant line when the backend starts, so "Are you okay?" plays with no API call. One worker thread plays lines in order. The fallbacks are `afplay`, then `say --`, then log-only.
- **Voice:** the stock voice "Sarah" on the free tier, with no cloning (README "Keys"). It's labeled as an automated assistant. **STT is not used.**
- **Status:** synthesis with the real key works (PR #16). The end-to-end check in PR #19 ran against a **local mock** endpoint. Real ElevenLabs latency on the demo laptop is `TODO(measure)`.

### Cognition: Devin

Devin opened these PRs (author `devin-ai-integration[bot]` on GitHub). Humans reviewed and merged them:

| PR | State | What Devin built |
| --- | --- | --- |
| [#14](https://github.com/saksh12/firsthour/pull/14) | Open | `/comms/inbound` accepts the gateway's `{from, body, …}` shape, HMAC-signed inbound with replay protection, malformed bodies get 422 |
| [#15](https://github.com/saksh12/firsthour/pull/15) | Open | `edge/calibrate.py`: records labeled trials and analyzes them into suggested thresholds. Its sample output is from **simulated** data |
| [#19](https://github.com/saksh12/firsthour/pull/19) | Merged | `backend/app/voice.py`: cached ElevenLabs TTS, single ordered playback worker, `say` option-injection fix, hermetic tests |
| [#28](https://github.com/saksh12/firsthour/pull/28) | Open | Dashboard push-to-talk speech input (Web Speech API) and optional Auto-listen. Tested only with a stubbed recognizer, not a real mic |
| this PR | Open | `docs/demo-script.md` and this draft |

Only the merged work (#19) is on `main`. Update the table when the others merge.

### Paper / Base44

Not claimed. The team hasn't picked one (`TODOS.md` Phase 0), and the dashboard wasn't built to either brief.

---

## Claims checklist before you paste this into Devpost

- No "99% accurate", "zero false alarms", "detects every fall", "clinically proven", "saves lives" or "unhackable".
- No false-alarm rate until calibration and a 20-trial non-fall check are done (`TODO(measure)`).
- No end-to-end latency until it's read off a real Sentry trace (`TODO(measure)`).
- Don't say QNX unless it ran on the Pi.
- Don't say the latch is a real lock or motor. It's simulated.
