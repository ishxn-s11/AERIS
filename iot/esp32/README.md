# ESP32 Node Firmware

Arduino-framework firmware for a physical AERIS sensor node. Publishes the exact
same JSON contract as the Python simulator (`iot/simulator/`), so the backend
cannot tell hardware and simulation apart.

## Wiring (defaults in `aeris_node.ino`)

| Sensor | Pin (ESP32) | Notes |
|---|---|---|
| MQ-2 (smoke/LPG) | GPIO 34 | ADC1, input-only |
| MQ-7 (CO) | GPIO 35 | ADC1, input-only |
| MQ-135 (gas proxy) | GPIO 32 | used as methane/combustible stand-in |
| Flame sensor DO | GPIO 26 | module pulls LOW on flame |
| DHT22 | GPIO 25 | 4.7–10 kΩ pull-up on data |

Power note: MQ heaters draw 150 mA+ each — use a 5 V supply rated ≥1 A, never
power them from the 3V3 rail.

## Setup

1. Arduino IDE → Boards Manager → install **esp32 by Espressif**.
2. Library Manager → install **PubSubClient**, **DHT sensor library** (+ Adafruit Unified Sensor).
3. Edit the CONFIGURATION block: Wi-Fi credentials, MQTT host (the machine running Mosquitto), and the node identity (`DEVICE_ID`, `ZONE_ID`, `ZONE_NAME`).
4. Flash, then open the Serial Monitor at 115200.

## Calibration (do not skip for real hardware)

The firmware ships with a raw-ADC → 0–1023 linear map only, clearly marked
`CALIBRATION_REQUIRED`. For any real deployment:

1. Burn-in each MQ sensor (24–48 h as per the datasheet).
2. Expose sensors to known gas concentrations and fit Rs/R0 curves.
3. Replace `mapAdc()` with per-channel conversion functions.
4. Re-tune `settings.hazard_thresholds` in the backend to the calibrated units.

Without this, readings are only useful for demonstrating the pipeline — which
is exactly what this prototype does.
