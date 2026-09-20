"use client";
/**
 * Factory process flow — a P&ID-style drawing rather than a heat map.
 *
 * Each zone is a process unit placed on its seeded 0–100 plant grid anchor
 * (the seed stores bay CENTRES). Units are wired together by process lines
 * routed orthogonally, with animated flow dashes showing material direction,
 * a valve symbol on the feed main, and equipment glyphs chosen from the unit
 * name. Risk colour rides on the unit outline and its score.
 *
 * The layout itself is SIMULATED demo data — it is a plausible plant, not a
 * surveyed one.
 */
import type { ZoneLive } from "@/lib/types";
import { RISK_HEX } from "@/lib/risk";

const HALF_W = 9;
const HALF_H = 6.5;

/* ── Process wiring ─────────────────────────────────────────────────────
 * Preferred connections by unit name, with hand-routed orthogonal paths for
 * the seeded topology (bends kept clear of the unit boxes). Anything that
 * does not match falls back to an auto-routed chain so other plants still
 * produce a connected diagram. */

const SEEDED_LINES: {
  from: RegExp;
  to: RegExp;
  d: string;
  label: string;
  lx: number;
  ly: number;
}[] = [
  { from: /pipeline/i, to: /chemical|tank/i, d: "M30,45.5 V30 H50 V22.5", label: "LINE 01 · FEED TRANSFER", lx: 31.6, ly: 33.6 },
  { from: /chemical|tank/i, to: /boiler/i, d: "M41,16 H14 V13.5", label: "LINE 02 · FUEL GAS", lx: 25, ly: 14.7 },
  { from: /boiler/i, to: /production|process/i, d: "M14,26.5 V70 H64 V64.5", label: "LINE 03 · STEAM MAIN", lx: 15.6, ly: 45 },
  { from: /generator/i, to: /production|process/i, d: "M84,28.5 V40 H64 V51.5", label: "LINE 04 · PROCESS AIR", lx: 69, ly: 38.7 },
  { from: /production|process/i, to: /warehouse|loading/i, d: "M73,58 H88 V71.5", label: "LINE 05 · PRODUCT", lx: 76.5, ly: 56.7 },
];

/**
 * L-shaped fallback route between two unit anchors, plus where to set its
 * line tag. The tag sits on the long leg of the run rather than at the
 * midpoint of the straight line, so it never lands inside a unit box.
 */
function autoRoute(
  ax: number, ay: number, bx: number, by: number,
): { d: string; lx: number; ly: number; anchor: "start" | "middle" } {
  if (Math.abs(by - ay) >= Math.abs(bx - ax)) {
    const up = ay < by ? 1 : -1;
    const side = ax < bx ? -HALF_W : HALF_W;
    const legMid = (ay + up * HALF_H + by) / 2;
    return {
      d: `M${ax},${ay + up * HALF_H} V${by} H${bx + side}`,
      lx: ax + 1.1, ly: legMid + 0.4, anchor: "start",
    };
  }
  const right = ax < bx ? 1 : -1;
  const side = ay < by ? -HALF_H : HALF_H;
  const legMid = (ax + right * HALF_W + bx) / 2;
  return {
    d: `M${ax + right * HALF_W},${ay} H${bx} V${by + side}`,
    lx: legMid, ly: ay - 1.1, anchor: "middle",
  };
}

/**
 * Service tag for an auto-routed run, taken from the upstream unit's
 * equipment class — so an unseeded plant still reads as a process drawing
 * ("LINE 06 · PRODUCT LOADOUT") instead of anonymous plumbing.
 */
function serviceTag(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("boiler") || n.includes("furnace")) return "STEAM";
  if (n.includes("generator") || n.includes("compressor") || n.includes("pump")) return "UTILITY";
  if (n.includes("pipeline") || n.includes("manifold")) return "FEED";
  if (n.includes("production") || n.includes("process") || n.includes("reactor")) return "PROCESS";
  if (n.includes("warehouse") || n.includes("store")) return "STORAGE";
  if (n.includes("loading") || n.includes("bay")) return "LOADOUT";
  if (n.includes("control") || n.includes("office")) return "CONTROL";
  if (n.includes("chemical") || n.includes("tank")) return "PRODUCT";
  return "SERVICE";
}

/* ── Equipment glyphs (drawn in a 5 × 4.6 box, stroke-only) ─────────── */

