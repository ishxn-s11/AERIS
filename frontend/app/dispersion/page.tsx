"use client";
/** GIS dispersion page — satellite basemap with the Gaussian plume overlay. */
import { AppShell } from "@/components/shell";
import { DispersionMap } from "@/components/dispersion-map";
import { Empty, HeroMeta, PageHero } from "@/components/ui";
import { useZoneFeed } from "@/lib/use-zones";

function DispersionView() {
  const { zones, reload } = useZoneFeed();

  return (
    <>
      <PageHero
        eyebrow="Atmospheric model · satellite view"
        headline={
          <>
            Where the gas
            <br />
            will <span className="font-black text-volt">actually go</span>.
          </>
        }
        meta={
          <>
            <HeroMeta label="Gas sources" value={zones.length} />
            <HeroMeta label="Model" value="Gaussian plume" />
            <HeroMeta label="Stability" value="Pasquill A–F" />
            <HeroMeta label="Export" value="KML · Google Earth" />
          </>
        }
      />

      <section className="mt-10">
        {zones.length === 0 ? (
          <Empty>awaiting zone topology</Empty>
        ) : (
          <DispersionMap zones={zones} onSiteChanged={reload} />
        )}
      </section>

      <section className="mt-8 grid gap-px md:grid-cols-3">
        <Note index="01" title="Anchored to the real plant">
          The plume is drawn inside the selected site&apos;s own fence line,
          from its recorded coordinates and grid span — never at an assumed
          location. Set the site fix before trusting a single contour.
        </Note>
        <Note index="02" title="Weather drives reach">
          Wind speed sets dilution and the plume bearing sets direction.
          Stability class controls how fast the plume spreads — class F on a
          still night carries far further than class A at noon.
        </Note>
        <Note index="03" title="Read the rings">
          Contours are concentration isopleths (g/m³). The darkest band is
          IDLH / explosive range; the outermost ring sits on the gas&apos;s own
          first alarm level, so it is the evacuation boundary.
        </Note>
      </section>
    </>
  );
}

function Note({
  index, title, children,
}: {
  index: string; title: string; children: React.ReactNode;
}) {
  return (
    <div className="border-t border-line pt-4">
      <div className="micro text-volt">{index}</div>
      <h3 className="mt-2 text-sm font-semibold uppercase tracking-[0.14em] text-paper">
        {title}
      </h3>
      <p className="mt-2 font-mono text-[11px] leading-relaxed text-muted">{children}</p>
    </div>
  );
}

export default function Page() {
  return (
    <AppShell>
      <DispersionView />
    </AppShell>
  );
}
