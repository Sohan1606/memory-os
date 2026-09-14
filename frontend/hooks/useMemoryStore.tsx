"use client";
/**
 * Single source of truth for memory state on the client.
 *
 * Every view (graph, console, inspector, timeline, retrieval, chat, CTA) reads
 * from this store, and every mutation goes through it, so the UI can never show
 * stale memory after a create / edit / delete / conflict resolution.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";

import { api, ApiError } from "@/lib/api";
import type { GraphData, Health, Memory, MemoryEvent, Stats } from "@/lib/types";

interface StoreValue {
  memories: Memory[];
  graph: GraphData;
  events: MemoryEvent[];
  stats: Stats | null;
  health: Health | null;
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  select: (id: string | null) => void;
  refresh: () => Promise<void>;
  createMemory: (content: string, category?: string, source?: string) =>
    Promise<{ action: string; memory: Memory; conflict?: boolean; previous?: Memory }>;
  updateMemory: (id: string, patch: { content?: string; category?: string; importance?: number; reason?: string }) => Promise<Memory>;
  deleteMemory: (id: string) => Promise<void>;
  resetDemo: () => Promise<void>;
  deleteAll: () => Promise<void>;
}

const Ctx = createContext<StoreValue | null>(null);

const EMPTY_GRAPH: GraphData = { nodes: [], edges: [] };

export function MemoryStoreProvider({ children }: { children: React.ReactNode }) {
  const [memories, setMemories] = useState<Memory[]>([]);
  const [graph, setGraph] = useState<GraphData>(EMPTY_GRAPH);
  const [events, setEvents] = useState<MemoryEvent[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [list, g, tl, h] = await Promise.all([
        api.memories(), api.graph(), api.timeline(), api.health(),
      ]);
      if (!mounted.current) return;
      setMemories(list.memories);
      setStats(list.stats);
      setGraph(g);
      setEvents(tl.events);
      setHealth(h);
      setError(null);
      // Never keep a selection pointing at a memory that no longer exists.
      setSelectedId((cur) => (cur && list.memories.some((m) => m.id === cur) ? cur : null));
    } catch (err) {
      if (!mounted.current) return;
      setError(err instanceof ApiError ? err.message : "Unexpected error loading memory state.");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const createMemory: StoreValue["createMemory"] = useCallback(
    async (content, category, source = "manual") => {
      const result = await api.createMemory(content, category, 0.75, source);
      await refresh();
      return result;
    }, [refresh]);

  const updateMemory: StoreValue["updateMemory"] = useCallback(
    async (id, patch) => {
      const res = await api.updateMemory(id, patch);
      await refresh();
      return res.memory;
    }, [refresh]);

  const deleteMemory: StoreValue["deleteMemory"] = useCallback(
    async (id) => {
      await api.deleteMemory(id);
      setSelectedId((cur) => (cur === id ? null : cur));
      await refresh();
    }, [refresh]);

  const resetDemo = useCallback(async () => {
    await api.reset();
    setSelectedId(null);
    await refresh();
  }, [refresh]);

  const deleteAll = useCallback(async () => {
    await api.deleteAll();
    setSelectedId(null);
    await refresh();
  }, [refresh]);

  const value = useMemo<StoreValue>(() => ({
    memories, graph, events, stats, health, loading, error, selectedId,
    select: setSelectedId, refresh, createMemory, updateMemory, deleteMemory,
    resetDemo, deleteAll,
  }), [memories, graph, events, stats, health, loading, error, selectedId,
      refresh, createMemory, updateMemory, deleteMemory, resetDemo, deleteAll]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useMemoryStore(): StoreValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useMemoryStore must be used inside MemoryStoreProvider");
  return ctx;
}

export function useSelectedMemory(): Memory | null {
  const { memories, selectedId } = useMemoryStore();
  return useMemo(
    () => memories.find((m) => m.id === selectedId) ?? null,
    [memories, selectedId]);
}
