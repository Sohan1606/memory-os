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
