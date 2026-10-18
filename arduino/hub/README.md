# BOX-3 sensor hub

Build with Arduino ESP32 core **3.3.12** and LovyanGFX **1.2.33**:

```bash
arduino-cli lib install LovyanGFX@1.2.33
arduino-cli compile --fqbn esp32:esp32:esp32s3box arduino/hub
arduino-cli upload --fqbn esp32:esp32:esp32s3box -p <port> arduino/hub
```

Use the USB-C port on the main unit. The dock's port supplies power only.

The LCD shows the Pi's `D,<text>` messages and the hub's simulated latch state.
`A1` enables a pulsed speaker alarm and `A0` silences it. Audio runs in its own
task so I2S writes cannot block serial commands. The microphone peak uses atomic
updates so sampling cannot erase a concurrent peak. `U` acknowledges immediately
and relocks after 30 seconds; compile-time checks cover the timer boundary and
millisecond-counter rollover.

Driver settings come from [Espressif's BOX-3 board support](https://github.com/espressif/esp-bsp/tree/master/bsp/esp-box-3),
[LovyanGFX's BOX V3 setup](https://github.com/lovyan03/LovyanGFX/blob/1.2.33/src/lgfx/v1_autodetect/LGFX_AutoDetect_ESP32_all.hpp),
and [Espressif's ES8311 codec driver](https://github.com/espressif/esp-adf/blob/master/components/esp_codec_dev/device/es8311/es8311.c).
Only the display is configured in LovyanGFX; touch is unused so its I2C driver
cannot conflict with the sensor bus.

After flashing, verify the screen and audible output on the real board. A serial
acknowledgement confirms command handling, not speaker or display operation.
No alarm plays at boot. Use `A1` then `A0` for a short bench check, and leave the
hub locked with `L`. The full pipeline uses only Pi-verified signed commands.

Verified on the BOX-3 on Oct 17: operator confirmed timer text, beep start/stop,
and visible automatic relock. On the QNX Pi with the backend paused, BOOT still
started the audible alarm and ticking timer while the latch stayed locked.
Resuming the backend replayed the incident; signed reset closed it and emptied
the Pi's outbox. Wi-Fi itself was not disconnected, and fall thresholds still
need calibration.
