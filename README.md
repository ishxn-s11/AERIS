# AERIS — Aerial & Industrial Environmental Risk Intelligence System

**IoT + AI industrial hazardous-atmosphere monitoring and early-warning platform.**

AERIS continuously collects environmental data from distributed sensor nodes
(ESP32 hardware or the bundled simulator), processes the readings into
engineered features, detects hazardous conditions with a **hybrid rule engine +
machine-learning** pipeline, localizes the risk to a factory zone, and pushes
live alerts to a real-time dashboard.

**SENSE → CONNECT → PROCESS → PREDICT → LOCATE → ALERT → PREVENT**

> ⚠️ **PROTOTYPE — NOT CERTIFIED SAFETY EQUIPMENT.** AERIS is a decision-support
> and demonstration system. It must **not** replace certified industrial fire &
> gas detection equipment without appropriate engineering, testing, standards
> compliance (e.g. IEC 61508/61511 functional safety, ATEX/IECEx for explosive
> atmospheres), redundancy, and certification. Synthetic data and simulated
> sensors are used throughout and are clearly labeled; no claim is made that
> they represent real industrial measurements.

---

## Problem statement

Industrial incidents (fires, gas leaks, explosions) escalate in minutes, while
conventional gas detectors are point sensors that raise alarms only *after*
thresholds are crossed — with no trend context, no prediction, and no
site-wide picture. AERIS fuses distributed low-cost sensing with trend analysis
and ML to answer the questions a safety officer actually has:

1. Is the factory currently safe?
2. Which zone has the highest risk?
3. What hazard is being detected — and by which sensor?
4. Is the situation getting worse?
5. Are all IoT devices operating?
6. Which alerts still require acknowledgment?

## Who benefits

- **Safety officers** — live zone map, ranked alerts, explainable risk.
- **Plant operators** — device health and environmental trends.
- **Maintenance teams** — sensor malfunction / offline detection.
- **Emergency responders** — localized, timestamped incident context.

## System architecture (Mermaid diagrams in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md))

- **IoT layer** — ESP32 + MQ-2/MQ-7/MQ-135, DHT22, flame sensor, or the
  scenario-driven Python simulator; publishes JSON over MQTT.
- **Ingestion** — FastAPI MQTT subscriber (paho-mqtt, QoS 1) validates every
  frame against a strict schema; invalid frames are dropped fail-closed.
- **Processing** — per-device sliding windows: calibration hook, robust moving
  averages, spike-resistant rate-of-change, outlier filtering (MAD), and
  missing-value policy.
- **Detection** — three-level hybrid: **Level 1** hard safety rules (flame,
  critical limits) → **Level 2** trend analysis (rate thresholds) → **Level 3**
  ML prediction (Random Forest trained on synthetic data) that may only
  *escalate* situations the deterministic layer already flags.
- **Persistence** — PostgreSQL (readings, predictions, alerts, devices, zones,
  users) with time/device/zone indexes.
- **Presentation** — Next.js 15 + TypeScript + Tailwind dark dashboard, live
  via WebSocket (no refresh), charts via Recharts.

## Repository structure

```
aeris/
├── backend/            FastAPI app (api/, services/, ml/, mqtt/, models/, ...)
├── frontend/           Next.js dashboard (login, map, zones, alerts, devices, analytics)
├── iot/
│   ├── esp32/          Arduino firmware + wiring/calibration notes
│   └── simulator/      Scenario-driven multi-node MQTT simulator
├── ml/
│   ├── datasets/       Generated SYNTHETIC training data (git-ignored)
│   ├── training/       generate_dataset.py, train_model.py
│   └── models/         joblib artifacts (git-ignored)
├── mqtt/mosquitto.conf Broker config (anonymous — dev only)
├── docs/               ARCHITECTURE.md (mermaid diagrams)
├── docker-compose.yml  postgres + mosquitto + backend + frontend
└── .env.example
```

## Technology stack

| Layer | Tech |
|---|---|
| IoT | ESP32 (Arduino/PubSubClient), MQ-series, DHT22, flame sensor |
| Messaging | MQTT 3.1.1, Eclipse Mosquitto 2 |
| Backend | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, paho-mqtt |
| ML | scikit-learn, pandas, numpy, joblib |
| Database | PostgreSQL 16 |
| Frontend | Next.js 15, TypeScript, Tailwind CSS 4, Recharts |
| Testing | pytest (30 backend + 7 simulator tests) |

## Frontend design system

The console is built as an **editorial-industrial** interface rather than a stock
dashboard template: a near-black canvas, hairline rules instead of cards and
shadows, uppercase display headlines with deliberate weight contrast, mono
micro-labels (`LOCAL TIME`, `ZONE INDEX`), numbered sections, and a live marquee
of zone readings under the masthead.

