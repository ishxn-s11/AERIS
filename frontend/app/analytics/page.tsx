"use client";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/shell";
import { BarSeries, TimeSeries, type SeriesDef } from "@/components/charts";
import { Empty, HeroMeta, Metric, PageHero, Panel, Toggle } from "@/components/ui";
import { api } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import { RISK_HEX, SERIES } from "@/lib/risk";
import { useZoneFeed } from "@/lib/use-zones";
import type { AlertsSummary, DashboardSummary, Reading } from "@/lib/types";

type RangeKey = "1h" | "24h" | "7d";

const RANGE_HOURS: Record<RangeKey, number> = { "1h": 1, "24h": 24, "7d": 168 };
const MAX_POINTS = 220;

const THERMAL: SeriesDef[] = [{ key: "temperature", label: "Temperature", color: SERIES.temperature, unit: "°C" }];
const GAS: SeriesDef[] = [{ key: "methane", label: "Combustible gas", color: SERIES.methane, unit: "ppm" }];
const SMOKE: SeriesDef[] = [{ key: "smoke", label: "Smoke", color: SERIES.smoke, unit: "ADC" }];

/** Thin a long series down to a plottable number of points without inventing
 * values — every point kept is a real stored reading. */
function thin(rows: Reading[], max = MAX_POINTS) {
  if (rows.length <= max) return rows;
  const stride = Math.ceil(rows.length / max);
  return rows.filter((_, i) => i % stride === 0);
}

function AnalyticsView() {
  const { zones } = useZoneFeed(15_000);
  const { factoryId } = useFactory();
  const [range, setRange] = useState<RangeKey>("1h");
  const [zoneId, setZoneId] = useState<number | null>(null);
  const [history, setHistory] = useState<Reading[]>([]);
  const [alertStats, setAlertStats] = useState<AlertsSummary | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);

  // Default to the zone with the freshest data so the first paint isn't empty.
  const freshest = useMemo(
    () =>
      [...zones]
        .filter((z) => z.latest)
        .sort((a, b) => (b.latest?.timestamp ?? "").localeCompare(a.latest?.timestamp ?? ""))[0],
    [zones],
  );
  const focusId = zoneId ?? freshest?.zone_id ?? zones[0]?.zone_id ?? null;

  useEffect(() => {
    setSummary(null);
    api.summary(factoryId).then(setSummary).catch(() => undefined);
  }, [factoryId]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .alertsSummary(RANGE_HOURS[range], factoryId)
        .then((s) => alive && setAlertStats(s))
        .catch(() => undefined);
    setAlertStats(null);
    load();
    return () => {
      alive = false;
    };
  }, [range, factoryId]);

  useEffect(() => {
    if (focusId === null) return;
    let alive = true;
    setHistory([]);
    api
      .zoneHistory(focusId, RANGE_HOURS[range])
      .then((rows) => alive && setHistory(thin(rows)))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [focusId, range]);

  const focusZone = zones.find((z) => z.zone_id === focusId) ?? null;

  const hourBuckets = useMemo(
    () =>
      (alertStats?.by_hour ?? []).map(([bucket, count]) => ({
        hour: bucket.slice(11, 16),
        count,
      })),
    [alertStats],
  );

  const hazardRows = useMemo(
    () =>
      Object.entries(alertStats?.by_hazard ?? {})
        .sort((a, b) => b[1] - a[1])
        .slice(0, 6),
    [alertStats],
  );

  return (
    <>
      <PageHero
        eyebrow="Historical analytics"
        headline={
          <>
            Trends, not
            <br />
            <span className="font-black text-volt">snapshots</span>.
          </>
        }
        meta={
          <>
            <HeroMeta label="Window" value={range} />
            <HeroMeta label="Alerts in window" value={alertStats?.total ?? "—"} />
            <HeroMeta label="Critical" value={alertStats?.by_severity.critical ?? "—"} color={RISK_HEX.CRITICAL} />
            <HeroMeta label="Avg risk (all zones)" value={summary?.average_risk_score ?? "—"} />
            <HeroMeta label="Stored readings" value={history.length} />
          </>
        }
      />

      <section className="flex flex-wrap items-end gap-8 border-b border-line py-8">
        <div>
          <div className="micro mb-3 text-faint">Time range</div>
          <Toggle
            value={range}
            onChange={setRange}
            options={[
              { value: "1h", label: "Last hour" },
              { value: "24h", label: "Last 24 h" },
              { value: "7d", label: "Last 7 days" },
            ]}
          />
        </div>
        <div>
          <div className="micro mb-3 text-faint">Zone</div>
          <Toggle
            value={String(focusId ?? "")}
            onChange={(v) => setZoneId(Number(v))}
            options={zones.map((z) => ({ value: String(z.zone_id), label: z.name }))}
          />
        </div>
      </section>

      <section className="grid grid-cols-2 gap-6 py-10 md:grid-cols-4">
        <Metric label="Alerts total" value={alertStats?.total ?? "—"} sub={`${range} window`} />
        <Metric label="Critical" value={alertStats?.by_severity.critical ?? "—"} color={RISK_HEX.CRITICAL} />
        <Metric label="High" value={alertStats?.by_severity.high ?? "—"} color={RISK_HEX.HIGH} />
        <Metric label="Warning" value={alertStats?.by_severity.warning ?? "—"} color={RISK_HEX.WARNING} />
      </section>

      <Panel
        index="01"
        title="Zone trends"
        meta={focusZone ? `${focusZone.name} · ${range}` : undefined}
      >
        {history.length === 0 ? (
          <Empty>no stored readings for this window</Empty>
        ) : (
          <div className="grid gap-10 xl:grid-cols-3">
            <div>
              <div className="micro mb-4 text-faint">Temperature</div>
              <TimeSeries data={history} series={THERMAL} height={180} />
            </div>
            <div>
              <div className="micro mb-4 text-faint">Combustible gas</div>
              <TimeSeries data={history} series={GAS} height={180} />
            </div>
            <div>
              <div className="micro mb-4 text-faint">Smoke</div>
              <TimeSeries data={history} series={SMOKE} height={180} />
            </div>
          </div>
        )}
      </Panel>

      <Panel index="02" title="Alert frequency" meta={`per hour · ${range}`} className="mt-14">
        {hourBuckets.length === 0 ? (
          <Empty>no alerts in this window</Empty>
        ) : (
          <BarSeries data={hourBuckets} xKey="hour" yKey="count" color={RISK_HEX.HIGH} />
        )}
      </Panel>

      <Panel index="03" title="Incidents by hazard class" meta="all time" className="mt-14">
        {hazardRows.length === 0 ? (
          <Empty>no hazard records yet</Empty>
        ) : (
          <div className="space-y-px">
            {hazardRows.map(([hazard, count]) => {
              const max = hazardRows[0][1] || 1;
              const isDevice = hazard.startsWith("device") || hazard.startsWith("sensor");
              return (
                <div key={hazard} className="flex items-center gap-4 border-b border-[var(--color-line-soft)] py-3">
                  <span className="w-48 font-mono text-[12px] text-paper">
                    {hazard.replace(/_/g, " ")}
                  </span>
                  <span className="h-px flex-1 bg-[var(--color-line)]" />
                  <span
                    className="h-2"
                    style={{
                      width: `${Math.max(4, (count / max) * 220)}px`,
                      backgroundColor: isDevice ? RISK_HEX.WARNING : RISK_HEX.CRITICAL,
                    }}
                  />
                  <span className="w-10 text-right font-mono text-[12px] tabular-nums text-muted">
                    {count}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Panel>
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <AnalyticsView />
    </AppShell>
  );
}
