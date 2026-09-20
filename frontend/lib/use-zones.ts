"use client";
/** Shared hook: poll zone risk snapshots and overlay the newest WebSocket
 * values so the map and boards stay live between REST polls. */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useFactory } from "@/lib/factory";
import { useLiveFeed } from "@/lib/live";
import type { Reading, RiskForecast, ZoneLive } from "@/lib/types";

export function useZoneFeed(pollMs = 8000) {
  const { getZone } = useLiveFeed();
  // Defaults to the plant selected in the masthead; callers may override.
  const { factoryId: scopedFactory } = useFactory();
  const [polled, setPolled] = useState<ZoneLive[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api
      .zonesLive(scopedFactory)
      .then((z) => {
        setPolled(z);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [scopedFactory]);

  useEffect(() => {
    setPolled([]); // never render the previous plant's zones against a new scope
    load();
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, pollMs]);

  // Not memoised on purpose: the feed provider re-renders this hook's owner on
  // every pushed frame, which is exactly when the overlay must be recomputed.
  const zones: ZoneLive[] = polled.map((z) => {
    const live = getZone(z.zone_id);
    const newer =
      live.lastUpdate !== null &&
      (!z.latest || new Date(live.lastUpdate) >= new Date(z.latest.timestamp));
    if (!newer) return z;
    const latestReading = live.readings.at(-1);
    return {
      ...z,
      risk_level: live.riskLevel ?? z.risk_level,
      risk_score: live.riskScore || z.risk_score,
      predicted_hazard: live.hazard ?? z.predicted_hazard,
      latest: latestReading
        ? {
            device_id: latestReading.device_id,
            timestamp: latestReading.timestamp,
            temperature: latestReading.temperature,
            humidity: latestReading.humidity,
            co: latestReading.co,
            methane: latestReading.methane,
            smoke: latestReading.smoke,
            flame: latestReading.flame,
          }
        : z.latest,
    };
  });

  return { zones, loading, reload: load };
}

/** Time series for one zone: the REST backfill stitched to the live buffer,
 * de-duplicated by timestamp and trimmed to the most recent points. */
export function useReadingSeries(zoneId: number | null, limit = 160) {
  const { getZone } = useLiveFeed();
  const [seed, setSeed] = useState<Reading[]>([]);

  useEffect(() => {
    if (zoneId === null) return;
    let alive = true;
    setSeed([]);
    api
      .zoneTelemetry(zoneId)
      .then((rows) => alive && setSeed(rows))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [zoneId]);

  const live = zoneId === null ? [] : getZone(zoneId).readings;

  // Deliberately computed during render, not memoised: the live buffer is a
  // ref read, so its identity is the only signal that a new frame arrived. The
  // feed provider re-renders this component on every push, which is exactly
  // when the merge must run. Cost is a sort of ≤200 points at 2 Hz.
  const byTimestamp = new Map<string, Reading>();
  for (const r of [...seed, ...live]) byTimestamp.set(r.timestamp, r);
  return [...byTimestamp.values()]
    .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
    .slice(-limit);
}

/** Stored projections for one zone (oldest first) — drives the forecast chart. */
export function useForecastSeries(zoneId: number | null, limit = 60, pollMs = 30_000) {
  const [rows, setRows] = useState<RiskForecast[]>([]);

  useEffect(() => {
    if (zoneId === null) {
      setRows([]);
      return;
    }
    let alive = true;
    const load = () =>
      api
        .zoneForecast(zoneId, limit)
        .then((r) => alive && setRows(r))
        .catch(() => undefined);
    load();
    const id = setInterval(load, pollMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [zoneId, limit, pollMs]);

  return rows;
}

/** Latest projection per zone for the selected plant (fleet view). */
export function useFactoryForecasts(pollMs = 15_000) {
  const { factoryId } = useFactory();
  const [rows, setRows] = useState<RiskForecast[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .forecasts(factoryId)
        .then((r) => alive && setRows(r))
        .catch(() => undefined);
    setRows([]);
    load();
    const id = setInterval(load, pollMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [factoryId, pollMs]);

  return rows;
}

/** Risk-score trend for one zone, built from stored predictions. */
export function useRiskSeries(zoneId: number | null, limit = 60, pollMs = 10_000) {
  const [points, setPoints] = useState<{ timestamp: string; risk_score: number }[]>([]);

  useEffect(() => {
    if (zoneId === null) return;
    let alive = true;
    const load = () =>
      api
        .zonePredictions(zoneId, limit)
        .then((rows) => {
          if (!alive) return;
          setPoints(
            [...rows]
              .sort((a, b) => a.timestamp.localeCompare(b.timestamp))
              .map((p) => ({ timestamp: p.timestamp, risk_score: p.risk_score })),
          );
        })
        .catch(() => undefined);
    load();
    const id = setInterval(load, pollMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [zoneId, limit, pollMs]);

  return points;
}
