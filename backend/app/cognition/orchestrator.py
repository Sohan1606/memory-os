"""
Cognitive orchestrator.

Runs one conversational turn through the full cognitive loop and returns a
structured trace. Everything it reports is derived from work that actually
happened during the turn - if a stage produced nothing, it says so.

    perceive -> intent/need -> world -> retrieve/arbitrate -> predict
             -> attention -> authorize -> respond -> learn
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..agent.execution import TraceRecorder
from ..providers.capabilities import CapabilityRouter
from .arbitration import ArbiterV2
from .autonomy import AttentionEngine, AutonomyGovernor, TrustModel
from .causality import CausalGraph, DecisionLog
from .context_builder import ContextBuilder
from .continuity import ContinuityEngine
from .events import EventBus
from .focus import FocusTracker
from .user_control import CognitiveController

log = logging.getLogger(__name__)
from .influence import InfluenceLedger
from .intent import IntentEngine
from .intent_v2 import IntentEvolution, NeedDetector
from .health import MemoryHealthEngine
from .llm_extract import LLMExtractor
from .perception import PerceptionEngine
from .learning import LearningEngine
from .policy_engine import CognitivePolicyEngine
from .prediction import PredictionEngine
from .reputation import MemoryArbiter, ReputationStore
from .sandbox import Sandbox
from .self_model import Continuity, RecoveryManager, SelfModel
from .trust_v2 import CapabilityTrust
from .world import WorldModel


class Cognition:
    """Composition root for every v8 / v8.2 cognitive subsystem."""

    def __init__(self, db, memory, runtime) -> None:
        self.db = db
        self.memory = memory
        self.runtime = runtime
        self.bus = EventBus(db)
        self.world = WorldModel(db, self.bus)
        self.intent = IntentEngine(db, self.bus)
        self.predictions = PredictionEngine(db, self.bus)
        self.causal = CausalGraph(db, self.bus)
        self.decisions = DecisionLog(db, self.bus, self.causal)
        self.trust = TrustModel(db, self.bus)
        self.autonomy = AutonomyGovernor(db, self.bus, self.trust)
        self.attention = AttentionEngine(db, self.bus)
        self.reputation = ReputationStore(db, self.bus)
        # V8.1 arbiter kept for backwards compatibility; V8.2 uses ArbiterV2.
        self.arbiter = MemoryArbiter(self.reputation)
        self.learning = LearningEngine(db, self.bus)
        self.sandbox = Sandbox(db, self.bus, self.world)
        self.extractor = LLMExtractor(runtime.provider,
                                      lock=getattr(runtime, "llm_lock", None))
        self.perception = PerceptionEngine(
            self.bus, transcriber=getattr(runtime, "transcriber", None),
            provider=runtime.provider)
        self.health = MemoryHealthEngine(runtime.memory, self.reputation, self.bus)
        self.self_model = SelfModel(runtime)
        # V8.1 Continuity retained (its `resume` shape is part of the API
        # contract); V8.2 adds the selective ContinuityEngine on top.
        self.continuity_v1 = Continuity(db, self.bus, self.world, self.intent,
                                        self.predictions)
        self.recovery = RecoveryManager(self.bus)

        # ------------------------------------------------------ v8.2 additions
        self.router = CapabilityRouter(
            runtime.provider,
            embeddings_available_fn=lambda: bool(
                getattr(runtime.vectors, "available", False)))
        self.arbiter_v2 = ArbiterV2(db, self.bus, self.reputation)
        self.influence = InfluenceLedger(db, self.bus, self.reputation,
                                         self.causal)
        self.policy = CognitivePolicyEngine(db, self.bus)
        self.capability_trust = CapabilityTrust(db, self.bus)
        self.intent_evolution = IntentEvolution(db, self.bus, self.intent)
        self.needs = NeedDetector(db, self.bus)
        self.focus = FocusTracker(db, self.bus)
        # §19: natural-language control over the system's own cognition.
        self.control = CognitiveController(
            db, self.bus, memory, self.focus, self.policy, self.arbiter_v2,
            self.influence, self.reputation)
        self.continuity = ContinuityEngine(db, self.bus, self.world, self.intent,
                                           self.predictions)
        self.context = ContextBuilder(self)
        self.traces = TraceRecorder(db, self.bus)

    # ------------------------------------------------------------ the turn
    def process_turn(self, user_id: str, message: str, *,
                     conversation_id: str | None = None,
                     correlation_id: str | None = None) -> dict[str, Any]:
        """
        Run the cognitive loop for one user message.

        This runs alongside the agent - it does not generate the reply. It builds
        the understanding that the reply and the Observatory are based on.

        v8.2: the turn additionally routes by capability, applies explicit user
        behavioural instructions, records intent transitions, resolves focused
        object references and assembles the canonical context bundle.
        """
        cid = correlation_id or f"turn_{uuid.uuid4().hex[:12]}"
        self.bus.emit(user_id, "conversation.message", message[:200],
                      subject_kind="conversation", subject_id=conversation_id or "-",
                      correlation_id=cid)

        # --- v8.2: how will this turn actually execute? ---------------------
        route = self.router.route("memory_retrieval")
        self.bus.emit(
            user_id,
            "routing.selected" if not route.degraded else (
                "routing.not_configured" if route.mode == "NOT_CONFIGURED"
                else "routing.degraded"),
            f"Execution mode: {route.mode}", subject_kind="routing",
            subject_id=route.task, correlation_id=cid, payload=route.as_dict())

        # --- v8.2: explicit behavioural instructions ("be brief", "stop
        # interrupting") update policy through the same cognitive system. ---
        policy_changes = self.policy.apply_utterance(user_id, message,
                                                     correlation_id=cid)

        # --- v8.2: does "that memory" refer to something on screen? ---------
        reference = self.focus.resolve(user_id, message, correlation_id=cid)

        # --- v8.2 §19: an explicit cognitive command ("forget that", "why do
        # you believe that?") is executed here, against the real subsystems.
        # Ordinary conversation returns None and changes nothing.
        try:
            control = self.control.handle(
                user_id, message, session_id=conversation_id or "default",
                correlation_id=cid)
        except Exception as exc:  # pragma: no cover - never break a turn
            log.warning("Cognitive command failed: %s", exc)
            control = None

        # --- understanding -------------------------------------------------
        # The deterministic engine always runs: it is fast, predictable and
        # test-covered. When a real LLM is available its structured extraction is
        # merged on top, so model output can only ADD understanding, never
        # regress below the rule-based baseline.
        need = self.needs.detect(user_id, message,
                                 recent_needs=self.needs.recent(user_id, 3),
                                 correlation_id=cid)
        intent_report = self.intent_evolution.observe(user_id, message,
                                                      correlation_id=cid)
        intent = intent_report.get("current_intent")
        entities = self.world.observe(user_id, message, correlation_id=cid)

        extraction = self.extractor.extract(message)
        understanding = "deterministic"
        if extraction.available:
            understanding = "model-assisted"
            self.bus.emit(user_id, "extraction.completed",
                          "Model-assisted understanding of this message",
                          subject_kind="extraction", subject_id=cid,
                          correlation_id=cid,
                          payload={"entities": len(extraction.entities),
                                   "memories": len(extraction.memories),
                                   "model": self.extractor.status().get("model")})

            # A model-detected need only wins when the rules were unsure.
            if extraction.need and need.get("confidence", 0) < 0.6:
                need = {**extraction.need, "source": "llm"}

            if extraction.intent and not intent:
                intent = self.intent.record(
                    user_id, extraction.intent["goal"],
                    confidence=extraction.intent["confidence"],
                    correlation_id=cid, source="llm")

            for candidate in extraction.entities:
                if any(e["label"].lower() == candidate["label"].lower()
                       for e in entities):
                    continue
                try:
                    entities.append(self.world.upsert(
                        user_id, candidate["kind"], candidate["label"],
                        confidence=candidate["confidence"],
                        correlation_id=cid))
                except ValueError:
                    continue

        retrieved = self._retrieve(user_id, message, cid)

        # --- v8.2 arbitration: evidence-weighted, persisted, explainable -----
        arbitration = None
        if retrieved["candidates"]:
            arbitration = self.arbiter_v2.arbitrate(
                user_id, retrieved["candidates"], query=message,
                correlation_id=cid)

        # --- v8.2 influence: only the memory that actually won is recorded as
        # influencing this turn. Retrieval alone is never influence. -----------
        influences: list[dict[str, Any]] = []
        if arbitration and arbitration.get("winner"):
            winner = arbitration["winner"]
            try:
                influences.append(self.influence.record_influence(
                    user_id, winner["memory_id"],
                    influenced_kind="decision",
                    influenced_id=conversation_id or cid,
                    how=(f"Won arbitration for this turn and entered the "
                         f"response context (score {winner['score']})."),
                    weight=min(1.0, float(winner["score"])),
                    correlation_id=cid))
            except Exception:  # influence tracking must not break the turn
                pass

        # --- v8.2 continuity: what from earlier still matters here? ----------
        continuity_items = self.continuity.relevant_to(user_id, message)

        # --- v8.2 canonical context assembly ---------------------------------
        context_bundle = self.context.build(
            user_id, message, retrieved=retrieved["candidates"],
            thread_id=conversation_id, correlation_id=cid)
        self.bus.emit(
            user_id,
            "context.degraded" if context_bundle.degraded else (
                "context.truncated" if context_bundle.truncated
                else "context.assembled"),
            f"Assembled {len(context_bundle.items)} context item(s)",
            subject_kind="context", subject_id=cid, correlation_id=cid,
            payload={"items": len(context_bundle.items),
                     "truncated": context_bundle.truncated,
                     "degraded": context_bundle.degraded,
                     "unavailable": [u["source"] for u in
                                     context_bundle.unavailable]})

        predictions = self.predictions.assess_world(user_id, self.world,
                                                    correlation_id=cid)

        # Attention: is anything worth raising unprompted right now?
        attention = None
        at_risk = self.world.list(user_id, state="at_risk")
        if at_risk:
            attention = self.attention.consider(
                user_id, f"{len(at_risk)} tracked item(s) are at risk",
                importance=0.8, urgency=0.6,
                confidence=min(0.9, 0.4 + 0.1 * len(at_risk)),
                correlation_id=cid)

        # Capability trust: retrieval either worked or it did not.
        try:
            self.capability_trust.record(
                user_id, "memory_retrieval", not retrieved["degraded"],
                task_class="conversation", correlation_id=cid,
                detail="Retrieval during a conversational turn.")
        except Exception:
            pass

        return {
            "correlation_id": cid,
            "need": need,
            "intent": intent,
            "world_entities": entities,
            "retrieval": retrieved,
            "arbitration": arbitration,
            "predictions": predictions,
            "attention": attention,
            "autonomy_level": self.autonomy.level(user_id),
            "understanding": understanding,
            "extraction": extraction.as_dict(),
            # ------------------------------------------------------- v8.2
            "routing": route.as_dict(),
            "intent_transition": {
                "previous": intent_report.get("previous_intent"),
                "current": intent_report.get("current_intent"),
                "changed": intent_report.get("changed"),
                "changed_because": intent_report.get("changed_because"),
                "confidence": intent_report.get("confidence"),
                "uncertainty": intent_report.get("uncertainty"),
                "note": intent_report.get("note"),
                "emerging": intent_report.get("emerging_intent"),
            },
            "policy_changes": policy_changes,
            "reference": reference,
            "control": control,
            "influences": influences,
            "continuity": continuity_items,
            "context": context_bundle.as_dict(),
            "capabilities": self.router.report().as_dict(),
        }

    def _retrieve(self, user_id: str, message: str, cid: str) -> dict[str, Any]:
        """Retrieve memories and record that retrieval as real reputation evidence."""
        def primary() -> list[Any]:
            return self.memory.search(user_id, message, top_k=5)

        outcome = self.recovery.attempt(user_id, "memory retrieval", primary,
                                        correlation_id=cid)
        results = outcome["result"] or []
        candidates: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for r in results:
            mem = r.memory
            rep = self.reputation.get(user_id, mem.id)
            lifecycle = str(rep.get("lifecycle") or "candidate")
            entry = {
                "id": mem.id, "content": mem.content,
                "confidence": float(getattr(mem, "confidence", 0.7) or 0.7),
                "updated_at": getattr(mem, "updated_at", None),
                "source": getattr(mem, "source", "conversation"),
                "category": getattr(mem, "category", None),
                "status": getattr(mem, "status", "active"),
                "score": round(float(getattr(r, "score", 0.0) or 0.0), 4),
                # v8.2: retrieval explanations come from real retrieval
                # metadata, never from invented scores.
                "reasons": list(getattr(r, "reasons", []) or []),
                "semantic": round(float(getattr(r, "semantic", 0.0) or 0.0), 4),
                "keyword": round(float(getattr(r, "keyword", 0.0) or 0.0), 4),
                "strength": getattr(r, "strength", None),
                "lifecycle": lifecycle,
                "reputation": rep.get("reputation"),
            }
            # v8.2 §7: a quarantined or retired memory must not silently
            # re-enter normal retrieval. It is reported as excluded instead.
            if lifecycle in ("retired", "quarantined"):
                entry["excluded_because"] = (
                    f"Memory lifecycle is '{lifecycle}'.")
                excluded.append(entry)
                continue
            self.reputation.record_retrieval(user_id, mem.id)
            candidates.append(entry)

        if candidates:
            self.bus.emit(user_id, "memory.retrieved",
                          f"Recalled {len(candidates)} relevant memory(ies)",
                          subject_kind="memory", subject_id=candidates[0]["id"],
                          correlation_id=cid,
                          payload={"count": len(candidates),
                                   "ids": [c["id"] for c in candidates]})

        return {"candidates": candidates, "count": len(candidates),
                "excluded": excluded, "excluded_count": len(excluded),
                "degraded": outcome["degraded"],
                "mode": getattr(self.memory, "mode", None)}

    # -------------------------------------------------------------- surfaces
    def why(self, user_id: str, subject_kind: str, subject_id: str) -> dict[str, Any]:
        """'Why do you think that?' - the event history of a single object."""
        events = self.bus.for_subject(subject_kind, subject_id)
        rep = (self.reputation.get(user_id, subject_id)
               if subject_kind == "memory" else None)
        if not events:
            return {"subject": {"kind": subject_kind, "id": subject_id},
                    "events": [], "reputation": rep,
                    "explanation": "INSUFFICIENT EVIDENCE — I have no recorded history "
                                   "for that object."}
        first, last = events[0], events[-1]
        return {
            "subject": {"kind": subject_kind, "id": subject_id},
            "events": [e.as_dict() for e in events],
            "reputation": rep,
            "explanation": (f"First recorded as '{first.label}' on {first.created_at}; "
                            f"most recently '{last.label}' on {last.created_at}. "
                            f"{len(events)} recorded event(s)."),
        }

    def what_changed(self, user_id: str, since: int | None = None) -> dict[str, Any]:
        events = (self.bus.since(user_id, int(since)) if since is not None
                  else self.bus.recent(user_id, 40))
        changes = [e for e in events if e.type.split(".")[-1] in
                   ("created", "updated", "validated", "weakened", "contradicted",
                    "retired", "changed", "completed", "resolved", "detected")]
        return {"changes": [e.as_dict() for e in changes], "count": len(changes),
                "summary": (f"{len(changes)} change(s) recorded."
                            if changes else "Nothing has changed.")}

    def status(self, user_id: str) -> dict[str, Any]:
        """One structured snapshot powering the Observatory."""
        return {
            "autonomy": {"level": self.autonomy.level(user_id),
                         "trust": self.trust.all(user_id)},
            "world": self.world.summary(user_id),
            "intent": self.intent.current(user_id),
            "predictions": self.predictions.accuracy(user_id),
            "attention": self.attention.precision(user_id),
            "self": self.self_model.health_summary(),
            "policies": self.learning.policies(user_id),
            "events": self.bus.counts(user_id),
            # ------------------------------------------------------- v8.2
            "capabilities": self.router.report().as_dict(),
            "routing": self.router.routing_table(),
            "cognitive_policy": self.policy.list(user_id),
            "capability_trust": self.capability_trust.all(user_id),
            "continuity": self.continuity.open_items(user_id, limit=10),
            "need_accuracy": self.needs.accuracy(user_id),
        }

    # ---------------------------------------------------- v8.2 explanations
    def why_now(self, user_id: str, subject_kind: str,
                subject_id: str) -> dict[str, Any]:
        """
        'Why now?' — what made this relevant at this moment, from real events.
        """
        events = self.bus.for_subject(subject_kind, subject_id)
        if not events:
            return {"subject": {"kind": subject_kind, "id": subject_id},
                    "explanation": ("INSUFFICIENT EVIDENCE — nothing has been "
                                    "recorded about that object.")}
        latest = events[-1]
        turn = (self.bus.for_correlation(latest.correlation_id)
                if latest.correlation_id else [])
        triggers = [e for e in turn
                    if e.type in ("conversation.message", "memory.retrieved",
                                  "arbitration.resolved", "intent.changed",
                                  "world.updated", "continuity.resumed")]
        return {
            "subject": {"kind": subject_kind, "id": subject_id},
            "most_recent": latest.as_dict(),
            "turn_events": [e.as_dict() for e in triggers],
            "explanation": (
                f"The most recent change was '{latest.label}' at "
                f"{latest.created_at}. It happened during a turn that also "
                f"recorded {len(triggers)} related event(s)."
                if triggers else
                f"The most recent change was '{latest.label}' at "
                f"{latest.created_at}. No other correlated events were "
                "recorded in that turn."),
        }

    def explain_memory_use(self, user_id: str, memory_id: str) -> dict[str, Any]:
        """
        'Why did you use this memory?' — arbitration evidence plus influence and
        outcome history. Every claim is read back from stored records.
        """
        arbitrations = self.arbiter_v2.for_memory(memory_id, limit=5)
        impact = self.influence.impact(user_id, memory_id)
        rep = self.reputation.get(user_id, memory_id)

        if not arbitrations and impact["influence_count"] == 0:
            return {"memory_id": memory_id, "arbitrations": [],
                    "impact": impact, "reputation": rep,
                    "explanation": ("INSUFFICIENT EVIDENCE — this memory has not "
                                    "been recorded as winning arbitration or "
                                    "influencing a decision.")}

        wins = [a for a in arbitrations if a.get("winner_id") == memory_id]
        lines: list[str] = []
        if wins:
            lines.append(wins[0]["reason"])
        elif arbitrations:
            lines.append(
                f"This memory took part in {len(arbitrations)} arbitration(s) "
                "but did not win the most recent one.")
        lines.append(impact["summary"])
        lines.append(f"Current reputation: {rep['reputation']} "
                     f"(confidence and reputation are tracked separately).")
        return {"memory_id": memory_id, "arbitrations": arbitrations,
                "impact": impact, "reputation": rep,
                "explanation": " ".join(lines)}
