# AERIS Architecture

Mermaid diagrams for the four required views. Everything shown is implemented
in this repository except where explicitly marked *production path*.

## 1. Overall architecture

```mermaid
flowchart LR
    subgraph IoT["IoT layer"]
        S[MQ-2 / MQ-7 / MQ-135<br/>DHT22 / Flame] --> N[ESP32 node<br/>or Python simulator]
    end
    N -- "MQTT: aeris/FACTORY/ZONE/telemetry" --> B((Mosquitto<br/>broker))
    B -- "subscribe aeris/+/+/telemetry" --> API[FastAPI backend<br/>MQTT subscriber]
    API --> P[Processing engine<br/>windows · features · rates]
    P --> R[Hybrid risk engine<br/>L1 rules → L2 trends → L3 ML]
    R --> DB[(PostgreSQL<br/>readings · predictions · alerts)]
    API -- "WebSocket /ws" --> UI[Next.js dashboard<br/>map · charts · alerts]
    R -- "alert payload" --> A[AlertManager<br/>dedup + provider fan-out]
    A -- "database provider" --> DB
    A -- "websocket provider" --> UI
    A -. "email / SMS / push<br/>(production path)" .-> EXT[External channels]
```

## 2. Data pipeline (per telemetry frame)

```mermaid
flowchart TB
    RAW[Raw frame<br/>MQTT or HTTP] --> V[1. Validation<br/>Pydantic strict schema]
    V --> C[2. Calibration hook<br/>raw counts → units]
    C --> F[3-5. Noise filter · outliers · missing values<br/>MAD filter, no interpolation]
    F --> MA[6. Moving averages<br/>windowed means]
    F --> RT[7. Rate of change<br/>median-anchored /min]
    MA --> X[8. Feature vector<br/>14 engineered features]
    RT --> X
    X --> TH[9. Threshold check<br/>config-driven bands]
    X --> ML[10. ML inference<br/>Random Forest · joblib]
    TH --> R[11. Risk score 0-100<br/>+ band + explanation]
    ML --> R
    R --> D[12. PostgreSQL<br/>prediction + alert rows]
    R --> W[13. WebSocket broadcast<br/>telemetry / risk_update / alert]
```

## 3. IoT workflow (device to backend)

```mermaid
sequenceDiagram
    participant S as Sensors
    participant E as ESP32 / Simulator
    participant M as Mosquitto broker
    participant B as FastAPI subscriber
    S->>E: analog/digital reads every 2 s
    E->>M: PUBLISH aeris/FACTORY_01/ZONE_02/telemetry (QoS 1, JSON)
    M-->>B: deliver to subscriber (aeris/+/+/telemetry)
    B->>B: validate schema → ingest service
    Note over B: invalid frames dropped + logged (fail-closed)
    B->>B: features → rules → ML → risk
    B->>B: store reading + prediction + alerts
    B-->>UI: broadcast telemetry / risk_update / alert
```

## 4. Hazard response flow

```mermaid
flowchart LR
    A[Abnormal sensor pattern] --> B{Level 1<br/>hard rules}
    B -- "flame / critical gas" --> CRIT[Force CRITICAL]
    B --> C{Level 2<br/>trend analysis}
    C -- "rates exceed limits" --> ESC[Escalate band]
    C --> D{Level 3<br/>ML advisory}
    D -- "confident hazard match<br/>(escalate-only)" --> FUSE[Fuse score ≤ rule +30% ML]
    D --> FUSE
    FUSE --> Z[Zone identification<br/>device → zone → factory]
    Z --> AL[Alert raised<br/>severity + hazard + explanation]
    AL --> DB[(PostgreSQL)]
    AL --> DASH[Dashboard push<br/>banner + map + feed]
    AL --> OFF[Safety officer<br/>acknowledges]
```

## Deployment topology (production path)

```mermaid
flowchart TB
    subgraph Site["Industrial site (per site)"]
        N1[ESP32 nodes ×N] --> LB[Site broker<br/>Mosquitto cluster / bridge]
    end
    LB --> GW[Backend replicas<br/>stateless FastAPI ×K]
    GW --> PG[(PostgreSQL primary<br/>+ read replica)]
    GW --> R[(Redis<br/>zone state · pub/sub)]
    R --> F[Frontend + WS fan-out<br/>via Redis pub/sub]
    GW --> Q[Task queue<br/>Celery workers]
    Q --> CH[Email / SMS / WhatsApp / push]
```
