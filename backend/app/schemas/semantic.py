"""V9 canonical semantic schemas.

These models are the deterministic boundary around all meaning extraction.  An
LLM may propose this JSON, but no proposal can enter state until these schemas
accept it and the MeaningKernel's persistence policy approves it.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CognitiveType(str, Enum):
    FACT = "FACT"
    OBSERVATION = "OBSERVATION"
    CLAIM = "CLAIM"
    BELIEF = "BELIEF"
    HYPOTHESIS = "HYPOTHESIS"
    PREFERENCE = "PREFERENCE"
    VALUE = "VALUE"
    GOAL = "GOAL"
    INTENT = "INTENT"
    NEED = "NEED"
    PLAN = "PLAN"
    COMMITMENT = "COMMITMENT"
    DECISION = "DECISION"
    BOUNDARY = "BOUNDARY"
    QUESTION = "QUESTION"
    ASSUMPTION = "ASSUMPTION"
    PREDICTION = "PREDICTION"
    CONCLUSION = "CONCLUSION"
    CORRECTION = "CORRECTION"
    HYPOTHETICAL = "HYPOTHETICAL"
    CONTRADICTION = "CONTRADICTION"
    OUTCOME = "OUTCOME"
    EXPERIENCE = "EXPERIENCE"
    SKILL = "SKILL"
    PRINCIPLE = "PRINCIPLE"


class Provenance(str, Enum):
    USER_STATED = "USER_STATED"
    USER_INFERRED = "USER_INFERRED"
    MODEL_HYPOTHESIS = "MODEL_HYPOTHESIS"
    EXTERNAL_EVIDENCE = "EXTERNAL_EVIDENCE"
    SYSTEM_OBSERVED = "SYSTEM_OBSERVED"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"


class Modality(str, Enum):
    ASSERTED = "ASSERTED"
    TENTATIVE = "TENTATIVE"
    POSSIBLE = "POSSIBLE"
    DESIRED = "DESIRED"
    INTENDED = "INTENDED"
    OBLIGATED = "OBLIGATED"
    QUESTIONED = "QUESTIONED"
    HYPOTHETICAL = "HYPOTHETICAL"
    NEGATED = "NEGATED"
    OBSERVED = "OBSERVED"


class TemporalScope(SemanticModel):
    expression: str | None = Field(default=None, max_length=300)
    start: str | None = Field(default=None, max_length=80)
    end: str | None = Field(default=None, max_length=80)
    kind: str = Field(default="UNSPECIFIED", max_length=30)


class EvidenceRef(SemanticModel):
    kind: str = Field(min_length=1, max_length=60)
    id: str | None = Field(default=None, max_length=120)
    excerpt: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, max_length=300)


class RelationshipCandidate(SemanticModel):
    kind: str = Field(min_length=1, max_length=40)
    target_id: str | None = Field(default=None, max_length=120)
    target_reference: str | None = Field(default=None, max_length=300)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SemanticCandidate(SemanticModel):
    type: CognitiveType
    content: str = Field(min_length=1, max_length=4000)
    modality: Modality
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    source: str = Field(min_length=1, max_length=120)
    # Model proposals must state temporal interpretation explicitly. This keeps
    # omission from silently becoming UNSPECIFIED at the trust boundary.
    temporal_scope: TemporalScope
    status: str = Field(default="PROPOSED", max_length=40)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    relationships: list[RelationshipCandidate] = Field(default_factory=list, max_length=50)
    material: bool = False

    @field_validator("content", "source")
    @classmethod
    def no_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class SemanticRepresentation(SemanticModel):
    schema_version: str = "9.0"
    input_text: str = Field(min_length=1, max_length=4000)
    source: str = Field(default="conversation", max_length=120)
    candidates: list[SemanticCandidate] = Field(default_factory=list, max_length=20)
    ambiguous: bool = False
    ambiguity_reason: str | None = Field(default=None, max_length=500)
    compiler: str = Field(default="deterministic", max_length=60)

    @model_validator(mode="after")
    def ambiguity_is_explained(self) -> "SemanticRepresentation":
        if self.ambiguous and not self.ambiguity_reason:
            raise ValueError("ambiguous representations require ambiguity_reason")
        return self


class CognitiveObjectCreate(SemanticModel):
    type: CognitiveType
    content: str = Field(min_length=1, max_length=4000)
    modality: Modality = Modality.ASSERTED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance: Provenance = Provenance.USER_STATED
    source: str = Field(default="manual", min_length=1, max_length=120)
    temporal_scope: TemporalScope = Field(default_factory=TemporalScope)
    status: str = Field(default="ACTIVE", max_length=40)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = Field(default=None, max_length=100)

    @field_validator("content", "source")
    @classmethod
    def strip_nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


class CognitiveObjectUpdate(SemanticModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    modality: Modality | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str | None = Field(default=None, max_length=40)
    temporal_scope: TemporalScope | None = None
    evidence: list[EvidenceRef] | None = Field(default=None, max_length=50)
    reason: str = Field(default="User-directed update", min_length=1, max_length=500)


class RelationshipCreate(SemanticModel):
    source_id: str = Field(min_length=1, max_length=120)
    target_id: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=40)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance: Provenance = Provenance.USER_STATED
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=50)
    user_id: str | None = Field(default=None, max_length=100)


class MeaningCompileRequest(SemanticModel):
    text: str = Field(min_length=1, max_length=4000)
    source: str = Field(default="conversation", max_length=120)
    persist: bool = False
    thread_id: str | None = Field(default=None, max_length=100)
    user_id: str | None = Field(default=None, max_length=100)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value