All colour comes from design tokens declared in `frontend/app/globals.css`
(`@theme` → Tailwind utilities). Nothing uses the stock Tailwind palette:

| Token | Hex | Role |
|---|---|---|
| `ink` / `ink-2` / `ink-3` | `#08090a` / `#0c0e10` / `#121517` | canvas layers |
| `paper` / `muted` / `faint` | `#e9e7e2` / `#8a8c87` / `#5a5d59` | type ramp |
| `line` / `line-soft` | `rgba(233,231,226,.14)` / `.07` | hairline rules, drafting grid |
| `volt` | `#d8f14e` | signature accent (actions, indices, live markers) |
| `jade` / `brass` / `ember` / `scarlet` | `#8fe3b0` / `#e4c15b` / `#f0763c` / `#e5484d` | SAFE / WARNING / HIGH / CRITICAL |
| `iris` / `aqua` | `#a99bff` / `#5cc9c0` | chart series accents |

Risk semantics are consistent everywhere — floor plan, chips, metric numbers,
chart strokes and alert rules share the same four tones, so colour alone tells an
officer how bad a zone is. Charts stay mostly monochrome (temperature in paper
white, gas in volt, smoke in iris, CO in aqua) so the interface reads like
instrumentation, not decoration.

The floor plan (`frontend/components/factory-map.tsx`) is drawn as a technical
drawing: drafting grid, corner registration ticks, a service corridor, and one
labelled bay per zone. Each bay's stored `x_coordinate`/`y_coordinate` is the
**centre** of that bay on a 0–100 plant grid, and the bay shows its live risk
score as the dominant number. Clicking a bay opens the zone detail page.

## Quick start (Docker)

```bash
cp .env.example .env        # edit SECRET_KEY etc.
docker compose up --build   # postgres + mosquitto + backend + frontend
```

- Frontend: http://localhost:3000 (login `admin@aeris.io` / `admin123`)
- API + OpenAPI docs: http://localhost:8000/docs
- MQTT: `localhost:1883` (anonymous — dev only)

> **Port conflicts?** All host ports are configurable in `.env`
> (`POSTGRES_PORT`, `MQTT_PORT`, `BACKEND_PORT`, `FRONTEND_PORT`).

## Quick start (local dev, host-run services)

```bash
docker compose up -d postgres mosquitto   # infra only
pip install -r backend/requirements.txt
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend && npm install && npm run dev
python iot/simulator/simulator.py         # all six nodes, normal conditions
```

The backend auto-creates tables, seeds FACTORY_01 with six zones/devices
(NODE_01…NODE_06), and seeds two demo users on first start.

## Training the ML model

```bash
python ml/training/generate_dataset.py     # SYNTHETIC data (labeled scenarios)
python ml/training/train_model.py          # compares 3 classifiers, saves joblib
cp ml/models/aeris_model.joblib backend/app/ml/model_store/
# restart backend — loads the model; without it AERIS runs rules-only
```

Three model families are trained and compared (logistic regression, random
forest, gradient boosting); the best by macro-F1 is kept. RF reached
macro-F1 ≈ 0.96 on the synthetic generator. Metrics land in
`ml/models/*_metrics.json`. **This capability is bounded by the synthetic
generator's assumptions — see "Production limitations".**

## Running hazard scenarios

```bash
python iot/simulator/simulator.py --device NODE_02 --scenario gas_leak
python iot/simulator/simulator.py --device NODE_03 --scenario fire
python iot/simulator/simulator.py --device NODE_01 --scenario overheat
python iot/simulator/simulator.py --device NODE_06 --scenario sensor_failure
python iot/simulator/simulator.py --list-nodes
# options: --interval 2 --duration 90 --host ... --port 1883
```

| Scenario | Behavior | Expected outcome |
|---|---|---|
| `normal` | ambient noise only | all zones SAFE |
| `gas_leak` | CH4 ramps from t=15s, temp rises late | SAFE → WARNING → HIGH → CRITICAL, `gas_leak` alerts |
| `fire` | temp+smoke co-rise, flame trips at t≈25s | immediate CRITICAL `fire` alert |
| `overheat` | fast temperature rise, low gas | HIGH overheating warning |
| `smoke_increase` | smoke ramps alone | fire-signature escalation |
| `sensor_failure` | all channels freeze at fixed values | SENSOR_MALFUNCTION alert, device degraded |
| (stop publishing) | node goes silent | DEVICE_OFFLINE alert after 60 s |

## Primary end-to-end demonstration

1. Start everything; all zones green.
2. `python iot/simulator/simulator.py --device NODE_02 --scenario gas_leak`
3. Watch Chemical Storage on the dashboard (no refresh): **green → yellow →
   orange → red** as methane ramps through the configured bands.
4. A `CRITICAL — Possible combustible gas leak detected in Chemical Storage`
   alert appears (WebSocket push + banner + Alerts page).
