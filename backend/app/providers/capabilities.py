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

import re
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


# ------------------------------------------------------------- tool families
# V8.5.1 — capability families for tool-surface narrowing.
#
# A REAL small model (llama3.2:3b) offered ~40 tool schemas at once selects the
# wrong cognitive subsystem for questions a human finds unambiguous ("What
# missions am I working on?" -> get_attention_state). The correction is
# architectural, not semantic: BEFORE the model is asked to choose, the
# existing CapabilityRouter narrows the advertised tool surface to the
# capability FAMILIES that are plausibly relevant to the turn.
#
# Hard rules (enforced by tests):
#   * The router selects FAMILIES, never a final tool. Every selected family
#     contributes its complete toolset, so the model always has a genuine
#     choice and multi-tool reasoning within and across families survives.
#   * Cross-cutting families are never dropped: memory tools are always
#     offered, because the system prompt requires search_memory before
#     answering questions about the user.
#   * No signal, or signals spanning most families, means NO narrowing: the
#     full surface is offered (fail open). Narrowing is an optimisation of
#     what is advertised, never a gate on what may execute — the execution
#     registry in tools_node keeps every tool.
#   * The final tool choice remains a genuine model decision: the narrowed
#     surface is bound via bind_tools and the model's own tool_calls produce
#     the TOOL_DECISION trace entries, exactly as before.
@dataclass(frozen=True)
class ToolFamily:
    """One cognitive capability family and the honest signals that suggest it.

    `signals` are conservative word-boundary regexes evaluated against the
    user's message. They activate a FAMILY (a set of related tools), never an
    individual tool — the distinction that keeps this a surface-narrowing
    stage rather than keyword tool dispatch.
    """

    name: str
    description: str
    tools: tuple[str, ...]
    signals: tuple[str, ...] = ()
    focus_kinds: tuple[str, ...] = ()


TOOL_FAMILIES: tuple[ToolFamily, ...] = (
    ToolFamily(
        name="memory",
        description="Long-term memory: search, save, update, delete, "
                    "consolidate. Cross-cutting; always offered.",
        tools=("search_memory", "save_memory", "update_memory",
               "delete_memory", "consolidate_memory"),
        signals=(r"\bremember\b", r"\bmemor(?:y|ies)\b", r"\bforget\b",
                 r"\bprefer(?:ence)?s?\b", r"\babout me\b", r"\bknow\b"),
        focus_kinds=("memory",),
    ),
    ToolFamily(
        name="missions",
        description="Mission registry and current focus: reading missions, "
                    "lifecycle actions, steps, blockers, what is pending.",
        tools=("list_missions", "get_mission", "create_mission",
               "pause_mission", "resume_mission", "complete_mission",
               "abandon_mission", "update_mission_state", "add_mission_step",
               "complete_mission_step", "get_mission_blockers",
               "get_current_focus"),
        signals=(r"\bmissions?\b", r"\bworking on\b", r"\bnext step\b",
                 r"\bresume\b", r"\bunpause\b", r"\bpause\b", r"\bcontinue\b",
                 r"\bcarry on\b", r"\bon hold\b", r"\bcomplete\b",
                 r"\bfinish(?:ed)?\b", r"\babandon\b", r"\bblock(?:ed|ers?)\b",
                 r"\bpending\b", r"\bleave off\b", r"\bobjectives?\b",
                 r"\btrack\b"),
        focus_kinds=("mission",),
    ),
    ToolFamily(
        name="world",
        description="World model: projects, people, risks, constraints, "
                    "goals and recorded changes to them.",
        tools=("get_world_state", "get_world_changes"),
        signals=(r"\bprojects?\b", r"\bpeople\b", r"\brisks?\b",
                 r"\bconstraints?\b", r"\bgoals?\b", r"\bwhat changed\b"),
        focus_kinds=("entity",),
    ),
    ToolFamily(
        name="learned",
        description="Learned knowledge: skills, principles, experiences, "
                    "their evidence, and explicit corrections.",
        tools=("list_learned", "list_experiences", "inspect_learned",
               "correct_learned"),
        signals=(r"\bskills?\b", r"\bprinciples?\b", r"\blearn(?:ed|t)?\b",
                 r"\bexperiences?\b", r"\bevidence\b", r"\bstop using\b",
                 r"\bconfidence\b", r"\breputation\b"),
        focus_kinds=("skill", "principle", "experience"),
    ),
    ToolFamily(
        name="explanation",
        description="Explanation engine: WHY / WHY_NOT / WHY_NOW / "
                    "WHAT_CHANGED / WHAT_EVIDENCE / WHAT_ALTERNATIVES / "
                    "WHAT_CAUSED_CHANGE from canonical records.",
        tools=("explain_cognition", "explain"),
        signals=(r"\bwhy\b", r"\bwhy not\b", r"\bwhy now\b",
                 r"\bwhat changed\b", r"\bwhat evidence\b", r"\bbased on\b",
                 r"\bwhat alternatives?\b", r"\bwhat caused\b", r"\bexplain\b",
                 r"\breasons?\b"),
        focus_kinds=("decision",),
    ),
    ToolFamily(
        name="awareness",
        description="Predictions, background attention findings, historical "
                    "state and what-if simulation.",
        tools=("get_predictions", "get_attention_state",
               "get_historical_state", "simulate_scenario"),
        signals=(r"\bpredict(?:ions?)?\b", r"\bexpect(?:ing)?\b",
                 r"\banything i should know\b", r"\bshould i (?:know|be aware)\b",
                 r"\bwhat if\b", r"\bsimulate\b", r"\blast (?:week|month)\b",
                 r"\bback then\b", r"\bhistorical\b",
                 r"\bwhat did you know\b"),
    ),
    ToolFamily(
        name="portability",
        description="Data portability: export packages, import validation, "
                    "restore planning/application, restore history.",
        tools=("start_export", "inspect_export", "validate_import",
               "dry_run_restore", "inspect_restore_conflicts",
               "restore_selected", "inspect_restore_history"),
        signals=(r"\bexports?\b", r"\bimports?\b", r"\brestores?\b",
                 r"\bbackups?\b", r"\bpackages?\b", r"\bportab\w*\b"),
        focus_kinds=("export", "import"),
    ),
    ToolFamily(
        name="research",
        description="Connected research: sessions, explicit URL fetches, "
                    "evidence and derived claims.",
        tools=("start_research", "fetch_research_source", "list_research",
               "inspect_research", "inspect_research_evidence",
               "inspect_research_claims"),
        signals=(r"\bresearch\b", r"https?://", r"\burls?\b", r"\bsources?\b",
                 r"\bclaims?\b", r"\bfetch\b"),
        focus_kinds=("research",),
    ),
)

