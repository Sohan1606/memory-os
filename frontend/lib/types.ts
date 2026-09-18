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

/* ------------------------------------------------------------------ V8.2 */

/** One model capability, with the reason the state was assigned. */
export interface CapabilityReport {
  name: string;
  state: "SUPPORTED" | "NOT_SUPPORTED" | "UNKNOWN";
  reason: string;
}

export interface CapabilitiesResponse {
  provider: string;
  model: string | null;
  available: boolean;
  capabilities: CapabilityReport[];
}

/** How one task class will actually execute right now. */
export interface RouteDecision {
  task: string;
  mode: "MODEL_TOOLS" | "MODEL_STRUCTURED" | "MODEL" | "DETERMINISTIC"
      | "NOT_CONFIGURED";
  uses_model: boolean;
  degraded: boolean;
  missing: string[];
  reason: string;
}

/** One recorded step of the agent's execution. */
export interface ExecutionStep {
  stage: string;
  detail: string | null;
  at: string;
  payload?: Record<string, unknown>;
}

export interface ArbitrationRecord {
  id: string;
  query: string | null;
  winner_id: string | null;
  reason: string;
  uncertainty: number;
  conflict: boolean;
  created_at: string;
}

export interface MemoryInfluence {
  id: string;
  memory_id: string;
  influenced_kind: string;
  influenced_id: string;
  how: string;
  outcome_verdict: string | null;
  reputation_effect: string | null;
  created_at: string;
}

export interface MemoryImpact {
  memory_id: string;
  influence_count: number;
  resolved_count: number;
  supported: number;
  contradicted: number;
  unresolved: number;
  summary: string;
  reputation: Record<string, unknown>;
}

/** A learned behavioural policy, always carrying its evidence. */
export interface CognitivePolicy {
  key: string;
  value: string;
  confidence: number;
  is_default: boolean;
  evidence_count: number;
  evidence?: { evidence: string; at: string }[];
  explanation?: string;
}

/** Per-capability trust. `reliability` is null below the evidence threshold. */
export interface CapabilityTrustEntry {
  capability: string;
  task_class: string;
  successes: number;
  failures: number;
  total: number;
  reliability: number | null;
  label: "RELIABLE" | "MIXED" | "UNRELIABLE" | "SUPPRESSED"
       | "INSUFFICIENT EVIDENCE";
  detail: string;
}

export interface IntentTransition {
  id: string;
  intent_id: string;
  from_status: string | null;
  to_status: string;
  confidence: number;
  uncertainty: number;
  changed_because: string | null;
  evidence: string[];
  created_at: string;
}

export interface NeedHypothesis {
  id: string;
  need: string;
  confidence: number;
  signals: string[];
  utterance: string;
  was_correct: number | null;
  evaluated: boolean;
  created_at: string;
}

export interface ContinuityItem {
  id: string;
  kind: string;
  summary: string;
  reason: string;
  status: string;
  relevance?: number;
  created_at: string;
}

export interface FocusEntry {
  subject_kind: string;
  subject_id: string;
  label: string | null;
  session_id: string;
  updated_at: string;
}

/** Result of a natural-language cognitive command. */
export interface ControlResult {
  command: string;
  matched: string;
  applied: boolean;
  summary: string;
  requires?: string;
  memory_id?: string;
  policy_key?: string;
}

// ------------------------------------------------------------- v8.3 types

export interface MissionStep {
  id: string;
  summary: string;
  state: string;
  kind: string;
  completed_at: string | null;
}

export interface Mission {
  id: string;
  title: string;
  description: string | null;
  state: string;
  priority: number;
  progress: number;
  next_step: string | null;
  blocked_reason: string | null;
  waiting_on: string | null;
  steps: MissionStep[];
  last_activity_at: string | null;
  created_at: string;
}

export interface MissionBriefEntry {
  id: string;
  title: string;
  state: string;
  progress: number;
  days_since_activity: number;
  next_step?: string | null;
  blocked_reason?: string;
  waiting_on?: string;
  review_reason?: string;
}

export interface MissionBrief {
  open: number;
  active: MissionBriefEntry[];
  blocked: MissionBriefEntry[];
  waiting: MissionBriefEntry[];
  needs_review: MissionBriefEntry[];
  summary: string;
}

export interface BackgroundCycle {
  id: string;
  trigger: string;
  state: string;
  tasks_run: string[];
  findings: { summary?: string; reason?: string }[];
  changes_made: number;
  skipped_reason: string | null;
  duration_ms: number | null;
  started_at: string;
}

export interface BackgroundStatus {
  state: string;
  min_interval_s: number;
  max_tasks_per_cycle: number;
  recent_cycles: BackgroundCycle[];
  total_recent: number;
  empty_cycles: number;
  detail: string;
}

export interface WorldFreshness {
  freshness_class: string;
  stale: boolean;
  age_days: number | null;
  horizon_days: number;
  reason: string;
}

export interface WorldFact {
  id: string;
  kind: string;
  label: string;
  state: string;
  confidence: number;
  freshness: WorldFreshness;
}

export interface WorldSnapshot {
  entities: WorldFact[];
  count: number;
  by_kind: Record<string, number>;
  stale_count: number;
  detail: string;
}

export interface SilencePolicy {
  learned: boolean;
  accepted: number;
  rejected: number;
  total: number;
  rejection_ratio?: number;
  interruption_cost: number;
  verdict: string;
  detail: string;
}

export interface Suppression {
  id: string;
  topic: string;
  decision: string;
  suppressed_because: string | null;
  created_at: string;
}

export interface ObservationStats {
  total: number;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  detail: string;
}

export interface Connector {
  id: string;
  name: string;
  state: string;
  capabilities: string[];
  detail: string | null;
}

export interface ConnectorStatus {
  connectors: Connector[];
  total: number;
  connected: number;
  detail: string;
}
