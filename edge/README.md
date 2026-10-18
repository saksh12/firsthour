# edge/

Use [connect.md](../connect.md) for the complete Mac, Pi 5 and BOX-3 connection,
deployment and demo reset procedure.

The Pi-side safety node (IMPLEMENTATION.md sections 6.1–6.3, 9.3, 10).
`sensor_node.py` reads the hub's sensor lines, runs
two-sensor fusion, owns the local incident, and posts signed events to the backend.

## Run the BOX-3 on QNX

The Pi 5 QNX image has no USB-serial driver. The BOX-3 uses pyusb 1.3.1 and
the `libusb` apk instead. Install the pinned Python packages in a venv using
`edge/requirements.txt`. Keep `.env` at the repository root and use the same
`SHARED_SECRET` as the Mac backend.

```bash
sudo apk add libusb
sudo ~/fh-venv/bin/python edge/sensor_node.py --usb --backend http://<mac-hotspot-ip>:8000
```

The Mac backend must listen on `0.0.0.0`. Run the USB reader as root on this
QNX image; libusb crashed when tested as an ordinary user. The transport
handles QNX's unsupported kernel-driver query and still requires interface
claims to succeed. See the [libusb driver-query reference](https://libusb.sourceforge.io/api-1.0/group__libusb__dev.html#ga1cabd4660a274f715eeb82de112e0779).

Verified on the real BOX-3 and QNX Pi: sensors, LCD, audible alarm, latch status,
automatic relock and signed commands. Provisional same-table bottle-demo
thresholds are loaded on the Pi; full calibration remains pending. See
`calibration/README.md` for the measurements and limits.

### Speech-to-text with whisper.cpp (QNX AI module)

The Pi transcribes the person's spoken answers with `whisper.cpp` from
oss.qnx.com (IMPLEMENTATION.md 6.8). The microphone is on the laptop; the Pi
fetches each clip with a signed request and posts the text as `speech_text`.

```bash
sudo apk add whisper.cpp
# models: ggml-base.en.bin (demo), ggml-tiny.en.bin and ggml-small.en-q8_0.bin are in /data/home/qnxuser/fh-models/ on the demo Pi
sudo sh -c 'WHISPER_MODEL=/data/home/qnxuser/fh-models/ggml-base.en.bin /data/home/qnxuser/fh-venv/bin/python edge/sensor_node.py --usb --backend http://<mac-hotspot-ip>:8002'
```

The reader keeps one `whisper-server` on `127.0.0.1:8090` (never reachable from
the network) at QNX priority 8, below the reader's 10, with 3 threads. It falls
back to `whisper-cli` while the server loads or after it dies. Transcription
runs on its own thread with a 10 s timeout; the sensor loop never waits on it.
The words are not logged. With 4 threads instead of 3, decoding stalled at
272 ms per token (9.5 ms with 3).

Model choice, measured on the Pi 5 with the warm server, 3 threads, beam 5:

| Model | Synthetic `say` clips | Live AirPods clips (up to 8 s) |
| --- | --- | --- |
| `ggml-tiny.en` | 1.2-1.7 s (one 4.7 s outlier) | 1.16-1.55 s per clip |
| `ggml-base.en` (demo) | 2.7-3.6 s | 2.7-3.1 s per clip; clip upload to answer applied 3.8-4.3 s |
| `ggml-small.en-q8_0` | 3.8-5.2 s, misheard "I need help" as "I need health" | not run |

`base.en` is the demo model: its live transcripts were accurate in a noisy room
and it stays well inside the 10 s timeout. Switch models by changing
`WHISPER_MODEL` and restarting the reader.

### Recover after a BOX reset

QNX libusb can abort on physical BOX-3 RST. Live `--usb` isolates USB access in
a restartable child; the parent keeps its incident, clock and security state.
Reconnect locks the latch and restores the alarm/display for an open incident.
A pending unlock fails instead of being replayed. Access and busy errors still
fail visibly. Real helper termination and physical RST recovered with the same
parent PID; the operator also verified alarm/timer continuity during an incident.

Use [connect.md](../connect.md) to start the reader independently of SSH and to
run the signed demo reset. Physical RST does not resolve an incident. No sensor
readings or hub outputs are available while the hub is disconnected.

USB recovery uses the documented [PyUSB error codes](https://github.com/pyusb/pyusb/blob/v1.3.1/usb/core.py)
and [libusb error values](https://libusb.sourceforge.io/api-1.0/group__libusb__misc.html).

## Calibration (IMPLEMENTATION.md 10.2)

Record ~20 trials per label (`fall_beanbag`, `sit_mat`, `bottle_drop`,
`door_slam`, `footsteps`, `clap`, or your own). Enter starts a 4 s trial
(`--seconds` to change), `q` quits. Rows append to `edge/calibration/<label>.csv`
— commit the CSVs, they are the evidence behind the thresholds.

```bash
# on the laptop, from the fake Arduino:
python3 edge/sim_sensors.py | python3 edge/calibrate.py record --stdin --label clap

# on the Pi, from the real Arduino:
python3 edge/calibrate.py record --serial /dev/<port> --label fall_beanbag   # TODO(verify) QNX device path
```

No tty (piped input, CI): `record` captures **one** trial per run — pass
`--trials N` for more. A trial cut short by end of input (< 90 % of the
expected 20 Hz samples) is discarded, and `analyze` skips undersized trials.

Then analyze — per-label peaks, suggested thresholds, and every trial replayed
through the real `Fusion` with suggested and current thresholds:

```bash
python3 edge/calibrate.py analyze
python3 edge/calibrate.py analyze --write   # write the suggestion into edge/config.py
python3 edge/calibrate.py analyze --write --force   # write even when ranges overlap
```

`--write` refuses when some non-fall trial crosses both suggested thresholds
(ranges overlap); `--force` writes anyway and marks `(ranges overlapped)` in the
provenance comment.

Report what the data says: "N non-fall trials triggered the voice check." Never
claim an accuracy number the data doesn't support, and re-check in the demo room.
