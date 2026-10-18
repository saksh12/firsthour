# FirstHour: the 2-minute demo

This is the demo as it runs on `main` today. It replaces the plan in `IMPLEMENTATION.md` section 13 where the two disagree.

What changed since that plan:

- **No NFC, no servo** (README "Decided"). The latch is simulated. The backend queues a signed `unlock`, the Pi checks it (signature, freshness, nonce, its own open incident) and sends `U` to the Arduino. The Mac shows the door open only after the Pi reports `local_unlock`. Until then the dashboard door says "Unlocking…".
- **Speech input is in open PR #28** (push-to-talk in Chrome, Web Speech API). Lines marked **[if #28 is merged]** only apply once it's on `main`. Without it, the Operator types what the Faller says into the typed box.
- **No Arduino sketch on `main` yet** (`arduino/hub/` is empty; README Status: ⬜). Until it lands, M1 and M5 run on the fake Arduino: `python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin`, then type `f` + Enter for a fall. Lines marked **[hardware]** need the real Pi and Arduino.
- **Thresholds are placeholders.** `edge/config.py` says `SOUND_THRESHOLD = 400` and `ACCEL_THRESHOLD_G = 1.0` are placeholders until calibration. The tool is in open PR #15. Don't quote a false-alarm rate. It is `TODO(measure)`.

This is a prototype, not a medical device. Margaret, her medications and every phone number are fictional demo data.

## Roles

| Role | Holds | Does |
| --- | --- | --- |
| **Narrator** | Nothing | Speaks to the judges and points at the screen |
| **Faller** | Beanbag, crash mat | Drops the beanbag and sits onto the mat. **Nobody falls for real.** Answers the voice |
| **Caregiver** | The enrolled phone (or the simulator page) | Reads the alert and replies |
| **Operator** | The laptop | Runs the terminals and the typed box, sends the stranger's text, runs `demo_reset.sh` |

## Timeline (1:57 total)

| # | Time | Length |
| --- | --- | --- |
| Hook | 0:00 to 0:12 | 12 s |
| M1 + M2 | 0:12 to 0:40 | 28 s |
| M3 | 0:40 to 1:02 | 22 s |
| M4 | 1:02 to 1:27 | 25 s |
| M5 | 1:27 to 1:47 | 20 s |
| Close | 1:47 to 1:57 | 10 s |

The 3 seconds left over cover stragglers. If you run long, cut the Close first.

---

### Hook (0:00 to 0:12)

- **Narrator says:** "Someone who falls in the bathroom often can't reach a phone, and nobody wants a camera in there. FirstHour is a camera-free safety node: it hears the fall, checks on the person, and gets help in."
- **Everyone else:** in position. Dashboard (`http://localhost:5173`) visible, state `IDLE`, door `Locked`.
- **Fallback:** none needed.

### M1 + M2: the thud and the voice (0:12 to 0:40)

