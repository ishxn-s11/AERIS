"use client";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/shell";
import { Empty, HeroMeta, Metric, PageHero, Panel, timeAgo } from "@/components/ui";
import { LiveSpark } from "@/components/charts";
import { api } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import { RISK_HEX } from "@/lib/risk";
import { useZoneFeed } from "@/lib/use-zones";
import type { Device } from "@/lib/types";

const STATUS_TONE: Record<string, { dot: string; text: string }> = {
  online: { dot: "bg-jade", text: "text-jade" },
  degraded: { dot: "bg-brass", text: "text-brass" },
  offline: { dot: "bg-scarlet", text: "text-scarlet" },
};

function DevicesView() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { zones } = useZoneFeed(15_000);
  const { factoryId } = useFactory();

  const load = useCallback(
    () => api.devices(factoryId).then(setDevices).catch(() => undefined),
    [factoryId],
  );

  useEffect(() => {
    setDevices([]);
    load();
    const id = setInterval(load, 12_000);
    return () => clearInterval(id);
  }, [load]);

  const zoneName = (zoneId: number) => zones.find((z) => z.zone_id === zoneId)?.name ?? `zone ${zoneId}`;
  const zoneRisk = (zoneId: number) => zones.find((z) => z.zone_id === zoneId);

  const online = devices.filter((d) => d.status === "online").length;
  const offline = devices.filter((d) => d.status === "offline").length;

  async function markSeen(d: Device) {
    setBusyId(d.id);
    setError(null);
    try {
      const updated = await api.pingDevice(d.id);
      setDevices((list) => list.map((x) => (x.id === updated.id ? updated : x)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "action failed");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <PageHero
        eyebrow="Monitoring hardware"
        headline={
          <>
            A sensor that stops talking
            <br />
            is itself an <span className="font-black text-volt">incident</span>.
          </>
        }
        meta={
          <>
            <HeroMeta label="Nodes registered" value={devices.length} />
            <HeroMeta label="Reporting" value={online} color={RISK_HEX.SAFE} />
            <HeroMeta label="Not reporting" value={offline} color={offline > 0 ? RISK_HEX.CRITICAL : undefined} />
            <HeroMeta label="Offline threshold" value="configurable" />
          </>
        }
      />

      <section className="grid grid-cols-2 gap-6 py-10 md:grid-cols-4">
        <Metric label="Nodes total" value={devices.length} />
        <Metric label="Online" value={online} color={RISK_HEX.SAFE} />
        <Metric label="Offline" value={offline} color={offline > 0 ? RISK_HEX.CRITICAL : RISK_HEX.SAFE} />
        <Metric
          label="Firmware"
          value={new Set(devices.map((d) => d.firmware_version)).size}
          unit="versions"
        />
      </section>

      {error && (
        <p className="mb-6 border border-scarlet/50 bg-scarlet/10 px-3 py-2 text-xs text-scarlet">
          {error}
        </p>
      )}

      <Panel index="01" title="Node roster" meta="heartbeat tracked per MQTT frame">
        {devices.length === 0 ? (
          <Empty>no nodes registered</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse">
              <thead>
                <tr className="border-b border-line">
                  {["Node ID", "Zone", "Status", "Last seen", "Firmware", "Channel", "Temperature trend", "Action"].map(
                    (h) => (
                      <th key={h} className="micro py-3 text-left font-normal text-faint">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => {
                  const tone = STATUS_TONE[d.status] ?? STATUS_TONE.offline;
                  const zone = zoneRisk(d.zone_id);
                  return (
                    <tr key={d.id} className="border-b border-[var(--color-line-soft)]">
                      <td className="py-4 font-mono text-[12px] text-paper">{d.device_id}</td>
                      <td className="text-sm text-muted">{zoneName(d.zone_id)}</td>
                      <td>
                        <span className="flex items-center gap-2">
                          <span className={`h-1.5 w-1.5 ${tone.dot} ${d.status === "online" ? "blink" : ""}`} />
                          <span className={`micro ${tone.text}`}>{d.status}</span>
                        </span>
                      </td>
                      <td className="micro text-faint">{timeAgo(d.last_seen)}</td>
                      <td className="font-mono text-[11px] text-muted">{d.firmware_version}</td>
                      <td className="micro text-muted">
                        aeris/factory1/{zone?.name.toLowerCase().replace(/ /g, "-") ?? "unknown"}/telemetry
                      </td>
                      <td className="w-40">
                        <LiveSpark
                          zoneId={d.zone_id}
                          dataKey="temperature"
                          color={zone ? RISK_HEX[zone.risk_level] : "#8a8c87"}
                          height={28}
                        />
                      </td>
                      <td>
                        <button
                          onClick={() => markSeen(d)}
                          disabled={busyId === d.id}
                          className="micro border border-line px-3 py-2 text-muted transition-colors hover:border-paper hover:text-paper disabled:opacity-50"
                        >
                          {busyId === d.id ? "…" : "Force heartbeat"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel index="02" title="Failure detection" className="mt-14">
        <div className="grid gap-8 md:grid-cols-2">
          <div>
            <p className="text-sm leading-relaxed text-muted">
              A node is flagged <span className="text-scarlet">OFFLINE</span> when no telemetry
              frame arrives inside the configured silence window; a separate
              <span className="text-brass"> SENSOR MALFUNCTION</span> alert fires when values
              freeze abnormally or fall outside physical plausibility. Hardware faults are
              stored as their own hazard class so they never mask a real atmosphere event.
            </p>
          </div>
          <div className="space-y-px">
            {["DEVICE_OFFLINE", "SENSOR_MALFUNCTION"].map((h) => (
              <div key={h} className="flex items-baseline justify-between border-b border-[var(--color-line-soft)] py-3">
                <span className="font-mono text-[12px] text-paper">{h}</span>
                <span className="micro text-faint">separate hazard class</span>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <DevicesView />
    </AppShell>
  );
}
