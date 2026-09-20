"use client";
/** Factory scope context.
 *
 * Every plant-wide query (summary, zones, devices, alerts, forecasts) is
 * filtered by the selected factory, so a multi-tenant deployment can never
 * show one plant's operators another plant's incidents. The selection is
 * persisted locally: an operator watching the same plant across shifts should
 * not have to re-pick it after a reload.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import { api } from "@/lib/api";
import type { Factory } from "@/lib/types";

const STORAGE_KEY = "aeris_factory_id";

interface FactoryValue {
  factories: Factory[];
  /** null = every factory (default for a single-plant prototype). */
  factoryId: number | null;
  factory: Factory | null;
  select: (id: number | null) => void;
  loading: boolean;
}

const FactoryContext = createContext<FactoryValue>({
  factories: [],
  factoryId: null,
  factory: null,
  select: () => undefined,
  loading: true,
});

export function FactoryProvider({ children }: { children: React.ReactNode }) {
  const [factories, setFactories] = useState<Factory[]>([]);
  const [factoryId, setFactoryId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stored: number | null = null;
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      stored = raw === null ? null : Number(raw);
    } catch {
      stored = null;
    }

    let alive = true;
    api
      .factories()
      .then((list) => {
        if (!alive) return;
        setFactories(list);
        const valid = stored !== null && list.some((f) => f.id === stored);
        // A single-plant install has nothing to switch between: keep "all".
        if (valid && list.length > 1) setFactoryId(stored);
        setLoading(false);
      })
      .catch(() => alive && setLoading(false));

    return () => {
      alive = false;
    };
  }, []);

  const select = useCallback((id: number | null) => {
    setFactoryId(id);
    try {
      if (id === null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      /* private mode: the selection simply does not survive the reload */
    }
  }, []);

  const value = useMemo<FactoryValue>(
    () => ({
      factories,
      factoryId,
      factory: factories.find((f) => f.id === factoryId) ?? null,
      select,
      loading,
    }),
    [factories, factoryId, select, loading],
  );

  return <FactoryContext.Provider value={value}>{children}</FactoryContext.Provider>;
}

export function useFactory() {
  return useContext(FactoryContext);
}
