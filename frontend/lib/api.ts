/** Typed API client. Token kept in localStorage (prototype; httpOnly-cookie
 * session + refresh flow is the documented production upgrade). */
import type {
  Alert, AlertsSummary, BrokerProbeResult, ConnectionOverview, CsvImportResult, DashboardSummary,
  Device, DeviceConnectionRecipe, DeviceCredential, DeviceCredentialIssued,
  DeviceProtocol, DeviceVerification, DispersionDefaults, DispersionRequest,
  DispersionResult, Factory, NotificationStatus, Prediction, Reading,
  RiskForecast, SimulatorStatus, User, Zone, ZoneLive,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:38000";
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:38000/ws";

const TOKEN_KEY = "aeris_token";
const USER_KEY = "aeris_user";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  return raw ? (JSON.parse(raw) as User) : null;
}

function saveSession(token: string, user: User) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };
  if (options.body) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    clearSession();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
export async function login(email: string, password: string): Promise<User> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Login failed");
  }
  const data = (await res.json()) as { access_token: string; name: string; role: User["role"] };
  const user: User = { id: 0, name: data.name, email, role: data.role };
  saveSession(data.access_token, user);
  const me = await request<User>("/api/auth/me");
  saveSession(data.access_token, me);
  return me;
}

export function logout() {
  clearSession();
  window.location.href = "/login";
}

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------
/** `factory_id` is appended to every plant-wide query so a selected factory
 * never leaks another plant's zones, devices or KPIs. `null` means "all". */
function scope(factoryId?: number | null): string {
  return factoryId === null || factoryId === undefined ? "" : `factory_id=${factoryId}`;
}

function withQuery(path: string, factoryId?: number | null): string {
  const qs = scope(factoryId);
  if (!qs) return path;
  return path.includes("?") ? `${path}&${qs}` : `${path}?${qs}`;
}

