// FirstHour sensor hub for the Espressif ESP32-S3-BOX-3. Wire protocol: AGENTS.md /
// IMPLEMENTATION.md 6.1. No wiring: the BOX-3's own IMU and microphones are the two sensors.
// Build + flash (USB-C on the BOX itself):
//   arduino-cli compile --fqbn esp32:esp32:esp32s3box arduino/hub
//   arduino-cli upload  --fqbn esp32:esp32:esp32s3box -p <port> arduino/hub
//
// Pins and chip settings come from Espressif's BSP and drivers (github.com/espressif/esp-bsp:
// bsp/esp-box-3/include/bsp/esp-box-3.h, components/es7210, components/sensors/icm42670).
// The core's esp32s3box variant is the older BOX; BOX-3 differs (LRCK 45), so pins are set here.

#include <ESP_I2S.h>
#include <LovyanGFX.hpp>  // pinned to 1.2.33; see README.md
#include <Wire.h>
#include <atomic>

const int PIN_SDA = 8, PIN_SCL = 18;  // internal I2C: IMU, mic ADC, speaker codec, touch
const int MIC_MCLK = 2, MIC_SCLK = 17, MIC_LRCK = 45, MIC_DIN = 16;
const int SPEAKER_DOUT = 15, PIN_AMP = 46;
const int PIN_HELP = 0;               // BOOT button doubles as the help button
const uint8_t IMU_ADDR = 0x68;        // ICM-42607-P
const uint8_t MIC_ADC_ADDR = 0x40;     // dual-mic ADC
const uint8_t SPEAKER_ADDR = 0x18;     // ES8311
const uint32_t MIC_RATE = 16000;      // MCLK = 256 x 16 kHz = 4.096 MHz, a row in the ES7210 table

const unsigned long HOLD_MS = 30000;  // auto relock (UNLOCK_HOLD_SECONDS)
const unsigned long SAMPLE_MS = 50;   // 20 samples per second

I2SClass i2s;
std::atomic<int> soundPeak{0};
std::atomic<bool> alarmOn{false};
bool imuOk = false, micOk = false, audioReady = false, speakerOk = false, displayOk = false;
uint32_t unlockedAt = 0;
unsigned long nextSample = 0, helpDebounce = 0;
bool unlocked = false, helpWasDown = false, screenDirty = true;
String line, displayText = "READY";

// Match LovyanGFX's BOX V3 panel/bus settings without its touch driver sharing Wire's I2C.
class BoxDisplay : public lgfx::LGFX_Device {
  lgfx::Panel_ILI9342 panel;
  lgfx::Bus_SPI bus;
public:
  BoxDisplay() {
    auto b = bus.config();
    b.spi_host = SPI2_HOST;
    b.freq_write = 40000000;
    b.spi_3wire = true;
    b.pin_sclk = 7;
    b.pin_mosi = 6;
    b.pin_miso = -1;
    b.pin_dc = 4;
    bus.config(b);
    panel.setBus(&bus);
    auto p = panel.config();
    p.pin_cs = 5;
    p.pin_rst = -1;
    p.offset_rotation = 1;
    panel.config(p);
    setPanel(&panel);
  }
};
BoxDisplay display;

constexpr bool holdExpired(uint32_t now, uint32_t opened) {
  return uint32_t(now - opened) >= HOLD_MS;
}
static_assert(!holdExpired(29999, 0) && holdExpired(30000, 0));
static_assert(!holdExpired(14998, UINT32_MAX - 15000) && holdExpired(14999, UINT32_MAX - 15000));

void drawStatus() {
  if (!displayOk) return;
  display.fillRect(0, 54, 320, 186, TFT_BLACK);
  display.setTextSize(2);
  display.setTextColor(TFT_WHITE, TFT_BLACK);
  if (displayText.startsWith("TIME DOWN ")) {
    display.drawCenterString("TIME DOWN", 160, 62, 2);
    display.setTextSize(3);
    display.drawCenterString(displayText.substring(10), 160, 96, 4);
  } else {
    display.drawCenterString(displayText, 160, 92, 4);
  }
  display.setTextSize(2);
  display.setTextColor(unlocked ? TFT_GREEN : TFT_WHITE, TFT_BLACK);
  display.drawCenterString(unlocked ? "LATCH OPEN" : "LATCH LOCKED", 160, 169, 2);
  display.setTextSize(1);
  display.setTextColor(TFT_ORANGE, TFT_BLACK);
  const char *status = !speakerOk ? "AUDIO FAULT" : alarmOn.load() ? "ALARM ON" : "PRESS BOOT FOR HELP";
  display.drawCenterString(status, 160, 211, 2);
}

