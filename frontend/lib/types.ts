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
  learning_v841?: KnowledgeStats & { experience_count: number };
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

// ----------------------------------------------------------- v8.4.1 types
export interface ExperienceEvidence {
  observation_id: string;
  role: "supporting" | "counterexample";
  source: string;
  origin: string;
  content: string;
  epistemic_status: string;
  confidence: number;
}

export interface Experience {
  id: string;
  situation: string;
  action: string | null;
  outcome: string | null;
  success: boolean | null;
  confidence: number;
  scope_kind: string;
  scope_value: string | null;
  lifecycle: string;
  source: string;
  evidence: ExperienceEvidence[];
  evidence_count: number;
  created_at: string;
  updated_at: string;
}

export interface AbstractionReputation {
  reputation: string;
  score: number | null;
  evidence: number;
  retrievals: number;
  usages: number;
  successes: number;
  failures: number;
  neutral_outcomes: number;
  evidence_contradictions: number;
}

export interface KnowledgeEvidence {
  id: string;
  evidence_kind: string;
  evidence_id: string;
  stance: "supporting" | "counterexample";
  relation: string;
  quality: number;
  note: string | null;
}

export interface KnowledgeValidation {
  id: string;
  decision: "PASS" | "REJECT";
  confidence: number;
  metrics: Record<string, number | boolean | string[]>;
  reason: string;
  validator: string;
  created_at: string;
}

export interface KnowledgeUsage {
  id: string;
  item_id: string;
  item_kind: "skill" | "principle";
  influenced_kind: string;
  influenced_id: string;
  how: string;
  status: string;
  outcome_verdict: string | null;
  outcome_detail: string | null;
  outcome_evidence: string[];
  created_at: string;
  resolved_at: string | null;
}

export interface LearnedKnowledge {
  id: string;
  kind: "skill" | "principle";
  name: string;
  statement: string;
  trigger_text: string;
  procedure: string[];
  expected_outcome: string;
  confidence: number;
  reputation: AbstractionReputation;
  lifecycle: string;
  validation_status: string;
  scope_kind: string;
  scope_value: string | null;
  generality: number;
  evidence: KnowledgeEvidence[];
  supporting_evidence_count: number;
  counterexample_count: number;
  validations: KnowledgeValidation[];
  usages: KnowledgeUsage[];
  created_at: string;
  updated_at: string;
}

export interface KnowledgeStats {
  skills: number;
  principles: number;
  trusted_skills: number;
  trusted_principles: number;
  by_kind: Record<string, Record<string, number>>;
}

export interface KnowledgeExplanation {
  item: LearnedKnowledge;
  supporting_evidence: KnowledgeEvidence[];
  counterexamples: KnowledgeEvidence[];
  validation_history: KnowledgeValidation[];
  lifecycle_history: { previous_lifecycle: string | null; lifecycle: string;
                       reason: string; created_at: string }[];
  usage_history: KnowledgeUsage[];
  provenance: Record<string, unknown>;
  summary: string;
  note: string;
}

// ----------------------------------------------------------- v8.4.2 types
export interface DecisiveFactor {
  name: string;
  value: unknown;
  impact: "positive" | "negative" | "neutral";
  description: string;
}

export interface AlternativeCandidate {
  id: string;
  label: string;
  kind: string;
  score?: number | null;
  lifecycle?: string | null;
  rejection_reason: string;
  factors: Record<string, unknown>;
}

export interface ExplanationEvidence {
  id: string;
  kind: string;
  content: string;
  confidence: number;
  source: string;
  created_at: string;
  relation: string;
}

export interface CorrectionStateDiff {
  is_corrected: boolean;
  correction_type?: string | null;
  previous_lifecycle?: string | null;
  current_lifecycle?: string | null;
  reason?: string | null;
  created_at?: string;
  historical_state?: Record<string, unknown> | null;
  current_state?: Record<string, unknown> | null;
}

export interface ExplanationGraph {
  id: string;
  user_id: string;
  explanation_type: string;
  query_intent: string;
  subject: {
    kind: string;
    id: string;
    label: string;
    status?: string;
    lifecycle?: string;
    current_state?: Record<string, unknown>;
    historical_state?: Record<string, unknown> | null;
  };
  summary: string;
  decisive_factors: DecisiveFactor[];
  supporting_evidence: ExplanationEvidence[];
  counter_evidence: ExplanationEvidence[];
  alternatives: AlternativeCandidate[];
  causality: {
    upstream: { cause_kind: string; cause_id: string; relation: string; weight?: number }[];
    downstream: { effect_kind: string; effect_id: string; relation: string; weight?: number }[];
  };
  timeline: { id: number; type: string; summary: string; created_at: string; correlation_id?: string | null }[];
  correction?: CorrectionStateDiff | null;
  confidence?: number | null;
  provenance: {
    source: string;
    schema_version: string;
    generated_at: string;
    correlation_id?: string | null;
    evidence_count: number;
  };
  created_at: string;
}

export interface ExplanationSnapshotMeta {
  id: string;
  user_id: string;
  subject_kind?: string | null;
  subject_id?: string | null;
  explanation_type: string;
  query_intent: string;
  summary: string;
  created_at: string;
}

// ----------------------------------------------------------- v8.4.3 types
/**
 * Connected Research. Every shape here mirrors the backend's
 * ResearchEngine JSON exactly — the UI must never re-derive or guess a
 * field the backend did not send.
 */
