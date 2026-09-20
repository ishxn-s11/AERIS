"use client";
/** Editorial application shell: sticky masthead, live ticker, alert strip,
 * footer. All dashboard pages render inside this frame. */
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api, getStoredUser, logout } from "@/lib/api";
import { FactoryProvider, useFactory } from "@/lib/factory";
import { FactorySwitcher } from "@/components/factory-switcher";
import { LiveFeedProvider, useLiveFeed } from "@/lib/live";
import { useLocalClock } from "@/lib/use-clock";
import { RISK_HEX } from "@/lib/risk";
import type { User, ZoneLive } from "@/lib/types";

const NAV = [
  { href: "/dashboard", label: "Overview" },
  { href: "/map", label: "Floor Map" },
  { href: "/dispersion", label: "GIS Map" },
  { href: "/forecast", label: "Forecast" },
  { href: "/alerts", label: "Alerts" },
  { href: "/devices", label: "Nodes" },
  { href: "/analytics", label: "Analytics" },
  { href: "/connections", label: "Connections" },
  { href: "/security", label: "Security" },
];

/* ------------------------------------------------------------------ ticker */

function Ticker() {
  const [zones, setZones] = useState<ZoneLive[]>([]);
  const { factoryId } = useFactory();

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.zonesLive(factoryId).then((z) => alive && setZones(z)).catch(() => undefined);
    setZones([]);
    load();
    const id = setInterval(load, 15_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [factoryId]);

  if (zones.length === 0) {
    return (
      <div className="border-b border-line bg-ink-2">
        <div className="micro mx-auto max-w-[1500px] px-6 py-2 text-faint">
          Awaiting first telemetry frame…
        </div>
      </div>
    );
  }

  const items = zones.map((z) => ({
    key: z.zone_id,
    name: z.name,
    level: z.risk_level,
    score: z.risk_score,
    temp: z.latest?.temperature,
    gas: z.latest?.methane,
  }));

  const strip = (
    <div className="flex shrink-0 items-center">
      {items.map((z) => (
        <span key={z.key} className="flex shrink-0 items-center gap-2 px-6">
          <span className="h-1.5 w-1.5" style={{ backgroundColor: RISK_HEX[z.level] }} />
          <span className="micro text-paper">{z.name}</span>
          <span className="font-mono text-[11px] tabular-nums text-muted">
            {z.temp !== undefined ? `${z.temp.toFixed(1)}°C` : "—"}
          </span>
          <span className="font-mono text-[11px] tabular-nums text-muted">
            {z.gas !== undefined ? `${Math.round(z.gas)} ppm` : "—"}
          </span>
          <span className="font-mono text-[11px] tabular-nums" style={{ color: RISK_HEX[z.level] }}>
            {Math.round(z.score)}
          </span>
        </span>
      ))}
    </div>
  );

  return (
    <div className="marquee border-b border-line bg-ink-2 py-2">
      <div className="marquee-track">
        {strip}
        {strip}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ banner */

const BANNER: Record<string, string> = {
  warning: "border-brass/50",
  high: "border-ember/50",
  critical: "border-scarlet/60",
};

const BANNER_TEXT: Record<string, string> = {
  warning: "text-brass",
  high: "text-ember",
  critical: "text-scarlet",
};

function AlertStrip() {
  const { lastAlert } = useLiveFeed();
  if (!lastAlert || lastAlert.type !== "alert") return null;
  return (
    <Link
      href="/alerts"
      className={`block border-b bg-ink-2 ${BANNER[lastAlert.severity]}`}
    >
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-baseline gap-x-4 gap-y-1 px-6 py-3">
        <span className={`micro ${BANNER_TEXT[lastAlert.severity]}`}>
          {lastAlert.severity} alert
        </span>
        <span className="text-sm text-paper">{lastAlert.message}</span>
        <span className="micro ml-auto text-faint">open alerts →</span>
      </div>
    </Link>
  );
}

/* ------------------------------------------------------------------- frame */

function Frame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { connected } = useLiveFeed();
  const clock = useLocalClock();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const stored = getStoredUser();
    if (!stored) {
      router.replace("/login");
      return;
    }
    setUser(stored);
  }, [router]);

  if (!user) return <div className="min-h-screen bg-ink" />;

  return (
    <div className="min-h-screen bg-ink bg-blueprint">
      <header className="sticky top-0 z-30 border-b border-line bg-ink/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center gap-6 px-6 py-3.5">
          <Link href="/dashboard" className="flex items-baseline gap-px">
            <span className="text-xl font-black tracking-[-0.04em] text-paper">AERIS</span>
            <span className="text-volt">®</span>
          </Link>

          <nav className="hidden items-center gap-6 lg:flex">
            {NAV.map((item) => {
              const active =
                pathname === item.href ||
                (item.href !== "/" && pathname.startsWith(item.href));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`micro link-rule transition-colors ${
                    active ? "text-paper" : "text-muted hover:text-paper"
                  }`}
                >
                  {item.label}
                  {active && <span className="ml-1.5 text-volt">/</span>}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-6">
            <FactorySwitcher />
            <div className="hidden text-right sm:block">
              <div className="micro text-faint">Local time</div>
              <div className="font-mono text-[13px] tabular-nums text-paper">{clock}</div>
            </div>
            <div className="text-right">
              <div className="micro text-faint">Feed</div>
              <div className="flex items-center gap-1.5">
                <span
                  className={`h-1.5 w-1.5 ${connected ? "blink bg-volt" : "bg-scarlet"}`}
                />
                <span className={`micro ${connected ? "text-jade" : "text-scarlet"}`}>
                  {connected ? "Live" : "Retry"}
                </span>
              </div>
            </div>
            <button
              onClick={logout}
              className="micro border border-line px-3 py-2 text-muted transition-colors hover:border-paper hover:text-paper"
            >
              Sign out
            </button>
          </div>
        </div>

        <nav className="flex gap-5 overflow-x-auto border-t border-line px-6 py-2.5 lg:hidden">
          {NAV.map((item) => {
            const active =
              pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`micro shrink-0 ${active ? "text-paper" : "text-muted"}`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <Ticker />
      <AlertStrip />

      <main className="mx-auto max-w-[1500px] px-6 py-10">{children}</main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-3 px-6 py-7">
          <p className="micro max-w-2xl text-faint">
            AERIS prototype — decision-support only. Not certified fire &amp; gas detection
            equipment.
          </p>
          <p className="micro text-faint">
            {user.name} · {user.role.replace("_", " ")}
          </p>
        </div>
      </footer>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <FactoryProvider>
      <LiveFeedProvider>
        <Frame>{children}</Frame>
      </LiveFeedProvider>
    </FactoryProvider>
  );
}