const GLYPHS: Record<string, string> = {
  boiler: "M0.2,1.2 H4.4 V4.3 H0.2 Z M0.9,1.2 V0.2 H1.7 V1.2 M0.2,2.5 H4.4 M1.1,4.3 V4.7 M3.5,4.3 V4.7",
  vessel: "M1.1,0.5 Q2.3,-0.3 3.5,0.5 V4.2 H1.1 Z M1.1,2.6 H3.5 M2.3,4.2 V4.7",
  generator: "M0.1,1 H4.5 V4.3 H0.1 Z M1.7,2.65 H2.9 M2.3,2.05 V3.25 M2.3,2.65 m-0.6,0 a0.6,0.6 0 1,0 1.2,0 a0.6,0.6 0 1,0 -1.2,0",
  pipe: "M0,1.7 H4.5 M0,3.2 H4.5 M0.6,1.2 V3.7 M3.9,1.2 V3.7",
  gear: "M2.25,2.35 m-1.35,0 a1.35,1.35 0 1,0 2.7,0 a1.35,1.35 0 1,0 -2.7,0 M2.25,2.35 m-0.45,0 a0.45,0.45 0 1,0 0.9,0 a0.45,0.45 0 1,0 -0.9,0 M2.25,0.6 V1 M2.25,3.7 V4.1 M0.5,2.35 H0.9 M3.6,2.35 H4",
  warehouse: "M0,2 L2.25,0.3 L4.5,2 M0.4,1.9 V4.4 H4.1 V1.9 M1.5,4.4 V3 H3 V4.4",
  dock: "M0.6,0.8 H4.4 V4.4 H0.6 Z M4.4,1.9 H5 M4.4,3.1 H5 M1.5,4.4 V3.2 H3.4 V4.4",
  control: "M0.4,0.4 H4.1 V2.9 H0.4 Z M2.25,2.9 V4.2 M1.2,4.2 H3.3",
  box: "M0.3,0.3 H4.2 V4.2 H0.3 Z",
};

function glyphFor(name: string): string {
  const n = name.toLowerCase();
  if (n.includes("boiler") || n.includes("furnace")) return "boiler";
  if (n.includes("chemical") || n.includes("tank")) return "vessel";
  if (n.includes("generator") || n.includes("compressor") || n.includes("pump")) return "generator";
  if (n.includes("pipeline") || n.includes("pipe") || n.includes("manifold")) return "pipe";
  if (n.includes("production") || n.includes("process") || n.includes("reactor")) return "gear";
  if (n.includes("warehouse") || n.includes("store")) return "warehouse";
  if (n.includes("loading") || n.includes("bay")) return "dock";
  if (n.includes("control") || n.includes("office")) return "control";
  return "box";
}

/* ── Component ──────────────────────────────────────────────────────── */

