# FirstHour: Implementation Guide

> Camera-free QNX safety node that hears a fall, checks on the person in a familiar voice, relays triage to family by text, and lets help in through a guarded door unlock.
>
> **Event:** LA Hacks AI Hackathon, UCLA, Oct 17-18, 2026
> **Deadline:** Sunday Oct 18, 12:00 PM (per the Even starter pack; confirm on the LA Hacks schedule)
> **Written:** Saturday Oct 17, ~1:40 PM. About 22 hours left. Plan for 20.
> **Companion file:** `AGENTS.md` (rules for teammates and AI coding agents)

---

## 0. Read this first (2 minutes)

**What we are building:** a small box in a bathroom. When someone falls, it:

1. detects the fall locally using two sensors (sound thud + floor vibration),
2. asks "are you okay?" out loud in a calm, familiar synthetic voice,
3. runs a short scripted triage (head hit? pain where? can you move?),
4. texts a caregiver a clear summary and lets them reply,
5. unlocks the front door (a servo prop) only if strict rules pass.

**Non-negotiables (tape these to the table):**

1. **Vertical slice first.** A fake fall must travel through the whole system, using stubs, by **6:00 PM**. Real parts replace stubs one by one.
2. **Every external service has a stub and a fallback.** If Photon, ElevenLabs, StepFun or Sentry breaks at 3 AM, the demo still runs.
3. **QNX decision gate at 7:00 PM.** If QNX is not booting and reading a sensor by then, we drop the QNX prize and run plain Linux. We do not burn the night on it.
4. **No answer means escalate.** Silence after a fall is the worst case, so it triggers the alert.
5. **The Pi is the final gatekeeper for the door.** The cloud can ask. The Pi decides.
6. **Honest claims only** (see section 15). No made-up accuracy numbers.
7. **Code freeze Sunday 8:30 AM.** After that, only rehearsal and submission.
8. **Submit by 11:00 AM**, not 11:59.
9. **Nobody actually falls.** The "fall" is a beanbag drop plus a controlled sit onto a crash mat or thick cushion.

---

## 1. The five demo moments we must nail

Everything in this guide serves these. If a task does not help one of them, it waits.

| # | Moment | What judges see and hear | Main tech |
| --- | --- | --- | --- |
| M1 | The thud | Drop on the mat. Display flips to `TIME DOWN 00:01`. Terminal shows both sensors spiking. | QNX Pi, accelerometer, sound sensor |
| M2 | The voice | A calm voice asks if the person is okay. Teammate answers. A short triage follows. | ElevenLabs, StepFun |
| M3 | The text | Caregiver phone buzzes with a triage card. Caregiver replies. Reply is read aloud to the person on the floor. | Photon, StepFun |
| M4 | The door | Caregiver replies `UNLOCK <code>`. Servo clicks the latch open. A stranger's text is refused. | Rules + signed command + Arduino servo |
| M5 | The offline test | Unplug Wi-Fi. The box still alarms, shows the clock, and unlocks on an NFC tag tap. | QNX local logic, NFC |

Bonus if time allows: paramedic NFC tap opens the handoff sheet on a phone or laptop.

---

## 2. Assumptions and open questions

We assumed these. **Each one needs an owner to confirm it.** Cross them off here.

| # | Question | Assumed answer | Owner | By |
| --- | --- | --- | --- | --- |
| Q1 | Team size and skills | 4 people (roles in section 5) | All | 2:00 PM |
| Q2 | Which QNX board image do they give us, and which AI modules exist at oss.qnx.com? | Pi 4 class board; one audio/ML module usable | Edge lead | 3:00 PM |
| Q3 | Does Photon 2.0 beta support **outbound phone calls**, and does an inbound reply give us the **sender's number**? | Unknown. iMessage/SMS sending is documented; voice calling is not confirmed. | Comms lead | 3:00 PM (ask at the Photon table) |
| Q4 | StepFun base URL for our key | Docs disagree between `api.stepfun.com/v1` and `api.stepfun.ai/v1`. Use whichever matches the key. | Backend lead | 2:30 PM |
| Q5 | ElevenLabs: is voice cloning available on our tier? Realtime speech-to-text? | Maybe not. Fallback is a stock voice. | Comms lead | 2:30 PM |
| Q6 | Sentry Uptime Monitoring needs a public URL | We expose `/health` through a tunnel (ngrok or cloudflared) | Frontend/Sentry | 4:00 PM |
| Q7 | Arduino model, Grove shield, accelerometer chip, NFC board type, which NFC tags we get | Unknown. Read the module labels. | Edge lead | 2:30 PM |
| Q8 | Paper or Base44? | Pick one after reading both challenge pages. Do not chase both. | Frontend | 3:00 PM |
| Q9 | Who plays the "caregiver" phone? | A teammate's phone, enrolled in the allowlist. Judges can hold it. | All | before demo |
| Q10 | Are there extra sponsor submission forms? | Check each challenge's "View Additional Information" page. | Frontend | 5:00 PM |

---

## 3. Architecture

```text
 FLOOR / BATHROOM                       LAPTOP (the brain)                      THE WORLD
 ┌───────────────────────┐  USB serial  ┌────────────────────────────────┐
 │ ARDUINO (sensor hub)  │◄────────────►│ PI RUNNING QNX (edge node)     │
 │  sound sensor         │  lines/JSON  │  fall detection (fusion)       │
 │  accelerometer        │              │  local alarm + display state   │
 │  ultrasonic (floor)   │              │  verifies SIGNED commands      │
 │  NFC board            │              │  offline NFC unlock            │
 │  servo (door latch)   │              │  heartbeat to backend          │
 │  buzzer / LED / LCD   │              └──────────────┬─────────────────┘
 └───────────────────────┘                     HTTP over local Wi-Fi
                                                       │ events up, commands polled down
                                       ┌───────────────▼────────────────┐
                                       │ BACKEND (FastAPI, Python)       │
                                       │  incident state machine         │     ┌──────────┐
                                       │  triage (StepFun)               │────►│ StepFun  │
                                       │  voice (ElevenLabs)             │────►│ElevenLabs│
                                       │  unlock rules + signing         │     └──────────┘
                                       │  Sentry spans, logs             │────►┌──────────┐
                                       └───────┬───────────────┬─────────┘     │ Sentry   │
                                               │               │               └──────────┘
                              ┌────────────────▼───┐   ┌───────▼──────────┐
                              │ COMMS GATEWAY      │   │ DASHBOARD (web)  │
                              │ (Node, Photon SDK) │   │ live incident,   │
                              │ text / call out,   │   │ handoff sheet,   │
                              │ replies in         │   │ phone simulator  │
                              └────────┬───────────┘   └──────────────────┘
                                       ▼
                                caregiver's phone
```

