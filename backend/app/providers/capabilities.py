"""
V8.2 — Model capability model and task-aware execution routing.

Design rules (§3 of the V8.2 brief):

  * A capability is only reported as SUPPORTED when there is a concrete reason
    to believe it: the provider class genuinely implements it AND the resolved
    model is not on a known-unsupported list. We never *infer* a capability
    from a model name we do not recognise — unknown means UNKNOWN, and UNKNOWN
    is treated as unavailable for routing purposes.
  * Routing never silently downgrades. If a task requires a capability the
    active provider cannot supply, the router returns an explicit
    NOT_CONFIGURED / DEGRADED decision naming the missing capability.
  * The deterministic fallback remains a first-class execution mode, not an
    error path.

Nothing in this module calls a model. It only describes and decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- capabilities
GENERATION = "generation"
STRUCTURED_OUTPUT = "structured_output"
TOOL_CALLING = "tool_calling"
VISION = "vision"
EMBEDDINGS = "embeddings"

CAPABILITIES: tuple[str, ...] = (
    GENERATION, STRUCTURED_OUTPUT, TOOL_CALLING, VISION, EMBEDDINGS,
)

# Capability support states. UNKNOWN is deliberately distinct from
# NOT_SUPPORTED: "we have no evidence" is not the same claim as "it cannot".
SUPPORTED = "SUPPORTED"
NOT_SUPPORTED = "NOT_SUPPORTED"
UNKNOWN = "UNKNOWN"

# ---------------------------------------------------------------- execution
# Execution modes a task can be routed to.
MODE_MODEL = "MODEL"                      # plain generation
MODE_MODEL_TOOLS = "MODEL_TOOLS"          # generation + tool loop
MODE_MODEL_STRUCTURED = "MODEL_STRUCTURED"  # schema-constrained extraction
MODE_DETERMINISTIC = "DETERMINISTIC"      # rule engine, no model
MODE_NOT_CONFIGURED = "NOT_CONFIGURED"    # capability genuinely absent

# ---------------------------------------------------------------- task classes
# Each task declares what it REQUIRES and what it can gracefully fall back to.
# `fallback=None` means there is no honest deterministic equivalent, so an
# unavailable capability must surface as NOT_CONFIGURED rather than a guess.
@dataclass(frozen=True)
class TaskSpec:
    name: str
    requires: tuple[str, ...]
    preferred_mode: str
    fallback_mode: str | None
    description: str


TASKS: dict[str, TaskSpec] = {
    "conversation": TaskSpec(
        "conversation", (GENERATION,), MODE_MODEL, MODE_DETERMINISTIC,
        "Compose a normal conversational reply."),
    "memory_retrieval": TaskSpec(
        "memory_retrieval", (GENERATION, TOOL_CALLING), MODE_MODEL_TOOLS,
        MODE_DETERMINISTIC,
        "Answer using long-term memory, calling search tools when useful."),
    "structured_extraction": TaskSpec(
        "structured_extraction", (GENERATION, STRUCTURED_OUTPUT),
        MODE_MODEL_STRUCTURED, MODE_DETERMINISTIC,
        "Extract intent/need/entities/memories against a strict schema."),
    "tool_execution": TaskSpec(
        "tool_execution", (GENERATION, TOOL_CALLING), MODE_MODEL_TOOLS,
        MODE_DETERMINISTIC,
        "Let the model choose and invoke a real tool."),
    "vision": TaskSpec(
        "vision", (VISION,), MODE_MODEL, None,
        "Interpret image content. There is no honest deterministic fallback."),
    "embedding": TaskSpec(
        "embedding", (EMBEDDINGS,), MODE_MODEL, MODE_DETERMINISTIC,
        "Produce vectors for semantic retrieval."),
}


# ------------------------------------------------------------------ known models
# Models we have concrete knowledge about. Anything absent stays UNKNOWN.
# Keys are matched as a prefix against the resolved model tag (lowercased).
#
# This table encodes only *negative* and *confirmed* knowledge that matters for
# routing. It is intentionally small: we would rather say UNKNOWN than assert a
# capability a 0.5B model does not really have.
_KNOWN_MODELS: tuple[tuple[str, dict[str, str]], ...] = (
    # Qwen2.5 instruct series: tool calling is supported from 1.5b upward in
    # practice; the 0.5b tag emits tool syntax unreliably, so we do not claim it.
    ("qwen2.5:0.5b", {TOOL_CALLING: NOT_SUPPORTED, VISION: NOT_SUPPORTED,
                      STRUCTURED_OUTPUT: SUPPORTED}),
    ("qwen2.5", {TOOL_CALLING: SUPPORTED, VISION: NOT_SUPPORTED,
                 STRUCTURED_OUTPUT: SUPPORTED}),
    ("qwen3", {TOOL_CALLING: SUPPORTED, VISION: NOT_SUPPORTED,
               STRUCTURED_OUTPUT: SUPPORTED}),
    ("llama3.2-vision", {TOOL_CALLING: NOT_SUPPORTED, VISION: SUPPORTED,
                         STRUCTURED_OUTPUT: SUPPORTED}),
    ("llama3.1", {TOOL_CALLING: SUPPORTED, VISION: NOT_SUPPORTED,
                  STRUCTURED_OUTPUT: SUPPORTED}),
    ("llama3.2", {TOOL_CALLING: SUPPORTED, VISION: NOT_SUPPORTED,
                  STRUCTURED_OUTPUT: SUPPORTED}),
    ("llava", {TOOL_CALLING: NOT_SUPPORTED, VISION: SUPPORTED,
               STRUCTURED_OUTPUT: UNKNOWN}),
    ("mistral", {TOOL_CALLING: SUPPORTED, VISION: NOT_SUPPORTED,
                 STRUCTURED_OUTPUT: SUPPORTED}),
    ("phi3", {TOOL_CALLING: NOT_SUPPORTED, VISION: NOT_SUPPORTED,
              STRUCTURED_OUTPUT: UNKNOWN}),
    ("gemma", {TOOL_CALLING: NOT_SUPPORTED, VISION: NOT_SUPPORTED,
               STRUCTURED_OUTPUT: UNKNOWN}),
    # OpenAI chat models used by this project genuinely support all three.
    ("gpt-4o", {TOOL_CALLING: SUPPORTED, VISION: SUPPORTED,
                STRUCTURED_OUTPUT: SUPPORTED}),
    ("gpt-4.1", {TOOL_CALLING: SUPPORTED, VISION: SUPPORTED,
                 STRUCTURED_OUTPUT: SUPPORTED}),
)


def known_model_profile(model: str | None) -> dict[str, str]:
    """Return recorded capability knowledge for a model tag, or {} if unknown."""
    if not model:
        return {}
    tag = str(model).strip().lower()
    for prefix, profile in _KNOWN_MODELS:
        if tag.startswith(prefix):
            return dict(profile)
    return {}


@dataclass
class CapabilityReport:
    """What the ACTIVE provider+model can actually do, with a reason for each."""

    provider: str
    model: str | None
    available: bool
    states: dict[str, str] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)

    def state(self, capability: str) -> str:
        return self.states.get(capability, UNKNOWN)

    def supports(self, capability: str) -> bool:
        """True only for an explicit SUPPORTED. UNKNOWN is never good enough."""
        return self.states.get(capability) == SUPPORTED

    def reason(self, capability: str) -> str:
        return self.reasons.get(capability, "No information recorded.")

    def missing(self, required: tuple[str, ...]) -> list[str]:
        return [c for c in required if not self.supports(c)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model,
            "available": self.available,
            "capabilities": [
                {"name": c, "state": self.states.get(c, UNKNOWN),
                 "reason": self.reasons.get(c, "No information recorded.")}
                for c in CAPABILITIES
            ],
            "supported": [c for c in CAPABILITIES if self.supports(c)],
            "unsupported": [c for c in CAPABILITIES
                            if self.states.get(c) == NOT_SUPPORTED],
            "unknown": [c for c in CAPABILITIES
                        if self.states.get(c, UNKNOWN) == UNKNOWN],
        }


def describe(provider, *, embeddings_available: bool = False) -> CapabilityReport:
    """
    Build an honest capability report for a live provider.

    `embeddings_available` is supplied by the runtime because embeddings come
    from the local VectorStore (ONNX), not from the chat provider.
    """
    status = provider.status()
    report = CapabilityReport(provider=status.name, model=status.model,
                              available=status.available)

    # --- embeddings: owned by the vector store, never by the chat model ---
    if embeddings_available:
        report.states[EMBEDDINGS] = SUPPORTED
        report.reasons[EMBEDDINGS] = "Local embedding model is loaded."
    else:
        report.states[EMBEDDINGS] = NOT_SUPPORTED
        report.reasons[EMBEDDINGS] = (
            "No embedding model is loaded; retrieval falls back to keywords.")

    # --- the deterministic provider claims nothing ---
    if status.name == "demo" or not status.available:
        for cap in (GENERATION, STRUCTURED_OUTPUT, TOOL_CALLING, VISION):
            report.states[cap] = NOT_SUPPORTED
            report.reasons[cap] = (
                "No language model is active; the deterministic planner has no "
                "model capabilities." if status.name == "demo"
                else f"Provider unavailable: {status.detail}")
        return report

    # --- a real model is active ---
    report.states[GENERATION] = SUPPORTED
    report.reasons[GENERATION] = f"{status.name} model {status.model} is reachable."

    profile = known_model_profile(status.model)

    for cap in (STRUCTURED_OUTPUT, TOOL_CALLING, VISION):
        if cap in profile:
            report.states[cap] = profile[cap]
            verdict = {SUPPORTED: "is documented to support",
                       NOT_SUPPORTED: "is known NOT to support",
                       UNKNOWN: "has unconfirmed support for"}[profile[cap]]
            report.reasons[cap] = f"Model '{status.model}' {verdict} {cap}."
        else:
            report.states[cap] = UNKNOWN
            report.reasons[cap] = (
                f"Model '{status.model}' is not in the known-capability table, so "
                f"{cap} is UNKNOWN and will not be relied upon.")

    # The provider class itself can veto: a provider that cannot bind tools must
    # never be routed a tool task even if the model could.
    if not status.supports_tool_calling and report.states[TOOL_CALLING] == SUPPORTED:
        report.states[TOOL_CALLING] = NOT_SUPPORTED
        report.reasons[TOOL_CALLING] = (
            f"Provider '{status.name}' does not expose tool binding: {status.detail}")

    return report


@dataclass
class RouteDecision:
    """The chosen execution mode for one task, with an auditable reason."""

    task: str
    mode: str
    degraded: bool
    missing: list[str]
    reason: str
    provider: str
    model: str | None

    @property
    def uses_model(self) -> bool:
        return self.mode in (MODE_MODEL, MODE_MODEL_TOOLS, MODE_MODEL_STRUCTURED)

    def as_dict(self) -> dict[str, Any]:
        return {"task": self.task, "mode": self.mode, "degraded": self.degraded,
                "missing_capabilities": self.missing, "reason": self.reason,
                "provider": self.provider, "model": self.model,
                "uses_model": self.uses_model}


class CapabilityRouter:
    """
    Chooses an execution mode per task from real capability evidence.

    The router is intentionally stateless and cheap: the underlying provider
    status is already cached, and routing must never add latency to a turn.
    """

    def __init__(self, provider, *, embeddings_available_fn=None) -> None:
        self.provider = provider
        self._embeddings_fn = embeddings_available_fn or (lambda: False)

    def report(self) -> CapabilityReport:
        return describe(self.provider,
                        embeddings_available=bool(self._embeddings_fn()))

    def route(self, task: str) -> RouteDecision:
        spec = TASKS.get(task)
        if spec is None:
            raise ValueError(f"Unknown task class: {task!r}")

        rep = self.report()
        missing = rep.missing(spec.requires)

        if not missing:
            return RouteDecision(
                task=task, mode=spec.preferred_mode, degraded=False, missing=[],
                reason=(f"All required capabilities ({', '.join(spec.requires)}) are "
                        f"SUPPORTED by {rep.provider}/{rep.model}."),
                provider=rep.provider, model=rep.model)

        detail = "; ".join(f"{c}={rep.state(c)} — {rep.reason(c)}" for c in missing)

        if spec.fallback_mode is None:
            return RouteDecision(
                task=task, mode=MODE_NOT_CONFIGURED, degraded=True, missing=missing,
                reason=(f"NOT CONFIGURED: '{task}' requires {', '.join(missing)} and "
                        f"there is no honest fallback. {detail}"),
                provider=rep.provider, model=rep.model)

        return RouteDecision(
            task=task, mode=spec.fallback_mode, degraded=True, missing=missing,
            reason=(f"DEGRADED: falling back to {spec.fallback_mode} because "
                    f"{', '.join(missing)} unavailable. {detail}"),
            provider=rep.provider, model=rep.model)

    def routing_table(self) -> list[dict[str, Any]]:
        """Every task and how it would execute right now — used by /api/provider."""
        return [self.route(name).as_dict() for name in TASKS]
