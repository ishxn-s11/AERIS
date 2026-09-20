"use client";
/** Projection board: where every zone is heading in the next N minutes.
 *
 * The page answers "is the situation getting worse?" before it answers "how bad
 * is it now" — the horizon is the headline, not a footnote.
 */
import { useMemo, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/shell";
import {
  ForecastChart, ForecastDrivers, ForecastReadout, ForecastStrip, methodLabel,
} from "@/components/forecast";
import { Empty, HeroMeta, Metric, Panel, PageHero, RiskChip } from "@/components/ui";
import { useFactory } from "@/lib/factory";
import { useLiveFeed } from "@/lib/live";
import { RISK_BANDS, RISK_HEX, RISK_ORDER } from "@/lib/risk";
import { useFactoryForecasts, useForecastSeries, useZoneFeed } from "@/lib/use-zones";
import type { RiskForecast, ZoneLive } from "@/lib/types";

type Projection = Omit<RiskForecast, "id" | "zone_id" | "timestamp"> & {
  id?: number;
  zone_id?: number;
  timestamp?: string;
  delta?: number;
};

function Board() {
  const { factory, factories, factoryId } = useFactory();
  const { zones } = useZoneFeed();
  const stored = useFactoryForecasts();
  const { getZone } = useLiveFeed();
  const [selected, setSelected] = useState<number | null>(null);

  const storedByZone = useMemo(() => {
    const map = new Map<number, RiskForecast>();
    for (const f of stored) map.set(f.zone_id, f);
    return map;
  }, [stored]);

  // Live projections arrive on every risk frame; the stored rows are the
  // fallback for a zone that has not published since the page opened.
  const rows = useMemo(
    () =>
      zones
        .map((z: ZoneLive) => {
          const live = getZone(z.zone_id).forecast;
          const projection: Projection | null = live ?? storedByZone.get(z.zone_id) ?? null;
          return {
            zone: z,
            projection,
            predicted: projection?.predicted_risk_score ?? z.risk_score,
          };
        })
        .sort(
          (a, b) =>
            RISK_ORDER[a.zone.risk_level] - RISK_ORDER[b.zone.risk_level] ||
            b.predicted - a.predicted,
        ),
    [zones, storedByZone, getZone],
  );

  const escalating = rows.filter((r) => r.predicted - r.zone.risk_score > 1);
  const breaches = rows.filter((r) => r.projection?.expected_breach_minutes != null);
  const worst = rows[0] ?? null;
  const horizon = rows.find((r) => r.projection)?.projection?.horizon_minutes ?? 10;
  const focusId = selected ?? worst?.zone.zone_id ?? null;
  const focus = rows.find((r) => r.zone.zone_id === focusId) ?? null;
  const series = useForecastSeries(focusId);

  return (
    <>
      <PageHero
        eyebrow={`Forecast · +${horizon} min horizon · ${factory ? factory.name : factoryId === null ? "all plants" : "—"}`}
        headline={
          <>
            Not where the plant is.
            <br />
            Where it is <span className="font-black text-volt">heading</span>.
          </>
        }
        meta={
          <>
            <HeroMeta
              label="Scope"
              value={factory ? factory.name : factoryId === null ? `${factories.length || 1} plant(s)` : "—"}
            />
            <HeroMeta label="Zones tracked" value={rows.length} />
            <HeroMeta
              label="Escalating"
              value={escalating.length}
              color={escalating.length > 0 ? RISK_HEX.HIGH : RISK_HEX.SAFE}
            />
            <HeroMeta
              label="Breach expected"
              value={breaches.length}
              color={breaches.length > 0 ? RISK_HEX.CRITICAL : RISK_HEX.SAFE}
            />
          </>
        }
      />

      <section className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 md:grid-cols-4">
        <Metric
          label="Soonest breach"
          value={
            breaches.length > 0
              ? Math.min(...breaches.map((b) => b.projection!.expected_breach_minutes!)).toFixed(0)
              : "—"
          }
          unit="min"
          color={breaches.length > 0 ? RISK_HEX.CRITICAL : undefined}
          sub={breaches.length > 0 ? breaches[0].zone.name : "no band crossing inside the horizon"}
        />
        <Metric
          label="Highest projection"
          value={worst ? Math.round(worst.predicted) : "—"}
          unit="/100"
          color={worst ? RISK_HEX[worst.zone.risk_level] : undefined}
          sub={worst ? worst.zone.name : "awaiting projections"}
        />
        <Metric
          label="Projections stored"
          value={stored.length}
          unit={`zone${stored.length === 1 ? "" : "s"}`}
        />
        <Metric
          label="Source"
          value={rows.some((r) => r.projection && !("id" in r.projection)) ? "live" : "polled"}
          sub="live = pushed over WebSocket with every risk frame"
        />
      </section>

      <Panel
        index="01"
        title="Projection board"
        meta={`predicted band from the +${horizon} min projection, sorted by current severity`}
        action={
          <div className="flex flex-wrap gap-2">
            {(["SAFE", "WARNING", "HIGH", "CRITICAL"] as const).map((lv) => (
              <RiskChip key={lv} level={lv} />
            ))}
          </div>
        }
        className="mt-4"
      >
        {rows.length === 0 ? (
          <Empty>awaiting zone telemetry</Empty>
        ) : (
          <div>
            <div className="micro grid grid-cols-[1.6fr_repeat(3,minmax(0,1fr))] gap-4 border-b border-line pb-3 text-faint">
              <span>Zone</span>
              <span>Now</span>
              <span>Projected</span>
              <span>Driver</span>
            </div>
            {rows.map(({ zone, projection, predicted }) => {
              const delta = predicted - zone.risk_score;
              const tone = projection ? RISK_HEX[projection.predicted_risk_level] : "var(--color-muted)";
              return (
                <button
                  key={zone.zone_id}
                  onClick={() => setSelected(zone.zone_id)}
                  className={`grid w-full grid-cols-[1.6fr_repeat(3,minmax(0,1fr))] items-baseline gap-4 border-b border-line-soft py-3 text-left transition-colors last:border-b-0 hover:bg-ink-2 ${
                    zone.zone_id === focusId ? "bg-ink-2" : ""
                  }`}
                >
                  <span className="flex items-baseline gap-3 truncate">
                    <span className="text-[13px] text-paper">{zone.name}</span>
                    {zone.zone_id === focusId && <span className="micro text-volt">focus</span>}
                  </span>
                  <span className="font-mono text-[11px] tabular-nums text-muted">
                    {zone.risk_score.toFixed(0)} · {zone.risk_level}
                  </span>
                  <span className="flex items-baseline gap-2">
                    <span className="font-mono text-[13px] tabular-nums" style={{ color: tone }}>
                      {projection ? predicted.toFixed(0) : "—"}
                    </span>
                    {projection && Math.abs(delta) > 1 && (
                      <span
                        className="micro"
                        style={{ color: delta > 0 ? RISK_HEX.HIGH : RISK_HEX.SAFE }}
                      >
                        {delta > 0 ? `+${delta.toFixed(0)}` : delta.toFixed(0)}
                      </span>
                    )}
                  </span>
                  <span className="micro truncate text-faint">
                    {projection?.expected_breach_minutes
                      ? `breach in ${projection.expected_breach_minutes} min`
                      : projection
                        ? methodLabel(projection.method)
                        : "no projection"}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </Panel>

      {focus && (
        <Panel
          index="02"
          title={`Zone projection · ${focus.zone.name}`}
          action={
            <div className="flex flex-wrap items-center gap-3">
              <RiskChip level={focus.zone.risk_level} score={focus.zone.risk_score} />
              <Link href={`/zone/${focus.zone.zone_id}`} className="micro text-volt hover:text-paper">
                zone detail →
              </Link>
            </div>
          }
          className="mt-14"
        >
          <div className="grid gap-10 xl:grid-cols-[1.4fr_1fr]">
            <div>
              <div className="micro mb-4 text-faint">Projected risk score, stored projections</div>
              <ForecastChart data={series} bands={RISK_BANDS} />
            </div>
            <div className="space-y-8">
              <div>
                <div className="micro mb-4 text-faint">Horizon comparison</div>
                <ForecastStrip
                  forecast={focus.projection}
                  currentScore={focus.zone.risk_score}
                  currentLevel={focus.zone.risk_level}
                  horizon={focus.projection?.horizon_minutes}
                />
              </div>
              <div>
                <div className="micro mb-3 text-faint">Readout</div>
                <ForecastReadout forecast={focus.projection} />
              </div>
              {focus.projection && focus.projection.drivers.length > 0 && (
                <div>
                  <div className="micro mb-3 text-faint">Why the projection moved</div>
                  <ForecastDrivers drivers={focus.projection.drivers} />
                </div>
              )}
            </div>
          </div>
        </Panel>
      )}
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <Board />
    </AppShell>
  );
}
