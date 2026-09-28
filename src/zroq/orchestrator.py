"""Core orchestration without execution authority."""

from __future__ import annotations

import uuid

from .action_kernel import ActionKernel
from .capabilities import CapabilityRegistry
from .contracts import ActionRequest, Confirmation, Context, Session, TaskResult, utc_now
from .memory import MemoryOSAdapter
from .planning import DeterministicPlanner, SpecialistRegistry
from .providers import ModelProvider, ProviderUnavailable


class Orchestrator:
    def __init__(
        self,
        memory: MemoryOSAdapter,
        provider: ModelProvider,
        planner: DeterministicPlanner,
        specialists: SpecialistRegistry,
        registry: CapabilityRegistry,
        kernel: ActionKernel,
    ) -> None:
        self.memory = memory
        self.provider = provider
        self.planner = planner
        self.specialists = specialists
        self.registry = registry
        self.kernel = kernel

    def handle(self, user_text: str, session: Session, confirmation: Confirmation | None = None) -> TaskResult:
        task_id = str(uuid.uuid4())
        memory_context = self.memory.read(session.owner_id, task_id, "understand user request")
        context = Context(
            task_id=task_id,
            owner_id=session.owner_id,
            session_id=session.session_id,
            memory=memory_context,
            time=utc_now(),
            labels={"memory_state": memory_context.state.value},
        )
        try:
            intent = self.provider.propose_intent(user_text, context)
            proposals = self.provider.propose_additional_actions(user_text, context)
        except ProviderUnavailable as exc:
            return TaskResult(task_id, "REPORT_UNAVAILABLE", str(exc), context=context)

        if "what else" in user_text.lower() or "what can be done" in user_text.lower() or (proposals and intent.ambiguous):
            return TaskResult(task_id, "SUGGESTIONS", "Suggestions only; no actions were executed.", intent, proposals=proposals, context=context)
        if intent.ambiguous:
            if "known-capability" in intent.missing_fields:
                return TaskResult(task_id, "REPORT_UNAVAILABLE", "No known Phase 2 capability matches that request.", intent=intent, proposals=proposals, context=context)
            return TaskResult(task_id, "ASK", "The request is ambiguous and needs clarification.", intent=intent, proposals=proposals, context=context)

        plan = self.planner.create_plan(intent, context, self.registry)
        findings = self.specialists.review(plan, context)
        if findings:
            return TaskResult(task_id, "REPORT_DENIED", "Plan boundary review failed: " + ", ".join(findings), intent, plan, proposals=proposals, context=context)
        if not plan.steps:
            return TaskResult(task_id, "REPORT_UNAVAILABLE", "No available capability could produce a plan.", intent, plan, proposals=proposals, context=context)

        results = []
        for step in plan.steps:
            resolution = self.registry.resolve(step.capability_id, step.operation)
            if not resolution.available or resolution.manifest is None:
                return TaskResult(task_id, "REPORT_UNAVAILABLE", "Capability is unavailable.", intent, plan, tuple(results), proposals, context)
            action = ActionRequest(
                action_id=str(uuid.uuid4()),
                task_id=task_id,
                owner_id=session.owner_id,
                principal_id=session.principal_id,
                device_id=session.device_id,
                capability_id=step.capability_id,
                capability_version=resolution.manifest.version,
                operation=step.operation,
                parameters=step.parameters,
                purpose=step.purpose,
                risk=step.risk,
                expected_effect=step.expected_effect,
                verification_requirement=step.verification,
                idempotency_key=str(uuid.uuid4()),
                created_at=utc_now(),
                timeout_seconds=resolution.manifest.timeout_seconds,
            )
            result = self.kernel.execute(action, session, confirmation)
            results.append(result)
            if result.status.value in {"DENIED", "AUTHORIZATION_REQUIRED", "FAILED", "UNKNOWN", "CANCELLED", "STOPPED"}:
                status = "REPORT_DENIED" if result.status.value == "DENIED" else result.status.value
                return TaskResult(task_id, status, result.message, intent, plan, tuple(results), proposals, context)
        return TaskResult(task_id, "VERIFIED", "All planned actions reached verified outcome.", intent, plan, tuple(results), proposals, context)
