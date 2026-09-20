"use client";
/** Live-feed context: one shared WebSocket for the whole dashboard.
 *
 * All pages subscribe here instead of opening their own sockets, and the feed
 * ring buffers (per zone) power the live charts without page refreshes.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";
import { WS_URL } from "@/lib/api";
import type { LiveForecast, LiveMessage, Reading, RiskLevel } from "@/lib/types";

const MAX_POINTS = 240; // ring buffer size per zone (~8 min at 2s cadence)

export interface ZoneLiveState {
  readings: Reading[];
  riskLevel: RiskLevel | null;
  riskScore: number;
  hazard: string | null;
  explanation: string[];
  /** Newest projection pushed with the risk frame (+N minute horizon). */
  forecast: LiveForecast | null;
  lastUpdate: string | null;
}

interface LiveFeedValue {
  connected: boolean;
  getZone: (zoneId: number) => ZoneLiveState;
  lastAlert: LiveMessage | null;
  alertLog: LiveMessage[];
}

const LiveFeedContext = createContext<LiveFeedValue>({
  connected: false,
  getZone: () => ({ readings: [], riskLevel: null, riskScore: 0, hazard: null, explanation: [], forecast: null, lastUpdate: null }),
  lastAlert: null,
  alertLog: [],
});

export function LiveFeedProvider({ children }: { children: React.ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [lastAlert, setLastAlert] = useState<LiveMessage | null>(null);
  const [alertLog, setAlertLog] = useState<LiveMessage[]>([]);
  // Refs avoid re-render churn at 2 Hz; a version counter triggers repaints.
  const zonesRef = useRef<Map<number, ZoneLiveState>>(new Map());
  const [, setVersion] = useState(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      ws = new WebSocket(WS_URL);
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retryTimer = setTimeout(connect, 3000); // auto-reconnect
      };
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data) as LiveMessage;
        if (msg.type === "telemetry") {
          const zone = zonesRef.current.get(msg.zone_id) ?? {
            readings: [], riskLevel: null, riskScore: 0, hazard: null, explanation: [], forecast: null, lastUpdate: null,
          };
          const reading: Reading = {
            id: Date.now(), device_id: msg.device_id, timestamp: msg.timestamp,
            temperature: msg.temperature, humidity: msg.humidity, co: msg.co,
            methane: msg.methane, smoke: msg.smoke, flame: msg.flame,
          };
          zone.readings = [...zone.readings.slice(-(MAX_POINTS - 1)), reading];
          zone.lastUpdate = msg.timestamp;
          zonesRef.current.set(msg.zone_id, zone);
          setVersion((v) => v + 1);
        } else if (msg.type === "risk_update") {
          const zone = zonesRef.current.get(msg.zone_id) ?? {
            readings: [], riskLevel: null, riskScore: 0, hazard: null, explanation: [], forecast: null, lastUpdate: null,
          };
          zone.riskLevel = msg.risk_level;
          zone.riskScore = msg.risk_score;
          zone.hazard = msg.predicted_hazard;
          zone.explanation = msg.explanation;
          zone.forecast = msg.forecast ?? zone.forecast;
          zone.lastUpdate = msg.timestamp;
          zonesRef.current.set(msg.zone_id, zone);
          setVersion((v) => v + 1);
        } else if (msg.type === "alert") {
          setLastAlert(msg);
          setAlertLog((log) => [msg, ...log].slice(0, 50));
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, []);

  const getZone = useCallback((zoneId: number): ZoneLiveState => {
    return zonesRef.current.get(zoneId) ?? {
      readings: [], riskLevel: null, riskScore: 0, hazard: null, explanation: [], forecast: null, lastUpdate: null,
    };
  }, []);

  const value = useMemo(
    () => ({ connected, getZone, lastAlert, alertLog }),
    [connected, getZone, lastAlert, alertLog],
  );
  return <LiveFeedContext.Provider value={value}>{children}</LiveFeedContext.Provider>;
}

export function useLiveFeed() {
  return useContext(LiveFeedContext);
}