bool writeReg(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t addr, uint8_t reg, uint8_t *buf, int n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(addr, (uint8_t)n) != n) return false;
  for (int i = 0; i < n; i++) buf[i] = Wire.read();
  return true;
}

bool initImu() {
  uint8_t who = 0;
  if (!readRegs(IMU_ADDR, 0x75, &who, 1) || (who != 0x60 && who != 0x67)) return false;  // WHO_AM_I
  writeReg(IMU_ADDR, 0x21, (0 << 5) | 7);  // ACCEL_CONFIG0: +-16 g (a thud must not clip), 400 Hz
  writeReg(IMU_ADDR, 0x1F, 0x03);          // PWR_MGMT0: accelerometer low-noise mode, gyro off
  delay(50);
  return true;
}

void readAccel(float &ax, float &ay, float &az) {
  uint8_t b[6];
  ax = ay = az = 0;
  if (!imuOk || !readRegs(IMU_ADDR, 0x0B, b, 6)) return;  // ACCEL_DATA_X1.., big endian
  ax = (int16_t)(b[0] << 8 | b[1]) / 2048.0f;             // 2048 LSB/g at +-16 g
  ay = (int16_t)(b[2] << 8 | b[3]) / 2048.0f;
  az = (int16_t)(b[4] << 8 | b[5]) / 2048.0f;
}

// Register sequence from Espressif's es7210_config_codec(): standard I2S, 16 bit, no TDM,
// 30 dB mic gain, 2.87 V bias, 16 kHz from a 4.096 MHz MCLK.
bool initMics() {
  i2s.setPins(MIC_SCLK, MIC_LRCK, SPEAKER_DOUT, MIC_DIN, MIC_MCLK);
  audioReady = i2s.begin(I2S_MODE_STD, MIC_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO);
  if (!audioReady) return false;
  const uint8_t seq[][2] = {
    {0x00, 0xFF}, {0x00, 0x32},                            // software reset
    {0x09, 0x30}, {0x0A, 0x30},                            // power-up timing
    {0x23, 0x2A}, {0x22, 0x0A}, {0x21, 0x2A}, {0x20, 0x0A},  // high-pass filters (removes DC)
    {0x11, 0x60}, {0x12, 0x00},                            // I2S, 16 bit, TDM off
    {0x40, 0xC3},                                          // analog power, VMID
    {0x41, 0x70}, {0x42, 0x70},                            // mic bias 2.87 V
    {0x43, 0x1A}, {0x44, 0x1A}, {0x45, 0x1A}, {0x46, 0x1A},  // mic gain 30 dB
    {0x47, 0x08}, {0x48, 0x08}, {0x49, 0x08}, {0x4A, 0x08},  // mic power
    {0x07, 0x20}, {0x02, 0xC1}, {0x04, 0x01}, {0x05, 0x00},  // OSR, ADC clock (div 1, doubler, DLL), LRCK
    {0x06, 0x04},                                          // DLL power down
    {0x4B, 0x0F}, {0x4C, 0x0F},                            // mic bias, ADC, PGA power on
    {0x00, 0x71}, {0x00, 0x41},                            // enable
  };
  for (auto &r : seq)
    if (!writeReg(MIC_ADC_ADDR, r[0], r[1])) return false;
  return true;
}

// Espressif esp-adf/components/esp_codec_dev/device/es8311/es8311.c:
// slave I2S, 16 bits, 16 kHz, MCLK 4.096 MHz; DAC-only playback, -12 dB volume.
bool initSpeaker() {
  if (!audioReady) return false;
  const uint8_t seq[][2] = {
    {0x0D, 0xFA}, {0x44, 0x08}, {0x44, 0x08},
    {0x01, 0x30}, {0x02, 0x00}, {0x03, 0x10}, {0x16, 0x24},
    {0x04, 0x10}, {0x05, 0x00}, {0x0B, 0x00}, {0x0C, 0x00},
    {0x10, 0x1F}, {0x11, 0x7F}, {0x00, 0x80}, {0x01, 0x3F},
    {0x06, 0x03}, {0x13, 0x10}, {0x1B, 0x0A}, {0x1C, 0x6A}, {0x44, 0x58},
    {0x09, 0x0C}, {0x0A, 0x0C}, {0x02, 0x00}, {0x05, 0x00},
    {0x03, 0x10}, {0x04, 0x20}, {0x07, 0x00}, {0x08, 0xFF}, {0x06, 0x03},
    {0x17, 0xBF}, {0x0E, 0x02}, {0x12, 0x00}, {0x14, 0x1A},
    {0x0D, 0x01}, {0x15, 0x40}, {0x37, 0x08}, {0x45, 0x00},
    {0x32, 0xA7}, {0x31, 0x00},
  };
  for (auto &r : seq)
    if (!writeReg(SPEAKER_ADDR, r[0], r[1])) return false;
  return true;
}