- **Faller does:** drops the beanbag beside the mat and sits onto the mat **[hardware]**.
- **Operator does:** points at the edge terminal (`FALL SUSPECTED sound=… accel_delta_g=…`) and the dashboard, which flips to `CHECKING` with the time-down clock running.
- **Laptop says** (fixed script, pre-cached audio, no model call): "This is an automated assistant." then "Are you okay? Say 'I'm fine' or 'I need help.'"
- **Faller says:** "Help, I slipped and can't move my leg." **[if #28 is merged]** The Operator holds Space or the mic button while the Faller speaks. Otherwise the Operator types it into the typed box.
- The state goes to `ALERTING`. The triage card fills in next to the raw transcript. Let one more question play ("Did you hit your head?") and the Faller answers "No."
- **Narrator says:** "Two sensors have to agree within 400 milliseconds, the sound and the floor vibration, before it asks anything. The questions are a fixed script. The AI only structures what she said, and you can see her exact words next to it."
- **Fallbacks:**
  - No real sensors: Operator types `f` + Enter in the `sim_sensors.py | sensor_node.py --stdin` terminal. Say "the sensors are simulated here".
  - No edge at all: Operator runs `python3 scripts/simulate_fall.py` (it plays the Pi, the Faller and the Caregiver). Narrate over the dashboard.
  - Speech misheard or #28 not merged: typed box.
  - No audio: the dashboard still shows the question. Read it out yourself.
  - The Faller freezes: say nothing. After 8 s of silence once the question has played, the backend escalates anyway.

### M3: the text (0:40 to 1:02)

- **Caregiver does:** holds up the phone as the alert arrives: `FALL ALERT: Margaret (Bathroom). Down …`, the triage summary, `NOTE: on a blood thinner.` (from the profile, never from a model) and `Reply UNLOCK <code> to open the front door.`
- **Caregiver texts:** "I'm 2 minutes away."
- **Laptop says** (to the Faller): one calm sentence. StepFun `step-5-preview` rewrites it (3 s cap, else the original text), and it's spoken through ElevenLabs (else macOS `say`).
- **Narrator says:** "Her daughter gets a triage card, not just 'help'. Her reply is rewritten into one calm sentence and read out loud, so the person on the floor knows help is coming."
- **Fallbacks:**
  - Photon not delivering (or `STUB_PHOTON=1`): `dashboard/caregiver-simulator.html` on the laptop is the phone. It shows the alert from the stub inbox and sends replies to the same `/comms/inbound` endpoint. Say "the carrier link is simulated here."
  - Real Photon on a shared line: the alert only goes out if the caregiver has texted the agent line before (see the checklist). If that was missed, the backend resends the alert as soon as the caregiver texts the line.
  - StepFun slow or down: the original text is read. Say nothing or "AI rewrite is off; the text is read as written."

### M4: the door (1:02 to 1:27)

- **Operator does:** in the simulator page, clicks **Be a stranger** (`+13105559999`, fictional) and sends `UNLOCK 1234`.
- The dashboard unlock log shows it **refused** with the reason. The door stays `Locked`.
- **Caregiver does:** replies `UNLOCK <code>` with the code from **this** alert. The simulator's **UNLOCK (code from alert)** button fills it in. The code is new for every incident and works once. Don't read a fixed code from this script.
- The door panel shows `Unlocking…` and then `Unlocked` once the Pi reports `local_unlock`. The latch LED lights **[hardware]**. It relocks after `UNLOCK_HOLD_SECONDS` (30 in `.env.example`).
- **Narrator says:** "A stranger is refused. The door needs an enrolled number, a one-time code and an open incident. Then the Pi checks the signed command itself before it opens the latch. Every try is logged with its reason. The latch is simulated: there's no motor, but the security chain is real."
- **Fallbacks:**
  - Door stuck on `Unlocking…` or `Unlock failed`: the Pi never got `ACK,U` from the latch. Say "the Pi didn't get the latch's confirmation, so it reports the door as still locked," and move on. That is the safe behavior.
  - Pi offline: the door won't open (by design). Show the refused stranger and the granted line in the unlock log, then go on to M5.
  - Wrong code three times: unlocks are locked out for this incident. `demo_reset.sh` before the next run.

### M5: the offline test (1:27 to 1:47)

- **Operator does:** cuts the network. **[hardware]** Unplug the Pi's Wi-Fi or turn the hotspot off. **Sim:** stop the backend (Ctrl-C) while `sensor_node.py --stdin` keeps running.
- **Faller does:** a second beanbag drop **[hardware]**. Sim: Operator types `f` + Enter.
- The edge terminal shows `backend unreachable …; running locally`. The local incident opens, the buzzer goes on and `TIME DOWN` counts. The door stays locked.
- **Operator does:** reconnects. The Pi replays its outbox and the backend gets the event.
- **Narrator says:** "With the network gone, detection, the alarm and the clock all keep running on the device. The door stays locked: offline, nothing unlocks it. When the network comes back, the box replays its log."
- **Fallbacks:**
  - Can't cut the network safely at the venue: run `sensor_node.py --stdin --offline` in a second terminal and fire a fall there.
  - Edge on Linux instead of QNX: say "the edge runs on Linux today". Don't say QNX unless it booted on the Pi and ran this code.

### Close (1:47 to 1:57)

- **Narrator does:** shows the Sentry `fall_incident` trace for this run.
- **Narrator says:** "This incident took `TODO(measure)` seconds end to end, and the slowest step was `TODO(measure)`." Read both numbers off **this** trace. If there's no trace, leave the numbers out. "FirstHour is a prototype, not a medical device, built to be tested with clinicians and older adults."
- **Fallback:** no Sentry trace (no DSN or no network): skip the numbers and say the closing line.

### If everything fails

Play the backup video on the second laptop and walk through the architecture diagram in `AGENTS.md`. The backup video is `TODO`: it isn't recorded yet.

---

## Pre-run checklist

Run through this before **every** run.

**Reset**

- [ ] `bash scripts/demo_reset.sh`: the backend is back in `IDLE`, and the Pi gets a signed `reset` (latch locked, alarm off, fall cooldown cleared, `READY`). The Pi obeys it only when no local incident is open, so the script resolves the open incident first.
- [ ] The dashboard shows `IDLE` and the door shows `Locked`.

**Processes** (one terminal each, from the repo root)

- [ ] Backend: `backend/.venv/bin/uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8000`. Its log says `sentry enabled` and `triage: StepFun step-5-preview with rule fallback`.
- [ ] Dashboard: `python3 -m http.server 5173 -d dashboard`. Open `http://localhost:5173` **on the laptop**: the operator buttons answer 403 from any other device.
- [ ] Caregiver simulator: `http://localhost:5173/caregiver-simulator.html` in a second tab (it only works with `STUB_PHOTON=1`).
- [ ] Gateway (only with `STUB_PHOTON=0`): `cd comms-gateway && npm run dev`.
- [ ] Edge: on the Pi **[hardware]**, or `python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin`.

**`.env` keys** (one line per key: the scripts read the first copy of a key and the backend reads the last)

- [ ] `SHARED_SECRET` is set, and it's the same value on the Pi.
- [ ] `STUB_*` set for this run: `STUB_STEPFUN=0` + `STEPFUN_API_KEY` + `STEPFUN_BASE_URL` (the `.ai` host for our key), `STUB_ELEVENLABS=0` + `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`, `STUB_PHOTON=0` + `PHOTON_TOKEN` + `PHOTON_PROJECT_ID` + `PHOTON_AGENT_HANDLE` (or keep `STUB_PHOTON=1` and use the simulator), `STUB_EDGE=0` with the real Pi.
- [ ] `SENTRY_DSN` is set.
- [ ] `CAREGIVER_NUMBERS` includes the Caregiver's phone.
- [ ] `BACKEND_URL` on the Pi is the laptop's LAN IP. `SERIAL_PORT` is set **[hardware]**.

**Hardware and network**

- [ ] The Pi's clock is within 10 s of the laptop's (`date -u` on both). Otherwise every poll gets 401 and every command is refused as stale.
- [ ] The phone hotspot is set up and tested, and both the laptop and the Pi have joined it once.
- [ ] Real Photon on a shared line: the Caregiver has texted the agent line at least once, so the alert can be sent into that conversation.

**Audio**

- [ ] Laptop speaker volume tested in the room. The backend caches the fixed phrases when it starts (`voice.prewarm`), so start it a minute before the run. With a real key, check that `backend/audio_cache/` has files.
- [ ] Chrome has microphone permission for `localhost:5173`, and the mic works in the room **[if #28 is merged]**. Chrome's speech recognition needs internet. If it says "Speech service unreachable", use the typed box.
- [ ] Phone volume up, other notifications off.

**Bag**

- [ ] Beanbag, crash mat, spare USB cable, tape.
- [ ] Backup video open on the second laptop.
