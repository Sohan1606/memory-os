"use client";
/**
 * Facade polling hook (Z-UI.1 WP-UI-1).
 *
 * Subordinate read-model only: the frontend never derives authority from
 * these values — it renders what the backend produced. Polling is modest
 * (10s), pauses when the tab is hidden, and exposes a typed condition so
 * every surface can render truthful loading/error/unavailable states.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { SystemCondition } from "@/lib/types";

export interface FacadeState<T> {
  condition: SystemCondition;
  data: T | null;
  reason: string | null;
  refresh: () => void;
  lastUpdated: number | null;
}

export function useFacade<T extends { available: boolean; reason?: string }>(
  fetcher: () => Promise<T>,
  intervalMs = 10_000,
): FacadeState<T> {
  const [condition, setCondition] = useState<SystemCondition>("LOADING");
  const [data, setData] = useState<T | null>(null);
  const [reason, setReason] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const result = await fetcher();
        if (cancelled || !mounted.current) return;
        setData(result);
        setLastUpdated(Date.now());
        if (result.available === false) {
          setReason((result as { reason?: string }).reason ?? "Unavailable");
          setCondition("UNAVAILABLE");
        } else {
          setReason(null);
          setCondition("READY");
        }
      } catch {
        if (cancelled || !mounted.current) return;
        setCondition("ERROR");
        setReason("Cannot reach the backend.");
      }
    };
    load();
    if (intervalMs <= 0) return () => { cancelled = true; };
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, intervalMs);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [fetcher, intervalMs, tick]);

  return { condition, data, reason, refresh, lastUpdated };
}
