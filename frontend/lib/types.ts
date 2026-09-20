/** Shared API types mirroring backend Pydantic schemas. */

export type RiskLevel = "SAFE" | "WARNING" | "HIGH" | "CRITICAL";
export type DeviceStatus = "online" | "offline" | "degraded";
export type AlertSeverity = "warning" | "high" | "critical";
/** How a node reaches the platform: broker publish, or REST POST. */
export type DeviceProtocol = "mqtt" | "http";

export interface User {
  id: number;
  name: string;
  email: string;
  role: "admin" | "safety_officer" | "operator";
}

export interface Factory {
  id: number;
  name: string;
  location: string;
  /** Site fix. Null until the plant is surveyed — the GIS map asks for
   * coordinates rather than inventing a location. */
  latitude: number | null;
  longitude: number | null;
  /** Metres spanned by the 0-100 zone grid. */
  site_span_m: number | null;
}

export interface Zone {
  id: number;
  factory_id: number;
  name: string;
  description: string;
  x_coordinate: number;
  y_coordinate: number;
  risk_level: RiskLevel;
}

export interface Device {
  id: number;
  device_id: string;
  zone_id: number;
  /** Transport this node was onboarded on. */
  protocol: DeviceProtocol;
  status: DeviceStatus;
  last_seen: string | null;
  firmware_version: string;
}

export interface Reading {
  id: number;
  device_id: string;
  timestamp: string;
  temperature: number;
  humidity: number;
  co: number;
  methane: number;
  smoke: number;
  flame: boolean;
}

export interface Alert {
  id: number;
  device_id: number | null;
  zone_id: number | null;
  timestamp: string;
  severity: AlertSeverity;
  hazard_type: string;
  message: string;
  sensor_values: Record<string, number | string> | null;
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}

export interface Prediction {
  id: number;
  zone_id: number;
  device_id: number | null;
  timestamp: string;
  risk_score: number;
  risk_level: RiskLevel;
  predicted_hazard: string | null;
  model_confidence: number | null;
  explanation: string[] | null;
}

export interface DashboardSummary {
  active_sensors: number;
  total_zones: number;
  safe_zones: number;
  warning_zones: number;
  high_zones: number;
  critical_zones: number;
  active_alerts: number;
  average_risk_score: number;
  factory_id: number;
  factory_name: string;
}

export interface ZoneLive {
  zone_id: number;
  /** Owning plant — lets the floor plan draw one P&ID per factory. */
  factory_id: number;
  factory_name: string | null;
  factory_latitude: number | null;
  factory_longitude: number | null;
  factory_site_span_m: number | null;
  name: string;
  x: number;
  y: number;
  risk_level: RiskLevel;
  risk_score: number;
  predicted_hazard: string | null;
  latest: {
    device_id: string;
    timestamp: string;
    temperature: number;
    humidity: number;
    co: number;
    methane: number;
    smoke: number;
    flame: boolean;
  } | null;
}

export interface RiskForecast {
  id: number;
  zone_id: number;
  timestamp: string;
  horizon_minutes: number;
  predicted_risk_score: number;
  predicted_risk_level: RiskLevel;
  predicted_hazard: string | null;
  confidence: number | null;
  method: "ml_regressor" | "trend_extrapolation";
  expected_breach_minutes: number | null;
  drivers: string[];
}

/** Live projection attached to a `risk_update` frame (no id — never persisted
 * for every frame, only rate-limited to one row per minute per zone). */
export type LiveForecast = Omit<RiskForecast, "id" | "zone_id" | "timestamp"> & {
  delta?: number;
};

export interface NotificationChannel {
  name: string;
  enabled: boolean;
  configured: boolean;
  min_severity: AlertSeverity | null;
  recipients: number;
  transport: string;
  detail: string | null;
  provider_class: string;
}

export interface NotificationStatus {
  bus: {
    mode: "redis" | "in_process";
    channel: string;
    redis_url_configured: boolean;
    published: number;
    publish_failures: number;
  };
  dispatch: {
    workers: number;
    events_received: number;
    channels: { name: string; min_severity: string | null;
      counters: { sent: number; skipped: number; failed: number;
        last_error: string | null; last_sent_at: string | null } }[];
  };
  channels: NotificationChannel[];
}

export interface DeviceCredential {
  device_id: string;
  has_credential: boolean;
  username: string | null;
  enabled: boolean;
  issued_at: string | null;
  rotated_at: string | null;
}

export interface DeviceCredentialIssued {
  device_id: string;
  username: string;
  password: string;
  mqtt_topic: string | null;
  warning: string;
}

export interface AlertsSummary {
  hours: number;
  total: number;
  by_severity: Record<"critical" | "high" | "warning", number>;
  by_hour: [string, number][];
  by_hazard: Record<string, number>;
}

