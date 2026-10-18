# FirstHour: the 2-minute demo

The demo as it runs on `main` plus PR #39 on Sun Oct 18. Hardware: ESP32-S3-BOX-3 on a Raspberry Pi 5 running QNX 8, laptop running the backend, gateway and dashboard, every stub off. Commands and paths come from `connect.md`, `edge/README.md` and `edge/supervisor.py`.

This is a prototype, not a medical device. Margaret, her medications and every phone number are fictional demo data. **Nobody falls.** The "fall" is a bottle dropped on the table next to the BOX-3, the impact the thresholds were tuned on (`edge/calibration/README.md`).

## Roles

| Role | Holds | Does |
| --- | --- | --- |
| **Narrator** | Nothing | Talks to the judges and points at the screens |
| **Person** | Bottle, AirPods paired to the laptop | Drops the bottle, then answers the voice as Margaret |
| **Caregiver** | The enrolled phone | Reads the alert and replies |
| **Stranger** | A second phone, **not** on `CAREGIVER_NUMBERS` | Sends one wrong `UNLOCK` |
| **Operator** | The laptop, with an SSH terminal on the Pi | Runs `pidin` and the kill, presses "Mark resolved", runs `demo_reset.sh` |

## Timeline (2:00)

| Beat | Time | Length |
| --- | --- | --- |
| Hook | 0:00 to 0:10 | 10 s |
| M1 + M2: the thud and the voice | 0:10 to 0:40 | 30 s |
| M3: the text | 0:40 to 1:05 | 25 s |
| M4: the door | 1:05 to 1:25 | 20 s |
| M5: QNX and offline | 1:25 to 1:50 | 25 s |
| Close: Sentry trace | 1:50 to 2:00 | 10 s |

If you run long, cut the `pidin` line in M5 first, then the Close numbers.

---

### Hook (0:00 to 0:10)

- **Narrator says:** "A fall in the bathroom often means no phone in reach, and nobody wants a camera in there. FirstHour hears the fall, checks on the person, and lets help in."
- **Screens:** dashboard state `IDLE`, door `Locked`. BOX-3 shows `READY` and `LATCH LOCKED`.
- **Fallback:** none needed.

### M1 + M2: the thud and the voice (0:10 to 0:40)

- **Person does:** drops the bottle on the table next to the BOX-3. Do not use BOOT to start: BOOT sends `help_button`, which skips the first question (`README.md`, "Decided").
- **Judges see:** the BOX-3 screen flips to `TIME DOWN 00:01` and the alarm pulses. The dashboard goes to `CHECKING` with the clock running.
- **Laptop says** (pre-cached ElevenLabs audio): "This is an automated assistant." then "Are you okay? Say 'I'm fine' or 'I need help.'"
- **Person says** into the AirPods: "I need help. I can't move my leg."
- **Judges see:** the dashboard records the answer, the Pi transcribes it with whisper.cpp (measured 3.8-4.3 s from clip upload to answer applied, `edge/README.md`), the Conversation panel shows what the Pi heard, and the state goes to `ALERTING`. The triage record fills in next to the raw words.
- **Narrator says:** "Two sensors on the Espressif board have to agree within 400 milliseconds. The Pi, on QNX, opens the incident and starts the alarm before any network call. Her answer is transcribed on the Pi with whisper.cpp from QNX's own repository. The questions are a fixed script; the AI only structures what she said."
- **Fallbacks:**
  - The drop does not trigger: Person knocks firmly on the table. Still nothing: press **BOOT** on the BOX-3 (skips the first question; say "that's the help button").
  - BOX-3 or Pi down: on the laptop, `python3 edge/sim_sensors.py | python3 edge/supervisor.py --stdin`, then type `f` + Enter. Say "the sensors are simulated here."
  - No edge at all: `python3 scripts/simulate_fall.py --url http://localhost:8000` plays the Pi, the person and the caregiver.
  - Speech misheard or no transcript: Operator types the answer in the dashboard's typed answer box.
  - Person freezes: say nothing. Silence after the question has played escalates by itself.
  - No audio: read the question aloud from the dashboard.

### M3: the text (0:40 to 1:05)

- **Caregiver does:** holds up the phone as the iMessage alert arrives. It shows time down, the triage summary, `NOTE: on a blood thinner.` (from the profile) and "How soon can you be here?". It carries **no** door code.
- **Caregiver texts:** `on my way, 10 min`. Reply within 20 s of the alert, or the backend starts a voice call (`VOICE_CALL_AFTER_SECONDS`).
- **Judges see and hear:** the dashboard Caregivers panel marks the caregiver assigned with an ETA. The laptop says "Sarah is about 10 minutes away." A second text arrives on the caregiver's phone with the one-time door code. The dashboard log says a door code was texted, without the code.
- **Narrator says:** "Her daughter gets a triage card, not just 'help'. Only someone who says they are coming gets the door code, and only by text to their own phone."
- **Fallbacks:**
  - No alert on the phone: check the dashboard delivery status. `blocked` means the caregiver has not texted the agent line yet; texting it now resends the alert.
  - Photon down: restart the backend with `STUB_PHOTON=1` and use `http://localhost:5173/caregiver-simulator.html` as the phone. It posts to the same `/comms/inbound` endpoint. The simulator only works with `STUB_PHOTON=1`, because the backend refuses unsigned inbound otherwise. Say "the carrier link is simulated here."
  - StepFun slow: a plain message is read as written after 10 s. The ETA claim does not use StepFun.

