"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/shell";
import { FactoryMap } from "@/components/factory-map";
import { Empty, HeroMeta, PageHero, RiskChip, timeAgo } from "@/components/ui";
import { useZoneFeed } from "@/lib/use-zones";
import { RISK_HEX, RISK_ORDER } from "@/lib/risk";

const LEGEND = [
  { level: "SAFE", note: "0–30 · nominal" },
  { level: "WARNING", note: "31–60 · watch" },
  { level: "HIGH", note: "61–80 · intervene" },
  { level: "CRITICAL", note: "81–100 · evacuate" },
] as const;

function MapView() {
  const router = useRouter();
  const { zones } = useZoneFeed();

  // One process diagram per plant. When the masthead scope is "all plants" the
  // feed returns every zone, and merging them into a single drawing would wire
  // units from different factories together.
  const plants = [...zones]
    .reduce((acc, z) => {
      const group = acc.find((g) => g.id === z.factory_id);
      if (group) group.zones.push(z);
      else acc.push({ id: z.factory_id, name: z.factory_name ?? `Plant ${z.factory_id}`, zones: [z] });
      return acc;
    }, [] as { id: number; name: string; zones: typeof zones }[])
    .sort((a, b) => a.id - b.id);

  const critical = zones.filter((z) => z.risk_level === "CRITICAL").length;
  const highest = [...zones].sort(
    (a, b) => RISK_ORDER[a.risk_level] - RISK_ORDER[b.risk_level] || b.risk_score - a.risk_score,
  )[0];

  return (
    <>
      <PageHero
        eyebrow="Factory floor · process flow"
        headline={
          <>
            Every line.
            <br />
            One live <span className="font-black text-volt">risk picture</span>.
          </>
        }
        meta={
          <>
            <HeroMeta label="Process units" value={zones.length} />
            <HeroMeta
              label="Critical"
              value={critical}
              color={critical > 0 ? RISK_HEX.CRITICAL : RISK_HEX.SAFE}
            />
            <HeroMeta
              label="Highest risk"
              value={highest ? `${highest.name} · ${Math.round(highest.risk_score)}` : "—"}
            />
            <HeroMeta
              label="Frame age"
              value={highest?.latest ? timeAgo(highest.latest.timestamp) : "—"}
            />
          </>
        }
      />

      {zones.length === 0 ? (
        <div className="mt-10">
          <Empty>awaiting zone telemetry</Empty>
        </div>
      ) : (
        <>
          {plants.map((plant) => (
            <section
              key={plant.id}
              className="corner-ticks mx-auto mt-10 w-full max-w-[1180px] border border-line p-5"
            >
              <div className="mb-3 flex items-baseline justify-between border-b border-line-soft pb-3">
                <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-paper">
                  {plant.name}
                </h2>
                <span className="micro text-faint">
                  {plant.zones.length} process units · P&amp;ID sheet 1 of 1
                </span>
              </div>
              <FactoryMap zones={plant.zones} onSelect={(id) => router.push(`/zone/${id}`)} />
              <div className="micro mt-4 flex flex-wrap gap-x-6 gap-y-1 border-t border-line-soft pt-3 text-faint">
                <span className="text-volt">▸ animated dashes = material flow</span>
                <span>unit outline = live risk colour</span>
                <span>▲ = predicted hazard</span>
                <span>click a unit for telemetry</span>
              </div>
            </section>
          ))}

          <section className="mt-10 grid gap-px md:grid-cols-2 xl:grid-cols-4">
            {LEGEND.map((l) => (
              <div key={l.level} className="border-t border-line pt-3">
                <RiskChip level={l.level} />
                <div className="micro mt-2 text-faint">{l.note}</div>
              </div>
            ))}
          </section>

          <section className="mt-14">
            <div className="flex items-baseline gap-4 border-t border-line pt-4">
              <span className="micro text-volt">01</span>
              <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-paper">
                Zone index
              </h2>
              <span className="micro ml-auto text-faint">click any zone for telemetry</span>
            </div>

            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[820px] border-collapse">
                <thead>
                  <tr className="border-b border-line">
                    {["Zone", "Risk", "Gas ppm", "Temp °C", "CO", "Smoke", "Flame", "Predicted hazard", "Updated"].map(
                      (h) => (
                        <th key={h} className="micro py-3 text-left font-normal text-faint">
                          {h}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {zones.map((z, i) => (
                    <tr key={z.zone_id} className="border-b border-[var(--color-line-soft)]">
                      <td className="py-3">
                        <Link href={`/zone/${z.zone_id}`} className="flex items-baseline gap-3">
                          <span className="micro text-faint">Z{String(i + 1).padStart(2, "0")}</span>
                          <span className="text-sm text-paper hover:text-volt">{z.name}</span>
                        </Link>
                      </td>
                      <td className="py-3">
                        <RiskChip level={z.risk_level} score={z.risk_score} />
                      </td>
                      <td className="font-mono text-[12px] tabular-nums" style={{ color: RISK_HEX[z.risk_level] }}>
                        {z.latest ? Math.round(z.latest.methane) : "—"}
                      </td>
                      <td className="font-mono text-[12px] tabular-nums text-muted">
                        {z.latest ? z.latest.temperature.toFixed(1) : "—"}
                      </td>
                      <td className="font-mono text-[12px] tabular-nums text-muted">
                        {z.latest ? Math.round(z.latest.co) : "—"}
                      </td>
                      <td className="font-mono text-[12px] tabular-nums text-muted">
                        {z.latest ? Math.round(z.latest.smoke) : "—"}
                      </td>
                      <td className="font-mono text-[12px] tabular-nums">
                        {z.latest?.flame ? <span className="text-scarlet">DETECTED</span> : <span className="text-faint">clear</span>}
                      </td>
                      <td className="micro text-muted">
                        {z.predicted_hazard ? z.predicted_hazard.replace("_", " ") : "—"}
                      </td>
                      <td className="micro text-faint">{z.latest ? timeAgo(z.latest.timestamp) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}

export default function Page() {
  return (
    <AppShell>
      <MapView />
    </AppShell>
  );
}
