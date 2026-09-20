"use client";
/** Chart primitives. Styled to read as instrumentation, not as a stock chart
 * library: hairline grids, mono tick labels, no fills or gradient shading. */
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { formatTime } from "@/components/ui";
import { useLiveFeed } from "@/lib/live";

export interface SeriesDef {
  key: string;
  label: string;
  color: string;
  unit?: string;
}

function ChartTooltip({
  active,
  payload,
  label,
  series,
}: {
  active?: boolean;
  payload?: { dataKey?: string | number; value?: number | string }[];
  label?: string;
  series: SeriesDef[];
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="border border-line bg-ink-3 px-3 py-2">
      <div className="micro text-faint">{label ? formatTime(String(label)) : ""}</div>
      <div className="mt-2 space-y-1">
        {payload.map((p) => {
          const def = series.find((s) => s.key === p.dataKey);
          if (!def) return null;
          return (
            <div key={String(p.dataKey)} className="flex items-center gap-3">
              <span className="h-1.5 w-1.5" style={{ backgroundColor: def.color }} />
              <span className="micro text-muted">{def.label}</span>
              <span className="ml-auto font-mono text-[11px] tabular-nums text-paper">
                {typeof p.value === "number" ? p.value.toFixed(1) : p.value}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Legend({ series }: { series: SeriesDef[] }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2">
      {series.map((s) => (
        <span key={s.key} className="flex items-center gap-2">
          <span className="h-px w-4" style={{ backgroundColor: s.color }} />
          <span className="micro text-muted">
            {s.label}
            {s.unit ? ` (${s.unit})` : ""}
          </span>
        </span>
      ))}
    </div>
  );
}

export function TimeSeries<T extends object>({
  data,
  series,
  height = 210,
  yDomain,
  showLegend = true,
}: {
  data: T[];
  series: SeriesDef[];
  height?: number;
  yDomain?: [number | "auto", number | "auto"];
  showLegend?: boolean;
}) {
  return (
    <div>
      {showLegend && (
        <div className="mb-4">
          <Legend series={series} />
        </div>
      )}
      <div style={{ height }} className="w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data as Record<string, unknown>[]} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
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
              domain={yDomain}
              stroke="rgba(233,231,226,0.14)"
              tick={{ fill: "#5a5d59", fontSize: 9, fontFamily: "var(--font-geist-mono)" }}
              tickLine={false}
              width={52}
            />
            <Tooltip
              content={<ChartTooltip series={series} />}
              cursor={{ stroke: "rgba(233,231,226,0.25)", strokeWidth: 1 }}
            />
            {series.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={s.color}
                strokeWidth={1.4}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/** Sparse bar series (e.g. alerts per hour) in the same hairline language. */
export function BarSeries<T extends object>({
  data,
  xKey,
  yKey,
  color,
  height = 170,
}: {
  data: T[];
  xKey: string;
  yKey: string;
  color: string;
  height?: number;
}) {
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data as Record<string, unknown>[]} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid stroke="rgba(233,231,226,0.07)" vertical={false} />
          <XAxis
            dataKey={xKey}
            stroke="rgba(233,231,226,0.14)"
            tick={{ fill: "#5a5d59", fontSize: 9, fontFamily: "var(--font-geist-mono)" }}
            tickLine={false}
            minTickGap={30}
          />
          <YAxis
            allowDecimals={false}
            stroke="rgba(233,231,226,0.14)"
            tick={{ fill: "#5a5d59", fontSize: 9, fontFamily: "var(--font-geist-mono)" }}
            tickLine={false}
            width={52}
          />
          <Tooltip
            cursor={{ fill: "rgba(233,231,226,0.05)" }}
            contentStyle={{
              background: "#121517",
              border: "1px solid rgba(233,231,226,0.14)",
              borderRadius: 0,
              fontFamily: "var(--font-geist-mono)",
              fontSize: 11,
            }}
            labelStyle={{ color: "#5a5d59" }}
          />
          <Bar dataKey={yKey} fill={color} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Sparkline fed straight from the shared WebSocket buffer for one zone.
 * Renders nothing until frames arrive — no invented history. */
export function LiveSpark({
  zoneId,
  dataKey = "temperature",
  color,
  height = 30,
}: {
  zoneId: number;
  dataKey?: string;
  color: string;
  height?: number;
}) {
  const { getZone } = useLiveFeed();
  return (
    <MiniSpark data={getZone(zoneId).readings} dataKey={dataKey} color={color} height={height} />
  );
}

/** Inline sparkline for rows and table cells. */
export function MiniSpark<T extends object>({
  data,
  dataKey,
  color,
  height = 34,
}: {
  data: T[];
  dataKey: string;
  color: string;
  height?: number;
}) {
  if (data.length < 2) return <div style={{ height }} />;
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data as Record<string, unknown>[]}
          margin={{ top: 2, right: 0, bottom: 2, left: 0 }}
        >
          <YAxis hide domain={["auto", "auto"]} />
          <Line
            type="monotone"
            dataKey={dataKey}
            stroke={color}
            strokeWidth={1.2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