5. The prediction record (risk score, level, hazard, ML confidence, plain-English
   contributing factors) is stored in PostgreSQL.
6. Sign in as `safety@aeris.io / safety123` and acknowledge the alert.

## Connecting a real ESP32

See [`iot/esp32/README.md`](iot/esp32/README.md) for wiring, library installs,
and the mandatory calibration notes. The firmware publishes the identical
topic/payload contract as the simulator; keep the `--interval` (2 s) in sync.

## Data processing workflow

Raw frame → schema validation (Pydantic, `extra="forbid"`, physical ranges) →
device/zone resolution → persistence → sliding-window features (calibration
hook → MAD outlier filter → moving averages → median-anchored rates) →
rule engine (L1 hard rules, L2 trends) → ML inference (advisory only) → fused
risk score + band → prediction row + zone risk update → alert fan-out
(deduped, provider interface) → WebSocket broadcast.

Every choice is documented in code:
`backend/app/ml/features.py` (filtering/missing-value policy),
`backend/app/ml/risk_engine.py` (banding, fusion, explanation),
`backend/app/config/settings.py` (all thresholds).

## Hazard detection logic

- **Level 1 — hard safety rules:** flame detected, methane/CO/smoke beyond
  critical limits → force CRITICAL regardless of ML.
- **Level 2 — trend analysis:** configured rate-of-change thresholds
  (e.g. gas +60/min) escalate to HIGH/WARNING; smoke+temp co-rise is a fire
  signature.
- **Level 3 — ML:** pattern recognition (SAFE/WARNING/FIRE_RISK/GAS_LEAK/
  CRITICAL). ML can only escalate situations the rule layer already elevated
  (`ml_advisory_min_rule_score`); it can never initiate an alarm from a calm
  baseline. Score fusion blends ≤30% ML influence.

Risk bands (configurable): 0–30 SAFE · 31–60 WARNING · 61–80 HIGH · 81–100 CRITICAL.

## API documentation

Interactive OpenAPI at `/docs`. Highlights:

```
POST /api/auth/login | /api/auth/register      GET /api/auth/me
GET  /api/dashboard/summary | /zones-live | /alerts-summary | /predictions
GET  /api/factories[/{id}] | /api/zones[/{id}] | /api/zones/{id}/telemetry
GET  /api/zones/{id}/history?hours= | /api/zones/{id}/predictions
GET  /api/devices[/{id}]      POST /api/devices/{id}/ping
GET  /api/alerts               POST /api/alerts/{id}/acknowledge
POST /api/telemetry            (HTTP ingestion path)
WS   /ws                       (telemetry + risk_update + alert push)
```

## Security

JWT access tokens (bcrypt password hashing), role-based authorization
(admin / safety_officer / operator — acknowledgment is officer+), strict
Pydantic validation on every boundary, CORS allow-list, `.env` secrets
(never committed), registration closes after first-run (admin-only). Device
authentication and TLS are **documented production gaps**, not implemented.

## Scalability path

The prototype architecture scales as follows (see ARCHITECTURE.md for the diagram):
broker clustering/bridging per site → stateless FastAPI replicas → Postgres with
partitioned readings + continuous aggregates → Redis for hot zone state and
Celery for fan-out (email/SMS/push providers plug into the existing
`AlertProvider` interface) → WebSocket fan-out via Redis pub/sub.

## Testing

```bash
cd backend && python -m pytest -q          # 30 tests: API, RBAC, risk engine, alerts, ML contract
cd iot/simulator && python -m pytest test_simulator.py -q   # payload contract + scenarios
```

## Production limitations (read before "deploying" anything)

1. **Not certified safety equipment** — no SIL/PL rating, no redundancy, no
   watchdogs, no IEC 61511 safety lifecycle. Use as decision-support only.
2. **Synthetic everything** — training data, sensor physics, and demo
   scenarios are simulated. Real deployment needs site-calibrated sensors and
   incident-labeled data.
3. **No device authentication** — MQTT is anonymous; devices are auto-
   provisioned on first telemetry. Production needs per-device certs/keys + ACLs.
4. **No TLS anywhere** (MQTT/HTTP/WS) in this configuration.
5. **Timing** — the prototype assumes online ingestion; a broker/DB outage can
   drop frames (no store-and-forward at the node, no backpressure).
6. **Model governance** — no drift monitoring, no retraining pipeline, no
   explainability beyond feature-based factors; SHAP-style attribution is a
   future step.

## Future improvements

- Redis + Celery alert fan-out (email / SMS / WhatsApp / push providers)
- Multi-factory support in the UI (the data model already supports it)
- Time-partitioned readings + continuous aggregates for long retention
- Streaming inference with forecast horizon ("risk in 10 minutes")
- Over-the-air firmware updates and device provisioning workflow
- Digital-twin floor plans uploaded per factory instead of the fixed layout
