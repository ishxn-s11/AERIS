"use client";
import type { ReactNode } from "react";
import type { RiskLevel } from "@/lib/types";
import { RISK_STYLES } from "@/lib/risk";

/* ------------------------------------------------------------------ time --*/

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString([], { hour12: false });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.toLocaleDateString([], { month: "short", day: "2-digit" })} ${formatTime(iso)}`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return "never";
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/* --------------------------------------------------------------- panels ---*/

/** Numbered editorial section: mono index, uppercase title, hairline rule. */
export function Panel({
  index,
  title,
  meta,
  action,
  children,
  className = "",
  bodyClassName = "",
}: {
  index?: string;
  title: string;
  meta?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={`border-t border-line pt-4 ${className}`}>
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        {index && <span className="micro text-volt">{index}</span>}
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-paper">{title}</h2>
        {meta && <span className="micro text-faint">{meta}</span>}
        {action && <div className="ml-auto">{action}</div>}
      </header>
      <div className={`mt-5 ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/** Big-number metric block — the reference's "statement number" treatment. */
export function Metric({
  label,
  value,
  unit,
  color,
  sub,
  className = "",
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  color?: string;
  sub?: ReactNode;
  className?: string;
}) {
  return (
    <div className={`border-t border-line pt-3 ${className}`}>
      <div className="micro text-faint">{label}</div>
      <div className="mt-3 flex items-baseline gap-1.5">
        <span
          className="font-mono text-[2.35rem] leading-none tracking-tight tabular-nums"
          style={color ? { color } : undefined}
        >
          {value}
        </span>
        {unit && <span className="micro text-muted">{unit}</span>}
      </div>
      {sub && <div className="micro mt-2.5 text-muted">{sub}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- chips ---*/

export function RiskChip({ level, score }: { level: RiskLevel; score?: number }) {
  const s = RISK_STYLES[level];
  return (
    <span className={`inline-flex items-center gap-1.5 border ${s.border} ${s.bg} px-2 py-1`}>
      <span className={`h-1.5 w-1.5 ${s.dot}`} />
      <span className={`micro ${s.text}`}>{s.label}</span>
      {score !== undefined && (
        <span className={`font-mono text-[10px] tabular-nums ${s.text}`}>{Math.round(score)}</span>
      )}
    </span>
  );
}

export function SeverityChip({ severity }: { severity: string }) {
  const map: Record<string, string> = {
    warning: "text-brass border-brass/40",
    high: "text-ember border-ember/40",
    critical: "text-scarlet border-scarlet/50",
  };
  return (
    <span className={`micro border px-2 py-1 ${map[severity] ?? "border-line text-muted"}`}>
      {severity}
    </span>
  );
}

export function StatusDot({ tone, label }: { tone: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span className={`h-1.5 w-1.5 ${tone}`} />
      {label && <span className="micro text-muted">{label}</span>}
    </span>
  );
}

/* ---------------------------------------------------------------- inputs --*/

export function Toggle<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-px">
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={`micro border px-3 py-2 transition-colors ${
            value === o.value
              ? "border-paper bg-paper text-ink"
              : "border-line text-muted hover:border-muted hover:text-paper"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- misc -----*/

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="border border-dashed border-line px-4 py-10 text-center">
      <p className="micro text-faint">{children}</p>
    </div>
  );
}

/** Hairline key/value row used for sensor readouts and metadata. */
export function ValueRow({
  label,
  value,
  unit,
  color,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  color?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line-soft py-2.5 last:border-b-0">
      <span className="micro text-muted">{label}</span>
      <span className="flex items-baseline gap-1">
        <span
          className="font-mono text-sm tabular-nums"
          style={color ? { color } : undefined}
        >
          {value}
        </span>
        {unit && <span className="micro text-faint">{unit}</span>}
      </span>
    </div>
  );
}

/** Page hero: mono eyebrow, weight-contrast display headline, meta strip. */
export function PageHero({
  eyebrow,
  headline,
  meta,
}: {
  eyebrow: string;
  headline: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="border-b border-line pb-8">
      <div className="micro text-muted">{eyebrow}</div>
      <h1 className="display mt-6 text-[clamp(2rem,5.4vw,4.2rem)] font-medium">{headline}</h1>
      {meta && <div className="mt-7 flex flex-wrap gap-x-8 gap-y-3">{meta}</div>}
    </header>
  );
}

export function HeroMeta({ label, value, color }: { label: string; value: ReactNode; color?: string }) {
  return (
    <div>
      <div className="micro text-faint">{label}</div>
      <div className="mt-1.5 font-mono text-sm tabular-nums" style={color ? { color } : undefined}>
        {value}
      </div>
    </div>
  );
}
