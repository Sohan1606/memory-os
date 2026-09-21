"""Typed request/response schemas."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str = Field(default="thread-main", min_length=1, max_length=100)
    user_id: str | None = Field(default=None, max_length=100)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


class ChatResponse(BaseModel):
    answer: str
    provider: str
    recalled: list[dict[str, Any]]
    activity: list[dict[str, Any]]
    thread_id: str
    # v8: the cognitive trace for this turn. `cognition` is None only if the
    # cognitive loop failed; the conversation still succeeds either way.
    correlation_id: str | None = None
    cognition: dict[str, Any] | None = None


class MemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    category: str | None = None
    importance: float = Field(default=0.7, ge=0.0, le=1.0)
    source: str = Field(default="manual", max_length=60)
    user_id: str | None = None

    @field_validator("content")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v.strip()


class MemoryUpdateRequest(BaseModel):
    content: str | None = Field(default=None, max_length=2000)
    category: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str = Field(default="Manual edit", max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(default="", max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)
    category: str | None = None
    user_id: str | None = None


class ConsolidateRequest(BaseModel):
    memory_ids: list[str] = Field(min_length=2)
    content: str | None = None
    user_id: str | None = None


class ImportRequest(BaseModel):
    payload: dict[str, Any]
    replace: bool = False
    user_id: str | None = None


# ------------------------------------------------------------------ v8 cognition
class SandboxRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    user_id: str | None = None


class AutonomyRequest(BaseModel):
    level: str = Field(description="observe | assist | prepare | act_low_risk | act")
    reason: str = Field(default="", max_length=300)
    user_id: str | None = None


class OutcomeRequest(BaseModel):
    memory_id: str = Field(min_length=1, max_length=100)
    positive: bool
    user_id: str | None = None


class PredictionResolveRequest(BaseModel):
    correct: bool
    note: str = Field(default="", max_length=300)
    user_id: str | None = None


class DecisionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=300)
    chosen: str = Field(min_length=1, max_length=300)
    alternatives: list[str] = Field(default_factory=list)
    expectation: str = Field(default="", max_length=300)
    influenced_by: list[str] = Field(default_factory=list)
    user_id: str | None = None


# --------------------------------------------------------------- v8.2 schemas
class InfluenceOutcomeRequest(BaseModel):
    """Attach an observed outcome to a recorded memory influence."""

    verdict: str = Field(
        description="SUPPORTED | CONTRADICTED | NEUTRAL | INSUFFICIENT EVIDENCE")
    detail: str = Field(min_length=1, max_length=500)
    # Evidence is required for verdicts that move reputation. Without it the
    # ledger downgrades the verdict to INSUFFICIENT EVIDENCE rather than
    # letting an unevidenced claim change a memory's track record.
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None

    @field_validator("verdict")
    @classmethod
    def known_verdict(cls, v: str) -> str:
        allowed = {"SUPPORTED", "CONTRADICTED", "NEUTRAL",
                   "INSUFFICIENT EVIDENCE"}
        value = v.strip().upper()
        if value not in allowed:
            raise ValueError(f"verdict must be one of {sorted(allowed)}")
        return value


class NeedEvaluationRequest(BaseModel):
    correct: bool
    user_id: str | None = None


class FocusRequest(BaseModel):
    """Which object the user currently has open in the inspector."""

    subject_kind: str = Field(min_length=1, max_length=40)
    subject_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(default="default", max_length=100)
    label: str | None = Field(default=None, max_length=200)
    user_id: str | None = None


class ControlRequest(BaseModel):
    """A natural-language instruction about the system's own cognition."""

    message: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(default="default", max_length=100)
    user_id: str | None = None


class PredictionObservationRequest(BaseModel):
    """
    An observation of reality, offered against an open prediction.

    `supports` is deliberately optional: when it is None the engine decides
    from the observation text whether the evidence actually bears on the
    prediction, and refuses to score it when it does not.
    """

    observation: str = Field(min_length=1, max_length=500)
    supports: bool | None = None
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


