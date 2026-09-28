"""Planner and specialist abstractions with no side-effect authority."""

from __future__ import annotations

from abc import ABC, abstractmethod
import uuid

from .capabilities import CapabilityRegistry
from .contracts import Context, Intent, Plan, PlanStep, Proposal


class Planner(ABC):
    @abstractmethod
    def create_plan(self, intent: Intent, context: Context, registry: CapabilityRegistry) -> Plan:
        raise NotImplementedError


class DeterministicPlanner(Planner):
    """Creates one-step plans from already structured intent.

    It never resolves authorization and never calls a capability.
    """

    def create_plan(self, intent: Intent, context: Context, registry: CapabilityRegistry) -> Plan:
        plan_id = str(uuid.uuid4())
        if intent.capability_id is None:
            return Plan(plan_id, intent.task_id, intent.goal, (), assumptions=("capability resolution required",))
        operation = str(intent.parameters.get("operation") or {
            "local.time": "read_current_time",
            "device.metadata": "inspect_device_metadata",
        }.get(intent.capability_id, ""))
        resolution = registry.resolve(intent.capability_id, operation or None)
        if not resolution.available or resolution.manifest is None:
            return Plan(plan_id, intent.task_id, intent.goal, (), assumptions=("capability unavailable",))
        step = PlanStep(
            step_id=str(uuid.uuid4()),
            capability_id=resolution.manifest.capability_id,
            operation=operation,
            parameters={k: v for k, v in intent.parameters.items() if k != "operation"},
            risk=resolution.manifest.risk,
            purpose=intent.goal,
            expected_effect=resolution.manifest.description,
            verification=resolution.manifest.verification,
        )
        return Plan(plan_id, intent.task_id, intent.goal, (step,))


class SpecialistAgent(ABC):
    specialist_id = "abstract-specialist"

    @abstractmethod
    def review(self, plan: Plan, context: Context) -> tuple[str, ...]:
        raise NotImplementedError


class PlanBoundaryReviewer(SpecialistAgent):
    """A deterministic reviewer that can flag planning problems only."""

    specialist_id = "plan-boundary-reviewer"

    def review(self, plan: Plan, context: Context) -> tuple[str, ...]:
        findings: list[str] = []
        if len(plan.steps) > 4:
            findings.append("plan_too_large_for_phase2")
        seen: set[str] = set()
        for step in plan.steps:
            if step.step_id in seen:
                findings.append("duplicate_step_id")
            seen.add(step.step_id)
            if step.capability_id == "filesystem.approved" and "path" not in step.parameters:
                findings.append("filesystem_path_missing")
        return tuple(findings)


class SpecialistRegistry:
    def __init__(self, specialists: tuple[SpecialistAgent, ...] = ()) -> None:
        self._specialists = specialists

    def review(self, plan: Plan, context: Context) -> tuple[str, ...]:
        findings: list[str] = []
        for specialist in self._specialists:
            findings.extend(specialist.review(plan, context))
        return tuple(findings)