export const api = {
  summary: (factoryId?: number | null) =>
    request<DashboardSummary>(withQuery("/api/dashboard/summary", factoryId)),
  zonesLive: (factoryId?: number | null) =>
    request<ZoneLive[]>(withQuery("/api/dashboard/zones-live", factoryId)),
  alertsSummary: (hours = 24, factoryId?: number | null) =>
    request<AlertsSummary>(withQuery(`/api/dashboard/alerts-summary?hours=${hours}`, factoryId)),
  factories: () => request<Factory[]>("/api/factories"),

  /* ── IoT connections ─────────────────────────────────────────────── */
  connectionOverview: () => request<ConnectionOverview>("/api/connections/overview"),
  testBroker: (body: {
    host?: string; port?: number; username?: string; password?: string;
    use_tls?: boolean; timeout_seconds?: number;
  }) =>
    request<BrokerProbeResult>("/api/connections/broker/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  addDeviceConnection: (body: {
    device_id: string; zone_id: number; protocol: DeviceProtocol;
    firmware_version?: string; mqtt_topic?: string; password?: string;
  }) =>
    request<DeviceConnectionRecipe>("/api/connections/devices", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deviceRecipe: (deviceDbId: number) =>
    request<DeviceConnectionRecipe>(`/api/connections/devices/${deviceDbId}/recipe`),
  verifyDeviceConnection: (deviceDbId: number) =>
    request<DeviceVerification>(`/api/connections/devices/${deviceDbId}/verify`, {
      method: "POST",
    }),
  /** Record a plant's site fix so the GIS map knows where the plant is. */
  setFactoryLocation: (
    factoryId: number,
    body: { latitude: number; longitude: number; site_span_m: number; location?: string },
  ) =>
    request<Factory>(`/api/factories/${factoryId}/location`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  zones: (factoryId?: number | null) =>
    request<Zone[]>(withQuery("/api/zones", factoryId)),
  zone: (id: number) => request<Zone>(`/api/zones/${id}`),
  zoneTelemetry: (id: number) => request<Reading[]>(`/api/zones/${id}/telemetry`),
  zoneHistory: (id: number, hours: number) =>
    request<Reading[]>(`/api/zones/${id}/history?hours=${hours}`),
  zonePredictions: (id: number, limit = 20) =>
    request<Prediction[]>(`/api/zones/${id}/predictions?limit=${limit}`),
  devices: (factoryId?: number | null) =>
    request<Device[]>(withQuery("/api/devices", factoryId)),
  pingDevice: (id: number) =>
    request<Device>(`/api/devices/${id}/ping`, { method: "POST" }),
  alerts: (params = "", factoryId?: number | null) =>
    request<Alert[]>(withQuery(`/api/alerts${params}`, factoryId)),
  acknowledgeAlert: (id: number) =>
    request<Alert>(`/api/alerts/${id}/acknowledge`, { method: "POST" }),

  // --- Risk projection (+N minutes) ----------------------------------------
  forecasts: (factoryId?: number | null) =>
    request<RiskForecast[]>(withQuery("/api/forecasts", factoryId)),
  zoneForecast: (id: number, limit = 60) =>
    request<RiskForecast[]>(`/api/forecasts/${id}?limit=${limit}`),

  // --- Security & notification channels (admin surfaces) -------------------
  notificationStatus: () => request<NotificationStatus>("/api/security/notifications"),
  deviceCredentials: () =>
    request<DeviceCredential[]>("/api/security/device-credentials"),
  registerDevice: (body: { device_id: string; zone_id: number;
    firmware_version?: string; mqtt_topic?: string | null }) =>
    request<DeviceCredentialIssued>("/api/security/devices/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rotateCredential: (deviceDbId: number) =>
    request<DeviceCredentialIssued>(
      `/api/security/devices/${deviceDbId}/credential/rotate`, { method: "POST" }),
  setDeviceEnabled: (deviceDbId: number, enabled: boolean) =>
    request<{ device_id: string; enabled: boolean }>(
      `/api/security/devices/${deviceDbId}/enabled?enabled=${enabled}`, { method: "POST" }),

  // --- Dispersion model (GIS map) ----------------------------------------
  dispersionDefaults: () =>
    request<DispersionDefaults>("/api/dispersion/defaults"),
  dispersionPredict: (body: DispersionRequest) =>
    request<DispersionResult>("/api/dispersion/predict", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  dispersionKmlUrl: (params: DispersionRequest & { source_lat: number; source_lng: number }) => {
    const qs = new URLSearchParams({
      gas_type: params.gas_type,
      sensor_value: String(params.sensor_value),
      wind_speed: String(params.wind_speed),
      wind_direction: String(params.wind_direction),
      stability_class: params.stability_class,
      temperature_c: String(params.temperature_c),
      source_lat: String(params.source_lat),
      source_lng: String(params.source_lng),
      max_distance: String(params.max_distance),
      resolution: String(params.resolution),
    });
    return `${API_BASE}/api/dispersion/kml?${qs}`;
  },

  /* ── CSV import / export ──────────────────────────────────────────── */
  exportConnectionsCsv: () => {
    const token = getToken();
    const url = `${API_BASE}/api/connections/devices/export`;
    // Open download directly — no request wrapper needed for a binary blob.
    const a = document.createElement("a");
    a.href = url;
    a.download = "aeris_connections.csv";
    if (token) a.href += `?token=${token}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  importConnectionsCsv: (file: File) => {
    const token = getToken();
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return fetch(`${API_BASE}/api/connections/devices/import`, {
      method: "POST",
      headers,
      body: file,
    }).then((r) => {
      if (!r.ok) throw new Error(`Import failed: ${r.status}`);
      return r.json();
    }) as Promise<CsvImportResult>;
  },

  /* ── HTTP gateway simulator ───────────────────────────────────────── */
  simulatorStatus: () => request<SimulatorStatus>("/api/connections/simulator"),
  startSimulator: () =>
    request<{ running: boolean }>("/api/connections/simulator/start", { method: "POST" }),
  stopSimulator: () =>
    request<{ running: boolean }>("/api/connections/simulator/stop", { method: "POST" }),

};
