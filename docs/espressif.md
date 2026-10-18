# FirstHour on the ESP32-S3-BOX-3

FirstHour is a camera-free fall-response prototype for a bathroom. The Espressif ESP32-S3-BOX-3 is its sensor hub: it hears and feels the fall, shows the clock, sounds the alarm and plays the door latch. A Raspberry Pi 5 on QNX 8 makes every decision. Sources: `arduino/hub/hub.ino` and `arduino/hub/README.md`. It is a prototype, not a medical device.

## What runs on the chip

One Arduino-ESP32 sketch (`arduino/hub/hub.ino`), built with Arduino ESP32 core 3.3.12 and LovyanGFX 1.2.33 for FQBN `esp32:esp32:esp32s3box`. That board variant targets the older ESP32-S3-BOX, so the sketch sets the BOX-3 pins itself, taken from Espressif's BSP (`esp-bsp/bsp/esp-box-3`).

- **`loop()`** handles serial commands, the help button, the latch relock timer and one sensor sample every 50 ms. It never calls `delay()`.
- **`micTask`**, a FreeRTOS task pinned to core 0, blocks on I2S reads and keeps the loudest sample in a `std::atomic`. The loop takes and resets the peak with one atomic exchange, so a blocking read never stalls the serial protocol and no peak is lost.
- **`alarmTask`**, a second FreeRTOS task, writes a quiet 1 kHz tone to I2S, pulsed 600 ms on in every second, while the alarm is on, and silence otherwise.

## Peripherals (all on the board, no wiring)

| Part | Bus and address | How we set it up |
| --- | --- | --- |
| ICM-42607-P IMU | I2C `0x68` (SDA 8, SCL 18, 400 kHz) | Checks `WHO_AM_I`, plus or minus 16 g so an impact does not clip, 400 Hz, accelerometer low-noise mode, gyro off. Read as big-endian 16-bit, 2048 LSB per g |
| ES7210 dual-mic ADC | I2C `0x40`, I2S MCLK 2, SCLK 17, LRCK 45, DIN 16 | Replays Espressif's `es7210_config_codec()` register sequence: standard I2S, 16 bit, TDM off, high-pass filters, 2.87 V mic bias, 30 dB gain, 16 kHz from a 4.096 MHz MCLK |
| ES8311 speaker codec | I2C `0x18`, I2S DOUT 15, amp enable GPIO 46 | Register sequence from Espressif's `esp_codec_dev` ES8311 driver: I2S slave, 16 bit, 16 kHz, DAC only. The amp pin is high only while the alarm is on |
| ILI9342 LCD | SPI2 at 40 MHz, 3-wire (SCLK 7, MOSI 6, DC 4, CS 5), backlight GPIO 47 | LovyanGFX with its BOX V3 panel and bus settings. Touch is not configured, so its I2C driver cannot fight the sensor bus |
| BOOT button | GPIO 0 | The help button, debounced, sends `B` |

At boot the hub prints `I,box3 imu=ok mic=ok speaker=ok lcd=ok` (or `FAIL` per part). The screen shows `AUDIO FAULT` if the speaker did not initialize.

## What the screen shows

- `READY`, or the Pi's `D,<text>` message. `TIME DOWN mm:ss` gets a large clock.
- `LATCH LOCKED`, or `LATCH OPEN` in green.
- `ALARM ON`, `PRESS BOOT FOR HELP`, or `AUDIO FAULT`.

## The simulated latch

There is no servo at the event, so the BOX-3 plays the latch. `U` opens it, shows `LATCH OPEN` and answers `ACK,U` at once. It relocks itself 30 s later (`HOLD_MS`). `L` locks it now. Two compile-time `static_assert`s check the relock boundary and `millis()` rollover. The hub opens only on a `U` from the Pi, and the Pi sends `U` only after it verifies a signed, fresh, single-use command for its own open incident.

## Why the hub reads the sensors, not the Pi

- The Pi runs QNX. Reading an analog microphone and an IMU from QNX directly would mean writing I2S, I2C and GPIO drivers during a hackathon. The BOX-3 already has the parts and Espressif's drivers.
- The Pi stays the safety brain: fusion, the incident, the alarm decision, signatures and the unlock decision. The hub only reports and acts.
- A blocking sensor read can never stall the Pi. The Pi gets clean text lines and treats anything malformed as noise.

## Serial protocol

USB CDC on the BOX-3's own USB-C port (Espressif USB Serial/JTAG, `303a:1001`), `\n`-terminated lines. QNX on the Pi 5 has no USB-serial driver for it, so the Pi reads it over raw USB with pyusb and libusb (`edge/README.md`).

| Direction | Line | Meaning |
| --- | --- | --- |
| Hub to Pi | `S,<ms>,<soundPeak>,<ax>,<ay>,<az>,-1` | Sample, 20 per second. `soundPeak` is the largest 16-bit microphone sample since the last line; `ax`, `ay`, `az` in g; `-1` because there is no distance sensor |
| Hub to Pi | `B` | BOOT pressed (help button) |
| Hub to Pi | `ACK,<cmd>` | Reply to `U`, `L`, `A1`, `A0` |
| Hub to Pi | `I,box3 ...` | Boot status; the Pi ignores it |
| Pi to hub | `U` / `L` | Open the latch (auto relock) / lock now |
| Pi to hub | `A1` / `A0` | Alarm on / off |
| Pi to hub | `D,<text>` | Screen text, up to 32 characters |

## Verified on the real board

From `arduino/hub/README.md` and `README.md`: the operator confirmed the `TIME DOWN` text, the alarm starting and stopping, `LATCH OPEN` returning to `LATCH LOCKED` by itself, and on the QNX Pi with the backend paused, the alarm and ticking clock kept going while the latch stayed locked. On the real Pi a full unlock ran: the Pi verified the signed command, the BOX-3 answered `ACK,U`, showed `LATCH OPEN` and relocked.

Limits: fall thresholds are provisional, from 19 bottle drops on one table. The microphone clips at full scale on those drops, and no non-fall checks have run yet (`edge/calibration/README.md`).
