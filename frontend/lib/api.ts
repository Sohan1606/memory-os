/**
 * Typed client for the FastAPI backend.
 * Always same-origin (/api/...) - Next.js rewrites proxy to the backend, so the
 * browser never needs to know the backend host.
 */
import type {
  ChatResponse, CognitionStatus, CognitiveEvent, GraphData, Health, Intervention,
  Memory, MemoryEvent, MemoryVersion, PredictionAccuracy, ResumeBriefing,
  HealthFinding, MemoryHealthReport, PerceptionResult, ProviderStatusResponse,
  SandboxResult, SearchResponse, SelfReport, Stats, TrustScore, WorldEntity,
  WorldSummary,
  ArbitrationRecord, CapabilitiesResponse, CapabilityTrustEntry,
  CognitivePolicy, ContinuityItem, ControlResult, ExecutionStep, FocusEntry,
  IntentTransition, MemoryImpact, MemoryInfluence, NeedHypothesis,
  RouteDecision,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("Cannot reach the MEMORY//OS backend.", 0);
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch { /* keep default */ }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  chat: (message: string, threadId: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, thread_id: threadId }),
    }),

  conversations: () =>
    request<{ id: string; title: string; created_at: string; updated_at: string }[]>(
      "/api/conversations"),

  threadMessages: (threadId: string) =>
    request<{ thread_id: string; messages: { role: string; content: string; created_at: string }[] }>(
      `/api/conversations/${encodeURIComponent(threadId)}/messages`),

  memories: (params?: { category?: string; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.category) qs.set("category", params.category);
    if (params?.q) qs.set("q", params.q);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<{ memories: Memory[]; stats: Stats }>(`/api/memories${suffix}`);
  },

  memory: (id: string) =>
    request<{ memory: Memory; versions: MemoryVersion[]; related: Memory[] }>(
      `/api/memories/${encodeURIComponent(id)}`),

  createMemory: (content: string, category?: string, importance = 0.7, source = "manual") =>
    request<{ action: string; memory: Memory; conflict?: boolean; previous?: Memory }>(
      "/api/memories",
      { method: "POST", body: JSON.stringify({ content, category, importance, source }) }),

  updateMemory: (id: string, patch: { content?: string; category?: string; importance?: number; reason?: string }) =>
    request<{ memory: Memory; versions: MemoryVersion[] }>(
      `/api/memories/${encodeURIComponent(id)}`,
      { method: "PATCH", body: JSON.stringify(patch) }),

  deleteMemory: (id: string) =>
    request<{ deleted: string }>(`/api/memories/${encodeURIComponent(id)}`, { method: "DELETE" }),

  search: (query: string, topK = 5) =>
    request<SearchResponse>("/api/memories/search",
      { method: "POST", body: JSON.stringify({ query, top_k: topK }) }),

  graph: () => request<GraphData>("/api/memories/graph"),

  timeline: () =>
    request<{ events: MemoryEvent[]; memories: Memory[] }>("/api/memories/timeline"),

  consolidate: (memoryIds: string[]) =>
    request<{ memory: Memory; source_memory_ids: string[] }>("/api/memories/consolidate",
      { method: "POST", body: JSON.stringify({ memory_ids: memoryIds }) }),

  events: () => request<{ events: MemoryEvent[] }>("/api/events"),

  exportAll: () => request<Record<string, unknown>>("/api/export"),

  reset: () => request<{ reset: boolean; seeded: number }>("/api/reset", { method: "POST" }),

  deleteAll: () =>
    request<{ deleted: number }>("/api/memories?confirm=true", { method: "DELETE" }),

  voiceStatus: () =>
    request<{ mode: string; detail: string; browser_fallback: boolean }>("/api/voice/status"),

  // ---------------------------------------------------------- v8 cognition
  cognitionStatus: () => request<CognitionStatus>("/api/cognition/status"),

  // v8.1
  providerStatus: () => request<ProviderStatusResponse>("/api/provider"),
  memoryHealth: () => request<MemoryHealthReport>("/api/memory-health"),
  applyHealthRemedy: (finding: HealthFinding) =>
    request<{ applied: boolean; memory_id: string; remedy: string }>(
      "/api/memory-health/apply",
      { method: "POST", body: JSON.stringify(finding) }),
  perceive: async (file: File): Promise<PerceptionResult> => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/perceive", { method: "POST", body });
    if (!res.ok) throw new Error(`Perception failed (${res.status})`);
    return res.json() as Promise<PerceptionResult>;
  },

  cognitiveEvents: (opts: { limit?: number; since?: number; kind?: string } = {}) => {
    const q = new URLSearchParams();
    if (opts.limit !== undefined) q.set("limit", String(opts.limit));
    if (opts.since !== undefined) q.set("since", String(opts.since));
    if (opts.kind) q.set("kind", opts.kind);
    return request<{ events: CognitiveEvent[]; counts: Record<string, number>;
                     latest_id: number }>(`/api/cognition/events?${q.toString()}`);
  },

  turn: (correlationId: string) =>
    request<{ correlation_id: string; events: CognitiveEvent[]; count: number }>(
      `/api/cognition/turn/${encodeURIComponent(correlationId)}`),

  why: (subjectKind: string, subjectId: string) =>
    request<{ events: CognitiveEvent[]; explanation: string;
              reputation: Record<string, unknown> | null }>(
      `/api/cognition/why?subject_kind=${encodeURIComponent(subjectKind)}` +
      `&subject_id=${encodeURIComponent(subjectId)}`),

  changes: (since?: number) =>
    request<{ changes: CognitiveEvent[]; count: number; summary: string }>(
      since === undefined ? "/api/cognition/changes"
                          : `/api/cognition/changes?since=${since}`),

  resume: () => request<ResumeBriefing>("/api/cognition/resume"),

  selfReport: () => request<SelfReport>("/api/cognition/self"),

  world: (params: { kind?: string; state?: string } = {}) => {
    const q = new URLSearchParams(params as Record<string, string>);
    return request<{ entities: WorldEntity[]; summary: WorldSummary;
                     due_soon: WorldEntity[] }>(`/api/world?${q.toString()}`);
  },

  intents: () => request<{ current: Record<string, unknown> | null;
                           history: Record<string, unknown>[] }>("/api/intents"),

  predictions: () => request<{ predictions: Record<string, unknown>[];
                               accuracy: PredictionAccuracy }>("/api/predictions"),

  autonomy: () => request<{ level: string; levels: string[]; trust: TrustScore[];
                            interventions: Intervention[];
                            precision: CognitionStatus["attention"] }>("/api/autonomy"),

  setAutonomy: (level: string, reason: string) =>
    request<{ level: string; trust: TrustScore[] }>("/api/autonomy",
      { method: "POST", body: JSON.stringify({ level, reason }) }),

  learning: () => request<{ policies: CognitionStatus["policies"];
                            self_evaluation: Record<string, {
                              question: string; answer: string; evidence: number }> }>(
    "/api/learning"),

  consolidateLearning: () =>
    request<{ proposed: { kind: string; statement: string; evidence: number;
                          status: string }[]; note: string }>(
      "/api/learning/consolidate", { method: "POST" }),

  simulate: (question: string) =>
    request<SandboxResult>("/api/sandbox",
      { method: "POST", body: JSON.stringify({ question }) }),

  sandboxHistory: () => request<{ runs: SandboxResult[] }>("/api/sandbox"),

  memoryReputation: (id: string) =>
    request<{ memory_id: string; lifecycle: string; reputation: string;
              evidence: number; retrievals: number; influences: number }>(
      `/api/memories/${encodeURIComponent(id)}/reputation`),

  /* ---------------------------------------------------------------- V8.2 */

  /** What the active model can genuinely do, and why we believe that. */
  capabilities: () =>
    request<{ capabilities: CapabilitiesResponse }>("/api/capabilities"),

  /** The full routing table: how each task class will actually execute. */
  routingTable: () =>
    request<{ routes: RouteDecision[] }>("/api/capabilities/route"),

  /** The recorded execution trace for one turn. */
  execution: (correlationId: string) =>
    request<{ correlation_id: string; steps: ExecutionStep[] }>(
      `/api/execution/${encodeURIComponent(correlationId)}`),

  recentExecutions: () =>
    request<{ traces: { correlation_id: string; steps: number;
                        started_at: string }[] }>("/api/execution"),

  arbitrations: () =>
    request<{ records: ArbitrationRecord[] }>("/api/arbitration"),

  arbitration: (id: string) =>
    request<ArbitrationRecord & { candidates: Record<string, unknown>[] }>(
      `/api/arbitration/${encodeURIComponent(id)}`),

  /** Recorded memory influences. `pending` = outcome not yet observed. */
  influences: (pending = false) =>
    request<{ influences: MemoryInfluence[]; count: number }>(
      `/api/influence${pending ? "?pending=true" : ""}`),

  recordInfluenceOutcome: (
    id: string,
    body: { verdict: string; detail: string; evidence: string[] },
  ) =>
    request<MemoryInfluence>(
      `/api/influence/${encodeURIComponent(id)}/outcome`,
      { method: "POST", body: JSON.stringify(body) }),

  /** The full causal story of one memory, as far as evidence allows. */
  memoryImpact: (id: string) =>
    request<MemoryImpact>(`/api/memories/${encodeURIComponent(id)}/impact`),

  cognitivePolicy: () =>
    request<{ effective: Record<string, string>; policies: CognitivePolicy[] }>(
      "/api/cognitive-policy"),

  explainPolicy: (key: string) =>
    request<CognitivePolicy>(
      `/api/cognitive-policy/${encodeURIComponent(key)}`),

  revertPolicy: (key: string) =>
    request<{ key: string; reverted: boolean }>(
      `/api/cognitive-policy/${encodeURIComponent(key)}`, { method: "DELETE" }),

  /** Per-capability trust, honest about insufficient evidence. */
  capabilityTrust: () =>
    request<{ capabilities: CapabilityTrustEntry[] }>("/api/trust"),

  intentTransitions: () =>
    request<{ transitions: IntentTransition[] }>("/api/intents/transitions"),

  explainIntent: (id: string) =>
    request<{ intent: Record<string, unknown> | null;
              transitions: IntentTransition[]; explanation: string }>(
      `/api/intents/${encodeURIComponent(id)}/why`),

  needs: () =>
    request<{ recent: string[]; hypotheses: NeedHypothesis[];
              accuracy: { evaluated: number; accuracy: number | null;
                          detail: string } }>("/api/needs"),

  evaluateNeed: (id: string, correct: boolean) =>
    request<{ id: string; need: string; correct: boolean }>(
      `/api/needs/${encodeURIComponent(id)}/evaluate`,
      { method: "POST", body: JSON.stringify({ correct }) }),

  continuity: () => request<{ items: ContinuityItem[] }>("/api/continuity"),

  closeContinuityItem: (id: string, note = "") =>
    request<ContinuityItem>(
      `/api/continuity/${encodeURIComponent(id)}/close`,
      { method: "POST", body: JSON.stringify({ note }) }),

  /** Object permanence: tell the backend what the user has open. */
  setFocus: (subjectKind: string, subjectId: string,
             opts: { sessionId?: string; label?: string } = {}) =>
    request<FocusEntry>("/api/focus", {
      method: "POST",
      body: JSON.stringify({
        subject_kind: subjectKind, subject_id: subjectId,
        session_id: opts.sessionId ?? "default", label: opts.label ?? null }),
    }),

  focus: (sessionId = "default") =>
    request<{ focus: FocusEntry[] }>(
      `/api/focus?session_id=${encodeURIComponent(sessionId)}`),

  clearFocus: (sessionId = "default") =>
    request<{ cleared: number }>(
      `/api/focus?session_id=${encodeURIComponent(sessionId)}`,
      { method: "DELETE" }),

  whyNow: (subjectKind: string, subjectId: string) =>
    request<Record<string, unknown>>(
      `/api/cognition/why-now?subject_kind=${encodeURIComponent(subjectKind)}` +
      `&subject_id=${encodeURIComponent(subjectId)}`),

  whyMemoryUsed: (id: string) =>
    request<Record<string, unknown>>(
      `/api/memories/${encodeURIComponent(id)}/why-used`),

  observePrediction: (id: string, observation: string,
                      opts: { supports?: boolean; evidence?: string[] } = {}) =>
    request<Record<string, unknown>>(
      `/api/predictions/${encodeURIComponent(id)}/observe`, {
        method: "POST",
        body: JSON.stringify({ observation, supports: opts.supports ?? null,
                               evidence: opts.evidence ?? [] }),
      }),

  /** Natural-language control over the system's own cognition. */
  control: (message: string, sessionId = "default") =>
    request<ControlResult>("/api/control", {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),
};
