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
