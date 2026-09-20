/**
 * Dashboard page — the main monitoring overview.
 * This was previously at `/`, now at `/dashboard` to allow the landing page.
 */
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getToken } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import type { DashboardSummary, ZoneLive } from "@/lib/types";
import { riskHexOf } from "@/lib/risk";

export default function DashboardPage() {
  const router = useRouter();
  const { factoryId } = useFactory();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [zones, setZones] = useState<ZoneLive[]>([]);

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    const load = () => {
      api.summary(factoryId).then(setSummary).catch(() => {});
      api.zonesLive(factoryId).then(setZones).catch(() => {});
    };
    load();
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, [factoryId, router]);

  if (!summary) {
    return (
      <div className="flex items-center justify-center h-[calc(100dvh-72px)]">
        <div className="font-mono text-micro text-muted animate-pulse">LOADING...</div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-6 py-12">
      <div className="mb-8">
        <div className="font-mono text-micro text-muted tracking-[0.3em] mb-2">01</div>
        <h1 className="display text-4xl font-bold">
          <span className="text-paper">SYSTEM </span>
          <span className="text-volt">OVERVIEW</span>
        </h1>
      </div>

      {/* KPI Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-12">
        <div className="border border-line p-4">
          <div className="font-mono text-[10px] text-muted tracking-wider">ACTIVE SENSORS</div>
          <div className="text-3xl font-bold text-paper mt-1">{summary.active_sensors}</div>
        </div>
        <div className="border border-line p-4">
          <div className="font-mono text-[10px] text-muted tracking-wider">TOTAL ZONES</div>
          <div className="text-3xl font-bold text-paper mt-1">{summary.total_zones}</div>
        </div>
        <div className="border border-line p-4">
          <div className="font-mono text-[10px] text-muted tracking-wider">ACTIVE ALERTS</div>
          <div className="text-3xl font-bold text-scarlet mt-1">{summary.active_alerts}</div>
        </div>
        <div className="border border-line p-4">
          <div className="font-mono text-[10px] text-muted tracking-wider">AVG RISK</div>
          <div className="text-3xl font-bold text-paper mt-1">{summary.average_risk_score}</div>
        </div>
      </div>

      {/* Zone Cards */}
      <div className="mb-8">
        <div className="font-mono text-micro text-muted tracking-[0.3em] mb-4">ZONE STATUS</div>
      </div>
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {zones.map((z) => (
          <button
            key={z.zone_id}
            onClick={() => router.push(`/zone/${z.zone_id}`)}
            className="text-left border border-line hover:border-accent/30 p-4 transition-colors"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-type text-sm uppercase tracking-wider">{z.name}</span>
              <span
                className="font-mono text-xs px-2 py-0.5"
                style={{
                  backgroundColor: riskHexOf(z.risk_level) + "20",
                  color: riskHexOf(z.risk_level),
                  border: `1px solid ${riskHexOf(z.risk_level)}40`,
                }}
              >
                {z.risk_level}
              </span>
            </div>
            <div className="font-mono text-2xl font-bold" style={{ color: riskHexOf(z.risk_level) }}>
              {z.risk_score.toFixed(1)}
            </div>
            {z.latest && (
              <div className="mt-2 font-mono text-[10px] text-muted">
                {z.latest.temperature.toFixed(1)}°C · CH₄ {z.latest.methane.toFixed(0)} · CO {z.latest.co.toFixed(0)}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
