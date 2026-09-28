"""Provider/model abstraction.

Provider output is always an untrusted proposal. It cannot call a tool or
return a policy decision.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import uuid
from typing import Any, Mapping

from .contracts import Context, Intent, Plan, Proposal, RiskLevel


class ProviderUnavailable(RuntimeError):
    pass


class ModelProvider(ABC):
    provider_id = "abstract"
    version = "1.0.0"

    @abstractmethod
    def propose_intent(self, user_text: str, context: Context) -> Intent:
        raise NotImplementedError

    @abstractmethod
    def propose_additional_actions(self, user_text: str, context: Context) -> tuple[Proposal, ...]:
        raise NotImplementedError


class LocalModelProvider(ModelProvider):
    """Interface for a future local model; unavailable until supplied."""

    provider_id = "local-model-interface"

    def propose_intent(self, user_text: str, context: Context) -> Intent:
        raise ProviderUnavailable("no local model configured")

    def propose_additional_actions(self, user_text: str, context: Context) -> tuple[Proposal, ...]:
        raise ProviderUnavailable("no local model configured")


class DeterministicProvider(ModelProvider):
    """Small deterministic parser for the safe vertical slice.

    It is intentionally narrow. Its output remains untrusted and must pass the
    registry, policy, authorization, Kernel, and verification path.
    """

    provider_id = "deterministic-provider"
    version = "1.0.0"

    def propose_intent(self, user_text: str, context: Context) -> Intent:
        text = user_text.strip()
        lower = text.lower()
        intent_id = str(uuid.uuid4())
        task_id = context.task_id
        base = dict(
            intent_id=intent_id,
            task_id=task_id,
            owner_id=context.owner_id,
            user_text=text,
            source="user",
            model_provider=f"{self.provider_id}@{self.version}",
        )
        if not text:
            return Intent(**base, goal="", capability_id=None, parameters={}, confidence=0.0, ambiguous=True, missing_fields=("request",))
        if "what else" in lower or "what can be done" in lower:
            return Intent(**base, goal=text, capability_id=None, parameters={}, confidence=0.95)
        if lower in {"time", "what time is it", "read current time", "current time"}:
            return Intent(**base, goal="read current time", capability_id="local.time", parameters={}, confidence=0.99)
        if lower in {"device metadata", "inspect device", "inspect device metadata"}:
            return Intent(**base, goal="inspect bounded device metadata", capability_id="device.metadata", parameters={}, confidence=0.99)
        match = re.fullmatch(r"inspect directory (.+)", text, flags=re.IGNORECASE)
        if match:
            return Intent(**base, goal="inspect approved directory", capability_id="filesystem.approved", parameters={"operation": "inspect_directory", "path": match.group(1).strip()}, confidence=0.99)
        match = re.fullmatch(r"create directory (.+)", text, flags=re.IGNORECASE)
        if match:
            return Intent(**base, goal="create approved directory", capability_id="filesystem.approved", parameters={"operation": "create_directory", "path": match.group(1).strip()}, confidence=0.99)
        match = re.fullmatch(r"read text file (.+)", text, flags=re.IGNORECASE)
        if match:
            return Intent(**base, goal="read approved text file", capability_id="filesystem.approved", parameters={"operation": "read_text_file", "path": match.group(1).strip()}, confidence=0.99)
        match = re.fullmatch(r"create text file (\S+)(?:\s+with\s+(.*))?", text, flags=re.IGNORECASE)
        if match:
            return Intent(**base, goal="create approved text file", capability_id="filesystem.approved", parameters={"operation": "create_text_file", "path": match.group(1), "content": match.group(2) or ""}, confidence=0.99)
        return Intent(**base, goal=text, capability_id=None, parameters={}, confidence=0.2, ambiguous=True, missing_fields=("known-capability",))

    def propose_additional_actions(self, user_text: str, context: Context) -> tuple[Proposal, ...]:
        lower = user_text.lower()
        if "meeting" not in lower:
            return ()
        return (
            Proposal(str(uuid.uuid4()), context.task_id, "Review relevant documents", "Suggestion only; no document access was performed.", None, risk=RiskLevel.R0),
            Proposal(str(uuid.uuid4()), context.task_id, "Check calendar", "Suggestion only; no calendar capability is installed.", None, risk=RiskLevel.R0),
            Proposal(str(uuid.uuid4()), context.task_id, "Prepare talking points", "Suggestion only; no document generation was performed.", None, risk=RiskLevel.R1),
            Proposal(str(uuid.uuid4()), context.task_id, "Create a reminder", "Suggestion only; no reminder capability is installed.", None, risk=RiskLevel.R1),
        )
