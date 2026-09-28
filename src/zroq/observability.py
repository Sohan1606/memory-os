"""In-process observability and security event collection."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from .audit import AuditLog
from .contracts import SecurityEvent, utc_now


class Observability:
    def __init__(self, audit: AuditLog) -> None:
        self.audit = audit
        self.counters: Counter[str] = Counter()
        self.security_events: list[SecurityEvent] = []

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def security(
        self,
        event_type: str,
        severity: str,
        actor: str,
        reason: str,
        action_id: str | None = None,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        event = SecurityEvent(event_type, severity, actor, action_id, task_id, reason, utc_now(), metadata or {})
        self.security_events.append(event)
        self.audit.append(
            "security." + event_type,
            actor,
            task_id,
            action_id,
            {"severity": severity, "reason": reason, "metadata": metadata or {}},
        )
        self.increment("security_events")
        return event