export function FactoryMap({
  zones,
  onSelect,
}: {
  zones: ZoneLive[];
  onSelect?: (zoneId: number) => void;
}) {
  const lines = buildLines(zones);

  return (
    <svg
      viewBox="0 0 100 94"
      className="w-full select-none"
      role="img"
      aria-label="Factory process flow diagram"
    >
      <defs>
        <pattern id="pid-grid" width="4" height="4" patternUnits="userSpaceOnUse">
          <path d="M 4 0 L 0 0 0 4" fill="none" stroke="rgba(233,231,226,0.055)" strokeWidth="0.12" />
        </pattern>
        <marker
          id="flow-arrow"
          viewBox="0 0 6 6"
          refX="5.2"
          refY="3"
          markerWidth="3.4"
          markerHeight="3.4"
          orient="auto-start-reverse"
        >
          <path d="M0,0.4 L6,3 L0,5.6 Z" fill="#d8f14e" />
        </marker>
      </defs>

      {/* plant boundary + drafting grid */}
      <rect x="1.5" y="1.5" width="97" height="91" fill="url(#pid-grid)" />
      <rect x="1.5" y="1.5" width="97" height="91" fill="none" stroke="rgba(233,231,226,0.16)" strokeWidth="0.2" />

      {/* corner registration ticks */}
      {[
        [1.5, 1.5, 1, 1],
        [98.5, 1.5, -1, 1],
        [1.5, 92.5, 1, -1],
        [98.5, 92.5, -1, -1],
      ].map(([x, y, dx, dy], i) => (
        <g key={i} stroke="#d8f14e" strokeWidth="0.25">
          <line x1={x} y1={y} x2={x + dx * 4} y2={y} />
          <line x1={x} y1={y} x2={x} y2={y + dy * 4} />
        </g>
      ))}

      {/* district labels */}
      <text x="4" y="10" className="font-mono" fontSize="1.3" fill="#5a5d59" letterSpacing="0.34">
        UNIT 100 · SERVICES &amp; FEED
      </text>
      <text x="4" y="46.5" className="font-mono" fontSize="1.3" fill="#5a5d59" letterSpacing="0.34">
        UNIT 200 · PROCESS
      </text>
      <text x="4" y="88" className="font-mono" fontSize="1.3" fill="#5a5d59" letterSpacing="0.34">
        UNIT 300 · STORAGE &amp; DISPATCH
      </text>

      {/* ── process lines (drawn under the units) ───────────────────── */}
      <g fill="none" strokeLinejoin="round">
        {lines.map((line, i) => (
          <g key={i}>
            <path d={line.d} stroke="#33383c" strokeWidth="1.15" />
            <path
              d={line.d}
              stroke="#d8f14e"
              strokeWidth="0.42"
              strokeDasharray="1.7 3.1"
              markerEnd="url(#flow-arrow)"
              opacity="0.9"
            >
              <animate
                attributeName="stroke-dashoffset"
                from="4.8"
                to="0"
                dur="1.15s"
                repeatCount="indefinite"
              />
            </path>
            {line.label && (
              <text
                x={line.lx}
                y={line.ly}
                textAnchor={line.anchor === "middle" ? "middle" : undefined}
                className="font-mono"
                fontSize="1.15"
                fill="#5a5d59"
                letterSpacing="0.18"
              >
                {line.label}
              </text>
            )}
          </g>
        ))}

        {/* feed main + isolation valve */}
        <path d="M1.5,52 H21" stroke="#33383c" strokeWidth="1.3" />
        <path d="M1.5,52 H21" stroke="#d8f14e" strokeWidth="0.5" strokeDasharray="1.8 3.2" opacity="0.9">
          <animate attributeName="stroke-dashoffset" from="5" to="0" dur="1.3s" repeatCount="indefinite" />
        </path>
        <path d="M8.5,50.6 L11.5,53.4 M11.5,50.6 L8.5,53.4" stroke="#e9e7e2" strokeWidth="0.34" />
        <text x="2.4" y="50.3" className="font-mono" fontSize="1.15" fill="#5a5d59" letterSpacing="0.18">
          FEED MAIN · EXT. SUPPLY
        </text>
      </g>

      {/* ── process units ───────────────────────────────────────────── */}
      {zones.map((z, index) => {
        const hex = RISK_HEX[z.risk_level];
        const critical = z.risk_level === "CRITICAL";
        const x = z.x - HALF_W;
        const y = z.y - HALF_H;
        const glyph = GLYPHS[glyphFor(z.name)] ?? GLYPHS.box;
        const latest = z.latest;

        return (
          <g
            key={z.zone_id}
            onClick={() => onSelect?.(z.zone_id)}
            className={onSelect ? "cursor-pointer" : undefined}
            role={onSelect ? "button" : undefined}
          >
            {/* unit body */}
            <rect x={x} y={y} width={HALF_W * 2} height={HALF_H * 2} fill="#0c0e10" />
            <rect
              x={x}
              y={y}
              width={HALF_W * 2}
              height={HALF_H * 2}
              fill={hex}
              fillOpacity="0.09"
              stroke={hex}
              strokeWidth={critical ? 0.55 : 0.34}
            >
              {critical && (
                <animate attributeName="stroke-opacity" values="1;0.3;1" dur="1.6s" repeatCount="indefinite" />
              )}
            </rect>

            {/* unit tag */}
            <text x={x + HALF_W * 2 - 1.1} y={y + 2.5} className="font-mono" fontSize="1.2" fill="#5a5d59" textAnchor="end" letterSpacing="0.14">
              Z{String(index + 1).padStart(2, "0")}
            </text>

            {/* unit name */}
            <text x={x + 1.1} y={y + 2.5} className="font-mono" fontSize="1.35" fill="#e9e7e2" letterSpacing="0.05">
              {z.name.toUpperCase()}
            </text>

            {/* risk score */}
            <text x={x + 1.1} y={y + 9.3} className="font-mono" fontSize="4.9" fill={hex}>
              {Math.round(z.risk_score)}
            </text>
            <text x={x + 1.1} y={y + 11.6} className="font-mono" fontSize="1.25" fill={hex} letterSpacing="0.2">
              {z.risk_level}
            </text>

            {/* equipment glyph */}
            <g transform={`translate(${x + 12}, ${y + 4.6})`} stroke="#8a8c87" strokeWidth="0.3" fill="none">
              <path d={glyph} />
            </g>

            {/* live readings */}
            <text x={x + 12} y={y + 11} className="font-mono" fontSize="1.05" fill="#8a8c87" letterSpacing="0.04">
              {latest ? `CH4 ${Math.round(latest.methane)}` : "NO SIGNAL"}
            </text>
            <text x={x + 12} y={y + 12.6} className="font-mono" fontSize="1.05" fill="#5a5d59" letterSpacing="0.04">
              {latest ? `${latest.temperature.toFixed(1)}°C · CO ${Math.round(latest.co)}` : "—"}
            </text>

            {/* predicted hazard */}
            <text
              x={x + HALF_W}
              y={y + HALF_H * 2 + 2.4}
              textAnchor="middle"
              className="font-mono"
              fontSize="1.15"
              fill={z.predicted_hazard ? hex : "#5a5d59"}
              letterSpacing="0.16"
            >
              {z.predicted_hazard
                ? `▲ PRED · ${z.predicted_hazard.replace("_", " ").toUpperCase()}`
                : "NO ANOMALY"}
            </text>
          </g>
        );
      })}

      {/* title block */}
      <g>
        <rect x="58" y="89.2" width="40.5" height="4.3" fill="#0c0e10" stroke="rgba(233,231,226,0.14)" strokeWidth="0.15" />
        <text x="59.2" y="91" className="font-mono" fontSize="1.2" fill="#8a8c87" letterSpacing="0.16">
          AERIS · PROCESS FLOW
        </text>
        <text x="59.2" y="92.8" className="font-mono" fontSize="1.05" fill="#5a5d59" letterSpacing="0.14">
          SIMULATED LAYOUT · SHEET 1 OF 1 · REV C
        </text>
      </g>
    </svg>
  );
}