### M4: the door (1:05 to 1:25)

- **Stranger does:** texts `UNLOCK 1234` to the agent line from the second phone. `TODO(verify)`: a stranger's phone through live Photon has not been run; it needs that phone to have texted the line once before.
- **Judges see:** the dashboard unlock log shows it **refused** with the reason. The door stays `Locked`.
- **Caregiver does:** replies `UNLOCK <code>` with the code from **this** incident's text. The code is new every incident and works once. Never read a fixed code from this script.
- **Judges see:** the dashboard door says `Unlocking...`. The Pi verifies the signed command and sends `U`. The BOX-3 shows `LATCH OPEN` and answers `ACK,U`. Only then the dashboard door shows open. The BOX-3 relocks itself after 30 s.
- **Narrator says:** "The stranger is refused. The door needs an enrolled number, a one-time code and an open incident, and then the Pi checks the signed command itself. The latch is simulated on the Espressif screen, but the security chain is real."
- **Fallbacks:**
  - No stranger phone: skip the stranger and say "a stranger with the right code was refused on this hardware on Saturday" (`README.md`, M4).
  - Door stuck on `Unlocking...` or `Unlock failed`: the Pi did not get `ACK,U`. Say "no confirmation from the latch, so the Pi reports it still locked. That is the safe default."
  - Photon down: simulator page (as in M3). Click "Be a stranger" (it fills a fictional number), type `UNLOCK 1234`, click "Send reply".

### M5: QNX priorities and offline (1:25 to 1:50)

Needs the Pi running in split mode (`edge/supervisor.py`, see the checklist). The SSH terminal is already open on the Pi in `/data/home/qnxuser/firsthour`, with the commands below typed in advance.

- **Operator shows the priorities:**

  ```bash
  sudo -n pidin | grep -E 'python|whisper'
  ```

  The safety process and its USB helper show priority 21, the uplink and the supervisor 10, `whisper-server` 8 (checked on the Pi Oct 18, `IMPLEMENTATION.md` 6.10). `TODO(verify)`: the `grep` filter; plain `sudo -n pidin` is the documented command.
- **Narrator says:** "On QNX the safety process runs at priority 21. The network runs at 10, in a separate process that signs nothing."
- **Operator does:** clicks **Mark resolved** on the dashboard (this closes the M1 incident and stops the alarm). Then finds the uplink's PID and kills it:

  ```bash
  sudo -n grep 'started uplink pid=' edge/logs/reader-stderr.log | tail -n 1
  sudo -n /data/home/qnxuser/fh-venv/bin/python -c 'import os, signal; os.kill(<uplink_pid>, signal.SIGKILL)'
  ```

  Use the latest PID: it changes on every restart. Do not use `slay`: it matches by process name, and the safety process and the uplink are both Python, so it could kill the safety process too. `TODO(verify)` on the Pi if you want `slay`.
- **Person does:** drops the bottle again **right after** the kill (the uplink is back within 1.6-1.8 s).
- **Judges see:** the BOX-3 alarm and `TIME DOWN` start with no network process alive. The supervisor restarts the uplink, the outbox replays, and the new fall appears on the dashboard.
- **Narrator says:** "We just killed the network process. The alarm and the clock started anyway, because they live in the safety process. The uplink came back and replayed the event. Offline, nothing unlocks the door."
- **Fallbacks:**
  - Single-process reader running, not split mode: skip `pidin` and the kill. Pause the backend instead: Ctrl-Z in its terminal, drop the bottle, show the alarm and clock, then `fg` and watch the incident replay. `TODO(verify)`: the Oct 17 check paused the backend, but the exact method is not recorded.
  - On the laptop with no Pi: `python3 edge/sim_sensors.py | python3 edge/sensor_node.py --stdin --offline`, type `f` + Enter. It shows the local alarm and clock with no backend at all, but no replay.
  - Do not unplug the Wi-Fi live: that path has not been tested yet (`README.md`, M5).

### Close (1:50 to 2:00)

- **Narrator does:** shows this run's `fall_incident` trace in Sentry.
- **Narrator says:** "This incident took `TODO(measure)` seconds from the thud to the door, and the slowest step was `TODO(measure)`." Read both off **this** trace. No trace: leave the numbers out. "FirstHour is a prototype, not a medical device."
- **Fallback:** no trace (no DSN or no network): skip the numbers and say the last sentence.

