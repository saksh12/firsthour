# Connect to the Pi 5 and ESP32-S3-BOX-3

Use this guide from the Mac in the demo setup. Read [AGENTS.md](AGENTS.md) first
and [HANDOFF.md](HANDOFF.md) for the latest deployed branch and test status.
Connection details below were verified on October 18, 2026.

The hardware is a Raspberry Pi 5 running QNX 8 and an Espressif
ESP32-S3-BOX-3. The BOX-3 reads its microphone and IMU. The Pi owns detection,
the incident, the alarm timer, and the final unlock decision.

## 1. Check the network and cable

Keep the Mac and Pi on the same phone hotspot. The currently verified addresses
are Mac `172.20.10.2` and Pi `172.20.10.3`. Addresses can change after reconnecting.
To check the Mac's address, run `ipconfig getifaddr en0` in a local terminal.
If Wi-Fi uses another interface, find it with `networksetup -listallhardwareports`.

Ordinary Wi-Fi can replace the hotspot if it permits communication between
clients. A venue or guest network may isolate clients. Verify SSH and backend
access before switching the demo. Update the backend URL when the Mac's address
changes. The Pi initiates HTTP requests to the Mac; the backend never connects
inbound to the Pi.

Connect the BOX-3 main unit's USB-C port to a USB-A port on the Pi with the
USB-A-to-C cable. The dock's USB-C port supplies power only. Leave the BOX-3
face-up at its calibrated table position.

A cloud agent such as Devin cannot reach these private addresses unless it has
an explicit network route and authorized SSH credentials. Have cloud agents
prepare PRs, then use an agent on this Mac for deployment and hardware checks.

## 2. SSH into the Pi

From the Mac, run:

```bash
ssh -o ConnectTimeout=5 qnxuser@172.20.10.3
```

The Mac's SSH key is installed. Other machines need an authorized key; ask the
operator to arrange access. Keep passwords and private keys out of docs and logs.
For root-only checks, the verified setup supports `sudo -n`.

On the Pi, check the runtime and existing reader:

```bash
uname -a
/data/home/qnxuser/fh-venv/bin/python --version
sudo -n pidin ar | grep '[s]ensor_node.py'
sudo -n tail -n 20 /data/home/qnxuser/firsthour/edge/logs/reader-stderr.log
```

The deployed files are under `/data/home/qnxuser/firsthour`. The virtual
environment is `/data/home/qnxuser/fh-venv`, with Python 3.14, pyserial 3.5 and
pyusb 1.3.1. QNX's system Python is externally managed; use this virtual environment.

With USB recovery installed, expect one main process with `--usb` and one child
with `--usb-worker`. Their PIDs change. Do not start another reader or recorder
while these processes own the board. QNX may omit root processes from a normal
user's process listing, so run `pidin` with sudo. `tail -f` is unsupported on this
image; use bounded `tail -n` reads.

## 3. Check the backend from the Mac

The current hardware test backend is separate from the original port-8000
backend. It listens on `0.0.0.0:8002`, uses real Pi events, speaks with
ElevenLabs, and sends the person's recorded answers to the Pi's whisper.cpp
(`PI_STT=1`, see IMPLEMENTATION.md 6.8). StepFun and Photon are stubbed and
Sentry is off. The older port-8001 backend (no Pi speech-to-text) may still be
running; the Pi only talks to the one in its `--backend` URL.

Start it from the Mac repository root if it is stopped. The root `.env` must
hold `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID`; the fixed phrases are
cached under `backend/audio_cache/` on first start.

```bash
STUB_EDGE=0 STUB_STEPFUN=1 STUB_ELEVENLABS=0 STUB_PHOTON=1 SENTRY_DSN= PI_STT=1 \
  backend/.venv/bin/python -m uvicorn --app-dir backend app.main:app \
  --host 0.0.0.0 --port 8002 --no-access-log >> /tmp/fh-backend-8002.log 2>&1 &
curl --max-time 5 http://127.0.0.1:8002/health
curl --max-time 5 http://127.0.0.1:8002/incident
```

Expect `edge: "live"` and a recent `bath-qnx-usb-test` heartbeat. A stale Pi
heartbeat makes `/health` return 503. A heartbeat confirms the Pi process is
alive; use the Pi logs to confirm USB recovery and sensor activity.

