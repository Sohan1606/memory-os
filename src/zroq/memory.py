"""Versioned MEMORY//OS adapter boundary.

The production default is intentionally unavailable. No local memory store is
used as a substitute for MEMORY//OS canonical authority.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import (
    ActionRequest,
    ActionSnapshot,
    GovernanceDecision,
    GovernanceState,
    MemoryContext,
    MemoryState,
    utc_now,
)


class MemoryOSAdapter(ABC):
    contract_version = "MEMORY//OS-v10.2.0-adapter-v1"

    @abstractmethod
    def read(self, owner_id: str, task_id: str, purpose: str) -> MemoryContext:
        raise NotImplementedError

    @abstractmethod
    def propose(self, owner_id: str, task_id: str, proposal: Mapping[str, Any]) -> MemoryContext:
        raise NotImplementedError

    @abstractmethod
    def confirmed_mutation(self, owner_id: str, task_id: str, mutation: Mapping[str, Any]) -> MemoryContext:
        raise NotImplementedError

    @abstractmethod
    def history_evidence(self, owner_id: str, task_id: str, reference: str) -> MemoryContext:
        raise NotImplementedError

    @abstractmethod
    def governance(self, action: ActionRequest | ActionSnapshot) -> GovernanceDecision:
        raise NotImplementedError


class UnavailableMemoryOSAdapter(MemoryOSAdapter):
    """Fail-closed production placeholder until an approved adapter is supplied."""

    def _context(self, owner_id: str, task_id: str, message: str) -> MemoryContext:
        return MemoryContext(
            state=MemoryState.UNAVAILABLE,
            owner_id=owner_id,
            task_id=task_id,
            message=message,
            contract_version=self.contract_version,
        )

    def read(self, owner_id: str, task_id: str, purpose: str) -> MemoryContext:
        return self._context(owner_id, task_id, "MEMORY//OS adapter unavailable; no memory was fabricated")

    def propose(self, owner_id: str, task_id: str, proposal: Mapping[str, Any]) -> MemoryContext:
        return self._context(owner_id, task_id, "MEMORY//OS mutation proposal unavailable")

    def confirmed_mutation(self, owner_id: str, task_id: str, mutation: Mapping[str, Any]) -> MemoryContext:
        return self._context(owner_id, task_id, "MEMORY//OS confirmed mutation unavailable")

    def history_evidence(self, owner_id: str, task_id: str, reference: str) -> MemoryContext:
        return self._context(owner_id, task_id, "MEMORY//OS history/evidence unavailable")

    def governance(self, action: ActionRequest | ActionSnapshot) -> GovernanceDecision:
        return GovernanceDecision(
            state=GovernanceState.UNAVAILABLE,
            decision_id="memoryos-unavailable",
            owner_id=action.owner_id,
            action_digest=action.digest(),
            reason="applicable MEMORY//OS governance is unavailable",
            contract_version=self.contract_version,
        )


@dataclass
class TestMemoryOSAdapter(MemoryOSAdapter):
    """Explicit test double for the external MEMORY//OS contract.

    This class is only for deterministic tests. It is deliberately not used
    by the default runtime and does not claim to implement MEMORY//OS.
    """

    allow_side_effects: bool = True
    contradictory: bool = False
    contract_version: str = "TEST-DOUBLE-not-MEMORY//OS"

    def read(self, owner_id: str, task_id: str, purpose: str) -> MemoryContext:
        return MemoryContext(
            state=MemoryState.READ,
            owner_id=owner_id,
            task_id=task_id,
            items=(),
            provenance=({"source": "test-double", "purpose": purpose},),
            message="test-only governed empty context",
            contract_version=self.contract_version,
        )

    def propose(self, owner_id: str, task_id: str, proposal: Mapping[str, Any]) -> MemoryContext:
        return MemoryContext(
            state=MemoryState.PROPOSE,
            owner_id=owner_id,
            task_id=task_id,
            items=(dict(proposal),),
            provenance=({"source": "test-double"},),
            message="test-only proposal; not canonical memory",
            contract_version=self.contract_version,
        )

    def confirmed_mutation(self, owner_id: str, task_id: str, mutation: Mapping[str, Any]) -> MemoryContext:
        return MemoryContext(
            state=MemoryState.CONFIRMED_MUTATION,
            owner_id=owner_id,
            task_id=task_id,
            items=(dict(mutation),),
            provenance=({"source": "test-double"},),
            message="test-only confirmed mutation; not canonical memory",
            contract_version=self.contract_version,
        )

    def history_evidence(self, owner_id: str, task_id: str, reference: str) -> MemoryContext:
        return MemoryContext(
            state=MemoryState.HISTORY_EVIDENCE,
            owner_id=owner_id,
            task_id=task_id,
            provenance=({"source": "test-double", "reference": reference},),
            message="test-only history/evidence",
            contract_version=self.contract_version,
        )

    def governance(self, action: ActionRequest | ActionSnapshot) -> GovernanceDecision:
        if self.contradictory:
            state = GovernanceState.CONTRADICTORY
            reason = "test-only contradictory governance"
        elif self.allow_side_effects:
            state = GovernanceState.ALLOW
            reason = "test-only explicit allow; not MEMORY//OS"
        else:
            state = GovernanceState.DENY
            reason = "test-only governance denial"
        return GovernanceDecision(
            state=state,
            decision_id="test-double-" + action.action_id,
            owner_id=action.owner_id,
            action_digest=action.digest(),
            reason=reason,
            contract_version=self.contract_version,
        )