/** WebSocket message union pushed by the backend. */
export type LiveMessage =
  | { type: "telemetry"; device_id: string; zone_id: number; zone_name: string; timestamp: string;
      temperature: number; humidity: number; co: number; methane: number; smoke: number; flame: boolean }
  | { type: "risk_update"; zone_id: number; zone_name: string; device_id: string;
      risk_level: RiskLevel; risk_score: number; predicted_hazard: string | null;
      model_confidence: number | null; explanation: string[]; timestamp: string;
      forecast: LiveForecast | null }
  | { type: "alert"; severity: AlertSeverity; hazard_type: string; message: string;
      zone_name: string | null; device_id: string | null; risk_score: number | null;
      sensor_values: Record<string, number | string> | null; explanation: string[] };

// --- Dispersion model types -----------------------------------------------
export interface DispersionRequest {
  gas_type: string;
  sensor_value: number;
  wind_speed: number;
  wind_direction: number;
  stability_class: string;
  temperature_c: number;
  source_x: number;
  source_y: number;
  max_distance: number;
  resolution: number;
}

export interface ContourPoint {
  x: number;
  y: number;
}

export interface Contour {
  level: number;
  coordinates: ContourPoint[];
}

export interface SafetyZone {
  level_g_m3: number;
  concentration_ppm: number;
  zone_type: string;
  color: string;
  coordinates: ContourPoint[];
  label: string;
}

export interface DispersionResult {
  source_x: number;
  source_y: number;
  plume_axis_angle: number;
  wind_direction_from: number;
  wind_speed: number;
  stability_class: string;
  source_gas: string;
  emission_rate_kg_s: number;
  max_concentration: number;
  max_distance_downwind: number;
  /** First alarm level for the gas, in ppm (methane's is 10 %LEL). */
  alarm_threshold_ppm: number;
  /** Downwind distance where the plume falls below that alarm level, metres.
   * 0 when the release never reaches it. Capped by the requested max distance. */
  alarm_distance_downwind: number;
  alarm_reached: boolean;
  contours: Contour[];
  safety_zones: SafetyZone[];
  /** Map-frame metre offsets from the source: x = east, y = north. */
  heatmap: { x: number; y: number; intensity: number }[];
}

export interface DispersionDefaults {
  wind_speed: number;
  wind_direction: number;
  stability_class: string;
  temperature_c: number;
  stability_classes: { id: string; name: string; description: string }[];
  gas_types: { id: string; name: string; mw: number; lel?: string; pel?: string; idlh?: string }[];
}

/* ── IoT connections (broker onboarding + node provisioning) ────────── */

export interface BrokerInfo {
  host: string;
  port: number;
  use_tls: boolean;
  auth_mode: string;
  username: string | null;
  topic_prefix: string;
  client_id: string;
  subscribe_filter: string;
  qos: number;
  /** Live state of the backend's own subscriber — never assumed. */
  connected: boolean;
  transport: string;
  rejected_frames: number;
  note: string;
}

export interface HttpIngestInfo {
  endpoint: string;
  url: string;
  method: string;
  auth_required: boolean;
  auth_header: string;
  note: string;
}

export interface DeviceConnection {
  id: number;
  device_id: string;
  protocol: DeviceProtocol;
  status: DeviceStatus;
  enabled: boolean;
  last_seen: string | null;
  firmware_version: string;
  zone_id: number;
  zone_name: string | null;
  factory_id: number | null;
  factory_name: string | null;
  /** Broker topic for MQTT nodes, REST path for HTTP nodes. */
  endpoint: string | null;
  username: string | null;
  has_credential: boolean;
  credential_enabled: boolean;
  issued_at: string | null;
  rotated_at: string | null;
  last_authenticated_at: string | null;
}

export interface ConnectionOverview {
  broker: BrokerInfo;
  http_ingest: HttpIngestInfo;
  devices: DeviceConnection[];
  total: number;
  by_protocol: Record<string, number>;
  online: number;
  revoked: number;
  unprovisioned: number;
  auto_provision: boolean;
}

export interface BrokerProbeStep {
  name: string;
  ok: boolean;
  ms: number | null;
  detail: string;
}

export interface BrokerProbeResult {
  ok: boolean;
  host: string;
  port: number;
  use_tls: boolean;
  stage: string;
  latency_ms: number | null;
  detail: string;
  connack_code: number | null;
  steps: BrokerProbeStep[];
}

export interface DeviceConnectionRecipe {
  device_id: string;
  protocol: DeviceProtocol;
  factory_name: string;
  zone_name: string;
  username: string | null;
  /** Present exactly once, at onboarding or rotation. */
  password: string | null;
  mqtt_host: string | null;
  mqtt_port: number | null;
  mqtt_topic: string | null;
  http_url: string | null;
  sample_payload: Record<string, unknown>;
  publish_command: string | null;
  curl_command: string | null;
  acl_line: string | null;
  warning: string;
}

export interface DeviceVerification {
  device_id: string;
  protocol: DeviceProtocol;
  status: string;
  enabled: boolean;
  last_seen: string | null;
  last_reading_at: string | null;
  broker_connected: boolean | null;
  verdict: string;
}

/* ── CSV import/export ──────────────────────────────────────────────── */

export interface CsvImportResult {
  total_rows: number;
  registered: number;
  skipped: number;
  errors: { row: number; error: string }[];
}

export interface SimulatorStatus {
  running: boolean;
  note: string;
}
