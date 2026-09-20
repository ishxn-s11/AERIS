"use client";
/** Security & provisioning console.
 *
 * Two questions an operator/administrator has to answer here:
 *  1. If an alert fires at 3am, which channels actually deliver it — and is the
 *     one channel I care about configured?
 *  2. Which nodes are trusted, when were their credentials last issued, and how
 *     do I revoke one?
 */
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/shell";
import {
  Empty, HeroMeta, Metric, PageHero, Panel, StatusDot, ValueRow, formatDateTime, timeAgo,
} from "@/components/ui";
import { api, getStoredUser } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import { RISK_HEX } from "@/lib/risk";
import type {
  Device, DeviceCredential, DeviceCredentialIssued, NotificationStatus, Zone,
} from "@/lib/types";

const SEVERITY_TONE: Record<string, string> = {
  warning: "text-brass",
  high: "text-ember",
  critical: "text-scarlet",
};

function Security() {
  const { factory } = useFactory();
  const user = getStoredUser();
  const isAdmin = user?.role === "admin";

  const [status, setStatus] = useState<NotificationStatus | null>(null);
  const [credentials, setCredentials] = useState<DeviceCredential[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [issued, setIssued] = useState<DeviceCredentialIssued | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ device_id: "", zone_id: "" });

  const load = useCallback(() => {
    api.notificationStatus().then(setStatus).catch(() => undefined);
    api.deviceCredentials().then(setCredentials).catch(() => undefined);
    api.devices().then(setDevices).catch(() => undefined);
    api.zones().then(setZones).catch(() => undefined);
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 20_000);
    return () => clearInterval(id);
  }, [load]);

  const counters = new Map(
    (status?.dispatch.channels ?? []).map((c) => [c.name, c.counters]),
  );
  const credentialByDevice = new Map(credentials.map((c) => [c.device_id, c]));
  const provisioned = credentials.filter((c) => c.has_credential).length;
  const misconfigured = (status?.channels ?? []).filter((c) => c.enabled && !c.configured);

  async function register(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body = { device_id: form.device_id.trim(), zone_id: Number(form.zone_id) };
      if (!body.device_id || !body.zone_id) throw new Error("Device id and zone are required");
      setIssued(await api.registerDevice(body));
      setForm({ device_id: "", zone_id: "" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  async function rotate(deviceDbId: number) {
    setError(null);
    try {
      setIssued(await api.rotateCredential(deviceDbId));
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rotation failed");
    }
  }

  async function toggle(deviceDbId: number, enabled: boolean) {
    setError(null);
    try {
      await api.setDeviceEnabled(deviceDbId, enabled);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    }
  }

  return (
    <>
      <PageHero
        eyebrow={`Security · identity & delivery ${factory ? `· ${factory.name}` : ""}`}
        headline={
          <>
            Trusted nodes.
            <br />
            Delivered <span className="font-black text-volt">warnings</span>.
          </>
        }
        meta={
          <>
            <HeroMeta label="Alert bus" value={status?.bus.mode ?? "—"} color={status?.bus.mode === "redis" ? RISK_HEX.SAFE : RISK_HEX.WARNING} />
            <HeroMeta label="Channels enabled" value={(status?.channels ?? []).filter((c) => c.enabled).length} />
            <HeroMeta label="Nodes provisioned" value={`${provisioned} / ${devices.length}`} />
            <HeroMeta label="Role" value={user?.role ?? "—"} />
          </>
        }
      />

      <section className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 md:grid-cols-4">
        <Metric
          label="Bus mode"
          value={status?.bus.mode === "redis" ? "Redis" : "In-process"}
          sub={
            status?.bus.mode === "redis"
              ? `channel ${status.bus.channel}`
              : "Redis unset — dispatch happens in this process"
          }
          color={status?.bus.mode === "redis" ? RISK_HEX.SAFE : RISK_HEX.WARNING}
        />
        <Metric
          label="Events fanned out"
          value={status?.dispatch.events_received ?? "—"}
          sub={`${status?.dispatch.workers ?? "—"} delivery workers`}
        />
        <Metric
          label="Publish failures"
          value={status?.bus.publish_failures ?? "—"}
          color={(status?.bus.publish_failures ?? 0) > 0 ? RISK_HEX.HIGH : undefined}
        />
        <Metric
          label="Misconfigured"
          value={misconfigured.length}
          color={misconfigured.length > 0 ? RISK_HEX.CRITICAL : RISK_HEX.SAFE}
          sub={misconfigured.length > 0 ? misconfigured.map((c) => c.name).join(", ") : "every enabled channel is configured"}
        />
      </section>

      {/* --------------------------------------------------------- channels */}
      <Panel
        index="01"
        title="Notification channels"
        meta="email + SMS behind the AlertProvider interface"
        className="mt-4"
      >
        {(status?.channels ?? []).length === 0 ? (
          <Empty>no channels reported</Empty>
        ) : (
          <div className="grid gap-px lg:grid-cols-2">
            {status!.channels.map((channel) => {
              const counter = counters.get(channel.name);
              return (
                <article key={channel.name} className="border border-line p-5">
                  <header className="flex items-baseline gap-3">
                    <StatusDot
                      tone={channel.enabled && channel.configured ? "bg-jade" : channel.enabled ? "bg-scarlet" : "bg-muted"}
                    />
                    <h3 className="text-sm font-semibold uppercase tracking-[0.14em] text-paper">
                      {channel.name}
                    </h3>
                    <span className="micro ml-auto text-faint">{channel.provider_class}</span>
                  </header>

                  <div className="mt-4">
                    <ValueRow label="Enabled" value={channel.enabled ? "yes" : "no"} />
                    <ValueRow
                      label="Configured"
                      value={channel.configured ? "yes" : "no"}
                      color={channel.configured ? RISK_HEX.SAFE : RISK_HEX.CRITICAL}
                    />
                    <ValueRow
                      label="Minimum severity"
                      value={channel.min_severity ?? "any"}
                      color={channel.min_severity ? RISK_HEX[channel.min_severity === "critical" ? "CRITICAL" : channel.min_severity === "high" ? "HIGH" : "WARNING"] : undefined}
                    />
                    <ValueRow label="Recipients" value={channel.recipients} />
                    <ValueRow label="Transport" value={channel.transport} />
                    <ValueRow label="Sent / skipped / failed" value={`${counter?.sent ?? 0} / ${counter?.skipped ?? 0} / ${counter?.failed ?? 0}`} />
                    {counter?.last_sent_at && (
                      <ValueRow label="Last delivery" value={timeAgo(counter.last_sent_at)} />
                    )}
                    {counter?.last_error && (
                      <ValueRow label="Last error" value={counter.last_error} color={RISK_HEX.CRITICAL} />
                    )}
                  </div>

                  {channel.detail && (
                    <p className="micro mt-4 border-t border-line-soft pt-3 text-faint">{channel.detail}</p>
                  )}
                </article>
              );
            })}
          </div>
        )}

        <p className="micro mt-5 text-faint">
          Channels are off unless an operator enables them and supplies recipients — this prototype
          never sends outbound messages by accident. Minimum severity is the per-channel gate: SMS
          defaults to critical only.
        </p>
      </Panel>

      {/* ------------------------------------------------------- credentials */}
      <Panel
        index="02"
        title="Device credentials"
        meta="pre-registration · one broker username + password per node"
        className="mt-14"
      >
        {error && (
          <div className="mb-5 border border-scarlet/50 px-4 py-3">
            <p className="micro text-scarlet">{error}</p>
          </div>
        )}

        {issued && (
          <div className="mb-6 border border-volt/50 bg-ink-2 p-5">
            <div className="flex flex-wrap items-baseline gap-3">
              <span className="micro text-volt">credential issued — shown once</span>
              <button onClick={() => setIssued(null)} className="micro ml-auto text-faint hover:text-paper">
                dismiss
              </button>
            </div>
            <div className="mt-4 grid gap-x-8 md:grid-cols-2">
              <div>
                <ValueRow label="Device" value={issued.device_id} />
                <ValueRow label="MQTT username" value={issued.username} />
                <ValueRow label="Topic" value={issued.mqtt_topic ?? "—"} />
              </div>
              <div>
                <div className="micro text-faint">Password</div>
                <div className="mt-2 select-all break-all border border-line bg-ink px-3 py-2 font-mono text-[12px] text-paper">
                  {issued.password}
                </div>
                <p className="micro mt-3 text-faint">{issued.warning}</p>
              </div>
            </div>
          </div>
        )}

        {isAdmin ? (
          <form onSubmit={register} className="mb-8 grid gap-3 border border-line p-5 md:grid-cols-[1.4fr_1fr_auto]">
            <label className="block">
              <span className="micro text-faint">Device id</span>
              <input
                value={form.device_id}
                onChange={(e) => setForm((f) => ({ ...f, device_id: e.target.value }))}
                placeholder="NODE_05"
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
            <button
              type="submit"
              disabled={busy}
              className="micro mt-6 self-start border border-paper bg-paper px-4 py-2.5 text-ink transition-opacity hover:opacity-85 disabled:opacity-40"
            >
              {busy ? "registering…" : "Pre-register node"}
            </button>
          </form>
        ) : (
          <p className="micro mb-8 border border-line px-4 py-3 text-faint">
            Registration and rotation are admin-only. Your role ({user?.role}) can view the
            credential status but not change it.
          </p>
        )}

        {devices.length === 0 ? (
          <Empty>no nodes registered</Empty>
        ) : (
          <div>
            <div className="micro grid grid-cols-[1.1fr_1fr_1fr_0.8fr_auto] gap-4 border-b border-line pb-3 text-faint">
              <span>Node</span>
              <span>Credential</span>
              <span>Issued</span>
              <span>Status</span>
              <span className="text-right">Actions</span>
            </div>
            {devices.map((device) => {
              const credential = credentialByDevice.get(device.device_id);
              const enabled = credential?.enabled ?? true;
              return (
                <div
                  key={device.id}
                  className="grid grid-cols-[1.1fr_1fr_1fr_0.8fr_auto] items-center gap-4 border-b border-line-soft py-3 last:border-b-0"
                >
                  <div>
                    <div className="font-mono text-[13px] text-paper">{device.device_id}</div>
                    <div className="micro text-faint">
                      {zones.find((z) => z.id === device.zone_id)?.name ?? `zone ${device.zone_id}`}
                    </div>
                  </div>
                  <div className="font-mono text-[11px] tabular-nums text-muted">
                    {credential?.has_credential ? (
                      credential.username
                    ) : (
                      <span className="text-brass">none — auto-provisioned</span>
                    )}
                  </div>
                  <div className="micro text-faint">
                    {credential?.rotated_at
                      ? `rotated ${timeAgo(credential.rotated_at)}`
                      : credential?.issued_at
                        ? formatDateTime(credential.issued_at)
                        : "—"}
                  </div>
                  <div className="flex items-center gap-2">
                    <StatusDot
                      tone={enabled ? "bg-jade" : "bg-scarlet"}
                      label={enabled ? "trusted" : "revoked"}
                    />
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    {isAdmin && (
                      <>
                        <button
                          onClick={() => rotate(device.id)}
                          className="micro border border-line px-3 py-2 text-muted transition-colors hover:border-paper hover:text-paper"
                        >
                          Rotate
                        </button>
                        <button
                          onClick={() => toggle(device.id, !enabled)}
                          className={`micro border px-3 py-2 transition-colors ${
                            enabled
                              ? "border-scarlet/50 text-scarlet hover:border-scarlet"
                              : "border-jade/40 text-jade hover:border-jade"
                          }`}
                        >
                          {enabled ? "Revoke" : "Restore"}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <p className="micro mt-5 text-faint">
          The broker is the enforcement point: Mosquitto cannot query PostgreSQL, so run
          <span className="text-muted"> backend/scripts/sync_mqtt_credentials.py</span> to project
          these credentials into the password file and per-device ACL, then start the stack with the
          auth overlay. Revoking here takes effect in the backend immediately and at the broker on
          the next sync.
        </p>
      </Panel>

      <p className={`micro mt-10 stagger ${SEVERITY_TONE.critical}`}>
        Prototype security posture — per-device credentials and ACLs are real; TLS, secret storage
        and credential rotation policy are the documented production gaps.
      </p>
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <Security />
    </AppShell>
  );
}
