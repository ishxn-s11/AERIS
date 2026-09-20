"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/shell";
import { TimeSeries, type SeriesDef } from "@/components/charts";
import {
  ForecastChart, ForecastDrivers, ForecastReadout, ForecastStrip,
} from "@/components/forecast";
import {
  Empty, HeroMeta, Metric, PageHero, Panel, RiskChip, SeverityChip, timeAgo,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useLiveFeed } from "@/lib/live";
import { RISK_BANDS, RISK_HEX, SERIES } from "@/lib/risk";
import {
  useForecastSeries, useReadingSeries, useRiskSeries, useZoneFeed,
} from "@/lib/use-zones";
import type { Alert, Device, Prediction, Zone } from "@/lib/types";

const TEMP: SeriesDef[] = [{ key: "temperature", label: "Temperature", color: SERIES.temperature, unit: "°C" }];
const GAS: SeriesDef[] = [{ key: "methane", label: "Combustible gas", color: SERIES.methane, unit: "ppm" }];
const SMOKE: SeriesDef[] = [{ key: "smoke", label: "Smoke", color: SERIES.smoke, unit: "ADC" }];
const CO: SeriesDef[] = [{ key: "co", label: "Carbon monoxide", color: SERIES.co, unit: "ppm" }];
const RISK: SeriesDef[] = [{ key: "risk_score", label: "Risk score", color: SERIES.risk, unit: "0-100" }];

