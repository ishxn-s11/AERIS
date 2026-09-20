"use client";
/** Risk-projection surfaces (+N minute horizon).
 *
 * A forecast is advisory: it never creates an incident by itself, so the UI
 * always shows *how* the number was produced (`method`) and *why* it moved
 * (`drivers`). An operator must be able to tell a learned projection from
 * arithmetic extrapolation before acting on it.
 */
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, RiskChip, ValueRow, formatTime } from "@/components/ui";
import { RISK_HEX, riskHexOf } from "@/lib/risk";
import type { LiveForecast, RiskForecast } from "@/lib/types";

const METHOD_LABEL: Record<string, string> = {
  ml_regressor: "ML regressor",
  trend_extrapolation: "trend extrapolation",
};

export function methodLabel(method: string | null | undefined): string {
  if (!method) return "unknown";
  return METHOD_LABEL[method] ?? method.replace(/_/g, " ");
}

/** Current → projected comparison strip. The arrow renders the direction and
 * the band colour of the *destination*, because that is what the operator has
 * to prepare for. */
export function ForecastStrip({
  forecast,
  currentScore,
  currentLevel,
  horizon,
  compact = false,
}: {
  forecast: LiveForecast | null | undefined;
  currentScore: number;
  currentLevel: string;
  horizon?: number;
  compact?: boolean;
}) {
  if (!forecast) {
    return (
      <div className="micro text-faint">
        projection pending — awaits the first scored frame
      </div>
    );
  }

  const minutes = horizon ?? forecast.horizon_minutes;
  const delta = forecast.delta ?? forecast.predicted_risk_score - currentScore;
  const arrow = delta > 1 ? "↑" : delta < -1 ? "↓" : "→";
  const tone = delta > 1 ? RISK_HEX[forecast.predicted_risk_level] : "var(--color-muted)";

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
      <div className="flex items-baseline gap-2">
        <span className="micro text-faint">now</span>
        <span className="font-mono text-sm tabular-nums" style={{ color: riskHexOf(currentLevel) }}>
          {Math.round(currentScore)}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-base leading-none" style={{ color: tone }}>{arrow}</span>
        <span className="micro text-faint">+{minutes} min</span>
      </div>

      <div className="flex items-baseline gap-2">
        <span
          className="font-mono text-2xl leading-none tabular-nums"
          style={{ color: RISK_HEX[forecast.predicted_risk_level] }}
        >
          {Math.round(forecast.predicted_risk_score)}
        </span>
        <RiskChip level={forecast.predicted_risk_level} />
        <span className="micro" style={{ color: tone }}>
          {delta > 0 ? `+${delta.toFixed(1)}` : delta.toFixed(1)}
        </span>
      </div>

      {!compact && (
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
          <span className="micro text-faint">
            method · <span className="text-muted">{methodLabel(forecast.method)}</span>
          </span>
          {forecast.confidence !== null && forecast.confidence !== undefined && (
            <span className="micro text-faint">
              confidence · <span className="text-muted">{(forecast.confidence * 100).toFixed(0)}%</span>
            </span>
          )}
          <span className="micro text-faint">
            breach ·{" "}
            <span className="text-muted">
              {forecast.expected_breach_minutes !== null &&
              forecast.expected_breach_minutes !== undefined
                ? `in ${forecast.expected_breach_minutes} min`
                : "not within horizon"}
            </span>
          </span>
          {forecast.predicted_hazard && (
            <span className="micro text-faint">
              hazard · <span className="text-muted">{forecast.predicted_hazard.replace("_", " ")}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/** Projection history: predicted score over time, band cut-points drawn as
 * reference lines so an operator sees *which* line the zone is heading for. */
export function ForecastChart({
  data,
  height = 190,
  bands,
}: {
  data: RiskForecast[];
  height?: number;
  bands?: { safe_max: number; warning_max: number; high_max: number };
}) {
  if (data.length === 0) return <Empty>no stored projections for this zone</Empty>;

  const rows = data.map((f) => ({
    timestamp: f.timestamp,
    predicted_risk_score: f.predicted_risk_score,
    confidence: f.confidence === null ? undefined : f.confidence * 100,
  }));

  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={rows} margin={{ top: 6, right: 10, bottom: 0, left: -18 }}>
          <defs>
            <linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#d8f14e" stopOpacity={0.22} />
              <stop offset="100%" stopColor="#d8f14e" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(233,231,226,0.07)" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(v: string) => formatTime(v).slice(0, 5)}
            stroke="rgba(233,231,226,0.14)"
            tick={{ fill: "#5a5d59", fontSize: 9, fontFamily: "var(--font-geist-mono)" }}
            tickLine={false}
            minTickGap={38}
          />
          <YAxis
            domain={[0, 100]}
            stroke="rgba(233,231,226,0.14)"
            tick={{ fill: "#5a5d59", fontSize: 9, fontFamily: "var(--font-geist-mono)" }}
            tickLine={false}
            width={46}
          />
          {bands && (
            <>
              <ReferenceLine y={bands.safe_max} stroke={RISK_HEX.WARNING} strokeDasharray="2 4" strokeOpacity={0.5} />
              <ReferenceLine y={bands.warning_max} stroke={RISK_HEX.HIGH} strokeDasharray="2 4" strokeOpacity={0.5} />
              <ReferenceLine y={bands.high_max} stroke={RISK_HEX.CRITICAL} strokeDasharray="2 4" strokeOpacity={0.5} />
            </>
          )}
          <Tooltip
            contentStyle={{
              background: "#121517",
              border: "1px solid rgba(233,231,226,0.14)",
              borderRadius: 0,
              fontFamily: "var(--font-geist-mono)",
              fontSize: 11,
            }}
            labelStyle={{ color: "#5a5d59" }}
            labelFormatter={(v: string) => formatTime(v)}
          />
          <Area
            type="monotone"
            dataKey="predicted_risk_score"
            stroke="#d8f14e"
            strokeWidth={1.4}
            fill="url(#forecastFill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** "Why the projection moved" — the auditable half of the forecast. */
export function ForecastDrivers({ drivers }: { drivers: string[] }) {
  if (drivers.length === 0) return null;
  return (
    <ul className="space-y-1.5">
      {drivers.map((d) => (
        <li key={d} className="flex gap-2.5">
          <span className="micro text-volt">—</span>
          <span className="font-mono text-[11px] leading-relaxed text-muted">{d}</span>
        </li>
      ))}
    </ul>
  );
}

/** Row shape shared by the fleet-wide projection table. */
export function ForecastRow({
  name,
  level,
  score,
  forecast,
  onOpen,
}: {
  name: string;
  level: string;
  score: number;
  forecast: LiveForecast | RiskForecast | null;
  onOpen?: () => void;
}) {
  const predicted = forecast?.predicted_risk_score ?? null;
  const rising = predicted !== null && predicted - score > 1;
  return (
    <div
      className={`grid grid-cols-[1.6fr_repeat(3,minmax(0,1fr))] items-baseline gap-4 border-b border-line-soft py-3 last:border-b-0 ${
        onOpen ? "cursor-pointer hover:bg-ink-2" : ""
      }`}
      onClick={onOpen}
    >
      <span className="truncate text-[13px] text-paper">{name}</span>
      <span className="font-mono text-[11px] tabular-nums text-muted">
        {score.toFixed(0)} · <span style={{ color: riskHexOf(level) }}>{level}</span>
      </span>
      <span className="font-mono text-[11px] tabular-nums text-paper">
        {predicted === null ? "—" : `${predicted.toFixed(0)} ${rising ? "↑" : ""}`}
      </span>
      <span className="micro text-faint">
        {forecast?.expected_breach_minutes ? `breach ${forecast.expected_breach_minutes}m` : methodLabel(forecast?.method)}
      </span>
    </div>
  );
}

/** Compact readout used on the dashboard side rail. */
export function ForecastReadout({ forecast }: { forecast: LiveForecast | null | undefined }) {
  if (!forecast) return <Empty>no projection yet</Empty>;
  return (
    <div>
      <ValueRow
        label={`Projected score (+${forecast.horizon_minutes} min)`}
        value={forecast.predicted_risk_score.toFixed(1)}
        color={RISK_HEX[forecast.predicted_risk_level]}
      />
      <ValueRow label="Projected band" value={forecast.predicted_risk_level} color={RISK_HEX[forecast.predicted_risk_level]} />
      <ValueRow label="Method" value={methodLabel(forecast.method)} />
      <ValueRow
        label="Confidence"
        value={forecast.confidence === null || forecast.confidence === undefined
          ? "n/a"
          : `${(forecast.confidence * 100).toFixed(0)}%`}
      />
      <ValueRow
        label="Next band breach"
        value={forecast.expected_breach_minutes === null || forecast.expected_breach_minutes === undefined
          ? "not within horizon"
          : `${forecast.expected_breach_minutes} min`}
      />
    </div>
  );
}