**Why it is split this way:**

- **Arduino = sensors and actuators.** Analog sensors (the Grove sound sensor) cannot be read by a Pi directly, and Grove/GPIO drivers on QNX are a time sink. The Arduino reads everything and prints lines over USB serial.
- **Pi on QNX = safety brain.** Fusion logic, alarm state, signature checks, offline unlock. This is the part the QNX prize is about, so it must hold up with the network gone.
- **Laptop = heavy AI and comms.** StepFun, ElevenLabs, Photon, Sentry. The Pi never needs these to stay safe.
- **Pi polls the backend for commands** instead of accepting inbound connections. No port or firewall problems on venue Wi-Fi.

**Design rules:**

1. The backend never touches hardware directly. It issues signed commands. The Pi verifies and acts.
2. Every command carries a timestamp and nonce. The Pi rejects stale or repeated ones.
3. All times are UTC epoch milliseconds in messages.
4. All state changes are logged with the incident id.

---

## 4. Repo layout and setup

```text
firsthour/
├── AGENTS.md
├── IMPLEMENTATION.md
├── .env.example
├── arduino/
│   └── hub/hub.ino            # sensors + servo + NFC + buzzer
├── edge/                      # runs on the Pi (QNX), plain Python
│   ├── sensor_node.py         # serial reader, fusion, event posting, command polling
│   ├── security.py            # HMAC verify, nonce cache
│   ├── config.py
│   └── sim_sensors.py         # fake sensor feed for laptop testing
├── backend/                   # FastAPI
│   ├── app/main.py
│   ├── app/incident.py        # state machine
│   ├── app/triage.py          # StepFun + rule-based fallback
│   ├── app/voice.py           # ElevenLabs + audio cache + stub
│   ├── app/comms.py           # talks to comms-gateway, formats alerts
│   ├── app/unlock.py          # rules (the important file)
│   ├── app/security.py        # signing
│   ├── app/telemetry.py       # Sentry setup + span helpers
│   ├── app/models.py          # pydantic schemas (the contracts)
│   └── tests/
├── comms-gateway/             # Node/TypeScript, Photon SDK wrapper
├── dashboard/                 # web UI
├── scripts/
│   ├── simulate_fall.py       # fires a fake fall event at the backend
│   ├── send_test_alert.py
│   └── demo_reset.sh          # resets state between rehearsals
└── docs/
    ├── sentry-story.md        # running notes for the Sentry challenge
    ├── demo-script.md
    └── devpost-draft.md
```

**One-time setup (everyone):**

```bash
git clone <repo> && cd firsthour
cp .env.example .env            # then fill in keys (never commit .env)
# backend
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# comms gateway (new terminal)
cd comms-gateway && npm install && npm run dev
# dashboard (new terminal)
cd dashboard && npm install && npm run dev
```

**`.env.example`:** see the file in the repo root (stub switches, StepFun, ElevenLabs, Photon, Sentry, `SHARED_SECRET`, `CAREGIVER_NUMBERS`, `NFC_ALLOWED_UIDS`, `INCIDENT_WINDOW_SECONDS=600`, `UNLOCK_HOLD_SECONDS=30`).

---

## 5. Roles (adjust to your team size)

| Role | Owns | Folders |
| --- | --- | --- |
| **A: Edge lead** | QNX boot, Arduino sketch, sensors, servo, NFC, fusion logic, lock prop | `arduino/`, `edge/` |
| **B: Backend lead** | State machine, StepFun triage, unlock rules, signing, tests | `backend/` |
| **C: Comms and voice lead** | ElevenLabs, speech input, Photon gateway, caregiver simulator | `comms-gateway/`, `backend/app/voice.py`, `backend/app/comms.py` |
| **D: Frontend, Sentry, pitch lead** | Dashboard, Sentry setup and story, Devpost, demo video, judge Q&A | `dashboard/`, `docs/`, `backend/app/telemetry.py` |

Three people? Merge C into B and D. Five? Add a second person on hardware (A2) to build the lock prop and demo set.

**Working rules:** commit often to your own branch, merge to `main` only when `main` still runs, never edit another role's folder without telling them, and update the contracts in section 6 **before** changing code that depends on them.

---

## 6. Contracts (the source of truth)

If code and this section disagree, fix one of them right away and tell everyone.

### 6.1 Arduino to Pi (USB serial, 115200 baud, one line per message, `\n` terminated)

Arduino sends:

```text
S,<ms>,<soundPeak>,<ax>,<ay>,<az>,<distCm>      # sensor sample, 20 per second
N,<uidHex>                                      # NFC tag seen
B                                               # help button pressed (if wired)
ACK,<cmd>                                       # reply to a command
```

Pi sends:

```text
U          # unlock (servo to UNLOCK_DEG, auto relock after hold time)
L          # lock now
A1 / A0    # buzzer on / off
D,<text>   # show short text on LCD/segment display
```

### 6.2 Edge to backend (HTTP POST, JSON)

`POST /edge/event`

```json
{
  "device_id": "bath-01",
  "ts": 1760000000000,
  "type": "fall_suspected",
  "sensors": { "sound_peak": 812, "accel_delta_g": 1.9, "dist_cm": 28 },
  "local_incident_id": "bath-01-1760000000000"
}
```

`type` is one of: `fall_suspected`, `heartbeat`, `help_button`, `nfc_tap`, `local_unlock`, `cancelled`.

Header: `X-Signature: hex(HMAC_SHA256(SHARED_SECRET, ts + "." + raw_body))`.

### 6.3 Backend to edge (Pi polls `GET /edge/commands?device_id=bath-01` every 1 second)

