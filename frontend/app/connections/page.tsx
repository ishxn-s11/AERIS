"use client";
/**
 * IoT connections console.
 *
 * The page answers the three questions onboarding actually raises:
 *  1. Is the broker we are configured against reachable, and will it accept a
 *     credential? (probe, with the failing stage named)
 *  2. How do I connect a new node — and what exactly do I type on it?
 *     (protocol-specific recipe: broker publish vs REST POST)
 *  3. What is connected right now, and is it actually delivering?
 *     (register with liveness, credential state and a per-node verify)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/shell";
import {
  Empty, HeroMeta, Metric, PageHero, Panel, StatusDot, ValueRow, formatDateTime, timeAgo,
} from "@/components/ui";
import { api, getStoredUser } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import { RISK_HEX } from "@/lib/risk";
import type {
  BrokerProbeResult, ConnectionOverview, CsvImportResult, DeviceConnection,
  DeviceConnectionRecipe, DeviceProtocol, DeviceVerification, SimulatorStatus, Zone,
} from "@/lib/types";

const PROTOCOLS: { id: DeviceProtocol; label: string; hint: string }[] = [
  { id: "mqtt", label: "MQTT", hint: "Node publishes frames to the broker; ACL-bound topic" },
  { id: "http", label: "HTTP / REST", hint: "Node POSTs frames to /api/telemetry with its token" },
];

function Connections() {
  const { factory } = useFactory();
  const user = getStoredUser();
  const canProvision = user?.role === "admin" || user?.role === "safety_officer";

  const [overview, setOverview] = useState<ConnectionOverview | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [probe, setProbe] = useState<BrokerProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [recipe, setRecipe] = useState<DeviceConnectionRecipe | null>(null);
  const [verifications, setVerifications] = useState<Record<number, DeviceVerification>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [simulator, setSimulator] = useState<SimulatorStatus | null>(null);
  const [importResult, setImportResult] = useState<CsvImportResult | null>(null);
  const [importBusy, setImportBusy] = useState(false);

  const [form, setForm] = useState({
    device_id: "",
    zone_id: "",
    protocol: "mqtt" as DeviceProtocol,
    password: "",
  });

  const load = useCallback(() => {
    api.connectionOverview().then(setOverview).catch(() => undefined);
    api.zones().then(setZones).catch(() => undefined);
    api.simulatorStatus().then(setSimulator).catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [load]);

  const broker = overview?.broker;
  const devices = useMemo(() => overview?.devices ?? [], [overview]);

  async function runProbe() {
    setProbing(true);
    setError(null);
    try {
      // No body values: probe the broker this backend is actually configured
      // against, with its own credentials.
      setProbe(await api.testBroker({}));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Broker probe failed");
    } finally {
      setProbing(false);
    }
  }

  async function addDevice(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const body = {
      device_id: form.device_id.trim(),
      zone_id: Number(form.zone_id),
      protocol: form.protocol,
      ...(form.password ? { password: form.password } : {}),
    };
    if (!body.device_id || !body.zone_id) {
      setError("Device id and zone are required");
      return;
    }
    setBusy(true);
    try {
      setRecipe(await api.addDeviceConnection(body));
      setForm({ device_id: "", zone_id: "", protocol: form.protocol, password: "" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the connection");
    } finally {
      setBusy(false);
    }
  }

  async function verify(deviceDbId: number) {
    setError(null);
    try {
      const result = await api.verifyDeviceConnection(deviceDbId);
      setVerifications((v) => ({ ...v, [deviceDbId]: result }));
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    }
  }

  async function toggle(deviceDbId: number, enabled: boolean) {
    setError(null);
    try {
      await api.setDeviceEnabled(deviceDbId, enabled);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the node");
    }
  }

  async function toggleSimulator() {
    setError(null);
    try {
      if (simulator?.running) {
        await api.stopSimulator();
      } else {
        await api.startSimulator();
      }
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not toggle simulator");
    }
  }

  async function handleImport(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportBusy(true);
    setImportResult(null);
    try {
      const result = await api.importConnectionsCsv(file);
      setImportResult(result);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setImportBusy(false);
      e.target.value = "";
    }
  }

  return (
    <>
      <PageHero
        eyebrow={`Integration · transports ${factory ? `· ${factory.name}` : ""}`}
        headline={
          <>
            Bring the floor
            <br />
            <span className="font-black text-volt">online</span>.
          </>
        }
        meta={
          <>
            <HeroMeta
              label="Broker"
              value={broker ? `${broker.host}:${broker.port}` : "—"}
              color={broker?.connected ? RISK_HEX.SAFE : RISK_HEX.CRITICAL}
            />
            <HeroMeta label="MQTT nodes" value={overview?.by_protocol.mqtt ?? "—"} />
            <HeroMeta label="HTTP nodes" value={overview?.by_protocol.http ?? 0} />
            <HeroMeta
              label="Auto-provision"
              value={overview ? (overview.auto_provision ? "on" : "off") : "—"}
              color={overview?.auto_provision ? RISK_HEX.WARNING : RISK_HEX.SAFE}
            />
          </>
        }
      />

      <section className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 md:grid-cols-4">
        <Metric
          label="Broker link"
          value={broker?.connected ? "Connected" : "Down"}
          color={broker?.connected ? RISK_HEX.SAFE : RISK_HEX.CRITICAL}
          sub={broker?.auth_mode ?? "—"}
        />
        <Metric
          label="Nodes online"
          value={`${overview?.online ?? 0} / ${overview?.total ?? 0}`}
          sub={`${overview?.unprovisioned ?? 0} without a credential`}
          color={(overview?.unprovisioned ?? 0) > 0 ? RISK_HEX.WARNING : undefined}
        />
        <Metric
          label="Rejected frames"
          value={broker?.rejected_frames ?? 0}
          sub="unregistered or disabled senders"
          color={(broker?.rejected_frames ?? 0) > 0 ? RISK_HEX.HIGH : undefined}
        />
        <Metric
          label="Revoked"
          value={overview?.revoked ?? 0}
          sub="nodes whose frames are refused"
          color={(overview?.revoked ?? 0) > 0 ? RISK_HEX.CRITICAL : undefined}
        />
      </section>

      {error && (
        <div className="mb-8 border border-scarlet/50 px-4 py-3">
          <p className="micro text-scarlet">{error}</p>
        </div>
      )}

      {/* ------------------------------------------------------------ broker */}
      <Panel
        index="01"
        title="MQTT broker"
        meta="environment-configured · verified live, not assumed"
      >
        <div className="grid gap-px lg:grid-cols-[1.15fr_1fr]">
          <div className="border border-line p-5">
            <div className="micro mb-4 text-faint">Active connection</div>
            <ValueRow label="Host" value={broker ? `${broker.host}:${broker.port}` : "—"} />
            <ValueRow
              label="Transport"
              value={broker?.use_tls ? `${broker.transport} + TLS` : `${broker?.transport ?? "—"} (plaintext)`}
            />
            <ValueRow label="Auth mode" value={broker?.auth_mode ?? "—"} />
            <ValueRow label="Backend user" value={broker?.username ?? "anonymous"} />
            <ValueRow label="Topic prefix" value={broker?.topic_prefix ?? "—"} />
            <ValueRow label="Client id" value={broker?.client_id ?? "—"} />
            <ValueRow label="Subscription" value={broker?.subscribe_filter ?? "—"} />
            <ValueRow label="QoS" value={broker?.qos ?? "—"} />
            <ValueRow
              label="Live state"
              value={broker?.connected ? "subscribed & receiving" : "not connected"}
              color={broker?.connected ? RISK_HEX.SAFE : RISK_HEX.CRITICAL}
            />
            <p className="micro mt-4 border-t border-line-soft pt-3 text-faint">{broker?.note}</p>
          </div>

          <div className="border border-line p-5">
            <div className="flex flex-wrap items-baseline gap-3">
              <span className="micro text-faint">Connectivity test</span>
              {canProvision && (
                <button
                  onClick={runProbe}
                  disabled={probing}
                  className="micro ml-auto border border-paper bg-paper px-3 py-2 text-ink transition-opacity hover:opacity-85 disabled:opacity-40"
                >
                  {probing ? "probing…" : "Test broker"}
                </button>
              )}
            </div>

            {!canProvision && (
              <p className="micro mt-4 border border-line px-3 py-2 text-faint">
                Probing is admin / safety-officer only. Your role ({user?.role}) can see the
                reported state but not initiate a connection.
              </p>
            )}

            {!probe && canProvision && (
              <p className="micro mt-5 text-faint">
                Opens a socket to the broker, completes the MQTT CONNECT, then disconnects — safe
                against a live broker. It never subscribes or publishes, so running it cannot
                disturb ingestion.
              </p>
            )}

            {probe && (
              <div className="mt-5">
                <div className="flex items-baseline gap-3">
                  <StatusDot tone={probe.ok ? "bg-jade" : "bg-scarlet"} />
                  <span className={`text-sm font-semibold uppercase tracking-[0.14em] ${probe.ok ? "text-jade" : "text-scarlet"}`}>
                    {probe.ok ? "Broker accepted the connection" : `Failed at the ${probe.stage} stage`}
                  </span>
                </div>
                <p className="micro mt-3 text-muted">{probe.detail}</p>
                <p className="micro mt-1 text-faint">
                  {probe.connack_code !== null
                    ? `CONNACK 0x${probe.connack_code.toString(16).padStart(2, "0")}`
                    : "no CONNACK"}
                  {probe.latency_ms !== null && ` · ${probe.latency_ms} ms to socket`}
                </p>

                <div className="mt-5 space-y-2">
                  {probe.steps.map((step) => (
                    <div key={step.name} className="border-t border-line-soft pt-2">
                      <div className="flex items-baseline gap-3">
                        <StatusDot tone={step.ok ? "bg-jade" : "bg-brass"} />
                        <span className="micro text-paper">{step.name}</span>
                        <span className="micro ml-auto tabular-nums text-faint">
                          {step.ms !== null ? `${step.ms} ms` : "—"}
                        </span>
                      </div>
                      {step.detail && (
                        <p className="micro mt-1 text-faint">{step.detail}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="mt-5 border border-line p-5">
          <div className="micro mb-3 text-faint">HTTP ingest — the non-MQTT path</div>
          <ValueRow label="Endpoint" value={overview ? `${overview.http_ingest.method} ${overview.http_ingest.endpoint}` : "—"} />
          <ValueRow label="URL" value={overview?.http_ingest.url ?? "—"} />
          <ValueRow
            label="Token enforcement"
            value={overview?.http_ingest.auth_required ? "required" : "off (prototype default)"}
            color={overview?.http_ingest.auth_required ? RISK_HEX.SAFE : RISK_HEX.WARNING}
          />
          <ValueRow label="Credential" value={overview?.http_ingest.auth_header ?? "—"} />
          <p className="micro mt-3 text-faint">{overview?.http_ingest.note}</p>
        </div>
      </Panel>

      {/* --------------------------------------------------------- add node */}
      <Panel
        index="02"
        title="Add a device connection"
        meta="one form · transport-specific recipe out the other side"
        className="mt-14"
      >
        {canProvision ? (
          <form onSubmit={addDevice} className="grid gap-4 border border-line p-5 lg:grid-cols-[1fr_1fr_1.2fr_auto]">
            <label className="block">
              <span className="micro text-faint">Device id</span>
              <input
                value={form.device_id}
                onChange={(e) => setForm((f) => ({ ...f, device_id: e.target.value }))}
                placeholder="NODE_07"
                className="mt-2 w-full border border-line bg-ink-2 px-3 py-2 font-mono text-[13px] text-paper outline-none focus:border-volt"
              />
            </label>
            <label className="block">
              <span className="micro text-faint">Zone</span>
              <select
                value={form.zone_id}
                onChange={(e) => setForm((f) => ({ ...f, zone_id: e.target.value }))}
                className="mt-2 w-full cursor-pointer border border-line bg-ink-2 px-3 py-2 font-mono text-[13px] text-paper outline-none focus:border-volt"
              >
                <option value="">select a zone…</option>
                {zones.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.name}
                  </option>
                ))}
              </select>
            </label>
            <div className="block">
              <span className="micro text-faint">Transport</span>
              <div className="mt-2 grid grid-cols-2 gap-px">
                {PROTOCOLS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    title={p.hint}
                    onClick={() => setForm((f) => ({ ...f, protocol: p.id }))}
                    className={`border px-3 py-2 font-mono text-[11px] uppercase tracking-wider transition-colors ${
                      form.protocol === p.id
                        ? "border-volt bg-volt/10 text-volt"
                        : "border-line text-muted hover:border-volt/50 hover:text-paper"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              <p className="micro mt-2 text-faint">
                {PROTOCOLS.find((p) => p.id === form.protocol)?.hint}
              </p>
            </div>
            <button
              type="submit"
              disabled={busy}
              className="micro mt-6 self-start border border-paper bg-paper px-4 py-2.5 text-ink transition-opacity hover:opacity-85 disabled:opacity-40"
            >
              {busy ? "connecting…" : "Add connection"}
            </button>

            <label className="block lg:col-span-4">
              <span className="micro text-faint">
                Password override (optional — leave blank to keep the credential already flashed
                onto the hardware; supplying one rotates it deliberately)
              </span>
              <input
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
                placeholder="minimum 8 characters"
                className="mt-2 w-full border border-line bg-ink-2 px-3 py-2 font-mono text-[13px] text-paper outline-none focus:border-volt"
              />
            </label>
          </form>
        ) : (
          <p className="micro border border-line px-4 py-3 text-faint">
            Adding connections is restricted to admin and safety-officer roles. Your role
            ({user?.role}) can inspect the register below.
          </p>
        )}

        {recipe && <RecipeCard recipe={recipe} onDismiss={() => setRecipe(null)} />}

        <p className="micro mt-5 text-faint">
          Registration is idempotent: re-adding a node updates its zone and transport but never
          wipes the secret already flashed onto the device. Rotation is always explicit.
        </p>
      </Panel>

      {/* ------------------------------------------------------- register */}
      <Panel
        index="03"
        title="Connection register"
        meta={`${devices.length} nodes · click verify to test liveness`}
        className="mt-14"
      >
        {/* --- Import / Export / Simulator toolbar --- */}
        <div className="mb-6 flex flex-wrap items-center gap-3 border-b border-line-soft pb-4">
          {canProvision && (
            <>
              <button
                onClick={() => api.exportConnectionsCsv()}
                className="micro border border-line px-3 py-2 text-muted transition-colors hover:border-volt hover:text-volt"
              >
                Export CSV
              </button>
              <label className={`micro cursor-pointer border px-3 py-2 transition-colors ${importBusy ? "border-muted text-muted" : "border-line text-muted hover:border-volt hover:text-volt"}`}>
                {importBusy ? "Importing…" : "Import CSV"}
                <input
                  type="file"
                  accept=".csv"
                  onChange={handleImport}
                  className="hidden"
                  disabled={importBusy}
                />
              </label>
            </>
          )}
          <span className="micro mx-1 text-faint">|</span>
          <button
            onClick={toggleSimulator}
            className={`micro border px-3 py-2 transition-colors ${
              simulator?.running
                ? "border-jade/50 text-jade hover:border-jade"
                : "border-line text-muted hover:border-volt hover:text-volt"
            }`}
          >
            {simulator?.running ? "Stop HTTP simulator" : "Start HTTP simulator"}
          </button>
          {simulator && (
            <span className="micro text-faint">
              {simulator.running
                ? "POSTs telemetry for HTTP devices every 4-7 s"
                : "Simulator off — HTTP nodes stay silent"}
            </span>
          )}
        </div>

        {importResult && (
          <div className="mb-5 border border-volt/50 bg-ink-2 p-4">
            <div className="flex flex-wrap items-baseline gap-3">
              <span className="micro text-volt">Import complete</span>
              <button onClick={() => setImportResult(null)} className="micro ml-auto text-faint hover:text-paper">dismiss</button>
            </div>
            <p className="mt-2 font-mono text-[11px] text-muted">
              {importResult.registered} registered, {importResult.skipped} skipped (out of {importResult.total_rows} rows)
            </p>
            {importResult.errors.length > 0 && (
              <div className="mt-3 max-h-32 overflow-y-auto border border-line p-2">
                {importResult.errors.map((e, i) => (
                  <div key={i} className="font-mono text-[10px] text-scarlet">
                    Row {e.row}: {e.error}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {devices.length === 0 ? (
          <Empty>no nodes registered</Empty>
        ) : (
          <div className="overflow-x-auto">
            <div className="micro grid min-w-[900px] grid-cols-[1fr_0.7fr_1.5fr_0.9fr_0.8fr_0.7fr_auto] gap-4 border-b border-line pb-3 text-faint">
              <span>Node</span>
              <span>Transport</span>
              <span>Endpoint</span>
              <span>Credential</span>
              <span>Last seen</span>
              <span>State</span>
              <span className="text-right">Actions</span>
            </div>
            {devices.map((device) => (
              <ConnectionRow
                key={device.id}
                device={device}
                verification={verifications[device.id]}
                canProvision={canProvision}
                onVerify={() => verify(device.id)}
                onToggle={() => toggle(device.id, !device.enabled)}
              />
            ))}
          </div>
        )}

        <p className="micro mt-5 text-faint">
          MQTT nodes are authorised by the broker ACL — run
          <span className="text-muted"> backend/scripts/sync_mqtt_credentials.py</span> after
          onboarding and reload the broker. HTTP nodes are authenticated by the backend on each
          frame, so they need no broker grant.
        </p>
      </Panel>

      <p className="micro mt-10 text-faint">
        Prototype transport posture — per-node credentials and ACLs are enforced; TLS, a
        secrets manager and certificate-based device identity are the documented production gaps.
      </p>
    </>
  );
}

/* ── Recipe card ──────────────────────────────────────────────────────── */

function RecipeCard({
  recipe,
  onDismiss,
}: {
  recipe: DeviceConnectionRecipe;
  onDismiss: () => void;
}) {
  const isMqtt = recipe.protocol === "mqtt";
  const snippet = isMqtt
    ? [
        recipe.publish_command,
        "",
        "# broker ACL — append to the generated file and reload mosquitto",
        recipe.acl_line,
      ]
        .filter((line): line is string => line !== null)
        .join("\n")
    : [
        recipe.curl_command,
        "",
        "# body",
        JSON.stringify(recipe.sample_payload, null, 2),
      ]
        .filter((line): line is string => line !== null)
        .join("\n");

  return (
    <div className="mt-6 border border-volt/50 bg-ink-2 p-5">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="micro text-volt">
          {recipe.protocol.toUpperCase()} connection ready — {recipe.device_id}
        </span>
        <button onClick={onDismiss} className="micro ml-auto text-faint hover:text-paper">
          dismiss
        </button>
      </div>

      <div className="mt-4 grid gap-x-8 gap-y-2 md:grid-cols-2">
        <div>
          <ValueRow label="Device" value={recipe.device_id} />
          <ValueRow label="Plant" value={recipe.factory_name} />
          <ValueRow label="Zone" value={recipe.zone_name} />
          <ValueRow label="Username" value={recipe.username ?? "—"} />
          {isMqtt ? (
            <>
              <ValueRow label="Broker" value={`${recipe.mqtt_host}:${recipe.mqtt_port}`} />
              <ValueRow label="Topic" value={recipe.mqtt_topic ?? "—"} />
            </>
          ) : (
            <ValueRow label="Endpoint" value={recipe.http_url ?? "—"} />
          )}
        </div>
        <div>
          <div className="micro text-faint">
            {recipe.password ? "Password — shown once" : "Password"}
          </div>
          {recipe.password ? (
            <div className="mt-2 select-all break-all border border-line bg-ink px-3 py-2 font-mono text-[12px] text-paper">
              {recipe.password}
            </div>
          ) : (
            <p className="mt-2 border border-line px-3 py-2 font-mono text-[11px] text-brass">
              existing credential retained — not shown again
            </p>
          )}
          <p className="micro mt-3 text-faint">{recipe.warning}</p>
        </div>
      </div>

      <div className="mt-5">
        <div className="flex items-baseline gap-3">
          <span className="micro text-faint">
            {isMqtt ? "Publish + ACL" : "POST example"}
          </span>
          <button
            onClick={() => navigator.clipboard?.writeText(snippet).catch(() => undefined)}
            className="micro ml-auto border border-line px-3 py-2 text-muted transition-colors hover:border-volt hover:text-volt"
          >
            Copy
          </button>
        </div>
        <pre className="mt-3 overflow-x-auto border border-line bg-ink p-4 font-mono text-[11px] leading-relaxed text-muted">
          {snippet}
        </pre>
      </div>
    </div>
  );
}

/* ── Register row ─────────────────────────────────────────────────────── */

function ConnectionRow({
  device,
  verification,
  canProvision,
  onVerify,
  onToggle,
}: {
  device: DeviceConnection;
  verification?: DeviceVerification;
  canProvision: boolean;
  onVerify: () => void;
  onToggle: () => void;
}) {
  const live = device.status === "online";
  const state = !device.enabled
    ? { tone: "bg-scarlet", label: "revoked" }
    : live
      ? { tone: "bg-jade", label: "online" }
      : { tone: "bg-muted", label: "silent" };

  return (
    <div className="grid min-w-[900px] grid-cols-[1fr_0.7fr_1.5fr_0.9fr_0.8fr_0.7fr_auto] items-center gap-4 border-b border-line-soft py-3 last:border-b-0">
      <div>
        <div className="font-mono text-[13px] text-paper">{device.device_id}</div>
        <div className="micro text-faint">
          {device.zone_name ?? `zone ${device.zone_id}`}
          {device.factory_name ? ` · ${device.factory_name}` : ""}
        </div>
      </div>

      <div>
        <div className="font-mono text-[11px] uppercase tracking-wider text-paper">
          {device.protocol}
        </div>
        <div className="micro text-faint">{device.firmware_version}</div>
      </div>

      <div className="truncate font-mono text-[11px] text-muted" title={device.endpoint ?? ""}>
        {device.endpoint ?? "—"}
      </div>

      <div className="font-mono text-[11px] text-muted">
        {device.has_credential ? (
          <>
            <div className="tabular-nums text-paper">{device.username}</div>
            <div className="micro text-faint">
              {device.rotated_at ? `rotated ${timeAgo(device.rotated_at)}` : "issued"}
              {device.last_authenticated_at ? ` · auth ${timeAgo(device.last_authenticated_at)}` : ""}
            </div>
          </>
        ) : (
          <span className="text-brass">none — auto-provisioned</span>
        )}
      </div>

      <div className="micro text-faint">
        {device.last_seen ? timeAgo(device.last_seen) : "never"}
      </div>

      <div className="flex flex-col gap-1">
        <StatusDot tone={state.tone} label={state.label} />
        {verification && (
          <span
            className={`micro ${verification.last_reading_at ? "text-jade" : "text-brass"}`}
            title={
              verification.last_reading_at
                ? `last frame ${formatDateTime(verification.last_reading_at)}`
                : "no frames stored"
            }
          >
            {verification.verdict}
          </span>
        )}
      </div>

      <div className="flex items-center justify-end gap-2">
        <button
          onClick={onVerify}
          className="micro border border-line px-3 py-2 text-muted transition-colors hover:border-paper hover:text-paper"
        >
          Verify
        </button>
        {canProvision && (
          <button
            onClick={onToggle}
            className={`micro border px-3 py-2 transition-colors ${
              device.enabled
                ? "border-scarlet/50 text-scarlet hover:border-scarlet"
                : "border-jade/40 text-jade hover:border-jade"
            }`}
          >
            {device.enabled ? "Revoke" : "Restore"}
          </button>
        )}
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <AppShell>
      <Connections />
    </AppShell>
  );
}
