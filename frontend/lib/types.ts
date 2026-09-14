export type Category =
  | "IDENTITY" | "PREFERENCE" | "PROJECT" | "GOAL" | "HABIT"
  | "CONTEXT" | "RELATIONSHIP" | "FACT" | "COMMUNICATION_STYLE";

export interface Memory {
  id: string;
  user_id: string;
  content: string;
  category: Category | string;
  importance: number;
  confidence: number;
  status: string;
  version: number;
  source: string;
  thread_id: string | null;
  reinforcement_count: number;
  created_at: string;
  updated_at: string;
  related_memory_ids: string[];
}

export interface RetrievalResult {
  memory: Memory;
  score: number;
  semantic: number;
  keyword: number;
  reasons: string[];
  strength: "strong" | "weak";
}

export interface PathStep {
  kind: "query" | "memory" | "related" | "context" | "response";
  id?: string;
  label: string;
  category?: string;
  score?: number;
  via?: string;
}

export interface SearchResponse {
  query: string;
  state: "STRONG" | "WEAK" | "NO_STRONG_MATCH" | "EMPTY";
  results: RetrievalResult[];
  path: PathStep[];
  mode: string;
}

export interface MemoryVersion {
  version: number;
  content: string;
  category: string;
  reason: string;
  created_at: string;
}

export interface MemoryEvent {
  id: number;
  memory_id: string | null;
  event_type: string;
  source: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface GraphData {
  nodes: Memory[];
  edges: { source: string; target: string }[];
}

export interface ChatActivity { type: string; [k: string]: unknown }

export interface ChatResponse {
  answer: string;
  provider: string;
  recalled: RetrievalResult[];
  activity: ChatActivity[];
  thread_id: string;
  correlation_id: string | null;
  cognition: TurnCognition | null;
}

export interface Health {
  status: string;
  api: string;
  agent: { framework: string; graph: string; checkpointer: string };
  memory: { backend: string; path: string; count: number };
  vector: { backend: string; mode: string; count: number; error: string | null };
  embeddings: { model: string | null; dimension: number | null; mode: string };
  provider: { name: string; model: string | null; available: boolean; tool_calling: boolean; detail: string; mode?: string };
  voice: { mode: string; detail: string };
  langmem: { state: string; version: string | null; detail: string };
  cognition: { event_types: number; events: number; autonomy: string };
}

export interface Stats {
  total: number;
  by_category: Record<string, number>;
  vector_count: number;
  retrieval_mode: string;
}

// ------------------------------------------------------------ v8 cognition
export interface CognitiveEvent {
  id: number;
  type: string;
  label: string;
  summary: string;
  subject_kind: string | null;
  subject_id: string | null;
  correlation_id: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface TurnCognition {
  need: string;
  need_confidence: number;
  intent: string | null;
  world_entities: { kind: string; label: string; state: string }[];
  recalled: number;
  degraded: boolean;
  arbitration: { winner: string; explanation: string; conflict: boolean } | null;
  predictions: number;
  attention: { decision: string; why_now: string; surface: boolean } | null;
  autonomy: string;
}

export interface WorldEntity {
  id: string;
  kind: string;
  label: string;
  state: string;
  confidence: number;
  due_at: string | null;
  updated_at: string;
}

export interface WorldSummary {
  total: number;
  by_kind: Record<string, Record<string, number>>;
  at_risk: number;
}

export interface Capability { name: string; state: string; detail: string }

export interface SelfReport {
  active: string[];
  degraded: string[];
  not_configured: string[];
  capabilities: Record<string, Capability>;
  limitations: string[];
}

export interface TrustScore {
  capability: string;
  successes: number;
  failures: number;
  total: number;
  reliability: number | null;
  label: string;
}

export interface Intervention {
  id: string;
  topic: string;
  decision: string;
  expected_value: number;
  rationale: string;
  created_at: string;
}

export interface PredictionAccuracy {
  calibration: string;
  accuracy: number | null;
  brier: number | null;
  resolved: number;
  detail?: string;
}

export interface CognitionStatus {
  autonomy: { level: string; trust: TrustScore[] };
  world: WorldSummary;
  intent: { label: string; status: string; confidence: number } | null;
  predictions: PredictionAccuracy;
  attention: { total: number; surfaced: number; suppressed: number;
               restraint: number | null; detail: string };
  self: SelfReport;
  policies: { id: string; key: string; value: string; rationale: string }[];
  events: Record<string, number>;
}

export interface SandboxResult {
  id: string;
  simulation: boolean;
  banner: string;
  question: string;
  kind: string;
  assumptions: string[];
  changed_variables: string[];
  projected_effects: string[];
  risks: string[];
  confidence: number;
  note: string;
}

export interface ResumeBriefing {
  first_time: boolean;
  away_seconds: number | null;
  briefing: string;
  open_commitments: WorldEntity[];
  at_risk: WorldEntity[];
  stale_memories: { id: string; content: string; updated_at: string }[];
}

// --------------------------------------------------------------- v8.1 types
export interface ProviderInfo {
  name: string;
  available: boolean;
  model: string | null;
  tool_calling: boolean;
  detail: string;
  /** "REAL AGENT" | "DETERMINISTIC FALLBACK" */
  mode: string;
}

export interface CapabilityState {
  state: string;
  detail: string;
}

export interface ProviderStatusResponse {
  provider: ProviderInfo;
  extraction: { state: string; model: string | null; detail: string };
  perception: Record<string, CapabilityState>;
}

export interface HealthFinding {
  memory_id: string;
  issue: string;
  remedy: string;
  confidence: number;
  evidence: string;
  related_id: string | null;
}

export interface MemoryHealthReport {
  total_active: number;
  findings: HealthFinding[];
  by_issue: Record<string, number>;
  grade: string;
  checked_at: string;
}

export interface PerceptionResult {
  id: string;
  modality: string;
  text: string;
  understanding: string;
  source: string;
  detail: string;
  bytes: number;
  checksum: string | null;
  created_at: string;
}
