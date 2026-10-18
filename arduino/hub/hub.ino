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
#include <Wire.h>

const int PIN_SDA = 8, PIN_SCL = 18;  // internal I2C: IMU, mic ADC, speaker codec, touch
const int MIC_MCLK = 2, MIC_SCLK = 17, MIC_LRCK = 45, MIC_DIN = 16;
const int PIN_HELP = 0;               // BOOT button doubles as the help button
const uint8_t IMU_ADDR = 0x68;        // ICM-42607-P
const uint8_t MIC_ADC_ADDR = 0x40;     // dual-mic ADC
const uint32_t MIC_RATE = 16000;      // MCLK = 256 x 16 kHz = 4.096 MHz, a row in the ES7210 table

const unsigned long HOLD_MS = 30000;  // auto relock (UNLOCK_HOLD_SECONDS)
const unsigned long SAMPLE_MS = 50;   // 20 samples per second

I2SClass i2s;
volatile int soundPeak = 0;  // written by the mic task, read + cleared by loop()
bool imuOk = false, micOk = false;
unsigned long relockAt = 0, nextSample = 0, helpDebounce = 0;
bool unlocked = false, alarmOn = false, helpWasDown = false;
String line;

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
  i2s.setPins(MIC_SCLK, MIC_LRCK, -1, MIC_DIN, MIC_MCLK);
  if (!i2s.begin(I2S_MODE_STD, MIC_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO)) return false;
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

// Own task so the blocking I2S read never stalls the serial protocol in loop().
void micTask(void *) {
  int16_t buf[256];
  for (;;) {
    size_t n = i2s.readBytes((char *)buf, sizeof(buf)) / 2;
    int peak = 0;
    for (size_t i = 0; i < n; i++) peak = max(peak, abs((int)buf[i]));
    if (peak > soundPeak) soundPeak = peak;
  }
}

void lockNow() { unlocked = false; }
void unlockNow() { unlocked = true; relockAt = millis() + HOLD_MS; }

void handleLine(String s) {
  s.trim();
  if (s == "U")       { unlockNow(); Serial.println("ACK,U"); }
  else if (s == "L")  { lockNow();   Serial.println("ACK,L"); }
  else if (s == "A1") { alarmOn = true;  Serial.println("ACK,A1"); }
  else if (s == "A0") { alarmOn = false; Serial.println("ACK,A0"); }
  // D,<text>: screen not driven yet; the Pi doesn't wait for an ACK on it.
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);  // no host reading must never block the sampling loop
  pinMode(PIN_HELP, INPUT_PULLUP);
  Wire.begin(PIN_SDA, PIN_SCL, 400000);
  delay(200);
  imuOk = initImu();
  micOk = initMics();
  if (micOk) xTaskCreatePinnedToCore(micTask, "mic", 4096, nullptr, 1, nullptr, 0);
  // Not part of the protocol: the Pi's parser ignores lines it doesn't know.
  Serial.printf("I,box3 imu=%s mic=%s\n", imuOk ? "ok" : "FAIL", micOk ? "ok" : "FAIL");
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') { handleLine(line); line = ""; }
    else if (line.length() < 64) line += c;
  }

  unsigned long now = millis();
  if (unlocked && now > relockAt) lockNow();

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
    int peak = soundPeak;
    soundPeak = 0;
    // distCm = -1: no ultrasonic sensor (optional by design).
    Serial.printf("S,%lu,%d,%.3f,%.3f,%.3f,-1\n", now, peak, ax, ay, az);
  }
}
