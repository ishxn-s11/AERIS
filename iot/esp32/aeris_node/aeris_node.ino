/**
 * AERIS ESP32 sensor node firmware (Arduino framework)
 * -----------------------------------------------------
 * Connects to Wi-Fi + MQTT, reads the sensor suite, publishes JSON telemetry
 * to  aeris/<FACTORY_ID>/<ZONE_ID>/telemetry  every PUBLISH_INTERVAL_MS.
 *
 * Dependencies (Arduino Library Manager):
 *   - PubSubClient (Nick O'Leary)
 *   - DHT sensor library (Adafruit) + Adafruit Unified Sensor
 *
 * IMPORTANT — CALIBRATION_REQUIRED:
 * MQ-series sensors report raw ADC counts (0-4095 on ESP32). The backend
 * thresholds assume the simulator's 0-1023 scale. Use mapAdc() below to
 * normalize, then fit real calibration curves against a reference gas
 * analyzer before any safety-relevant deployment. See /docs.
 *
 * Tested board: ESP32 DevKit v1 (WROOM-32)
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ============================ CONFIGURATION =================================
// Keep credentials out of source control in real deployments (use Secrets.h
// or NVS/provisioning). Defaults here are for bench testing only.
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

const char* MQTT_HOST     = "192.168.1.10";   // machine running Mosquitto
const uint16_t MQTT_PORT  = 1883;
// const char* MQTT_USER = "";                  // enable when broker auth is on
// const char* MQTT_PASS = "";

// Node identity — must match a zone seeded in the backend database.
const char* FACTORY_ID = "FACTORY_01";
const char* ZONE_ID    = "ZONE_02";
const char* ZONE_NAME  = "Chemical Storage";
const char* DEVICE_ID  = "NODE_02";           // unique per board

const uint32_t PUBLISH_INTERVAL_MS = 2000;    // keep in sync with the simulator
const uint32_t Serial_baud         = 115200;

// ============================== PIN MAP =====================================
// Adjust to your wiring.
const int PIN_MQ2_SMOKE = 34;   // MQ-2  smoke/LPG   (ADC1, input only)
const int PIN_MQ7_CO    = 35;   // MQ-7  CO          (ADC1, input only)
const int PIN_MQ135_AQ  = 32;   // MQ-135 air quality (optional secondary gas)
const int PIN_FLAME     = 26;   // flame sensor digital out (LOW = flame on most modules)
const int PIN_DHT       = 25;   // DHT22 data
DHT dht(PIN_DHT, DHT22);

WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

uint32_t lastPublish = 0;

// ======================= CALIBRATION (site-specific) ========================
// PROTOTYPE: raw 12-bit ADC (0-4095) mapped onto the simulator's 0-1023 scale
// so backend thresholds are comparable. Replace with per-sensor curves
// (Rs/R0 ratio + temperature compensation) fitted during commissioning.
float mapAdc(uint16_t raw) {
  return (float)raw * 1023.0f / 4095.0f;
}

// ============================ HELPERS =======================================
void connectWifi() {
  Serial.printf("[wifi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[wifi] connected, ip=%s rssi=%d\n",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

void connectMqtt() {
  while (!mqtt.connected()) {
    Serial.print("[mqtt] connecting...");
    String clientId = String("aeris-esp32-") + DEVICE_ID;
    bool ok = mqtt.connect(clientId.c_str() /*, MQTT_USER, MQTT_PASS */);
    if (ok) {
      Serial.println("ok");
    } else {
      Serial.printf("failed rc=%d — retry in 3s\n", mqtt.state());
      delay(3000);
    }
  }
}

float readTemperatureC() {
  float t = dht.readTemperature();
  return isnan(t) ? NAN : t;   // NaN handled below: frame skipped, not faked
}

float readHumidity() {
  float h = dht.readHumidity();
  return isnan(h) ? NAN : h;
}

bool flameDetected() {
  // Most flame modules pull the digital pin LOW when a flame is seen.
  return digitalRead(PIN_FLAME) == LOW;
}

// ============================== SETUP =======================================
void setup() {
  Serial.begin(Serial_baud);
  pinMode(PIN_FLAME, INPUT_PULLUP);
  analogReadResolution(12);          // 0-4095
  analogSetAttenuation(ADC_11db);    // full 0-3.3V range
  dht.begin();

  connectWifi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(512);
  connectMqtt();
}

// ============================== LOOP ========================================
void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWifi();   // auto-heal Wi-Fi
  if (!mqtt.connected()) connectMqtt();               // auto-heal MQTT
  mqtt.loop();

  uint32_t now = millis();
  if (now - lastPublish < PUBLISH_INTERVAL_MS) return;
  lastPublish = now;

  float temperature = readTemperatureC();
  float humidity    = readHumidity();
  if (isnan(temperature) || isnan(humidity)) {
    Serial.println("[dht] read failed — skipping frame (missing data > fake data)");
    return;
  }

  float smoke   = mapAdc(analogRead(PIN_MQ2_SMOKE));
  float co      = mapAdc(analogRead(PIN_MQ7_CO));
  float methane = mapAdc(analogRead(PIN_MQ135_AQ)); // PROXY: MQ-135 as combustible-gas stand-in
  bool  flame   = flameDetected();

  char payload[384];
  snprintf(payload, sizeof(payload),
    "{\"device_id\":\"%s\",\"factory_id\":\"%s\",\"zone_id\":\"%s\","
    "\"zone_name\":\"%s\",\"temperature\":%.1f,\"humidity\":%.1f,"
    "\"co\":%.0f,\"methane\":%.0f,\"smoke\":%.0f,\"flame\":%s,"
    "\"timestamp\":\"%04d-%02d-%02dT%02d:%02d:%02dZ\"}",
    DEVICE_ID, FACTORY_ID, ZONE_ID, ZONE_NAME,
    temperature, humidity, co, methane, smoke,
    flame ? "true" : "false",
    // ESP32 has no RTC: timestamp is placeholder until NTP sync — the backend
    // re-stamps on receipt, so this is informational only.
    2026, 1, 1, 0, 0, 0);

  String topic = String("aeris/") + FACTORY_ID + "/" + ZONE_ID + "/telemetry";
  bool sent = mqtt.publish(topic.c_str(), payload, false);
  Serial.printf("[pub] %s -> %s (%u bytes)\n", sent ? "ok" : "FAILED", topic.c_str(),
                (unsigned)strlen(payload));
}
