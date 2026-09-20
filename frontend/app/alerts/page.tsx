"use client";
import { useCallback, useEffect, useState } from "react";
import { AppShell } from "@/components/shell";
import {
  Empty, HeroMeta, Metric, PageHero, SeverityChip, Toggle,
  formatDateTime, timeAgo,
} from "@/components/ui";
import { api, getStoredUser } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import { RISK_HEX } from "@/lib/risk";
import type { Alert, AlertsSummary } from "@/lib/types";

type SevFilter = "all" | "critical" | "high" | "warning";
type AckFilter = "open" | "all" | "acknowledged";

function sensorChips(values: Alert["sensor_values"]) {
  if (!values) return null;
  return Object.entries(values)
    .slice(0, 7)
    .map(([k, v]) => (
      <span key={k} className="font-mono text-[10px] tabular-nums text-muted">
        {k.replace("temperature", "T").replace("methane", "CH4").replace("humidity", "RH").replace("smoke", "SMK").toUpperCase()}{" "}
        <span className="text-paper">{typeof v === "number" ? Math.round(v * 10) / 10 : String(v)}</span>
      </span>
    ));
}

function AlertsView() {
  const [severity, setSeverity] = useState<SevFilter>("all");
  const [ack, setAck] = useState<AckFilter>("open");
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [summary, setSummary] = useState<AlertsSummary | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const user = getStoredUser();
  const { factoryId } = useFactory();
  const canAcknowledge = user?.role === "admin" || user?.role === "safety_officer";

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: "120" });
    if (severity !== "all") params.set("severity", severity);
    if (ack !== "all") params.set("acknowledged", String(ack === "acknowledged"));
    return Promise.all([
      api.alerts(`?${params.toString()}`, factoryId),
      api.alertsSummary(24, factoryId),
    ]).then(([a, s]) => {
      setAlerts(a);
      setSummary(s);
    });
  }, [severity, ack, factoryId]);

  useEffect(() => {
    let alive = true;
    const run = () => load().catch((e) => alive && setError(e instanceof Error ? e.message : "load failed"));
    run();
    const id = setInterval(run, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [load]);

  async function acknowledge(alert: Alert) {
    setBusyId(alert.id);
    setError(null);
    try {
      const updated = await api.acknowledgeAlert(alert.id);
      setAlerts((list) => list.map((a) => (a.id === updated.id ? updated : a)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "acknowledge failed");
    } finally {
      setBusyId(null);
    }
  }

  const critical = alerts.filter((a) => a.severity === "critical").length;

  return (
    <>
      <PageHero
        eyebrow={`Incident log · last 24 hours${factoryId === null ? " · all plants" : ""}`}
        headline={
          <>
            What happened,
            <br />
            and who <span className="font-black text-volt">owns</span> it.
          </>
        }
        meta={
          <>
            <HeroMeta label="Alerts 24h" value={summary?.total ?? "—"} />
            <HeroMeta label="Critical 24h" value={summary?.by_severity.critical ?? "—"} color={RISK_HEX.CRITICAL} />
            <HeroMeta label="High 24h" value={summary?.by_severity.high ?? "—"} color={RISK_HEX.HIGH} />
            <HeroMeta label="Warning 24h" value={summary?.by_severity.warning ?? "—"} color={RISK_HEX.WARNING} />
            <HeroMeta label="Role" value={user?.role.replace("_", " ") ?? "—"} />
          </>
        }
      />

      <section className="grid grid-cols-2 gap-6 py-10 md:grid-cols-4">
        <Metric label="In view" value={alerts.length} sub="filtered records" />
        <Metric label="Critical in view" value={critical} color={RISK_HEX.CRITICAL} />
        <Metric label="Unacknowledged" value={alerts.filter((a) => !a.acknowledged).length} color={RISK_HEX.HIGH} />
        <Metric label="Acknowledged" value={alerts.filter((a) => a.acknowledged).length} color={RISK_HEX.SAFE} />
      </section>

      <section className="flex flex-wrap items-end gap-8 border-t border-line pt-5">
        <div>
          <div className="micro mb-3 text-faint">Severity</div>
          <Toggle
            value={severity}
            onChange={setSeverity}
            options={[
              { value: "all", label: "All" },
              { value: "critical", label: "Critical" },
              { value: "high", label: "High" },
              { value: "warning", label: "Warning" },
            ]}
          />
        </div>
        <div>
          <div className="micro mb-3 text-faint">Status</div>
          <Toggle
            value={ack}
            onChange={setAck}
            options={[
              { value: "open", label: "Open" },
              { value: "acknowledged", label: "Acknowledged" },
              { value: "all", label: "All" },
            ]}
          />
        </div>
        {!canAcknowledge && (
          <p className="micro text-faint">
            operator role · acknowledgment restricted to safety officers
          </p>
        )}
      </section>

      {error && (
        <p className="mt-6 border border-scarlet/50 bg-scarlet/10 px-3 py-2 text-xs text-scarlet">
          {error}
        </p>
      )}

      <section className="mt-10">
        {alerts.length === 0 ? (
          <Empty>no alerts match this filter</Empty>
        ) : (
          <div className="space-y-px">
            {alerts.map((a) => (
              <article
                key={a.id}
                className="grid gap-6 border border-line p-5 md:grid-cols-[6px_1fr_auto]"
                style={{ borderLeftColor: {
                  critical: RISK_HEX.CRITICAL, high: RISK_HEX.HIGH, warning: RISK_HEX.WARNING,
                }[a.severity], borderLeftWidth: 2 }}
              >
                <div className="hidden md:block" />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-4">
                    <SeverityChip severity={a.severity} />
                    <span className="micro text-faint">{a.hazard_type.replace("_", " ")}</span>
                    {a.acknowledged && (
                      <span className="micro text-jade">
                        acknowledged · {a.acknowledged_by ?? "officer"}
                      </span>
                    )}
                  </div>
                  <p className="mt-3 text-base text-paper">{a.message}</p>
                  {a.sensor_values && (
                    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 border-t border-[var(--color-line-soft)] pt-3">
                      {sensorChips(a.sensor_values)}
                    </div>
                  )}
                  <div className="micro mt-3 text-faint">
                    {formatDateTime(a.timestamp)} · {timeAgo(a.timestamp)}
                    {a.acknowledged_at ? ` · closed ${timeAgo(a.acknowledged_at)}` : ""}
                  </div>
                </div>

                <div className="flex items-start md:justify-end">
                  {a.acknowledged ? (
                    <span className="micro text-faint">closed</span>
                  ) : canAcknowledge ? (
                    <button
                      onClick={() => acknowledge(a)}
                      disabled={busyId === a.id}
                      className="micro border border-line px-4 py-3 text-paper transition-colors hover:border-volt hover:text-volt disabled:opacity-50"
                    >
                      {busyId === a.id ? "closing…" : "Acknowledge"}
                    </button>
                  ) : (
                    <span className="micro text-faint">awaiting officer</span>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <AlertsView />
    </AppShell>
  );
}