/* ── Wiring builder ──────────────────────────────────────────────────── */

type Line = { d: string; label: string; lx: number; ly: number; anchor?: "start" | "middle" };

function buildLines(zones: ZoneLive[]): Line[] {
  if (zones.length < 2) return [];

  const lines: Line[] = [];
  const connected = new Set<number>();

  // Seeded, hand-routed lines first.
  for (const spec of SEEDED_LINES) {
    const a = zones.find((z) => spec.from.test(z.name));
    const b = zones.find((z) => spec.to.test(z.name));
    if (!a || !b || a.zone_id === b.zone_id) continue;
    if (connected.has(a.zone_id) && connected.has(b.zone_id)) continue;
    lines.push({ d: spec.d, label: spec.label, lx: spec.lx, ly: spec.ly });
    connected.add(a.zone_id);
    connected.add(b.zone_id);
  }

  // Line numbering continues past the seeded runs (01–05) so every line on
  // the sheet carries a unique tag.
  let nextLineNo = SEEDED_LINES.length + 1;
  const route = (a: ZoneLive, b: ZoneLive): Line => {
    const r = autoRoute(a.x, a.y, b.x, b.y);
    const label = `LINE ${String(nextLineNo++).padStart(2, "0")} · ${serviceTag(a.name)}`;
    return { d: r.d, label, lx: r.lx, ly: r.ly, anchor: r.anchor };
  };

  // Chain any units the seeded map did not wire, so every plant connects.
  const leftovers = zones.filter((z) => !connected.has(z.zone_id));
  if (leftovers.length > 1) {
    // Join the loose units into their own run first.
    for (let i = 0; i < leftovers.length - 1; i++) {
      lines.push(route(leftovers[i], leftovers[i + 1]));
    }
    // Then tie the run back into the wired network when one exists.
    const anchor = zones.find((z) => connected.has(z.zone_id));
    const last = leftovers[leftovers.length - 1];
    if (anchor) {
      lines.push(route(anchor, last));
    }
  } else if (lines.length === 0 && leftovers.length === 0) {
    // No seeded line matched at all — fall back to a simple series run.
    for (let i = 0; i < zones.length - 1; i++) {
      lines.push(route(zones[i], zones[i + 1]));
    }
    return lines;
  }

  if (lines.length === 0) {
    for (let i = 0; i < zones.length - 1; i++) {
      lines.push(route(zones[i], zones[i + 1]));
    }
  }

  return lines;
}
