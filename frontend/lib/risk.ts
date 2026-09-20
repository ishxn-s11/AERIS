import type { RiskLevel } from "./types";

/** Custom risk palette — deliberately off-stock tones tuned for a near-black
 * editorial canvas: jade / brass / ember / scarlet. */
export const RISK_HEX: Record<RiskLevel, string> = {
  SAFE: "#8fe3b0",
  WARNING: "#e4c15b",
  HIGH: "#f0763c",
  CRITICAL: "#e5484d",
};

/** Class-name maps for Tailwind utilities (token-driven, see globals.css). */
export const RISK_STYLES: Record<
  RiskLevel,
  { text: string; bg: string; border: string; dot: string; label: string; accentVar: string }
> = {
  SAFE: { text: "text-jade", bg: "bg-jade/10", border: "border-jade/40", dot: "bg-jade", label: "SAFE", accentVar: "var(--color-jade)" },
  WARNING: { text: "text-brass", bg: "bg-brass/10", border: "border-brass/40", dot: "bg-brass", label: "WARNING", accentVar: "var(--color-brass)" },
  HIGH: { text: "text-ember", bg: "bg-ember/10", border: "border-ember/40", dot: "bg-ember", label: "HIGH", accentVar: "var(--color-ember)" },
  CRITICAL: { text: "text-scarlet", bg: "bg-scarlet/10", border: "border-scarlet/50", dot: "bg-scarlet", label: "CRITICAL", accentVar: "var(--color-scarlet)" },
};

export const SEVERITY_STYLES: Record<string, { border: string; text: string; rule: string }> = {
  warning: { border: "border-brass/40", text: "text-brass", rule: "bg-brass" },
  high: { border: "border-ember/40", text: "text-ember", rule: "bg-ember" },
  critical: { border: "border-scarlet/50", text: "text-scarlet", rule: "bg-scarlet" },
};

export const SEVERITY_HEX: Record<string, string> = {
  warning: RISK_HEX.WARNING,
  high: RISK_HEX.HIGH,
  critical: RISK_HEX.CRITICAL,
};

/** Data-series palette: mostly monochrome with three accents, so charts read
 * like the rest of the interface instead of a rainbow. */
export const SERIES = {
  temperature: "#e9e7e2",
  methane: "#d8f14e",
  smoke: "#a99bff",
  co: "#5cc9c0",
  humidity: "#8a8c87",
  risk: "#d8f14e",
} as const;

export function riskOf(level: RiskLevel | null) {
  return RISK_STYLES[level ?? "SAFE"];
}

/** Band string from anywhere (API, WebSocket, free text) -> palette colour.
 * Unknown bands fall back to the neutral muted tone rather than guessing. */
export function riskHexOf(level: string | null | undefined): string {
  if (level === "SAFE" || level === "WARNING" || level === "HIGH" || level === "CRITICAL") {
    return RISK_HEX[level];
  }
  return "var(--color-muted)";
}

/** Same bands the backend uses, drawn from settings.risk_bands so the UI can
 * annotate a chart with the real cut points instead of hard-coded 25/50/75. */
export const RISK_BANDS = { safe_max: 25, warning_max: 50, high_max: 75 } as const;

export const RISK_ORDER: Record<RiskLevel, number> = { CRITICAL: 0, HIGH: 1, WARNING: 2, SAFE: 3 };
