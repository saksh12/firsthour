# edge/

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

Verified on the real BOX-3 and QNX Pi: sensor samples and acknowledgements for
`L`, `A1`, `A0`, and `U`. The firmware still needs LCD and audible alarm output.
The fall thresholds remain uncalibrated.

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
