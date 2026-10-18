# Handoff: hardware bring-up (Sat Oct 17, ~10:30 PM PDT)

For the next coding agent. Read `AGENTS.md` first (rules and contracts), then this file. `TODOS.md` and `README.md` were up to date as of PR #30. Everything below is newer.

Use [connect.md](connect.md) for the consolidated Mac, QNX Pi and BOX-3 connection
steps, current test ports, signed reset and physical RST behavior.

## Where we are

### Current: Pi safety node split into QNX processes (#22 step 1, Sun Oct 18)

Branch `edge/qnx-process-split`, PR #38. `edge/supervisor.py` runs the safety
process (QNX priority 21), the uplink (10, HTTP only, signs nothing) and
`whisper-server` (8), and restarts any that die (`IMPLEMENTATION.md` 6.10).
Launch it with the "Split mode" command in `connect.md`; the single-process
reader is still the default.

Checked on the Pi: `pidin` priorities, `kill -9` of the uplink and of the safety
process both idle and during a knock incident. The restarted safety process kept
the incident id and clock and re-sent the alarm when the hub reconnected; a
signed `alarm_off` and `reset` were accepted afterwards. A transcript for a clip
the killed process had dispatched is refused, so that answer counts as silence.

Not checked live: CPU load on the uplink, unplugging Wi-Fi with the split
running, an unlock through the split (the unlock path is unit-tested).

### Current: QNX AI module live (#22, Sun Oct 18)
Branch `edge/box3-voice`. The Pi now transcribes the person's spoken answers
with `whisper.cpp` from oss.qnx.com (`IMPLEMENTATION.md` 6.8). The mic is on
the Mac (AirPods; the Brio mic fails in QNX `io-snd`): the dashboard records
each answer, the backend queues a signed `transcribe`, the Pi fetches the clip
once with a signed request and posts `speech_text`. Only a clear "I'm fine"
cancels. Words are never logged.

- Pi: one reader started per `connect.md` with
  `WHISPER_MODEL=/data/home/qnxuser/fh-models/ggml-base.en.bin`. It runs a
  resident `whisper-server` on `127.0.0.1:8090` at priority 8 with 3 threads.
  SIGTERM to the reader now stops the server too (verified live).
- Models on the Pi: tiny.en, base.en (demo), small.en-q8_0 (sha256 checked).
  Comparison table in `edge/README.md`. base.en live: whisper 2.7-3.1 s,
  upload to answer applied 3.8-4.3 s.
- Backend on port 8002, launched with `STUB_ELEVENLABS=0 STUB_STEPFUN=0 PI_STT=1`
  (Photon still stubbed). ElevenLabs stock voice played every line in three knock runs.
- StepFun: rules always win; the model may only add `head_strike=true`,
  `can_move=false` or `bleeding=true` (`merge_model`). Results that arrive after
  newer answers are not dropped; their worrying findings carry forward
  (`carry_forward`). Measured 5-38 s on `low`; `max_tokens` 2000 ran out on
  reasoning, so it is 5000 with a 45 s timeout.
- Dashboard shows "Unclear" for a question that was answered but not parsed,
  and "Not said" for one never answered.
- Start a run with a knock on the table, not BOOT: BOOT sends `help_button` and
  skips the first question.