void alarmTask(void *) {
  int16_t buf[256];
  unsigned phase = 0;
  for (;;) {
    bool sounding = alarmOn.load() && millis() % 1000 < 600;
    for (unsigned i = 0; i < 256; i += 2) {
      // Both slots carry the same quiet 1 kHz tone; ES8311 consumes one channel.
      int16_t sample = sounding ? (phase < 8 ? 1500 : -1500) : 0;
      buf[i] = buf[i + 1] = sample;
      phase = (phase + 1) % 16;
    }
    if (i2s.write(buf, sizeof(buf)) != sizeof(buf)) vTaskDelay(1);
  }
}

// Own task so the blocking I2S read never stalls the serial protocol in loop().
void micTask(void *) {
  int16_t buf[256];
  for (;;) {
    size_t n = i2s.readBytes((char *)buf, sizeof(buf)) / 2;
    int peak = 0;
    for (size_t i = 0; i < n; i++) peak = max(peak, abs((int)buf[i]));
    int previous = soundPeak.load();
    while (peak > previous && !soundPeak.compare_exchange_weak(previous, peak)) {}
  }
}

void lockNow() { unlocked = false; screenDirty = true; }
void unlockNow() { unlocked = true; unlockedAt = millis(); screenDirty = true; }

void handleLine(String s) {
  s.trim();
  if (s == "U")       { unlockNow(); Serial.println("ACK,U"); }
  else if (s == "L")  { lockNow();   Serial.println("ACK,L"); }
  else if (s == "A1" || s == "A0") {
    alarmOn.store(s == "A1");
    digitalWrite(PIN_AMP, speakerOk && alarmOn.load() ? HIGH : LOW);
    screenDirty = true;
    Serial.println(s == "A1" ? "ACK,A1" : "ACK,A0");
  }
  else if (s.startsWith("D,")) { displayText = s.substring(2, 34); screenDirty = true; }
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);  // no host reading must never block the sampling loop
  pinMode(PIN_HELP, INPUT_PULLUP);
  pinMode(PIN_AMP, OUTPUT);
  digitalWrite(PIN_AMP, LOW);
  pinMode(48, INPUT_PULLUP);  // BOX-3 LCD/touch reset, per LovyanGFX's BOX V3 setup
  pinMode(47, OUTPUT);
  digitalWrite(47, LOW);
  displayOk = display.init();
  if (displayOk) {
    display.setRotation(1);
    display.fillScreen(TFT_BLACK);
    display.setTextColor(TFT_WHITE, TFT_BLACK);
    display.setTextSize(2);
    display.drawCenterString("FIRSTHOUR", 160, 12, 2);
    digitalWrite(47, HIGH);
  }
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  delay(200);
  imuOk = initImu();
  micOk = initMics();
  speakerOk = initSpeaker();
  if (micOk) micOk = xTaskCreatePinnedToCore(micTask, "mic", 4096, nullptr, 1, nullptr, 0) == pdPASS;
  if (speakerOk) speakerOk = xTaskCreatePinnedToCore(alarmTask, "alarm", 4096, nullptr, 1, nullptr, 0) == pdPASS;
  // Not part of the protocol: the Pi's parser ignores lines it doesn't know.
  Serial.printf("I,box3 imu=%s mic=%s speaker=%s lcd=%s\n", imuOk ? "ok" : "FAIL",
                micOk ? "ok" : "FAIL", speakerOk ? "ok" : "FAIL", displayOk ? "ok" : "FAIL");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(line); line = ""; }
    else if (line.length() < 64) line += c;
  }

  unsigned long now = millis();
  if (unlocked && holdExpired(now, unlockedAt)) lockNow();

  bool helpDown = digitalRead(PIN_HELP) == LOW;
  if (helpDown && !helpWasDown && now > helpDebounce) {
    Serial.println("B");
    helpDebounce = now + 500;
  }
  helpWasDown = helpDown;

  if (now >= nextSample) {
    nextSample = now + SAMPLE_MS;
    float ax, ay, az;
    readAccel(ax, ay, az);
    int peak = soundPeak.exchange(0);
    // distCm = -1: no ultrasonic sensor (optional by design).
    Serial.printf("S,%lu,%d,%.3f,%.3f,%.3f,-1\n", now, peak, ax, ay, az);
  }
  if (screenDirty) { screenDirty = false; drawStatus(); }
}
