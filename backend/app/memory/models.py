"""Domain models for long-term memory."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

CATEGORIES = [
    "IDENTITY", "PREFERENCE", "PROJECT", "GOAL", "HABIT",
    "CONTEXT", "RELATIONSHIP", "FACT", "COMMUNICATION_STYLE",
]

EVENT_TYPES = [
    "MEMORY_CREATED", "MEMORY_REINFORCED", "MEMORY_RETRIEVED", "MEMORY_UPDATED",
    "MEMORY_CONSOLIDATED", "MEMORY_SUPERSEDED", "MEMORY_DELETED",
]


@dataclass
class Memory:
    id: str
    user_id: str
    content: str
    category: str
    importance: float = 0.5
    confidence: float = 0.7
    status: str = "active"
    version: int = 1
    source: str = "conversation"
    thread_id: str | None = None
    reinforcement_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    related_memory_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any, related: list[str] | None = None) -> "Memory":
        return cls(
            id=row["id"], user_id=row["user_id"], content=row["content"],
            category=row["category"], importance=row["importance"],
            confidence=row["confidence"], status=row["status"], version=row["version"],
            source=row["source"], thread_id=row["thread_id"],
            reinforcement_count=row["reinforcement_count"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            related_memory_ids=related or [],
        )


@dataclass
class RetrievalResult:
    memory: Memory
    score: float
    semantic: float
    keyword: float
    reasons: list[str]
    strength: str  # strong | weak

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory": self.memory.to_dict(),
            "score": round(self.score, 4),
            "semantic": round(self.semantic, 4),
            "keyword": round(self.keyword, 4),
            "reasons": self.reasons,
            "strength": self.strength,
        }