# ------------------------------------------------------------- v8.3 requests
class MissionCreateRequest(BaseModel):
    """Create a long-running mission (§10)."""

    title: str = Field(min_length=3, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scope: str | None = Field(default=None, max_length=500)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


class MissionStateRequest(BaseModel):
    """Move a mission through its lifecycle, always with a reason."""

    state: str = Field(min_length=3, max_length=20)
    reason: str = Field(min_length=3, max_length=500)
    evidence: list[str] = Field(default_factory=list)
    blocked_reason: str | None = Field(default=None, max_length=500)
    waiting_on: str | None = Field(default=None, max_length=500)
    user_id: str | None = None


class MissionStepRequest(BaseModel):
    summary: str = Field(min_length=3, max_length=300)
    kind: str = Field(default="task", max_length=20)
    depends_on: str | None = None
    user_id: str | None = None


class ObservationRequest(BaseModel):
    """Record evidence. Not automatically a memory (§17)."""

    content: str = Field(min_length=1, max_length=4000)
    source: str = Field(default="conversation", max_length=30)
    origin: str = Field(min_length=1, max_length=300)
    epistemic_status: str = Field(default="OBSERVED", max_length=20)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    subject_kind: str | None = None
    subject_id: str | None = None
    user_id: str | None = None


class OutcomeObservationRequest(BaseModel):
    """Report how something actually turned out (§18)."""

    observation: str = Field(min_length=1, max_length=2000)
    supports: bool | None = None
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


class WorldReconcileRequest(BaseModel):
    """Offer a world claim and let reconciliation decide what it means (§8)."""

    kind: str = Field(min_length=3, max_length=30)
    label: str = Field(min_length=3, max_length=120)
    state: str = Field(default="active", max_length=20)
    detail: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    explicit_correction: bool = False
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


class BackgroundControlRequest(BaseModel):
    """Enable / disable / pause / resume background cognition (§13)."""

    state: str = Field(min_length=4, max_length=10)
    reason: str | None = Field(default=None, max_length=300)
    user_id: str | None = None


class BackgroundRunRequest(BaseModel):
    trigger: str = Field(default="manual", max_length=40)
    force: bool = False
    deadline_s: float = Field(default=10.0, gt=0.0, le=60.0)
    user_id: str | None = None


class SimulationRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    assumptions: list[str] = Field(default_factory=list)
    user_id: str | None = None


class SimulationCommitRequest(BaseModel):
    """Applying a simulation requires explicit confirmation AND changes (§20)."""

    confirm: bool = False
    changes: list[dict] = Field(default_factory=list)
    user_id: str | None = None


class AttentionRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    urgency: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    mission_id: str | None = None
    user_id: str | None = None


class AttentionReactionRequest(BaseModel):
    accepted: bool
    detail: str | None = Field(default=None, max_length=500)
    user_id: str | None = None


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    user_id: str | None = None


# ----------------------------------------------------------- v8.4.1 requests
class ExperienceCreateRequest(BaseModel):
    situation: str = Field(min_length=3, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1)
    action: str | None = Field(default=None, max_length=1000)
    outcome: str | None = Field(default=None, max_length=2000)
    success: bool | None = None
    observation: str | None = Field(default=None, max_length=2000)
    context: dict[str, Any] = Field(default_factory=dict)
    intent: str | None = Field(default=None, max_length=500)
    need: str | None = Field(default=None, max_length=500)
    consequences: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    scope_kind: str = Field(default="user", max_length=30)
    scope_value: str | None = Field(default=None, max_length=200)
    pattern_key: str | None = Field(default=None, max_length=160)
    source: str = Field(default="conversation", max_length=80)
    provenance: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = Field(default=None, max_length=100)
    user_id: str | None = None


class ExperienceLifecycleRequest(BaseModel):
    lifecycle: str = Field(max_length=30)
    reason: str = Field(min_length=3, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)
    user_id: str | None = None


class SkillCandidateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    statement: str = Field(min_length=3, max_length=1000)
    trigger: str = Field(min_length=3, max_length=500)
    procedure: list[str] = Field(min_length=1)
    expected_outcome: str = Field(min_length=3, max_length=1000)
    supporting_experience_ids: list[str] = Field(min_length=1)
    counterexample_experience_ids: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    scope_kind: str = Field(default="user", max_length=30)
    scope_value: str | None = Field(default=None, max_length=200)
    pattern_key: str | None = Field(default=None, max_length=160)
    generalization_hint: str | None = Field(default=None, max_length=1000)
    source: str = Field(default="learning-engine", max_length=80)
    provenance: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None


class PrincipleCandidateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    statement: str = Field(min_length=3, max_length=1000)
    supporting_skill_ids: list[str] = Field(min_length=1)
    supporting_experience_ids: list[str] = Field(default_factory=list)
    counterexample_experience_ids: list[str] = Field(default_factory=list)
    application: list[str] = Field(default_factory=list)
    expected_outcome: str = Field(default="", max_length=1000)
    scope_kind: str = Field(default="user", max_length=30)
    scope_value: str | None = Field(default=None, max_length=200)
    pattern_key: str | None = Field(default=None, max_length=160)
    generality: float = Field(default=0.6, ge=0.0, le=1.0)
    source: str = Field(default="learning-engine", max_length=80)
    provenance: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = None


class KnowledgeRetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    scope: dict[str, str] = Field(default_factory=dict)
    current_world: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)
    user_id: str | None = None


class KnowledgeUseRequest(BaseModel):
    influenced_kind: str = Field(default="decision", max_length=30)
    influenced_id: str = Field(min_length=1, max_length=120)
    how: str = Field(min_length=3, max_length=500)
    context: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(default=0.5, ge=0.0, le=1.0)
    arbitration_id: str | None = Field(default=None, max_length=100)
    user_id: str | None = None


class KnowledgeOutcomeRequest(BaseModel):
    verdict: str = Field(max_length=40)
    detail: str = Field(min_length=1, max_length=1000)
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


class KnowledgeCorrectionRequest(BaseModel):
    action: str = Field(max_length=30)
    reason: str = Field(min_length=3, max_length=500)
    scope_kind: str | None = Field(default=None, max_length=30)
    scope_value: str | None = Field(default=None, max_length=200)
    evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


class DecisionOutcomeRequest(BaseModel):
    actual_outcome: str = Field(min_length=1, max_length=2000)
    positive: bool
    tradeoffs: str | None = Field(default=None, max_length=1000)
    lesson: str | None = Field(default=None, max_length=1000)
    regret_evidence: list[str] = Field(default_factory=list)
    user_id: str | None = None


class ExplanationQueryRequest(BaseModel):
    subject_kind: str | None = Field(default=None, max_length=50)
    subject_id: str | None = Field(default=None, max_length=120)
    explanation_type: str | None = Field(default=None, max_length=60)
    query_intent: str = Field(default="why", max_length=50)
    question: str | None = Field(default=None, max_length=1000)
    depth: int = Field(default=2, ge=1, le=5)
    persist: bool = Field(default=True)
    correlation_id: str | None = Field(default=None, max_length=100)
    user_id: str | None = None