### If everything fails

Play the backup video on the second laptop and walk through the architecture diagram in `AGENTS.md`. The backup video is `TODO`: not recorded yet.

---

## Pre-run checklist

**Once, at least 10 minutes before**

- [ ] The caregiver phone has texted the agent line once (the `PHOTON_AGENT_HANDLE` number). Each caregiver gets their own shared Photon line, and a shared line cannot text first. The gateway accepts replies on any of the project's lines, so `PHOTON_AGENT_HANDLE` is only the number you tell them to text.
- [ ] The stranger phone has also texted the agent line once.
- [ ] Phone hotspot on; laptop and Pi both on it. Check the laptop address: `ipconfig getifaddr en0` (`connect.md` section 1).

**`.env` at the repo root** (key names only; never print the values)

- [ ] `STUB_EDGE=0`, `STUB_STEPFUN=0`, `STUB_ELEVENLABS=0`, `STUB_PHOTON=0`, `PI_STT=1`.
- [ ] `SHARED_SECRET`, the same value as the Pi's `.env`.
- [ ] `STEPFUN_API_KEY`, `STEPFUN_BASE_URL` (the `.ai` host for our key), `STEPFUN_MODEL=step-5-preview`.
- [ ] `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`; for the call and companion: `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID`, `ELEVENLABS_WEBHOOK_SECRET`, `VOICE_TOOL_TOKEN`, `ELEVENLABS_COMPANION_AGENT_ID`.
- [ ] `PHOTON_TOKEN`, `PHOTON_PROJECT_ID`, `PHOTON_AGENT_HANDLE`.
- [ ] `SENTRY_DSN`.
- [ ] `CAREGIVER_NUMBERS` has the caregiver phone and **not** the stranger phone. `CAREGIVER_NAMES` maps it to "Sarah".
- [ ] `BACKEND_URL=http://localhost:8000` for the gateway.

**Processes on the laptop** (one terminal each, from the repo root)

- [ ] Backend: `backend/.venv/bin/uvicorn --app-dir backend app.main:app --host 0.0.0.0 --port 8000`. Start it at least a minute early so the fixed lines are generated and cached (a cached line played in 0.7 s, a fresh one took 4.7-4.8 s, measured Oct 18).
- [ ] Gateway: `cd comms-gateway && npm run dev`.
- [ ] Dashboard: `python3 -m http.server 5173 --bind 127.0.0.1 -d dashboard`, then open `http://localhost:5173/?backend=http://localhost:8000` **on the laptop** (operator buttons are refused from other devices). Choose the Pi speech-to-text answer mode.
- [ ] Tunnel for Sentry Uptime: `cloudflared tunnel --url http://localhost:8000`. A quick tunnel gets a new URL each start; update the Sentry monitor if it changed (`docs/prize-admin.md`).

**Pi** (`connect.md` sections 2 and 4, split mode)

- [ ] `ssh qnxuser@<pi-ip>`, then check nothing else owns the board: `sudo -n pidin ar | grep '[s]ensor_node.py'`.
- [ ] Start the supervisor detached, with the laptop's current address and port 8000:

  ```bash
  cd /data/home/qnxuser/firsthour
  sudo -n sh -c 'WHISPER_MODEL=/data/home/qnxuser/fh-models/ggml-base.en.bin nohup /data/home/qnxuser/fh-venv/bin/python -u edge/supervisor.py --usb --backend http://<mac-ip>:8000 >> edge/logs/reader-stderr.log 2>&1 </dev/null &'
  ```

- [ ] Clock skew: `date -u` on the Pi and the laptop agree within 10 s. Otherwise every poll gets 401 and every command is refused as stale.
- [ ] `curl --max-time 5 http://127.0.0.1:8000/health` on the laptop shows a fresh Pi heartbeat (503 means the Pi is silent).
- [ ] BOX-3 face-up at its table spot, on the main unit's USB-C port (the dock port is power only).

**Reset before every run**

- [ ] `BACKEND_URL=http://127.0.0.1:8000 bash scripts/demo_reset.sh`. Backend back to `IDLE`; the Pi gets a signed reset (latch locked, alarm off, fall cooldown cleared, `READY`).
- [ ] Dashboard `IDLE`, door `Locked`, Caregivers panel empty. BOX-3 shows `READY`.
- [ ] Wait 30 s after the last fall before the next drop (fall cooldown), unless the reset cleared it.

**Audio**

- [ ] Laptop speaker volume tested in the room. AirPods connected to the laptop, not to a phone.
- [ ] Chrome has microphone permission for `localhost:5173`.
- [ ] Only one dashboard tab with the microphone on (more tabs can start more companion sessions).
- [ ] Phone volume up, other notifications off.

**Bag**

- [ ] Plastic bottle, spare USB-A to USB-C cable, tape for the BOX-3 spot.
- [ ] Backup video on the second laptop.