export interface ResearchSession {
  id: string;
  user_id: string;
  question: string;
  state: "DRAFT" | "RUNNING" | "PARTIAL" | "COMPLETED" | "BLOCKED" | "FAILED" | string;
  provider_state: string;
  correlation_id?: string | null;
  source_count: number;
  evidence_count: number;
  claim_count: number;
  conflict_count: number;
  open_questions: string[];
  detail: string;
  created_at: string;
  updated_at: string;
}

export interface ResearchSource {
  id: string;
  session_id: string;
  user_id: string;
  canonical_url: string;
  domain: string;
  title: string | null;
  publisher: string | null;
  source_type: string;
  source_quality: number | null;
  availability: string;
  metadata: string;
  discovered_at: string;
  created_at: string;
}

export interface ResearchFetch {
  id: string;
  session_id: string;
  user_id: string;
  source_id: string | null;
  requested_url: string;
  final_url: string | null;
  status: "COMPLETED" | "FETCH_FAILED" | "BLOCKED" | "TIMEOUT" | string;
  http_status: number | null;
  content_type: string | null;
  latency_ms: number | null;
  redirect_count: number;
  redirect_chain: string[];
  content_hash: string | null;
  bytes_read: number | null;
  error_code: string | null;
  error_detail: string | null;
  correlation_id?: string | null;
  fetched_at: string;
  created_at: string;
}

export interface ResearchEvidence {
  id: string;
  session_id: string;
  user_id: string;
  source_id: string;
  fetch_id: string;
  excerpt: string;
  locator: string;
  evidence_type: string;
  evidence_strength: number;
  source_quality: number;
  freshness: string;
  injection_flags: string[];
  retrieved_at: string;
  content_hash: string;
  created_at: string;
}

export interface ResearchClaim {
  id: string;
  session_id: string;
  user_id: string;
  statement: string;
  evidence_ids: string[];
  source_ids: string[];
  evidence_strength: number;
  source_quality: number;
  corroboration_count: number;
  independent_domain_count: number;
  freshness: string;
  claim_confidence: number;
  conflict_group: string | null;
  status: "supported" | "contested" | "unsupported" | string;
  created_at: string;
  updated_at: string;
}

export interface ResearchConflict {
  id: string;
  session_id: string;
  user_id: string;
  conflict_group: string;
  claim_ids: string[];
  subject_key: string;
  reason: string;
  created_at: string;
}

export interface ResearchWorldUpdate {
  id: string;
  session_id: string;
  user_id: string;
  claim_id: string;
  world_entity_id: string | null;
  kind: string;
  label: string;
  proposed_confidence: number;
  applied_confidence: number | null;
  state: "PROPOSED" | "APPLIED" | "REJECTED" | string;
  reason: string;
  correlation_id?: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface ResearchStatus {
  available: boolean;
  detail: string;
  session_count: number;
  limits: {
    max_sources_per_session: number;
    max_evidence_per_fetch: number;
    max_claims_per_session: number;
  };
}

export interface ResearchFetchResult {
  status: string;
  fetch_id?: string;
  source_id?: string;
  http_status?: number | null;
  bytes_read?: number | null;
  evidence_created?: number;
  claims_created_or_updated?: number;
  conflicts_detected?: number;
  injection_flags?: string[];
  error_code?: string | null;
  detail: string;
}

// --------------------------------------------------------------- V8.4.4
export interface PortabilityExport {
  id: string;
  status: string;
  package_sha256: string;
  created_at: string;
  completed_at?: string | null;
  object_counts: Record<string, number>;
  integrity?: { status: string; integrity_valid?: boolean };
}

export interface PortabilityImport {
  id: string;
  filename: string;
  state: string;
  package_sha256: string;
  created_at: string;
  validated_at?: string | null;
}

export interface RestoreConflict {
  id: string;
  table_name: string;
  object_key: string;
  state: string;
  reason: string;
  resolution?: string | null;
  local?: Record<string, unknown>;
  imported?: Record<string, unknown>;
}

export interface PortabilityPlan {
  id: string;
  import_id: string;
  selected_domains: string[];
  tables: Record<string, number>;
  records: number;
  inserts: number;
  updates: number;
  skips: number;
  conflicts: RestoreConflict[];
  blockers: Record<string, unknown>[];
  status: string;
}

export interface RestoreOperation {
  id: string;
  import_id: string;
  status: string;
  applied_count: number;
  skipped_count: number;
  selected_domains: string[];
  started_at: string;
  finished_at?: string | null;
}

// ------------------------------------------------------------------ v8.5
export interface SessionPrincipal {
  user_id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  role: string;
  namespace: string;
  permissions: string[];
}

export interface AuthSessionResponse {
  auth_mode: "disabled" | "required";
  user: SessionPrincipal | null;
  session_id?: string;
  note?: string;
}

export interface DependencyState {
  state: "ACTIVE" | "DEGRADED" | "NOT_CONFIGURED" | "BLOCKED" | "FAILED";
  detail: string;
  required: boolean;
}

export interface ReadinessReport {
  status: "ready" | "not_ready";
  degraded_capabilities: string[];
  dependencies: Record<string, DependencyState>;
  auth_mode: string;
}

export interface MetricsSnapshot {
  scope: string;
  uptime_seconds: number;
  counters: Record<string, number>;
  requests: Record<string, {
    count: number; avg_ms: number; max_ms: number;
    buckets_ms: Record<string, number>;
  }>;
}

export interface SecurityEvent {
  id: number;
  type: string;
  summary: string;
  subject_kind?: string | null;
  subject_id?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RateLimitState {
  enabled: boolean;
  window_seconds: number;
  limits_per_minute: Record<string, number>;
  active_buckets: number;
}