Open the dashboard at
[localhost:5173](http://localhost:5173/?backend=http://localhost:8002).
Choose the Pi speech-to-text answer mode and allow the microphone (AirPods in the
demo). The page records after each question and shows the live conversation.
If its static server is stopped, start it from the Mac repository root:

```bash
python3 -m http.server 5173 --bind 127.0.0.1 -d dashboard
```

## 4. Start or update the reader when needed

The Pi has no working USB-serial driver for this board. Use `--usb`, not
`--serial /dev/ser10`. Raw USB uses `/usr/lib/libusb-1.0.so.0` and must run as
root on this image. Non-root libusb access crashed during bring-up. The BOX-3
USB identifier is `303a:1001`.

Before replacing a running reader, resolve the demo incident using step 5.
Find the current **parent** PID with the sudo `pidin` command above. Stop that
specific parent with SIGTERM; the detached launch below leaves SIGINT ignored.
Replace `<parent_pid>` before running this command on the Pi:

```bash
sudo -n /data/home/qnxuser/fh-venv/bin/python -c 'import os, signal; os.kill(<parent_pid>, signal.SIGTERM)'
```

Confirm that the parent, the `--usb-worker` helper and `whisper-server` have all
exited. Killing only the helper makes the parent restart it. Readers older than
the `edge/box3-voice` branch leave `whisper-server` running after SIGTERM; stop
that PID too. Do not reuse a historical PID from a handoff.

Check the deployed branch before copying code. The Pi can run a tested PR that
is ahead of `main`. Preserve its root `.env`, logs and calibration captures.
To deploy the edge code from the intended local checkout, run on the Mac:

```bash
scp edge/sensor_node.py edge/config.py edge/security.py qnxuser@172.20.10.3:/data/home/qnxuser/firsthour/edge/
```

The Pi root `.env` must contain `DEVICE_ID`, the same `SHARED_SECRET` as the
backend, and its measured demo thresholds. Do not print or overwrite secrets.
See [.env.example](.env.example) for key names and
[the calibration observations](edge/calibration/README.md) for threshold limits.

On the Pi, start one reader detached from SSH:

```bash
cd /data/home/qnxuser/firsthour
sudo -n mkdir -p edge/logs
sudo -n sh -c 'WHISPER_MODEL=/data/home/qnxuser/fh-models/ggml-base.en.bin nohup /data/home/qnxuser/fh-venv/bin/python -u edge/sensor_node.py --usb --backend http://172.20.10.2:8002 >> edge/logs/reader-stderr.log 2>&1 </dev/null &'
```

Use the current Mac address if it changed. `WHISPER_MODEL` turns on the Pi's
speech-to-text (the `whisper.cpp` apk; the model file is already on the Pi).
Leave it out to run without it. Verify three processes (reader, `--usb-worker`
helper, `whisper-server` at priority 8) and a fresh heartbeat after the
launching SSH session exits. This does not configure automatic startup after a
Pi reboot.

### Split mode (issue #22 step 1)

To run the safety process and the uplink as separate QNX processes, launch the
supervisor instead of the reader (same stop rule: SIGTERM, here to the
supervisor's PID, stops every child):

```bash
cd /data/home/qnxuser/firsthour
sudo -n sh -c 'WHISPER_MODEL=/data/home/qnxuser/fh-models/ggml-base.en.bin nohup /data/home/qnxuser/fh-venv/bin/python -u edge/supervisor.py --usb --backend http://172.20.10.2:8002 >> edge/logs/reader-stderr.log 2>&1 </dev/null &'
```

Deploy `edge/link.py`, `edge/uplink.py` and `edge/supervisor.py` with the three
files above. `sudo -n pidin` shows the safety process (and its USB helper) at
priority 21, the uplink at 10 and `whisper-server` at 8; the log line
`started <child> pid=` gives each PID. Checked on the Pi on Oct 18, including
`kill -9` of each child during a knock incident (IMPLEMENTATION.md 6.10).

## 5. Reset a demo incident

From the **Mac repository root**, run:

```bash
BACKEND_URL=http://127.0.0.1:8002 bash scripts/demo_reset.sh
```

The script reads the root `.env` without printing its secret. The backend
resolves the incident and queues signed alarm-off/reset commands. The Pi
verifies them, locks the latch, stops the alarm, clears detection cooldown and
shows `READY`. Confirm the accepted reset in the Pi logs.

Physical **RST** reboots only the BOX-3. It does not clear the Pi's incident.
An active alarm and elapsed timer resume after the hub reconnects. This was
observed on the real hardware. On QNX, physical RST can abort the native USB
helper; the parent restarts it while retaining the incident and security state.

**BOOT** is the help button. Pressing it starts an incident and alarm. Use
network, process and log checks above when a quiet connection check is needed.

## 6. Connect the BOX-3 directly to the Mac for firmware work

Complete the signed demo reset and stop the Pi reader before moving the cable.
Connect the BOX-3 main unit's USB-C port directly to the Mac with a data cable.

From the Mac repository root, identify the port:

```bash
arduino-cli board list
```

The previously verified port was `/dev/cu.usbmodem1101`; use the current result.
The firmware uses ESP32 core `3.3.12`, FQBN `esp32:esp32:esp32s3box`, and
LovyanGFX `1.2.33`. `hub.ino` supplies the BOX-3 pins because that board variant
also covers an older BOX.

For an intended firmware update, build and upload:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3box arduino/hub
arduino-cli upload --fqbn esp32:esp32:esp32s3box -p /dev/cu.usbmodem1101 arduino/hub
```

With no other process using that port, inspect the stream:

```bash
arduino-cli monitor -p /dev/cu.usbmodem1101 -c baudrate=115200
```

Expect boot status `I,box3 imu=ok mic=ok speaker=ok lcd=ok` and sensor lines
`S,<ms>,<soundPeak>,<ax>,<ay>,<az>,-1` at about 20 Hz. Close the monitor before
uploading or moving the cable. Routine connection does not require reflashing.

Move the BOX-3 back to the Pi and start the reader using step 4. Keep the
allowlist, one-time code, signatures and Pi confirmation in the unlock path.
Use the caregiver simulator for unlock tests; do not send raw `U` as a shortcut.

For dependency setup and firmware checks, see
[the hub guide](arduino/hub/README.md). For detection measurements, see
[the calibration observations](edge/calibration/README.md).