```json
{
  "commands": [
    {
      "id": "cmd-8f2",
      "action": "unlock",
      "incident_id": "inc-12",
      "ts": 1760000012000,
      "nonce": "b61c0e",
      "sig": "<hex HMAC of action|incident_id|ts|nonce>"
    }
  ]
}
```

`action` is one of: `unlock`, `lock`, `alarm_on`, `alarm_off`, `display`.

The Pi **must** check: signature valid, `ts` within 10 seconds, nonce not seen before, and (for `unlock`) a local incident is open.

### 6.4 Incident state machine

| State | Entered when | Leaves when |
| --- | --- | --- |
| `IDLE` | boot, or after resolve | `fall_suspected` event |
| `CHECKING` | fall event received | person says fine (to `CANCELLED`), says help or says nothing for 8 s (to `ALERTING`) |
| `ALERTING` | escalation | caregiver replies (to `RESPONDING`) |
| `RESPONDING` | any caregiver reply | valid unlock (to `UNLOCKED`) or resolve |
| `UNLOCKED` | unlock command accepted | hold time ends or resolve |
| `CANCELLED` | person confirms they are fine | immediate return to `IDLE` |
| `RESOLVED` | caregiver or paramedic marks resolved | return to `IDLE` |

**Rule:** silence is not "fine." Only a clear "I'm fine" cancels.

### 6.5 Triage record (what StepFun must return)

```json
{
  "conscious": true,
  "head_strike": false,
  "pain_location": "right hip",
  "can_move": false,
  "bleeding": false,
  "on_blood_thinner": true,
  "summary": "Conscious. Right hip pain, cannot move leg. No head strike. On a blood thinner.",
  "next_question": "Are you bleeding anywhere?",
  "done": false
}
```

`on_blood_thinner` comes from the patient profile, **not** from the model's guess.

### 6.6 Caregiver messages

Outbound alert text template:

```text
FALL ALERT: Margaret (Bathroom). Down 00:32.
Conscious. Right hip pain, cannot move leg. No head strike.
NOTE: on a blood thinner.
Reply UNLOCK 4821 to open the front door.
Or reply with a message and we'll read it to her.
```

Inbound grammar: `UNLOCK <4 digits>` unlocks if rules pass. Anything else is a message to read aloud (rewritten to one short calm sentence, max 20 words).

**Gateway → backend: `POST /comms/inbound`**

The comms-gateway forwards a caregiver reply as:

```json
{
  "from": "+13105550101",
  "body": "UNLOCK 4821",
  "platform": "imessage",
  "spaceId": "space_1",
  "senderId": "u_agent",
  "messageId": "msg_1"
}
```