function ZoneDetail({ zoneId }: { zoneId: number }) {
  const { getZone } = useLiveFeed();
  const { zones } = useZoneFeed();
  const [zone, setZone] = useState<Zone | null>(null);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [zoneAlerts, setZoneAlerts] = useState<Alert[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);

  const readings = useReadingSeries(zoneId);
  const riskPoints = useRiskSeries(zoneId, 80);
  const forecastSeries = useForecastSeries(zoneId);

  useEffect(() => {
    let alive = true;
    api.zone(zoneId).then((z) => alive && setZone(z)).catch(() => undefined);
    api.zonePredictions(zoneId, 25).then((p) => alive && setPredictions(p)).catch(() => undefined);
    api.alerts(`?zone_id=${zoneId}&limit=8`).then((a) => alive && setZoneAlerts(a)).catch(() => undefined);
    api
      .devices()
      .then((d) => alive && setDevices(d.filter((x) => x.zone_id === zoneId)))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [zoneId]);

  useEffect(() => {
    const id = setInterval(() => {
      api.zonePredictions(zoneId, 25).then(setPredictions).catch(() => undefined);
    }, 12_000);
    return () => clearInterval(id);
  }, [zoneId]);

  const live = getZone(zoneId);
  const snapshot = zones.find((z) => z.zone_id === zoneId);
  const level = live.riskLevel ?? snapshot?.risk_level ?? "SAFE";
  const score = live.riskScore || snapshot?.risk_score || 0;
  const hazard = live.hazard ?? snapshot?.predicted_hazard ?? null;
  const latest = live.readings.at(-1) ?? null;
  const values = latest ?? snapshot?.latest ?? null;

  const last = predictions[0] ?? null;
  const confidence = last?.model_confidence ?? null;
  const explanation = live.explanation.length > 0 ? live.explanation : (last?.explanation ?? []);
  // Newest live projection from the WebSocket; the stored series is the
  // fallback so the panel is populated on first paint after a reload.
  const projection = live.forecast ?? forecastSeries.at(-1) ?? null;

  return (
    <>
      <PageHero
        eyebrow={`Zone detail · ${zone?.description ?? "industrial zone"}`}
        headline={
          <>
            {zone?.name ?? "Zone"}
            <br />
            <span className="font-black" style={{ color: RISK_HEX[level] }}>
              {level}
            </span>
          </>
        }
        meta={
          <>
            <HeroMeta label="Risk score" value={`${Math.round(score)} / 100`} color={RISK_HEX[level]} />
            <HeroMeta label="Predicted hazard" value={hazard ? hazard.replace("_", " ") : "none"} />
            <HeroMeta label="Model confidence" value={confidence !== null ? confidence.toFixed(2) : "—"} />
            <HeroMeta
              label="Projected (+10 min)"
              value={projection ? `${projection.predicted_risk_score.toFixed(0)} · ${projection.predicted_risk_level}` : "—"}
              color={projection ? RISK_HEX[projection.predicted_risk_level] : undefined}
            />
            <HeroMeta label="Nodes in zone" value={devices.length} />
            <HeroMeta label="Last frame" value={values ? timeAgo(values.timestamp) : "—"} />
            <HeroMeta label="Open alerts" value={zoneAlerts.filter((a) => !a.acknowledged).length} />
          </>
        }
      />

      <section className="grid grid-cols-2 gap-x-6 gap-y-8 py-10 md:grid-cols-3 xl:grid-cols-6">
        <Metric label="Temperature" value={values ? values.temperature.toFixed(1) : "—"} unit="°C" />
        <Metric label="Humidity" value={values ? Math.round(values.humidity) : "—"} unit="%" />
        <Metric label="Combustible gas" value={values ? Math.round(values.methane) : "—"} unit="ppm" color={RISK_HEX[level]} />
        <Metric label="Carbon monoxide" value={values ? Math.round(values.co) : "—"} unit="ppm" />
        <Metric label="Smoke" value={values ? Math.round(values.smoke) : "—"} unit="ADC" />
        <Metric
          label="Flame"
          value={values ? (values.flame ? "DETECTED" : "clear") : "—"}
          color={values?.flame ? RISK_HEX.CRITICAL : undefined}
        />
      </section>

      <Panel index="01" title="Prediction rationale" meta="rule factors + model output">
        <div className="grid gap-10 lg:grid-cols-[1fr_1.1fr]">
          <div>
            <div className="flex flex-wrap items-baseline gap-4">
              <RiskChip level={level} score={score} />
              {hazard && (
                <span className="micro text-muted">predicted · {hazard.replace("_", " ")}</span>
              )}
            </div>
            <p className="mt-6 text-sm leading-relaxed text-muted">
              The three-level engine evaluates hard safety rules first, then trend
              rates, then the machine-learning classifier. Machine learning may only
              escalate a situation the deterministic rules already consider elevated —
              it can never raise an alarm from a calm baseline.
            </p>
            {confidence !== null && (
              <div className="mt-6 flex items-center gap-4 border-t border-line pt-4">
                <span className="micro text-faint">Model confidence</span>
                <span className="font-mono text-sm tabular-nums text-paper">
                  {confidence.toFixed(2)}
                </span>
                <span className="h-2 flex-1 bg-[var(--color-line)]">
                  <span
                    className="block h-full"
                    style={{ width: `${Math.round(confidence * 100)}%`, backgroundColor: RISK_HEX[level] }}
                  />
                </span>
              </div>
            )}
          </div>

          <div>
            <div className="micro mb-3 text-faint">Primary contributing factors</div>
            {explanation.length === 0 ? (
              <Empty>no elevated factors in the current window</Empty>
            ) : (
              <ul>
                {explanation.map((factor) => (
                  <li
                    key={factor}
                    className="flex gap-3 border-b border-[var(--color-line-soft)] py-3 text-sm text-paper"
                  >
                    <span className="text-volt">—</span>
                    {factor}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Panel>

      <Panel index="02" title="Telemetry" meta="live buffer stitched to stored history" className="mt-14">
        {readings.length === 0 ? (
          <Empty>no readings for this zone yet</Empty>
        ) : (
          <div className="grid gap-10 xl:grid-cols-2">
            <div>
              <div className="micro mb-4 text-faint">Temperature</div>
              <TimeSeries data={readings} series={TEMP} height={180} />
            </div>
            <div>
              <div className="micro mb-4 text-faint">Combustible gas</div>
              <TimeSeries data={readings} series={GAS} height={180} />
            </div>
            <div>
              <div className="micro mb-4 text-faint">Smoke</div>
              <TimeSeries data={readings} series={SMOKE} height={180} />
            </div>
            <div>
              <div className="micro mb-4 text-faint">Carbon monoxide</div>
              <TimeSeries data={readings} series={CO} height={180} />
            </div>
          </div>
        )}
      </Panel>

      <Panel index="03" title="Risk score history" className="mt-14">
        {riskPoints.length === 0 ? (
          <Empty>no stored predictions</Empty>
        ) : (
          <TimeSeries data={riskPoints} series={RISK} height={210} yDomain={[0, 100]} />
        )}
      </Panel>

      <Panel
        index="04"
        title="Risk projection"
        meta={projection ? `horizon +${projection.horizon_minutes} min · advisory only` : "awaiting projection"}
        className="mt-14"
      >
        <div className="grid gap-10 xl:grid-cols-[1.4fr_1fr]">
          <div>
            <div className="micro mb-4 text-faint">Stored projections, band cut-points marked</div>
            <ForecastChart data={forecastSeries} bands={RISK_BANDS} />
          </div>
          <div className="space-y-8">
            <div>
              <div className="micro mb-4 text-faint">Now vs. horizon</div>
              <ForecastStrip
                forecast={projection}
                currentScore={score}
                currentLevel={level}
                horizon={projection?.horizon_minutes}
              />
            </div>
            <div>
              <div className="micro mb-3 text-faint">Readout</div>
              <ForecastReadout forecast={projection} />
            </div>
            {projection && projection.drivers.length > 0 && (
              <div>
                <div className="micro mb-3 text-faint">Why the projection moved</div>
                <ForecastDrivers drivers={projection.drivers} />
              </div>
            )}
          </div>
        </div>
      </Panel>

      <div className="mt-14 grid gap-14 xl:grid-cols-2">
        <Panel index="05" title="Recent predictions" meta="newest first">
          {predictions.length === 0 ? (
            <Empty>no predictions stored</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    {["Time", "Score", "Band", "Hazard", "Conf."].map((h) => (
                      <th key={h} className="micro py-3 text-left font-normal text-faint">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {predictions.slice(0, 12).map((p) => (
                    <tr key={p.id} className="border-b border-[var(--color-line-soft)]">
                      <td className="micro py-3 text-faint">{timeAgo(p.timestamp)}</td>
                      <td className="font-mono text-[12px] tabular-nums" style={{ color: RISK_HEX[p.risk_level] }}>
                        {p.risk_score.toFixed(1)}
                      </td>
                      <td className="micro" style={{ color: RISK_HEX[p.risk_level] }}>
                        {p.risk_level}
                      </td>
                      <td className="micro text-muted">
                        {p.predicted_hazard ? p.predicted_hazard.replace("_", " ") : "—"}
                      </td>
                      <td className="font-mono text-[12px] tabular-nums text-muted">
                        {p.model_confidence !== null ? p.model_confidence.toFixed(2) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel index="06" title="Zone alerts">
          {zoneAlerts.length === 0 ? (
            <Empty>no alerts recorded for this zone</Empty>
          ) : (
            <div className="space-y-px">
              {zoneAlerts.map((a) => (
                <div key={a.id} className="border-b border-[var(--color-line-soft)] py-3">
                  <div className="flex flex-wrap items-baseline gap-4">
                    <SeverityChip severity={a.severity} />
                    <span className="text-sm text-paper">{a.message}</span>
                    <span className="micro ml-auto text-faint">{timeAgo(a.timestamp)}</span>
                  </div>
                  <div className="micro mt-2 flex gap-4 text-faint">
                    <span>{a.hazard_type.replace("_", " ")}</span>
                    <span>{a.acknowledged ? `acknowledged · ${a.acknowledged_by}` : "unacknowledged"}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel index="07" title="Device health" className="mt-14">
        {devices.length === 0 ? (
          <Empty>no devices bound to this zone</Empty>
        ) : (
          <div className="grid gap-px md:grid-cols-3">
            {devices.map((d) => (
              <div key={d.id} className="border border-line p-4">
                <div className="flex items-baseline justify-between">
                  <span className="font-mono text-[12px] text-paper">{d.device_id}</span>
                  <span className="flex items-center gap-2">
                    <span
                      className={`h-1.5 w-1.5 ${
                        d.status === "online" ? "bg-jade" : d.status === "degraded" ? "bg-brass" : "bg-scarlet"
                      }`}
                    />
                    <span className="micro text-muted">{d.status}</span>
                  </span>
                </div>
                <div className="micro mt-3 text-faint">
                  firmware {d.firmware_version} · seen {timeAgo(d.last_seen)}
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <div className="mt-14 border-t border-line pt-6">
        <Link href="/map" className="micro text-volt hover:text-paper">
          ← back to floor map
        </Link>
      </div>
    </>
  );
}

export default function Page() {
  const params = useParams<{ id: string }>();
  const zoneId = Number(params?.id);

  return (
    <AppShell>
      {Number.isFinite(zoneId) ? (
        <ZoneDetail zoneId={zoneId} />
      ) : (
        <Empty>unknown zone</Empty>
      )}
    </AppShell>
  );
}