# Families offered on EVERY narrowed surface, because the product contract
# depends on them regardless of topic (search_memory before user questions).
ALWAYS_OFFERED_FAMILIES: tuple[str, ...] = ("memory",)

# When signals activate more than this many families the message is broad, and
# narrowing would not meaningfully reduce the surface — fail open instead.
MAX_NARROWED_FAMILIES = 4


@dataclass
class ToolSurfaceDecision:
    """The advertised tool surface for one turn, with an auditable reason.

    `allowed` lists tool NAMES the model will see. It never restricts
    execution: tools_node keeps the full registry, so even an out-of-surface
    call by the model would still execute genuinely.
    """

    families: list[str]
    allowed: list[str]
    narrowed: bool
    reason: str
    signals: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"families": self.families, "allowed": self.allowed,
                "narrowed": self.narrowed, "reason": self.reason,
                "signals": self.signals}


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

    # ------------------------------------------------- V8.5.1 tool surface
    def tool_surface(self, message: str,
                     focus_kinds: tuple[str, ...] | list[str] = (),
                     ) -> ToolSurfaceDecision:
        """
        Decide which capability FAMILIES to advertise to the model this turn.

        This narrows the tool surface the model chooses from; it never chooses
        a tool. Rules:

          * A family is activated by an explicit signal in the message or by a
            live conversational focus of a kind it owns.
          * Families in ALWAYS_OFFERED_FAMILIES are always included.
          * No activated family, or more than MAX_NARROWED_FAMILIES activated,
            means no narrowing: the full surface is offered (fail open).

        The decision is honest and auditable: `signals` records exactly which
        pattern activated each family.
        """
        text = (message or "").lower()
        focus = {str(k).lower() for k in (focus_kinds or ())}

        hits: dict[str, list[str]] = {}
        for family in TOOL_FAMILIES:
            matched: list[str] = []
            for pattern in family.signals:
                if re.search(pattern, text):
                    matched.append(pattern)
            for kind in family.focus_kinds:
                if kind in focus:
                    matched.append(f"focus:{kind}")
            if matched:
                hits[family.name] = matched

        all_names = [f.name for f in TOOL_FAMILIES]
        all_tools = [t for f in TOOL_FAMILIES for t in f.tools]

        activated = [name for name in all_names if name in hits]
        substantive = [n for n in activated if n not in ALWAYS_OFFERED_FAMILIES]

        if not substantive:
            return ToolSurfaceDecision(
                families=all_names, allowed=all_tools, narrowed=False,
                reason="No capability signal detected; offering the full "
                       "tool surface so the model is not blinded.",
                signals=hits)
        if len(substantive) > MAX_NARROWED_FAMILIES:
            return ToolSurfaceDecision(
                families=all_names, allowed=all_tools, narrowed=False,
                reason=f"{len(substantive)} families signalled — the message "
                       "is broad, so narrowing would not help; offering the "
                       "full tool surface.",
                signals=hits)

        selected = [name for name in all_names
                    if name in activated or name in ALWAYS_OFFERED_FAMILIES]
        by_name = {f.name: f for f in TOOL_FAMILIES}
        allowed = [t for name in selected for t in by_name[name].tools]
        return ToolSurfaceDecision(
            families=selected, allowed=allowed, narrowed=True,
            reason="Narrowed to capability families "
                   f"{', '.join(selected)} from explicit signals; the model "
                   "makes the final tool choice within this surface.",
            signals=hits)