The backend reads `from`/`body` (aliases of the simulator page's `sender`/`text`) and ignores the rest.

Headers, same scheme as `POST /edge/event` (section 6.2):

```text
X-Timestamp: <epoch ms>
X-Signature: hex(HMAC_SHA256(SHARED_SECRET, ts + "." + raw_body))
```

A missing, bad, or stale (over 30 s old) signature is refused with 401, a malformed body with 422. Only while `STUB_PHOTON=1` are **unsigned** requests allowed, so the dashboard and caregiver-simulator pages keep working; a signature that is present is always verified. The gateway signs with `SHARED_SECRET` from the root `.env` (unset = forwards go unsigned).

The signature covers the whole body, so a captured reply can be resent inside the window. The backend remembers accepted signatures (and the gateway's `messageId`, which also catches a re-signed copy of the same Photon message) for the freshness window and refuses repeats with 409 `replay`; unsigned stub-mode requests are not deduped.

### 6.7 Dashboard live events (WebSocket from backend)

`incident_update`, `triage_update`, `message_in`, `message_out`, `unlock_attempt` (with result and reason), `device_status`.

---

## 7. The phases (step by step)

Times are for Saturday/Sunday. Each phase ends with an **acceptance test**. Do not move on without it.

### Phase 0: Setup and verification (1:45 to 3:00 PM)

**Everyone**
1. Create the repo, push the skeleton from section 4, add everyone.
2. Agree on roles (section 5). Put your name next to each question in section 2.

**A: Edge lead**
3. Go to the hardware lab. Collect: Raspberry Pi kit, Arduino kit, servo, sound sensor, 3-axis accelerometer, ultrasonic sensor, NFC/RFID board, buzzer, LCD or segment display, LED kit, Logitech webcam (used as a microphone), breadboard, jumper wires.
4. Collect craft supplies: cardboard, tape, popsicle sticks, rubber bands, zip ties.
5. Read the module labels and note the accelerometer chip and NFC board type. Ask the hardware volunteers whether a Grove base shield comes with the Arduino.
6. Go to the QNX table. Ask: which board image, how to flash, which `oss.qnx.com` AI modules work on it, and how audio capture works.

**B: Backend lead**
7. Get the StepFun key and test one call with the base URL that matches it.
8. Create the FastAPI skeleton with `/health`, the models in section 6, and the stub switches.

**C: Comms and voice lead**
9. Go to the Photon table. Ask: do we get outbound calls, do inbound replies carry the sender's number, how do we get credentials.
10. Get the ElevenLabs key. Test one text-to-speech call. Check whether voice cloning is available.

**D: Frontend lead**
11. Create the Sentry project and DSN. Read the Sentry challenge text twice.
12. Read both the Paper and Base44 challenge pages and pick one.

**Acceptance:** `/health` returns OK, one StepFun call and one ElevenLabs call work from a script, and Q2, Q3, Q4 and Q7 are answered in section 2.

### Phase 1: Vertical slice with stubs (3:00 to 6:00 PM)

**Goal:** `python scripts/simulate_fall.py` makes the whole story happen using only fakes. Nothing physical yet.

**B: Backend**
1. Implement the state machine (`incident.py`) from section 6.4.
2. Implement `POST /edge/event` and `GET /edge/commands`.
3. Implement `triage.py` with a **rule-based fallback first** (keyword matching on the answer). Add the StepFun path second.
4. Implement `unlock.py` rules and `security.py` signing (section 9). Write the tests listed there now, before the rest.

**C: Comms and voice**
5. Implement `voice.py`: pre-generate and cache audio for every fixed phrase at startup (so the first question plays instantly). Play audio on the laptop speaker.
6. Choose speech input for the person's answers. Try in this order and stop at the first that works in 15 minutes: ElevenLabs speech-to-text if your key supports it, a streaming provider from the sponsor list, then the browser Web Speech API in a Chrome tab as a last resort.
7. Build the **caregiver phone simulator** page (text box plus "send as +1310...") that posts to the same inbound endpoint Photon will use. This is the fallback if Photon is not ready.

**D: Frontend**
8. Build the live incident page: state badge, big time-on-floor clock, triage card, event log, device status.
9. Wire it to the WebSocket events in section 6.7.

**A: Edge** (runs in parallel, see Phase 2)

**Acceptance (the 6:00 PM test):**
- `simulate_fall.py` runs.
- The dashboard clock starts, the voice speaks, a typed answer becomes a triage card, the simulated caregiver gets the alert, `UNLOCK <code>` from the allowed number flips the virtual door to unlocked, and `UNLOCK 0000` from another number is refused and logged.

### Phase 2: Hardware bring-up (3:00 to 7:00 PM, in parallel with Phase 1)

**A: Edge lead, in this exact order. Stop and fix each step before the next.**

1. **Servo test, no QNX, no Pi.** Wire servo to Arduino (red to 5V, black or brown to GND, signal to pin 9). Upload the sketch in section 8. Type `U` in the Serial Monitor. The servo must move. Tune `LOCK_DEG` and `UNLOCK_DEG`.
2. **Build the lock prop** (cardboard door, hinge of tape, popsicle-stick latch on the servo horn, rubber band to pull the door open). Test 10 unlocks in a row.
3. **Sound sensor.** Print the peak value at 20 Hz. Clap, drop a beanbag, drop a bottle, walk. Note the numbers.
4. **Accelerometer.** Tape it flat on the mat. Use the library named on the module's wiki page. Print x, y, z. Drop the beanbag next to it.
5. **Ultrasonic.** Aim it across the floor at body height. Print distance. Lie a teammate's legs in front of it. Note the readings.
6. **NFC.** Read a tag UID and print it. Write down the UIDs of the tags you will enroll.
7. **Buzzer and display.** Text on the display, buzzer on and off by command.
8. **QNX.** Flash the image, get a shell over serial or SSH, run `python3` (or whichever runtime QNX provides), and read the Arduino serial port from a script.
9. **Edge script.** Put the fusion logic from section 10 into `sensor_node.py`. Test it first on the laptop with `sim_sensors.py`.

**Acceptance:** the Arduino prints clean `S,...` lines, `U` opens the lock prop reliably, and `sensor_node.py` prints "FALL SUSPECTED" when you drop the beanbag.

### Gate 1: QNX decision (7:00 PM sharp)

The QNX prize needs: **QNX OS, plus an open-source AI module from oss.qnx.com**, inside a working product.

Answer these four questions aloud, together:

1. Does the Pi boot QNX reliably?
2. Can a script on QNX read the Arduino serial port?
3. Can it POST an event to the backend over Wi-Fi?
4. Do we know which `oss.qnx.com` AI module we will run and what input it takes?

**If all four are yes:** continue with QNX.
**If any is no:** run `edge/` on plain Linux or on the laptop, tell the team, and drop the QNX prize. Spend the freed hours on the other five sponsors. This is a good trade, not a failure.

### Phase 3: Real integrations (7:00 to 10:30 PM)

Replace stubs one at a time. After each, rerun the Phase 1 acceptance test.

1. **Edge to backend (A and B).** Set `STUB_EDGE=0`. The Pi posts signed events. The Pi polls and verifies signed commands. Test with a wrong secret: the Pi must reject.
2. **StepFun (B).** Set `STUB_STEPFUN=0`. Use the prompt and fallback in section 11. Measure latency and print it in the log. Do not quote a latency number you did not measure.
3. **ElevenLabs (C).** Set `STUB_ELEVENLABS=0`. Generate and cache the fixed phrases. If cloning is available, clone **only a teammate who gave clear consent**. If not, use a stock voice. Keep the line "this is an assistant, not your daughter" in the script.
4. **Photon (C).** Build `comms-gateway` using the docs index at `https://docs.photon.codes/docs/llms.txt`. Send a text to the enrolled number. Receive a reply. If it takes more than 90 minutes, **stay on the simulator** and tell judges plainly that the carrier link is simulated.
5. **Sentry (D).** Initialize Sentry in the backend. Add spans for each stage (section 12). Create the uptime monitor on the tunneled `/health` endpoint.
6. **Local alarm on the Pi (A).** On fall, the Pi turns on the buzzer and shows `TIME DOWN` even if the backend is unreachable.

**Acceptance:** drop the beanbag, hear the voice, receive the real text on a real phone, send `UNLOCK <code>`, and watch the servo click.

### Phase 4: Hardening (10:30 PM to 1:30 AM)

1. **Security tests (B).** Run every case in section 9. All must pass.
2. **Offline test (A).** Turn off Wi-Fi mid-incident. Confirm: alarm stays on, display keeps counting, an enrolled NFC tag unlocks, an unknown tag does not, and everything logs locally. Reconnect and confirm the Pi syncs the local events to the backend.
3. **Thresholds (A).** Run the calibration in section 10. Commit the final numbers.
4. **False-alarm check (A).** Do 20 non-falls (bottle drop, door slam, footsteps, dropping a book). Record how many trigger the voice check. Report the real number, whatever it is.
5. **Dashboard polish (D).** Big type, high contrast, no clutter. Add the paramedic handoff sheet page. Make a phone-sized view.
6. **Sentry story (D).** Write down one real thing Sentry helped you find (a slow call in a trace, a failure in the logs). Put it in `docs/sentry-story.md` while you remember it.
7. **Demo reset (C).** `scripts/demo_reset.sh` must return everything to `IDLE` in one command, including relocking the servo.

**Acceptance:** run the full demo three times in a row with no manual fixes.

### Phase 5: Rest in shifts (1:30 to 7:00 AM)

Tired people ruin demos. Two people sleep first for about 3 hours, then swap. The person awake runs a **soak test**: one fall every 10 minutes, checking for leaks, stuck states, or the serial port dropping. Fix only bugs. No new features.

### Phase 6: Freeze and rehearse (7:00 to 8:30 AM)

1. Code freeze at 8:30. Tag the commit `demo-ready`.
2. Rehearse the demo script (section 13) five times. Time it. It must fit **under 2 minutes**.
3. Rehearse the three fallbacks (what to do if each external service dies).

### Phase 7: Submit (8:30 to 11:00 AM)

1. Record the backup demo video (screen plus box in one take).
2. Fill in the Devpost submission (`docs/devpost-draft.md`). Name every sponsor tool and say exactly how it is used.
3. Complete any sponsor-specific forms found in Q10.
4. **Submit by 11:00 AM.** Screenshot the confirmation.

### Phase 8: Table time (11:00 AM to 12:00 PM and judging)

Set up at your table. Run the demo once for yourselves. Keep the video ready on a laptop. Be there when judges visit.

---

## 8. Hardware build

### 8.1 Wiring (pin numbers are suggestions; the Grove shield's port labels win)

| Part | Connects to | Notes |
| --- | --- | --- |
| Servo | red to 5V, brown or black to GND, signal to **D9** | If it jitters or the Arduino resets, power the servo from a separate 5V source and join the grounds |
| Sound sensor (analog) | **A0** | Needs an analog pin, which is why the Arduino reads it, not the Pi |
| 3-axis accelerometer | I2C (SDA/SCL) | Tape flat on the mat. Use the library named on the module's wiki page |
| Ultrasonic | **D7** (single signal pin on Grove) | Aim across the floor at body height |
| NFC/RFID board | I2C or UART, depending on the board | Check the label. If I2C, it shares the bus with the accelerometer (different addresses) |
| Buzzer | **D5** | |
| LED socket | **D6** | Red = locked, green = unlocked, or breathing green when idle |
| Display (LCD or 4-digit) | I2C or its own pins | Shows `TIME DOWN mm:ss` |
| Help button (optional) | **D4** | Gives a non-speech way to respond |

### 8.2 Lock prop (swinging latch, "Option B")

1. Push a plastic horn onto the servo shaft. Tape or glue a popsicle stick to the horn so it sticks out like a clock hand.
2. Tape the servo next to the door's loose edge (the side away from the hinge). The stick lies **in front of** the door edge with a finger-width gap, so the door cannot swing out.
3. At `LOCK_DEG` (0) the stick blocks the door. At `UNLOCK_DEG` (90) it points away, clear of the door.
4. Hinge the door with a strip of tape. Add a rubber band that pulls the door open once the stick moves. That pop is your demo moment.
5. Keep a small gap while locked. If the door presses hard on the stick, the servo can stall or buzz.

### 8.3 Arduino sketch (`arduino/hub/hub.ino`)

```cpp
#include <Servo.h>

const int PIN_SERVO = 9, PIN_BUZZER = 5, PIN_ULTRA = 7, PIN_SOUND = A0;
const int LOCK_DEG = 0, UNLOCK_DEG = 90;
const unsigned long HOLD_MS = 30000;   // auto relock
const unsigned long SAMPLE_MS = 50;    // 20 samples per second

Servo bolt;
unsigned long relockAt = 0, nextSample = 0;
bool unlocked = false;
float baseline = 512;
int soundPeak = 0;
String line;

long readDistanceCm() {
  pinMode(PIN_ULTRA, OUTPUT);
  digitalWrite(PIN_ULTRA, LOW);  delayMicroseconds(2);
  digitalWrite(PIN_ULTRA, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_ULTRA, LOW);
  pinMode(PIN_ULTRA, INPUT);
  long us = pulseIn(PIN_ULTRA, HIGH, 30000);
  return us == 0 ? -1 : us / 58;
}

void readAccel(float &ax, float &ay, float &az) {
  ax = ay = az = 0;  // TODO: use the library named on the module's wiki page
}

void lockNow()   { bolt.write(LOCK_DEG);   unlocked = false; }
void unlockNow() { bolt.write(UNLOCK_DEG); unlocked = true; relockAt = millis() + HOLD_MS; }

void handleLine(String s) {
  s.trim();
  if (s == "U")       { unlockNow(); Serial.println("ACK,U"); }
  else if (s == "L")  { lockNow();   Serial.println("ACK,L"); }
  else if (s == "A1") { digitalWrite(PIN_BUZZER, HIGH); Serial.println("ACK,A1"); }
  else if (s == "A0") { digitalWrite(PIN_BUZZER, LOW);  Serial.println("ACK,A0"); }
  // D,<text> : write to the display here
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUZZER, OUTPUT);
  bolt.attach(PIN_SERVO);
  lockNow();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(line); line = ""; } else line += c;
  }

  int v = analogRead(PIN_SOUND);
  baseline = baseline * 0.99 + v * 0.01;
  int dev = abs(v - (int)baseline);
  if (dev > soundPeak) soundPeak = dev;

  unsigned long now = millis();
  if (unlocked && now > relockAt) lockNow();

  if (now >= nextSample) {
    nextSample = now + SAMPLE_MS;
    float ax, ay, az; readAccel(ax, ay, az);
    Serial.print("S,"); Serial.print(now); Serial.print(",");
    Serial.print(soundPeak); Serial.print(",");
    Serial.print(ax); Serial.print(","); Serial.print(ay); Serial.print(","); Serial.print(az);
    Serial.print(","); Serial.println(readDistanceCm());
    soundPeak = 0;
  }
  // NFC: when a tag is read, Serial.print("N,"); Serial.println(uidHex);
}
```

**Test it alone first:** open the Arduino Serial Monitor at 115200, type `U`, press Enter. The servo should move and relock after 30 seconds. Do not touch anything else until this works.

---

## 9. Door unlock rules and security

The unlock is the part judges will poke at. Build it carefully and test it hard.

### 9.1 Rules (all must be true)

1. **An incident is open** (a fall was confirmed in the last `INCIDENT_WINDOW_SECONDS`).
2. **Sender is on the allowlist** (`CAREGIVER_NUMBERS`).
3. **The reply contains the one-time code** from the alert text. A fresh 4-digit code is made for each incident and dies when it ends.
4. **Fewer than 3 wrong tries** in this incident.
5. **The command is signed**, fresh (under 10 seconds), and its nonce is new.
6. **The Pi agrees** that a local incident is open before it moves the servo.
7. **Every attempt is logged** (allowed or refused, with the reason).
8. **Auto relock** after `UNLOCK_HOLD_SECONDS`.

### 9.2 Backend rules (`backend/app/unlock.py`)

```python
import time, secrets, hmac, os

ALLOWED = set(os.environ["CAREGIVER_NUMBERS"].split(","))
WINDOW = int(os.getenv("INCIDENT_WINDOW_SECONDS", "600"))
incident = None  # {"id", "code", "expires", "tries"}

def open_incident(incident_id):
    global incident
    incident = {"id": incident_id, "code": f"{secrets.randbelow(10000):04d}",
                "expires": time.time() + WINDOW, "tries": 0}
    return incident["code"]  # goes into the alert text

def try_unlock(sender, text):
    inc = incident
    if not inc or time.time() > inc["expires"]:
        return deny(sender, "no active incident")
    if sender not in ALLOWED:
        return deny(sender, "not enrolled")
    if inc["tries"] >= 3:
        return deny(sender, "locked out")
    parts = text.strip().upper().split()
    if len(parts) != 2 or parts[0] != "UNLOCK" or not hmac.compare_digest(parts[1], inc["code"]):
        inc["tries"] += 1
        return deny(sender, "bad code")
    queue_signed_command("unlock", inc["id"])
    log_event("unlock granted", sender)
    return True
```

`deny()` logs the reason (to Sentry and the dashboard) and returns `False`.

### 9.3 Pi checks (`edge/security.py`)

```python
import hmac, hashlib, time

seen_nonces = set()

def valid_command(cmd, secret, local_incident_open):
    msg = f'{cmd["action"]}|{cmd["incident_id"]}|{cmd["ts"]}|{cmd["nonce"]}'.encode()
    good = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, cmd["sig"]):            return False, "bad signature"
    if abs(time.time() * 1000 - cmd["ts"]) > 10_000:         return False, "stale"
    if cmd["nonce"] in seen_nonces:                          return False, "replay"
    if cmd["action"] == "unlock" and not local_incident_open: return False, "no local incident"
    seen_nonces.add(cmd["nonce"])
    return True, "ok"
```

### 9.4 Tests (write these before the code, run them every time)

| # | Case | Expected |
| --- | --- | --- |
| 1 | Correct number, correct code, incident open | Unlock |
| 2 | Same, but no incident | Refused: no active incident |
| 3 | Unknown number, correct code | Refused: not enrolled |
| 4 | Allowed number, wrong code | Refused: bad code |
| 5 | Three wrong codes, then the right one | Refused: locked out |
| 6 | Incident older than the window | Refused |
| 7 | Command replayed (same nonce) | Pi refuses: replay |
| 8 | Command older than 10 s | Pi refuses: stale |
| 9 | Command with wrong signature | Pi refuses |
| 10 | Unknown NFC tag during an incident | Refused |
| 11 | Enrolled NFC tag during an incident | Unlock |
| 12 | Enrolled NFC tag with no incident | Refused |
| 13 | Relock after hold time | Servo returns to `LOCK_DEG` |
| 14 | Every case above | A log line with the reason |

**Be upfront about limits:** NFC tag IDs can be copied. For the demo that is fine. A real product would use something stronger. Say so if asked.

---

## 10. Fall detection (fusion and calibration)

### 10.1 Logic

A fall is suspected when **both** signals agree within a short window:

```python
WINDOW_MS = 400

class Fusion:
    def __init__(self, sound_t, accel_t):
        self.sound_t, self.accel_t = sound_t, accel_t
        self.last_sound = self.last_accel = -10**9

    def update(self, ts, sound_peak, accel_delta_g):
        if sound_peak > self.sound_t:    self.last_sound = ts
        if accel_delta_g > self.accel_t: self.last_accel = ts
        return abs(self.last_sound - self.last_accel) <= WINDOW_MS \
               and self.last_sound > 0 and self.last_accel > 0
```

Optional third signal: the ultrasonic distance stays below `FLOOR_CM` for 2 seconds after the event (a body on the floor). Use it only to add confidence. Do not require it, because it makes the demo fragile.

### 10.2 Calibrate with data, not guesses

1. Log every sensor line to a CSV with a label column.
2. Record about 20 of each: `fall_beanbag`, `sit_mat`, `bottle_drop`, `door_slam`, `footsteps`, `clap`.
3. Choose thresholds just above what the non-falls produce and below what the falls produce.
4. Commit the final numbers in `edge/config.py`.
5. Re-check them in the real demo room. Rooms change the numbers.

Report honestly what happened: "In 20 trials of each, N non-falls triggered the voice check." Never write "99% accurate."

### 10.3 Offline behavior (the Pi must do this without the backend)

When `fall_suspected` fires:

1. Open a **local incident** and start the clock.
2. Turn the buzzer on and show `TIME DOWN mm:ss`.
3. Try to POST the event. If the backend is unreachable, keep going locally.
4. Accept an **enrolled NFC tag** as an unlock while the local incident is open.
5. Log everything to a local file. Replay it to the backend when the network returns.

---

## 11. AI triage (StepFun)

### 11.1 What StepFun does and does not do

- **Does:** turn the person's spoken answers into the structured record in section 6.5, write the handoff summary, and rewrite the caregiver's reply into one calm sentence.
- **Does not:** diagnose, advise, or improvise questions. The questions are a **fixed script**.
- The first "Are you okay?" is **pre-recorded and instant**. No model call.

### 11.2 Question script

1. "Are you okay? Say 'I'm fine' or 'I need help.'" (instant, no model)
2. "Did you hit your head?"
3. "Where does it hurt?"
4. "Can you move your legs?"
5. "Are you bleeding anywhere?"

The person can stop at any point. The backend sends the alert after question 1 if the answer is not "I'm fine," and keeps updating the card as more answers arrive.

### 11.3 Call (`backend/app/triage.py`)

```python
import os, json
from openai import OpenAI

client = OpenAI(base_url=os.environ["STEPFUN_BASE_URL"], api_key=os.environ["STEPFUN_API_KEY"])

SYSTEM = """You turn short spoken answers from a person who may have fallen into a JSON record.
Rules:
- Output ONLY JSON with keys: conscious, head_strike, pain_location, can_move, bleeding, summary.
- Use null when the answer does not say. Never guess or invent.
- Do not give medical advice or diagnoses.
- summary: one plain sentence for a paramedic, using only what was said.
"""

def parse_answers(transcript: str, profile: dict) -> dict:
    resp = client.chat.completions.create(
        model=os.environ.get("STEPFUN_MODEL", "step-5-preview"),
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": transcript}],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=300,
        timeout=3,
        # check the StepFun docs for the parameter that sets LOW reasoning effort and use it
    )
    data = json.loads(resp.choices[0].message.content)
    data["on_blood_thinner"] = profile["on_blood_thinner"]   # from the profile, never the model
    return data
```

### 11.4 Safety rails

- **3-second timeout.** On timeout or bad JSON, fall back to **rule-based parsing** (keywords like "head", "hip", "can't move", "no") and a templated summary. The demo never waits on the model.
- **Validate the output** with a pydantic model before using it.
- **The medication list comes from the profile**, entered by a caregiver, not from the model.
- **Show the raw transcript** next to the record on the dashboard. Users can see and correct what the AI understood. StepFun's judges look for reliability and for users understanding and controlling the workflow.
- Log the latency of every call and use the measured numbers in the pitch.

### 11.5 Caregiver reply rewrite

Prompt: "Rewrite this message as one calm, short sentence (max 20 words) to be read aloud to someone who has fallen. Do not add information." Fall back to reading the original if the call fails.

---

## 12. Sentry plan

The challenge wants **at least two products beyond error monitoring** and a story about how observability shaped the build.

| Product | How we use it |
| --- | --- |
| **Tracing** | One trace per incident, with a span for each stage: `sensor_event`, `voice_check`, `speech_to_text`, `stepfun_triage`, `photon_send`, `unlock_decision`, `command_to_pi`. We read the real durations and fix the slowest one. |
| **Uptime Monitoring** | Monitor a public `/health` URL (through a tunnel). `/health` returns **503 if the Pi has not sent a heartbeat in 30 seconds**, so the monitor really reflects whether the box is alive. |
| **Logs** | Structured logs for every unlock attempt (with reason), every triage Q and A, every state change. This is the audit trail. |
| **Session Replay** (optional) | On the dashboard if the JS SDK is easy to add. |

**Do this as you go:**

1. Initialize Sentry on day one so the data exists when you need it.
2. Check Sentry's docs for the exact setup call for tracing and logs in the Python SDK.
3. When something is slow or broken, **look at Sentry first**, and write the finding in `docs/sentry-story.md` right away (what you saw, what you changed).
4. In the demo, show one real trace and one log line.

---

## 13. The 2-minute demo script

**Roles:** Narrator, Faller, Caregiver (holds the phone), Operator (laptop and `demo_reset.sh`).

| Time | What happens | Who | Moment |
| --- | --- | --- | --- |
| 0:00 to 0:15 | "A lot of people who fall can't reach a phone or a pendant, and nobody wants a camera in the bathroom. FirstHour is a camera-free safety node." | Narrator | Hook |
| 0:15 to 0:40 | Faller sits down onto the mat (beanbag drop beside it). Terminal shows both sensors spike. Display flips to `TIME DOWN`. Voice asks "Are you okay?" Faller: "Help, I slipped and can't move my leg." Triage continues. | Faller, Operator | M1, M2 |
| 0:40 to 1:05 | Caregiver's phone buzzes with the triage card. Caregiver replies "I'm 2 minutes away." The box reads it aloud, calmly. | Caregiver | M3 |
| 1:05 to 1:30 | A **stranger's phone** texts `UNLOCK` and nothing happens. Caregiver replies `UNLOCK 4821` and the latch clicks open. Say: "Only an enrolled number, with a one-time code, during a real incident, and every try is logged." | Narrator, Caregiver | M4 |
| 1:30 to 1:50 | Unplug Wi-Fi. Faller falls again (a second drop). Buzzer and clock still run. Tap the enrolled NFC tag and the latch opens. Say: "Detection, alarm and unlock live on the QNX device, not in the cloud." | Operator, Narrator | M5 |
| 1:50 to 2:00 | Flash the Sentry trace. "This took N seconds end to end. Here is the slowest step." (Use the real number.) Close: "A prototype, built to be validated with clinicians and older adults." | Narrator | Close |

**Before every run:**

- [ ] `demo_reset.sh`: state `IDLE`, servo locked, display `READY`
- [ ] Phone volume up, other notifications off
- [ ] Speaker volume tested, mic tested in the room
- [ ] Wi-Fi connected, backend, gateway and dashboard running
- [ ] Spare USB cable, tape and rubber bands in the bag
- [ ] Backup video open on a second laptop

---

## 14. Fallbacks and judge Q&A

### 14.1 If something breaks

| Failure | Fallback | Say |
| --- | --- | --- |
| Photon not working | Caregiver phone simulator page | "The carrier link is simulated here." |
| Photon voice call unavailable | Skip the call, use text | Do not claim a phone call |
| StepFun slow or down | Rule-based triage | "AI summary is off; the rules still triage." |
| ElevenLabs down | Pre-cached audio, or a stock voice | Keep it short |
| Speech-to-text unreliable | Typed answer box (operator types) | Only if truly needed |
| Servo jams | Green LED plus virtual door on the dashboard | "Latch prop is mechanical, here's the logic." |
| QNX unstable | Edge on Linux | Do not claim QNX |
| Venue Wi-Fi fails | Phone hotspot (set up and tested beforehand) | |
| Everything fails | Play the backup video, walk the architecture | |

### 14.2 Questions to prepare for

| Question | Answer |
| --- | --- |
| What if a stranger texts UNLOCK? | Needs an enrolled number, a one-time code, an open incident. The Pi re-checks. Every try is logged. |
| False alarms? | Two sensors must agree, then a voice check. In our test, N of 20 non-falls triggered the check. |
| What if the person can't speak? | Silence escalates. We also add a help button (and plan haptic and tap input). |
| Privacy? | No camera. Local detection. Caregiver enrolls who can be contacted. Data stays on the device and our backend for the demo. |
| Why QNX? | Real-time scheduling and process isolation, so detection, alarm and unlock keep working if the network or the cloud fails. (Show the offline test.) |
| Is the voice cloned from a real person? | Only with a teammate's consent, labeled as an assistant. Real use needs the family member's consent. |
| Can the AI give bad medical advice? | It only structures what was said and never advises. Questions are scripted. The transcript is visible. |
| Is it a medical device? | No. It's a prototype and not validated clinically. |
| Cost? | List the parts you actually used and their real prices, or say "under $X in parts" only if you added it up. |

---

## 15. Claims and ethics

| Safe to say | Do not say |
| --- | --- |
| "Camera-free." | "99% accurate" or "zero false alarms" |
| "Two sensors must agree before we check on the person." | "Detects every fall" |
| "Detection, alarm and NFC unlock work offline." (if you tested it) | Any latency number you did not measure |
| "Prototype built to be validated with clinicians and older adults." | "Clinically proven" or "saves lives" |
| "Unlock needs an enrolled number, a one-time code and an open incident." | "Unhackable" |
| "The voice is a synthetic assistant, used with consent." | Fall statistics without a source you can cite |

**Ethics and safety rules for the team:**

- The patient (Margaret), her medications and the phone numbers are **fictional demo data**.
- Clone only the voice of a teammate who agreed. Label it as an assistant.
- Never record or store real personal data.
- **Nobody falls for real.** Use the beanbag and a thick mat or cushion.
- Do not claim anything the code does not do.

---

## 16. Sponsor compliance checklist

Check each box with a screenshot or log line you can show.

| Sponsor | What their challenge asks for | Our proof |
| --- | --- | --- |
| **Main track: Beyond the Digital** | Intelligence in the physical world with embedded AI or hardware | The QNX node, sensors, servo, NFC |
| **QNX** | (1) product uses **QNX OS**; (2) includes **one open-source AI module from oss.qnx.com** | [ ] QNX booting on the Pi [ ] named module running on the Pi |
| **Photon** | A messaging or calling agent using **Photon 2.0 beta** | [ ] real text sent and received [ ] voice call if available |
| **StepFun** | App must **call `step-5-preview` at runtime**; explain what each model does; judged on completing the task, reliability and user control | [ ] call visible in logs [ ] transcript and record visible in UI |
| **ElevenLabs (MLH)** | Best use of ElevenLabs | [ ] voice plays in the demo |
| **Sentry** | At least **two products beyond error monitoring**, and how observability shaped the build | [ ] tracing [ ] uptime [ ] logs [ ] `docs/sentry-story.md` |
| **Paper or Base44** (pick one) | Paper: design fundamentals, ease of use, craft. Base44: a real problem, clear user, usable product. | [ ] dashboard built to the chosen brief |
| **Cognition (Devin)** | Use Devin to build; judged on creativity, novelty and polish | [ ] only claim it if you really used it, and note what it built |

Read each sponsor's "View Additional Information" page for extra rules or forms. Requirements can change.

---

## 17. Risks and the cut list

| Risk | Likelihood | Fix |
| --- | --- | --- |
| QNX takes too long | High | Gate 1 at 7:00 PM, then Linux |
| QNX can't read USB serial or audio | Medium | Ask mentors early. Use the Arduino for all sensors. |
| Photon voice calls unavailable | Medium | Text only plus simulator |
| Photon inbound reply lacks sender number | Medium | Simulator for the demo |
| Thresholds fail in the demo room | Medium | Recalibrate on site, keep a "force fall" hotkey |
| Servo stalls | Medium | Gap in the latch, separate power, LED fallback |
| Speech recognition mishears | Medium | Short scripted answers, quiet corner, typed fallback |
| Venue Wi-Fi isolation | Medium | Phone hotspot |
| Team exhaustion | High | Sleep in shifts, freeze code at 8:30 AM |

**What to cut, in this order, if time runs short:**

1. Paramedic NFC handoff sheet
2. Temperature or other extra sensors
3. Voice cloning (use a stock voice)
4. Session Replay
5. Paper or Base44 polish
6. Voice calls (keep text)
7. **Never cut:** the thud, the voice check, the text alert, the guarded unlock, the offline test, and the honest claims.

---

## 18. Submission checklist

- [ ] Code frozen and tagged `demo-ready` by 8:30 AM
- [ ] README or Devpost text explains each sponsor tool and exactly how it is used
- [ ] Backup demo video recorded and uploaded
- [ ] Devpost submitted by 11:00 AM, confirmation screenshotted
- [ ] Any sponsor-specific forms submitted
- [ ] No API keys or secrets in the repo (`git log -p` check, rotate any that leaked)
- [ ] All borrowed hardware accounted for before leaving
- [ ] Everyone knows where to stand at the table and who speaks

---

## Appendix: quick troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Servo twitches or Arduino resets | USB can't supply enough current | Separate 5V supply, shared ground |
| Servo hums but won't reach the angle | Mechanism binding | Loosen tape, adjust the stick, change the angles |
| No serial data on the Pi | Wrong port or baud rate | List ports, confirm 115200 |
| Pi can't reach backend | Different Wi-Fi or firewall | Use the laptop's LAN IP, or a phone hotspot |
| Pi rejects every command | Clock drift or secret mismatch | Sync clocks, check `SHARED_SECRET` |
| StepFun 401 or 404 | Wrong base URL for the key | Try the other URL (`.com` vs `.ai`) |
| ElevenLabs slow on first phrase | Not cached | Pre-generate phrases at startup |
| Sentry uptime monitor red | Tunnel down or Pi silent | Check the tunnel, check heartbeat age |
| Same fall triggers twice | No cooldown | Add a 30-second cooldown after each event |

**Useful starting points:** `https://docs.photon.codes/docs/llms.txt` (Photon docs index), `https://oss.qnx.com` (QNX open-source modules), the StepFun, ElevenLabs and Sentry developer docs for exact parameter names.
