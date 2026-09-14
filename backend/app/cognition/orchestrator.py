"""
Cognitive orchestrator.

Runs one conversational turn through the full cognitive loop and returns a
structured trace. Everything it reports is derived from work that actually
happened during the turn - if a stage produced nothing, it says so.

    perceive -> intent/need -> world -> retrieve/arbitrate -> predict
             -> attention -> authorize -> respond -> learn
"""

from __future__ import annotations

import uuid
from typing import Any

from .autonomy import AttentionEngine, AutonomyGovernor, TrustModel
from .causality import CausalGraph, DecisionLog
from .events import EventBus
from .intent import IntentEngine
from .health import MemoryHealthEngine
from .llm_extract import LLMExtractor
from .perception import PerceptionEngine
from .learning import LearningEngine
from .prediction import PredictionEngine
from .reputation import MemoryArbiter, ReputationStore
from .sandbox import Sandbox
from .self_model import Continuity, RecoveryManager, SelfModel
from .world import WorldModel


class Cognition:
    """Composition root for every v8 cognitive subsystem."""

    def __init__(self, db, memory, runtime) -> None:
        self.db = db
        self.memory = memory
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
        self.continuity = Continuity(db, self.bus, self.world, self.intent,
                                     self.predictions)
        self.recovery = RecoveryManager(self.bus)

    # ------------------------------------------------------------ the turn
    def process_turn(self, user_id: str, message: str, *,
                     conversation_id: str | None = None) -> dict[str, Any]:
        """
        Run the cognitive loop for one user message.

        This runs alongside the agent - it does not generate the reply. It builds
        the understanding that the reply and the Observatory are based on.
        """
        cid = f"turn_{uuid.uuid4().hex[:12]}"
        self.bus.emit(user_id, "conversation.message", message[:200],
                      subject_kind="conversation", subject_id=conversation_id or "-",
                      correlation_id=cid)

        # --- understanding -------------------------------------------------
        # The deterministic engine always runs: it is fast, predictable and
        # test-covered. When a real LLM is available its structured extraction is
        # merged on top, so model output can only ADD understanding, never
        # regress below the rule-based baseline.
        need = self.intent.detect_need(message)
        intent = self.intent.observe(user_id, message, correlation_id=cid)
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

        self.bus.emit(user_id, "need.detected",
                      f"Need looks like: {need['need']}", subject_kind="need",
                      subject_id=need["need"], correlation_id=cid, payload=need)

        retrieved = self._retrieve(user_id, message, cid)
        arbitration = self.arbiter.arbitrate(user_id, retrieved["candidates"]) \
            if len(retrieved["candidates"]) > 1 else None

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
        }

    def _retrieve(self, user_id: str, message: str, cid: str) -> dict[str, Any]:
        """Retrieve memories and record that retrieval as real reputation evidence."""
        def primary() -> list[Any]:
            return self.memory.search(user_id, message, top_k=5)

        outcome = self.recovery.attempt(user_id, "memory retrieval", primary,
                                        correlation_id=cid)
        results = outcome["result"] or []
        candidates: list[dict[str, Any]] = []
        for r in results:
            mem = r.memory
            self.reputation.record_retrieval(user_id, mem.id)
            candidates.append({
                "id": mem.id, "content": mem.content,
                "confidence": float(getattr(mem, "confidence", 0.7) or 0.7),
                "updated_at": getattr(mem, "updated_at", None),
                "source": getattr(mem, "source", "conversation"),
                "category": getattr(mem, "category", None),
                "score": round(float(getattr(r, "score", 0.0) or 0.0), 4),
            })

        if candidates:
            self.bus.emit(user_id, "memory.retrieved",
                          f"Recalled {len(candidates)} relevant memory(ies)",
                          subject_kind="memory", subject_id=candidates[0]["id"],
                          correlation_id=cid,
                          payload={"count": len(candidates),
                                   "ids": [c["id"] for c in candidates]})

        return {"candidates": candidates, "count": len(candidates),
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
        }