Not checked live: an all-silent recording with Pi speech-to-text (unit-tested).
Known gap: hedged amounts like "a little, not very much" stay Unclear unless
StepFun answers before the incident ends. Next: split the Pi safety node into
separate QNX processes (#22 step 1) on its own branch.

### Current: recover after BOX RST and keep the reader independent of SSH

Branch `edge/box3-usb-recovery`. PRs #33 and #34 are merged into main.
The operator reported an unexpected alarm, then no alarm after physical BOX-3
RST and another drop. Logs show sensor agreement at 07:05:11 UTC, sound 32768
and acceleration change about 0.07 g. The reader was later absent and its SSH
session ended with status 255. There is no captured USB exception from that
first exit, so the exact exit cause is unproven. A later 07:16:10 event while
networking was unavailable was preserved in the outbox. These events do not
establish whether the table impacts were bottle drops or other disturbances.

Restored the reader, then lost access while the hotspot was unavailable.
After the operator restored both devices to the hotspot, copied the tested
USB recovery code to the Pi and launched the reader with QNX `nohup`, stdin
disconnected and stderr appended to `edge/logs/reader-stderr.log`. It now
survives the launching SSH connection exiting. At 07:26 UTC the backend had a
fresh real-Pi heartbeat. Authenticated demo reset returned the backend to IDLE.

The final implementation isolates raw USB in a child process because physical
RST aborts the QNX libusb process before Python can catch an exception. The
parent alone owns the incident, clock, nonce cache, networking and unlock checks.
It restarts a failed USB helper after one second and restores locked/alarm/display
state only after that helper claims the hub. It never buffers or retries an
unlock. A pending unlock without confirmation fails; a late ACK is ignored.
Access and busy errors still fail visibly. Other reader exceptions now enter
the local audit log. Sensitivity is unchanged. Physical RST is not a demo reset;
use the signed reset described in `edge/README.md`.

Local verification: **347 passed**, one existing Starlette deprecation warning,
using `backend/.venv/bin/python -m pytest -q backend/tests edge/test_usb_source.py edge/test_usb_recovery.py edge/test_calibrate_usb.py`.
The initial exception-only recovery failed the quiet RST check. A detached shell
captured exit status 134, leading to process isolation. The final version passed
two real QNX checks at 07:34 UTC: killing only the helper with SIGKILL, then an
operator physical RST. The helper exited with -9 and -6 respectively, and USB
was reclaimed about 1.4 s later in each log. Parent PID 20893738 survived both;
current helper PID 21205012. Fresh real-Pi heartbeat, backend IDLE, latch locked.
During a later incident, `bath-qnx-usb-test-1792309007817`, the operator pressed
RST several times and confirmed the alarm and timer resumed. Logs keep that
same incident through helper aborts and restarts with the same parent PID.
Signed alarm-off/reset at 07:38:12 UTC then closed it and restored READY. This
also verifies open-incident recovery on the real hardware. No additional drops
were requested. The exact cause of that later sensor trigger remains unlabelled.

`connect.md` now consolidates local network/SSH access, USB ownership, deployed
paths, start/stop commands, firmware work and reset behavior. Links resolve and
shell examples pass `bash -n`. Cloud agents need a local-network route and an
authorized SSH key; a GitHub fork alone does not provide hardware access.

### Recording setup: bottle impact on a table

Current branch: `edge/box3-calibration`, based on the display/alarm branch.
The user plans to keep BOX-3 face-up on the same table as a bottle drop.
Treat this as a labelled simulated impact, not human-fall validation. No person
should fall for calibration or the demo, and the BOX-3 itself stays fixed.

Recorded 20 trials, retaining 19 clean bottle drops with 2,500 samples in
`edge/calibration/demo_bottle_drop_only.csv`. The fifth drop of the 19-drop
batch, overall trial 6, knocked BOX-3 off the table according to the operator.
It is preserved separately as `demo_bottle_box_fall.csv.excluded`. The first
attempt included a pickup and is preserved as `demo_bottle_impact.csv.excluded`.
Both exclusions are based on operator observations. All retained trials pass
the sample-completeness check. The operator requested no further loud tests;
do not ask for a replacement drop.

Loaded provisional **same-table demo only** values into the Pi's private root
`.env`: `SOUND_THRESHOLD=18602`, `ACCEL_THRESHOLD_G=0.0499`. The method and limits
are in `edge/calibration/README.md` and `demo_table_settings.json`. Replay through
the real fusion logic triggers all 19 recorded drops and none of their still
tails. These tails are not independent negative trials. Microphone peaks clip
on every drop. Separate non-fall checks and human-fall validation remain undone;
the full calibration task stays open. Repository defaults are unchanged.

The real QNX USB reader is restored against the isolated port-8001 backend.
It accepted a signed reset, locking the latch, stopping the alarm and showing
`READY`; live heartbeats are healthy. No physical drop was run after tuning.
Issues #22 and #23 remain outside this hardware slice. PR #33, display/alarm,
is open and this branch builds on it.

Added raw-USB recording to `edge/calibrate.py`, drained samples faster while
waiting for operator input, and clamped USB polling timeouts to at least 1 ms
because libusb interprets 0 as an infinite wait. Verification:
`backend/.venv/bin/python -m pytest -q backend/tests edge/test_usb_source.py edge/test_calibrate_usb.py`
passed **335 tests**, with one existing Starlette deprecation warning.

### Current work: hardware only

PR #32 is merged into `main`. The next branch is `edge/box3-display-alarm`.
Its firmware compiles with Arduino ESP32 3.3.12 and pinned LovyanGFX 1.2.33.
It adds `D,` text, simulated latch status, automatic relock display, and a pulsed
speaker alarm. Timer boundary/rollover checks pass during compilation. The
firmware is flashed on the BOX-3 over Mac USB. Boot reports
`imu=ok mic=ok speaker=ok lcd=ok`, and the sensor stream continues. The operator
confirmed `TIME DOWN 00:05` appeared and returned to `READY`. `A1` and `A0`
acknowledged during a short alarm test, and the operator confirmed it beeped and
stopped. The operator also confirmed `LATCH OPEN` returned to `LATCH LOCKED`
automatically after the hold. Left locked, alarm off, `READY`. The 332 Python
tests pass. Back on Pi USB, the real QNX edge node was started against the
isolated port-8001 backend. Paused that backend before BOOT; the operator
confirmed the timer kept counting, the alarm sounded, and the latch stayed
locked. Resumed the backend; Pi incident `bath-qnx-usb-test-1792303450801`
replayed and reached `ALERTING`. The Pi accepted signed `alarm_off` and `reset`,
closed its local incident, and reported latch locked/alarm off. Its persistent
outbox is empty. This tests an unreachable backend; Wi-Fi itself was not disconnected.

The user explicitly scoped current work to hardware. Leave pending issues #22
and #23 alone. Screen, audible alarm, visible automatic relock and local behavior
with the backend unreachable were verified above. Independent non-fall checks
and the full calibration set remain pending.

### Update: Mac and QNX USB bring-up, Sat Oct 17, 10:48 PM PDT

- Work is on `edge/box3-hub`. The BOX-3 enumerates on the Mac as `/dev/cu.usbmodem1101`, Espressif `303a:1001`.
- Compiled and flashed `arduino/hub/hub.ino` with Arduino ESP32 core 3.3.12. The uploader verified the written flash hashes.
- Serial boot reports `I,box3 imu=ok mic=ok`. A 286-sample stationary capture had microphone peaks 184–1413, ax 0.036–0.044 g, ay 0.040–0.054 g, az -0.992–-0.984 g. Sample timestamps advance by 50 ms. These are bring-up observations, not calibration thresholds.
- `U`, `L`, `A1`, and `A0` returned their expected acknowledgements. Sent `L` immediately after the unlock check. Auto-relock was not observed. Alarm audio and the LCD are still unimplemented; acknowledgements do not prove an audible alarm or a visible latch.
- Regression check: **332 passed**, including four new USB transport cases; one existing Starlette deprecation warning.
- Operator completed clap/tilt/button check. The capture showed a microphone peak of 16013 and changing acceleration across all axes. A later serial capture confirmed the BOOT button emits `B`.
- QNX now reads the BOX-3 through raw USB. The first run exposed an unsupported kernel-driver query; `UsbCdcSource` now handles only `NotImplementedError` and still lets claim and permission errors fail. Reference: [libusb kernel-driver query](https://libusb.sourceforge.io/api-1.0/group__libusb__dev.html#ga1cabd4660a274f715eeb82de112e0779). A bounded hardware check read 127 samples, microphone peaks 354–1664, and verified `ACK,L`, `ACK,A1`, `ACK,A0`, `ACK,U`, then `ACK,L`.
- Physical end-to-end test passed: BOOT opened Pi incident `bath-qnx-usb-test-1792302403920`; the backend alerted its stub inbox; a stranger with the correct code was refused; the enrolled fictional caregiver was accepted; the Pi verified the signed command, received `ACK,U`, and posted `local_unlock`. Both Pi and backend logs confirm that event. Signed reset then closed the Pi incident and locked the latch. Automatic relock and visible latch output remain unverified.
- The full `scripts/simulate_fall.py` stub smoke test also passed against the isolated backend.
- Current test setup: Mac `172.20.10.2`, Pi `172.20.10.3`; SSH works. Edge files and a private root `.env` are at `/data/home/qnxuser/firsthour/`. The real edge node runs as `bath-qnx-usb-test` against `http://172.20.10.2:8001`. Port 8001 is a separate backend with live edge checks and stub Photon, StepFun, and ElevenLabs; Sentry and voice playback are off. The original port-8000 backend was left running unchanged. Dashboard: `http://localhost:5173/?backend=http://localhost:8001`. Both incidents are reset to IDLE.
- Next: implement the BOX-3 LCD and audible alarm, calibrate fall detection, and integrate the QNX AI module. The microphone/IMU bring-up and signed unlock chain work; the full hardware acceptance is not complete.

Software is done and on `main`: backend, unlock rules, edge node, dashboard, the Photon gateway, StepFun and Sentry. The suite was 328 passed after the last merges. **The work now is hardware.** The team is new to hardware and asked the agent to drive it.

| Part | State |
| --- | --- |
| Pi 5 running QNX 8 | ✅ Boots from the microSD, joins the phone hotspot, reachable over SSH from the Mac |
| ESP32-S3-BOX-3 (sensor hub) | 🔶 Firmware running on Pi USB; microphone/IMU readings, BOOT, and command acknowledgements verified. LCD/audio pending |
| Edge on the Pi | ✅ `--usb` reads the real board on QNX; signed heartbeats, incident, unlock confirmation, and reset verified |
| Grove sensors + ESP32-S3-DevKitC-1 | ⏸ Backup only. No Grove-to-jumper cables until the MLH lab reopens in the morning |

## Files in the `edge/box3-hub` bring-up branch

- `arduino/hub/hub.ino` (new): BOX-3 firmware.
- `edge/sensor_node.py`: `UsbCdcSource` and the `--usb` flag.
- `edge/test_usb_source.py`: unsupported kernel query, regular driver handling, permission errors, fragmented lines, and writes tested without USB dependencies.
- `edge/requirements.txt`, `edge/README.md`, `TODOS.md`: dependencies, QNX run instructions, and verified checks.
- `HANDOFF.md`: this file.

`2026-10-17-221417-*.txt` and `.md` at the repo root are exported chat transcripts, outside this branch's intended changes. **They contain secrets (the hotspot password and more). Never commit them.** Delete them or move them out of the repo.

Verification command: `backend/.venv/bin/python -m pytest -q backend/tests edge/test_usb_source.py`.

## Hardware facts verified tonight (also posted to issue #22)

**Pi 5 on QNX 8**

- SSH: `ssh qnxuser@172.20.10.3` (hotspot IP; it can change). This Mac's SSH key is installed. The password is the image default; ask the team. Don't commit it.
- Network: phone hotspot "Manya's iPhone". The SSID uses a curly apostrophe, so `wpa_supplicant.conf` on the SD card holds it as hex. The Mac must be on the same hotspot.
- Packages come from `apk` (repo.oss.qnx.com, 8.0.5). Available and relevant: `whisper.cpp`, `onnxruntime`, `python3-onnxruntime`, `python3-numpy`. **The QNX sponsor prize needs one AI module from oss.qnx.com: `apk add whisper.cpp` is the plan.**
- System Python 3.14 is externally managed, so use the venv `~/fh-venv`. It already has `pyserial 3.5` and `pyusb 1.3.1`.
- **No USB-serial driver** (`devc-serusb` is absent and there's no apk for it). Use `apk add libusb` plus pyusb instead. libusb works **only as root**; as a normal user it segfaults. Hence `sudo ~/fh-venv/bin/python edge/sensor_node.py --usb`.
- Audio is USB only (no HAM, no audio jack). The Pi's clock was within a few seconds of the Mac's. Signed commands need less than 10 s of skew.

**ESP32-S3-BOX-3** (sources: Espressif esp-bsp `bsp/esp-box-3/include/bsp/esp-box-3.h`, `components/es7210`, `components/sensors/icm42670`)

- Built in: ICM-42607-P IMU at I2C 0x68, an ES7210 dual-mic ADC at 0x40, an ES8311 speaker codec at 0x18, a 2.4" ILI9342-class SPI LCD, and BOOT (GPIO0) and Mute (GPIO1) buttons.
- Internal I2C: SDA 8, SCL 18. Mic I2S: MCLK 2, SCLK 17, LRCK **45**, DIN 16. Speaker amp enable is GPIO46; LCD backlight is GPIO47.
- Arduino core `esp32:esp32` 3.3.12 is installed (`arduino-cli`). FQBN: `esp32:esp32:esp32s3box`. That variant is for the **older** BOX, so `hub.ino` sets the BOX-3 pins itself. The variant's `I2S_*` macros clash with plain names, so the sketch uses `MIC_*` names.
- Over USB it should enumerate as Espressif USB Serial/JTAG (`303a:1001`). The old DevKitC showed `303a:4001`.

## How the BOX-3 maps to the protocol (`hub.ino`)

- `S,<ms>,<soundPeak>,<ax>,<ay>,<az>,-1` at 20 Hz.
  - `soundPeak` is the max absolute 16-bit mic sample in the window, read by a separate FreeRTOS task so I2S reads never block the loop.
  - The accelerometer is set to ±16 g at 400 Hz, scaled at 2048 LSB/g.
  - `distCm` is `-1` (no ultrasonic sensor).
- `B` when BOOT is pressed. The edge opens an incident on `B`, so **the whole chain is testable with no sensors**: press BOOT, the Pi opens the incident, the caregiver sends `UNLOCK <code>`, the Pi sends `U`, and the BOX answers `ACK,U`.
- `U`/`L`/`A1`/`A0` each reply `ACK,<cmd>`. `D,<text>` is ignored for now.
- The boot line `I,box3 imu=ok|FAIL mic=ok|FAIL` tells you whether each chip initialised. The Pi's parser ignores `I,` lines.
- The mic init replays Espressif's `es7210_config_codec()` register sequence for standard I2S, 16 bit, 16 kHz with a 4.096 MHz MCLK, 30 dB gain and 2.87 V bias. **If `mic=FAIL` or `soundPeak` stays at 0**, debug here first: check the I2C ack at 0x40, and that MCLK is actually running before the register writes.

## Bring-up procedure (steps 1–3 verified above)

1. **Flash and check on the Mac.** The BOX-3 main unit's own USB-C port goes to the Mac. The DOCK's USB-C is power only.
   ```bash
   arduino-cli board list                                    # find the port (cu.usbmodem…)
   arduino-cli upload --fqbn esp32:esp32:esp32s3box -p <port> arduino/hub
   ```
   Read the serial output with pyserial, or with `arduino-cli monitor -p <port> -c baudrate=115200`. Expect the `I,box3 …` line and then `S,` lines. Tapping the table should move `ax/ay/az`; a clap should spike `soundPeak`; BOOT should print `B`; sending `U` should get `ACK,U`.
2. **Move the BOX-3 to the Pi** (any USB-A port; use the USB-A-to-C cable from the box). On the Pi: `sudo apk add libusb` if it's missing, copy the repo over (`scp`/`rsync`, or `git clone`), set `BACKEND_URL` to the Mac's hotspot IP (`:8000`), and use the same `SHARED_SECRET` as the Mac's `.env`. Then run `sudo ~/fh-venv/bin/python edge/sensor_node.py --usb`.
   - The backend must listen on `0.0.0.0` (see the README run block).
   - `UsbCdcSource` claims the CDC data interface and sends `SET_CONTROL_LINE_STATE` with DTR=1, RTS=0. **Never send RTS=1 with DTR=0**: on the ESP32's USB port that resets the chip into its bootloader.
3. **End to end with no sensors:** press BOOT, then use `dashboard/caregiver-simulator.html` to `UNLOCK <code>`. The dashboard door should flip to open only after the Pi reports `local_unlock`.
4. **Screen:** `TIME DOWN mm:ss` from `D,` lines, plus a LOCKED/OPEN latch panel. This needs an LCD library. Pick a pinned one that supports the BOX-3 (LovyanGFX has a BOX-3 config, or GFX Library for Arduino with ILI9342), and tell the team when you add it. For now `A1` (alarm) is only tracked in a variable; the speaker would need ES8311 init.
5. **Calibration:** use `edge/calibrate.py` (record and analyze, from PR #15). The thresholds in `edge/config.py` are placeholders, and `soundPeak` is now on a 0-32767 scale. Run about 20 trials per label (see AGENTS.md).
6. **QNX AI module:** `apk add whisper.cpp`, and wire it into the Pi's speech-to-text path (#22). Also update the AGENTS.md hardware facts (ESP32-S3-BOX-3 instead of an Arduino, Pi 5 + QNX facts, the `--usb` path) and `.env.example` (`SERIAL_PORT` note).

## Backup path (if the BOX-3 mics fight you)

The ESP32-S3-DevKitC-1 plus the two Grove modules (sound sensor, I2C 3-axis accelerometer, chip unknown) need Grove-to-female-jumper cables. They're on the MLH hardware lab menu as "Grove Jumper Wires", along with female-to-female jumpers. The lab reopens in the morning. The previous DevKitC version of `hub.ino` (Grove pins and auto-detection of MMA7660/LIS3DH/ADXL345/MPU6050) was never committed. Rewrite it from the pin map if you need it: sound on GPIO4, SDA on 8, SCL on 9, power from 3V3, never 5V. The Grove sound sensor could also plug into the BOX-3's Pmod headers on its bracket or dock.

## Open items outside hardware

- PRs: #31 (demo script and Devpost draft) needs review; #20's plan text is stale.
- Issues: #22 QNX, #23 Photon (the backend escalation call is still missing), #24 polish.
- Rotate `PHOTON_SIP_OUTBOUND_PASSWORD`: it leaked into a setup-tool log.
- Dashboard Auto-listen has to start after the question finishes playing, not on a 3 s timer. Keep it off until then.
- Code freeze is **Sun 8:30 AM** (tag `demo-ready`). Devpost is due by 11:00 AM.
