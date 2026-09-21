"""
Advanced Explanation Engine (V8.4.2).

Constructs evidence-backed, auditable explanation graphs and summaries for
every major cognitive result.

Does NOT invent synthetic chain-of-thought: every explanation is assembled
from observable, persisted records:
  - Canonical event history
  - Arbitration records & candidate factors
  - Knowledge evidence, validations, usages and transitions
  - Experience evidence & observations
  - Causal links & decision logs
  - Prediction accuracy & outcome evaluations
  - Attention suppressions & silence policies
  - Autonomy levels & capability trust records
  - World entity state & change logs
  - Intent transitions & need hypotheses
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

EXPLANATION_CLASSES = (
    "MEMORY_RECALL",
    "LEARNED_KNOWLEDGE_RETRIEVAL",
    "ARBITRATION",
    "DECISION_INFLUENCE",
    "TOOL_SELECTION",
    "INTERVENTION",
    "ATTENTION",
    "PREDICTION",
    "PREDICTION_OUTCOME",
    "LEARNING",
    "KNOWLEDGE_VALIDATION",
    "KNOWLEDGE_CORRECTION",
    "KNOWLEDGE_RETIREMENT",
    "CAUSAL_CHANGE",
    "CONTINUITY_CHANGE",
    "WORLD_CHANGE",
    "INTENT_CHANGE",
    "AUTONOMY_ACTION",
    "AUTONOMY_REFUSAL",
    "WHY_NOW",
    "EXPERIENCE_FORMATION",
    "POLICY_CHANGE",
    "MISSION_STATE_CHANGE",
    "RESEARCH_EVIDENCE",
)

QUERY_INTENTS = (
    "why",
    "why_not",
    "why_now",
    "what_changed",
    "what_evidence",
    "what_alternatives",
    "what_caused_change",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


class ExplanationEngine:
    """
    Subsystem for generating, persisting and auditing cognitive explanations.
    """

    def __init__(self, db, bus, cognition) -> None:
        self.db = db
        self.bus = bus
        self.cognition = cognition

    # ----------------------------------------------------------- core dispatch
    def explain(
        self,
        user_id: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        explanation_type: str | None = None,
        query_intent: str = "why",
        question: str | None = None,
        depth: int = 2,
        persist: bool = True,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Produce a structured, auditable explanation graph for the target.
        """
        intent = (query_intent or "why").lower().replace(" ", "_").strip()
        if intent not in QUERY_INTENTS:
            intent = "why"

        kind = (subject_kind or "").lower().strip()
        sid = (subject_id or "").strip()

        # Route by explicit type or inferred subject kind
        exp_type = (explanation_type or "").upper().strip()
        if not exp_type:
            exp_type = self._infer_explanation_type(kind, sid, intent)

        if exp_type == "MEMORY_RECALL" or kind == "memory":
            model = self.explain_memory_recall(user_id, sid, intent=intent, question=question)
        elif exp_type in ("LEARNED_KNOWLEDGE_RETRIEVAL", "KNOWLEDGE_VALIDATION",
                          "KNOWLEDGE_CORRECTION", "KNOWLEDGE_RETIREMENT") or kind in ("skill", "principle"):
            model = self.explain_knowledge(user_id, sid, kind=kind or "skill", exp_type=exp_type, intent=intent)
        elif exp_type == "ARBITRATION" or kind == "arbitration":
            model = self.explain_arbitration(user_id, sid, intent=intent)
        elif exp_type == "DECISION_INFLUENCE" or kind == "decision":
            model = self.explain_decision(user_id, sid, intent=intent)
        elif exp_type == "TOOL_SELECTION" or kind in ("tool", "routing"):
            model = self.explain_tool_selection(user_id, sid, intent=intent)
        elif exp_type == "INTERVENTION" or kind == "intervention":
            model = self.explain_intervention(user_id, sid, intent=intent)
        elif exp_type == "ATTENTION" or kind == "attention":
            model = self.explain_attention(user_id, intent=intent)
        elif exp_type == "PREDICTION" or kind == "prediction":
            model = self.explain_prediction(user_id, sid, intent=intent)
        elif exp_type == "PREDICTION_OUTCOME":
            model = self.explain_prediction_outcome(user_id, sid, intent=intent)
        elif exp_type in ("LEARNING", "EXPERIENCE_FORMATION") or kind == "experience":
            model = self.explain_experience(user_id, sid, intent=intent)
        elif exp_type == "CAUSAL_CHANGE" or kind == "causal":
            model = self.explain_causal_change(user_id, kind or "memory", sid, intent=intent)
        elif exp_type == "CONTINUITY_CHANGE" or kind == "continuity":
            model = self.explain_continuity(user_id, sid, intent=intent)
        elif exp_type == "WORLD_CHANGE" or kind in ("world", "entity"):
            model = self.explain_world_change(user_id, sid, intent=intent)
        elif exp_type == "INTENT_CHANGE" or kind == "intent":
            model = self.explain_intent_change(user_id, sid, intent=intent)
        elif exp_type in ("AUTONOMY_ACTION", "AUTONOMY_REFUSAL") or kind == "autonomy":
            model = self.explain_autonomy(user_id, sid, exp_type=exp_type, intent=intent)
        elif exp_type == "POLICY_CHANGE" or kind == "policy":
            model = self.explain_policy(user_id, sid, intent=intent)
        elif exp_type == "MISSION_STATE_CHANGE" or kind == "mission":
            model = self.explain_mission(user_id, sid, intent=intent)
        elif (exp_type == "RESEARCH_EVIDENCE"
              or kind in ("research", "research_claim", "research_source")):
            model = self.explain_research(user_id, kind or "research", sid, intent=intent)
        elif exp_type == "WHY_NOW":
            model = self.explain_why_now(user_id, kind, sid, question=question)
        else:
            model = self._explain_generic_subject(user_id, kind, sid, intent=intent)

        model["query_intent"] = intent
        model["correlation_id"] = correlation_id

        if persist:
            self.save_snapshot(user_id, model)
            self.bus.emit(
                user_id,
                "explanation.generated",
                f"Generated explanation for {model.get('subject', {}).get('kind')}:{model.get('subject', {}).get('id')}",
                subject_kind=model.get("subject", {}).get("kind"),
                subject_id=model.get("subject", {}).get("id"),
                correlation_id=correlation_id,
                payload={"explanation_id": model["id"], "type": model["explanation_type"], "intent": intent},
            )

        return model

    def _infer_explanation_type(self, kind: str, sid: str, intent: str) -> str:
        if kind == "memory":
            return "MEMORY_RECALL"
        if kind in ("skill", "principle"):
            return "LEARNED_KNOWLEDGE_RETRIEVAL"
        if kind == "arbitration":
            return "ARBITRATION"
        if kind == "decision":
            return "DECISION_INFLUENCE"
        if kind == "prediction":
            return "PREDICTION"
        if kind == "experience":
            return "LEARNING"
        if kind == "intervention":
            return "INTERVENTION"
        if kind == "attention":
            return "ATTENTION"
        if kind in ("world", "entity"):
            return "WORLD_CHANGE"
        if kind == "intent":
            return "INTENT_CHANGE"
        if kind == "autonomy":
            return "AUTONOMY_ACTION"
        if kind == "policy":
            return "POLICY_CHANGE"
        if kind == "mission":
            return "MISSION_STATE_CHANGE"
        if kind in ("research", "research_claim", "research_source"):
            return "RESEARCH_EVIDENCE"
        if intent == "why_now":
            return "WHY_NOW"
        if intent == "what_changed":
            return "WORLD_CHANGE"
        if intent == "what_caused_change":
            return "CAUSAL_CHANGE"
        return "MEMORY_RECALL"

    # ------------------------------------------------- 1. Memory Recall
    def explain_memory_recall(
        self, user_id: str, memory_id: str, intent: str = "why", question: str | None = None
    ) -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        mem_obj = self.cognition.memory.get(memory_id) if hasattr(self.cognition, "memory") else None
        mem = mem_obj if (mem_obj is not None and getattr(mem_obj, "user_id", None) == user_id) else None
        rep = self.cognition.reputation.get(user_id, memory_id) if hasattr(self.cognition, "reputation") else {}
        events = self.bus.for_subject("memory", memory_id, user_id=user_id)
        arbitrations = (
            self.cognition.arbiter_v2.for_memory(memory_id, limit=5, user_id=user_id)
            if hasattr(self.cognition, "arbiter_v2")
            else []
        )
        impact = (
            self.cognition.influence.impact(user_id, memory_id)
            if hasattr(self.cognition, "influence")
            else {"influence_count": 0, "summary": "No recorded influence."}
        )

        if not mem:
            return self._insufficient_evidence(
                exp_id, "MEMORY_RECALL", intent, "memory", memory_id,
                f"Memory '{memory_id}' was not found in the persistent store."
            )

        lifecycle = str(rep.get("lifecycle") or mem.status or "active")
        is_blocked = lifecycle in ("retired", "quarantined") or mem.status == "superseded"

        decisive_factors = [
            {"name": "status", "value": mem.status, "impact": "positive" if mem.status == "active" else "negative",
             "description": f"Memory is currently in '{mem.status}' status."},
            {"name": "lifecycle", "value": lifecycle, "impact": "positive" if not is_blocked else "negative",
             "description": f"Reputation lifecycle is '{lifecycle}'."},
            {"name": "confidence", "value": mem.confidence, "impact": "positive" if mem.confidence >= 0.6 else "neutral",
             "description": f"Confidence score is {mem.confidence:.2f}."},
            {"name": "importance", "value": mem.importance, "impact": "positive" if mem.importance >= 0.5 else "neutral",
             "description": f"Base importance is {mem.importance:.2f}."},
            {"name": "source", "value": mem.source, "impact": "neutral",
             "description": f"Ingested via '{mem.source}'."},
        ]

        # Alternative analysis from recent arbitration
        alternatives = []
        if arbitrations:
            for arb in arbitrations:
                for cand in arb.get("candidates", []):
                    cid = cand.get("id") or cand.get("memory_id")
                    if cid != memory_id:
                        alternatives.append({
                            "id": cid,
                            "label": (cand.get("content") or "")[:60],
                            "kind": "memory",
                            "score": cand.get("score"),
                            "lifecycle": cand.get("lifecycle"),
                            "rejection_reason": (
                                cand.get("blocked_reason")
                                or f"Lower arbitration score ({cand.get('score', 0):.2f}) than winner"
                            ),
                            "factors": cand.get("factors") or {},
                        })

        # Correction / version history
        versions = self.db.query(
            "SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version ASC", (memory_id,)
        )
        correction = None
        if len(versions) > 1 or any("correct" in str(v["reason"]).lower() for v in versions):
            first_v = versions[0]
            latest_v = versions[-1]
            correction = {
                "is_corrected": True,
                "correction_type": "version_update",
                "historical_state": {"content": first_v["content"], "version": first_v["version"], "reason": first_v["reason"]},
                "current_state": {"content": latest_v["content"], "version": latest_v["version"], "reason": latest_v["reason"]},
                "reason": latest_v["reason"],
                "created_at": latest_v["created_at"],
            }

        state_now = {
            "lifecycle": lifecycle,
            "status": mem.status,
            "eligible_for_retrieval": not is_blocked,
            "content": mem.content,
            "category": mem.category,
            "confidence": mem.confidence,
        }
        state_then = correction["historical_state"] if correction else None
        change_reason = correction["reason"] if correction else None

        # Intent-specific summary
        if intent == "why_not":
            if is_blocked:
                summary = (
                    f"Memory '{mem.content[:60]}' was NOT selected because its lifecycle is '{lifecycle}' "
                    f"and status is '{mem.status}' (quarantined/retired/superseded memories are excluded from retrieval)."
                )
            else:
                summary = (
                    f"Memory '{mem.content[:60]}' is active (lifecycle: {lifecycle}), but was either not matched "
                    f"by query terms or was outranked by higher-relevance candidates."
                )
        elif intent == "what_evidence":
            summary = (
                f"Memory content: \"{mem.content}\". Source: {mem.source}, confidence: {mem.confidence:.2f}, "
                f"reinforcement count: {mem.reinforcement_count}. {impact['summary']}"
            )
        elif intent == "what_alternatives":
            summary = (
                f"Memory competed in {len(arbitrations)} arbitration(s) against {len(alternatives)} alternative candidate(s)."
            )
        else:
            summary = (
                f"Memory \"{mem.content[:60]}\" was retrieved because it is active (lifecycle: {lifecycle}, "
                f"confidence: {mem.confidence:.2f}) and matched the conversational context. {impact['summary']}"
            )

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "MEMORY_RECALL",
            "query_intent": intent,
            "subject": {
                "kind": "memory",
                "id": memory_id,
                "label": mem.content[:80],
                "status": mem.status,
                "lifecycle": lifecycle,
                "current_state": state_now,
                "historical_state": state_then,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"ver_{v['id']}", "kind": "version", "content": v["content"],
                 "confidence": mem.confidence, "source": v["reason"], "created_at": v["created_at"], "relation": "version_history"}
                for v in versions
            ],
            "counter_evidence": [],
            "alternatives": alternatives,
            "causality": {
                "upstream": self.cognition.causal.upstream("memory", memory_id, user_id=user_id) if hasattr(self.cognition, "causal") else [],
                "downstream": self.cognition.causal.downstream("memory", memory_id, user_id=user_id) if hasattr(self.cognition, "causal") else [],
            },
            "timeline": [
                {"id": e.id, "type": e.type, "summary": e.summary, "created_at": e.created_at, "correlation_id": e.correlation_id}
                for e in events
            ],
            "correction": correction,
            "state_now": state_now,
            "state_then": state_then,
            "change_reason": change_reason,
            "confidence": mem.confidence,
            "provenance": {
                "source": mem.source,
                "schema_version": "8.4.2",
                "generated_at": _now(),
                "evidence_count": len(versions) + len(events),
            },
            "created_at": _now(),
        }

    # -------------------------------- 2. Learned Knowledge (Skill / Principle)
    def explain_knowledge(
        self, user_id: str, item_id: str, kind: str = "skill", exp_type: str = "LEARNED_KNOWLEDGE_RETRIEVAL", intent: str = "why"
    ) -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        item = self.cognition.knowledge.get(user_id, item_id) if hasattr(self.cognition, "knowledge") else None

        if not item:
            return self._insufficient_evidence(
                exp_id, exp_type, intent, kind, item_id,
                f"{kind.title()} '{item_id}' was not found in learned knowledge."
            )

        rep = item.get("reputation") or {}
        lifecycle = str(item.get("lifecycle") or "candidate")
        validations = item.get("validations") or []
        transitions = item.get("transitions") or []
        usages = item.get("usages") or []
        evidence = item.get("evidence") or []
        supporting = [e for e in evidence if e.get("stance") == "supporting"]
        counterexamples = [e for e in evidence if e.get("stance") == "counterexample"]

        # Was it corrected or retired?
        is_corrected = any(
            t.get("lifecycle") in ("retired", "weakened", "outdated", "contradicted", "rescoped")
            for t in transitions
        ) or lifecycle == "retired"
        latest_transition = transitions[-1] if transitions else None

        if lifecycle == "retired" and exp_type in ("LEARNED_KNOWLEDGE_RETRIEVAL", ""):
            exp_type = "KNOWLEDGE_RETIREMENT"
        elif is_corrected and exp_type in ("LEARNED_KNOWLEDGE_RETRIEVAL", ""):
            exp_type = "KNOWLEDGE_CORRECTION"

        state_now = {
            "lifecycle": lifecycle,
            "eligible_for_retrieval": lifecycle in ("trusted", "provisional"),
            "scope": {"kind": item.get("scope_kind"), "value": item.get("scope_value")},
            "confidence": item.get("confidence", 0.0),
            "statement": item.get("statement"),
        }

        state_then = None
        if latest_transition:
            prev_life = latest_transition.get("previous_lifecycle", "trusted")
            state_then = {
                "lifecycle": prev_life,
                "eligible_for_retrieval": prev_life in ("trusted", "provisional"),
                "scope": {"kind": item.get("scope_kind"), "value": item.get("scope_value")},
            }
        elif len(transitions) > 1:
            prev_life = transitions[0].get("lifecycle", "candidate")
            state_then = {
                "lifecycle": prev_life,
                "eligible_for_retrieval": prev_life in ("trusted", "provisional"),
                "scope": {"kind": item.get("scope_kind"), "value": item.get("scope_value")},
            }

        change_reason = latest_transition.get("reason") if latest_transition else None

        correction = None
        if is_corrected and latest_transition:
            correction = {
                "is_corrected": True,
                "correction_type": latest_transition.get("lifecycle"),
                "previous_lifecycle": latest_transition.get("previous_lifecycle"),
                "current_lifecycle": latest_transition.get("lifecycle"),
                "reason": latest_transition.get("reason"),
                "created_at": latest_transition.get("created_at"),
                "historical_state": state_then,
                "current_state": state_now,
            }

        decisive_factors = [
            {"name": "lifecycle", "value": lifecycle,
             "impact": "positive" if lifecycle in ("trusted", "provisional") else "negative",
             "description": f"Lifecycle status is '{lifecycle}' (eligible for retrieval: {lifecycle in ('trusted', 'provisional')})."},
            {"name": "confidence", "value": item.get("confidence", 0.0),
             "impact": "positive" if item.get("confidence", 0.0) >= 0.7 else "neutral",
             "description": f"Confidence is {item.get('confidence', 0.0):.2f} based on {len(supporting)} supporting evidence."},
            {"name": "reputation", "value": rep.get("reputation", "INSUFFICIENT EVIDENCE"),
             "impact": "positive" if rep.get("reputation") in ("TRUSTED", "RELIABLE") else "neutral",
             "description": f"Reputation is '{rep.get('reputation')}' from {rep.get('evidence', 0)} observed outcomes."},
            {"name": "validation_status", "value": item.get("validation_status"),
             "impact": "positive" if item.get("validation_status") == "passed" else "neutral",
             "description": f"Validation status: {item.get('validation_status')}."},
            {"name": "scope", "value": f"{item.get('scope_kind')}:{item.get('scope_value') or 'all'}",
             "impact": "neutral",
             "description": f"Applicable scope: {item.get('scope_kind')}={item.get('scope_value') or 'global'}."},
        ]

        if counterexamples:
            decisive_factors.append({
                "name": "counterexamples", "value": len(counterexamples),
                "impact": "negative",
                "description": f"Contains {len(counterexamples)} recorded counterexample(s).",
            })

        # Alternatives from arbitration
        arbitrations = (
            self.cognition.arbiter_v2.for_subject(user_id, item_id, limit=5)
            if hasattr(self.cognition, "arbiter_v2")
            else []
        )
        alternatives = []
        for arb in arbitrations:
            for cand in arb.get("candidates", []):
                cid = cand.get("id") or cand.get("item_id")
                if cid != item_id:
                    alternatives.append({
                        "id": cid,
                        "label": cand.get("name") or cand.get("label") or cid,
                        "kind": cand.get("kind") or kind,
                        "score": cand.get("score"),
                        "lifecycle": cand.get("lifecycle"),
                        "rejection_reason": cand.get("blocked_reason") or "Outranked by winner during arbitration",
                        "factors": cand.get("factors") or {},
                    })

        # Synthesize summary according to query intent
        name = item.get("name") or item_id
        if intent == "why_not":
            if lifecycle in ("retired", "quarantined", "candidate", "weakened", "contradicted", "outdated"):
                summary = (
                    f"{kind.title()} '{name}' was NOT trusted or selected because its lifecycle is '{lifecycle}' "
                    f"(reason: {latest_transition.get('reason') if latest_transition else 'unmet criteria'})."
                )
            else:
                summary = (
                    f"{kind.title()} '{name}' is active, but was not selected due to scope mismatch or higher-ranked alternatives."
                )
        elif intent == "what_changed":
            if transitions:
                summary = (
                    f"{kind.title()} '{name}' underwent {len(transitions)} transition(s). Most recent: "
                    f"moved from '{latest_transition.get('previous_lifecycle')}' to '{latest_transition.get('lifecycle')}' "
                    f"on {latest_transition.get('created_at')} because: {latest_transition.get('reason')}."
                )
            else:
                summary = f"{kind.title()} '{name}' has remained in '{lifecycle}' since creation."
        elif intent == "what_evidence":
            summary = (
                f"{kind.title()} '{name}' is supported by {len(supporting)} evidence item(s) and has "
                f"{len(counterexamples)} counterexample(s). Validation: {validations[0]['reason'] if validations else 'pending'}."
            )
        elif intent == "what_alternatives":
            summary = f"{kind.title()} '{name}' competed in {len(arbitrations)} arbitration(s) against {len(alternatives)} alternative(s)."
        elif intent == "what_caused_change" and latest_transition:
            summary = (
                f"Change to '{latest_transition.get('lifecycle')}' was caused by: {latest_transition.get('reason')} "
                f"at {latest_transition.get('created_at')}."
            )
        else:
            summary = (
                f"{kind.title()} '{name}' is {lifecycle} with confidence {item.get('confidence', 0.0):.2f}. "
                f"Backed by {len(supporting)} supporting evidence item(s) and reputation '{rep.get('reputation', 'INSUFFICIENT EVIDENCE')}'. "
                f"Expected outcome: {item.get('expected_outcome') or 'positive execution'}."
            )

        events = self.bus.for_subject(kind, item_id, user_id=user_id)

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": exp_type,
            "query_intent": intent,
            "subject": {
                "kind": kind,
                "id": item_id,
                "label": name,
                "status": item.get("validation_status"),
                "lifecycle": lifecycle,
                "current_state": state_now,
                "historical_state": state_then,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": e.get("id", f"ev_{i}"), "kind": e.get("evidence_kind", "observation"),
                 "content": e.get("note") or e.get("evidence_id"), "confidence": e.get("quality", 0.8),
                 "source": e.get("relation", "derived_from"), "created_at": e.get("created_at", _now()),
                 "relation": e.get("relation", "supporting")}
                for i, e in enumerate(supporting)
            ],
            "counter_evidence": [
                {"id": e.get("id", f"cev_{i}"), "kind": e.get("evidence_kind", "observation"),
                 "content": e.get("note") or e.get("evidence_id"), "confidence": e.get("quality", 0.8),
                 "source": e.get("relation", "counterexample"), "created_at": e.get("created_at", _now()),
                 "relation": "counterexample"}
                for i, e in enumerate(counterexamples)
            ],
            "alternatives": alternatives,
            "causality": {
                "upstream": self.cognition.causal.upstream(kind, item_id, user_id=user_id) if hasattr(self.cognition, "causal") else [],
                "downstream": self.cognition.causal.downstream(kind, item_id, user_id=user_id) if hasattr(self.cognition, "causal") else [],
            },
            "timeline": [
                {"id": e.id, "type": e.type, "summary": e.summary, "created_at": e.created_at, "correlation_id": e.correlation_id}
                for e in events
            ],
            "correction": correction,
            "state_now": state_now,
            "state_then": state_then,
            "change_reason": change_reason,
            "confidence": item.get("confidence", 0.0),
            "provenance": {
                "source": item.get("source", "learning"),
                "schema_version": "8.4.2",
                "generated_at": _now(),
                "evidence_count": len(evidence) + len(validations),
                "usages_count": len(usages),
            },
            "created_at": _now(),
        }

    # ------------------------------------------------ 3. Arbitration
    def explain_arbitration(
        self, user_id: str, record_id_or_subject: str, intent: str = "why"
    ) -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        # Try direct record query or find latest record where winner or candidate matches
        row = self.db.query_one(
            "SELECT * FROM arbitration_records WHERE id=? AND user_id=?",
            (record_id_or_subject, user_id),
        )
        if not row:
            row = self.db.query_one(
                "SELECT * FROM arbitration_records WHERE user_id=? AND (winner_id=? OR candidates LIKE ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, record_id_or_subject, f"%{record_id_or_subject}%"),
            )

        if not row:
            return self._insufficient_evidence(
                exp_id, "ARBITRATION", intent, "arbitration", record_id_or_subject,
                f"No arbitration record found for '{record_id_or_subject}'."
            )

        candidates = _loads(row["candidates"], [])
        winner_id = row["winner_id"]
        reason = row["reason"]
        conflict = bool(row["conflict"])
        uncertainty = float(row["uncertainty"] or 0.0)

        winner_cand = next((c for c in candidates if (c.get("id") or c.get("memory_id")) == winner_id), None)
        alternatives = []
        for c in candidates:
            cid = c.get("id") or c.get("memory_id")
            if cid != winner_id:
                rejection_reason = (
                    c.get("blocked_reason")
                    or (f"Lower overall arbitration score ({c.get('score', 0):.2f}) compared to winner ({winner_cand.get('score', 0):.2f})"
                        if winner_cand else "Lower score in competitive ranking")
                )
                alternatives.append({
                    "id": cid,
                    "label": (c.get("content") or c.get("name") or cid)[:60],
                    "kind": c.get("kind") or "memory",
                    "score": c.get("score"),
                    "lifecycle": c.get("lifecycle"),
                    "rejection_reason": rejection_reason,
                    "factors": c.get("factors") or {},
                })

        decisive_factors = []
        if winner_cand and winner_cand.get("factors"):
            for fname, fval in winner_cand["factors"].items():
                decisive_factors.append({
                    "name": fname,
                    "value": fval,
                    "impact": "positive" if float(fval or 0) > 0.4 else "neutral",
                    "description": f"Winner scored {fval} in '{fname}'.",
                })
        decisive_factors.append({
            "name": "conflict_detected", "value": conflict, "impact": "negative" if conflict else "positive",
            "description": "Direct contradiction or tension between candidates was detected." if conflict else "No direct contradiction among top candidates.",
        })
        decisive_factors.append({
            "name": "uncertainty", "value": uncertainty, "impact": "negative" if uncertainty > 0.4 else "positive",
            "description": f"Arbitration uncertainty: {uncertainty:.2f}.",
        })

        if intent == "what_alternatives":
            summary = f"Arbitration evaluated {len(candidates)} candidate(s). {len(alternatives)} alternative(s) were rejected."
        elif intent == "why_not" and alternatives:
            summary = f"Alternatives were rejected: " + "; ".join(f"{a['id']}: {a['rejection_reason']}" for a in alternatives[:3])
        else:
            summary = f"Winner '{winner_id}' was selected. Reason: {reason}. Conflict: {conflict}, uncertainty: {uncertainty:.2f}."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "ARBITRATION",
            "query_intent": intent,
            "subject": {
                "kind": "arbitration",
                "id": row["id"],
                "label": f"Arbitration: {row['query'] or 'query'}",
                "status": "resolved",
                "lifecycle": "resolved",
                "current_state": {"winner_id": winner_id, "query": row["query"], "conflict": conflict, "uncertainty": uncertainty},
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"cand_{c.get('id') or i}", "kind": "candidate", "content": json.dumps(c.get("factors", {})),
                 "confidence": float(c.get("confidence", 0.7)), "source": "arbiter_v2", "created_at": row["created_at"], "relation": "scored_candidate"}
                for i, c in enumerate(candidates)
            ],
            "counter_evidence": [],
            "alternatives": alternatives,
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": 1.0 - uncertainty,
            "provenance": {
                "source": "arbiter_v2",
                "schema_version": "8.4.2",
                "generated_at": _now(),
                "correlation_id": row["correlation_id"],
                "evidence_count": len(candidates),
            },
            "created_at": _now(),
        }

    # ------------------------------------------------ 4. Decision Influence
    def explain_decision(self, user_id: str, decision_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        dec = self.cognition.decisions.get(decision_id) if hasattr(self.cognition, "decisions") else None

        if not dec or dec.get("user_id") != user_id:
            return self._insufficient_evidence(
                exp_id, "DECISION_INFLUENCE", intent, "decision", decision_id,
                f"Decision '{decision_id}' was not found."
            )

        upstream = self.cognition.causal.upstream("decision", decision_id, user_id=user_id) if hasattr(self.cognition, "causal") else []
        downstream = self.cognition.causal.downstream("decision", decision_id, user_id=user_id) if hasattr(self.cognition, "causal") else []
        events = self.bus.for_subject("decision", decision_id, user_id=user_id)

        decisive_factors = [
            {"name": "chosen", "value": dec.get("chosen"), "impact": "positive",
             "description": f"Chosen action: {dec.get('chosen')}."},
            {"name": "status", "value": dec.get("status"), "impact": "neutral",
             "description": f"Status is '{dec.get('status')}'."},
            {"name": "expected_outcome", "value": dec.get("expected_outcome"), "impact": "neutral",
             "description": f"Expected: {dec.get('expected_outcome')}."},
        ]
        if dec.get("actual_outcome"):
            decisive_factors.append({
                "name": "actual_outcome", "value": dec.get("actual_outcome"), "impact": "positive" if dec.get("regret") == 0.0 else "negative",
                "description": f"Actual outcome: {dec.get('actual_outcome')}.",
            })
        if dec.get("regret") is not None:
            decisive_factors.append({
                "name": "regret", "value": dec.get("regret"), "impact": "positive" if dec.get("regret") == 0.0 else "negative",
                "description": f"Regret score: {dec.get('regret')}. ({dec.get('regret_basis', '')})",
            })

        alternatives = [
            {"id": f"alt_{i}", "label": alt, "kind": "alternative_choice", "score": None, "lifecycle": None,
             "rejection_reason": "Not chosen in favor of selected alternative", "factors": {}}
            for i, alt in enumerate(dec.get("alternatives", []))
            if alt != dec.get("chosen")
        ]

        if intent == "what_caused_change" or intent == "why":
            act_outcome = dec.get("actual_outcome")
            outcome_str = f"Outcome: {act_outcome}." if act_outcome else "Awaiting outcome."
            summary = (
                f"Decision '{dec.get('summary')}': chose '{dec.get('chosen')}' over {len(alternatives)} alternative(s). "
                f"Influenced by {len(upstream)} upstream factor(s). {outcome_str}"
            )
        elif intent == "what_alternatives":
            summary = f"Evaluated alternatives: " + ", ".join(dec.get("alternatives", [])) + f". Tradeoffs: {dec.get('tradeoffs') or 'none noted'}."
        else:
            summary = f"Decision summary: {dec.get('summary')}. Regret: {dec.get('regret', 'uncalculated')}."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "DECISION_INFLUENCE",
            "query_intent": intent,
            "subject": {
                "kind": "decision",
                "id": decision_id,
                "label": dec.get("summary"),
                "status": dec.get("status"),
                "lifecycle": dec.get("status"),
                "current_state": dec,
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"ev_{i}", "kind": "evidence", "content": ev, "confidence": 0.9, "source": "decision_log", "created_at": dec["created_at"], "relation": "decision_evidence"}
                for i, ev in enumerate(dec.get("regret_evidence", []))
            ],
            "counter_evidence": [],
            "alternatives": alternatives,
            "causality": {"upstream": upstream, "downstream": downstream},
            "timeline": [
                {"id": e.id, "type": e.type, "summary": e.summary, "created_at": e.created_at, "correlation_id": e.correlation_id}
                for e in events
            ],
            "correction": None,
            "confidence": 0.85,
            "provenance": {
                "source": "decision_log",
                "schema_version": "8.4.2",
                "generated_at": _now(),
                "evidence_count": len(upstream) + len(downstream),
            },
            "created_at": _now(),
        }

    # ------------------------------------------------ 5. Tool Selection
    def explain_tool_selection(self, user_id: str, task_or_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        route = self.cognition.router.route(task_or_id or "memory_retrieval") if hasattr(self.cognition, "router") else None

        if not route:
            return self._insufficient_evidence(
                exp_id, "TOOL_SELECTION", intent, "routing", task_or_id,
                f"No router information available for task '{task_or_id}'."
            )

        decisive_factors = [
            {"name": "mode", "value": route.mode, "impact": "positive" if not route.degraded else "negative",
             "description": f"Execution mode selected: {route.mode}."},
            {"name": "uses_model", "value": route.uses_model, "impact": "neutral",
             "description": f"Model usage: {route.uses_model}."},
            {"name": "degraded", "value": route.degraded, "impact": "negative" if route.degraded else "positive",
             "description": f"Degraded execution: {route.degraded}."},
        ]

        summary = (
            f"Task '{route.task}' routed to mode '{route.mode}' (degraded: {route.degraded}). Reason: {route.reason}."
        )

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "TOOL_SELECTION",
            "query_intent": intent,
            "subject": {
                "kind": "routing",
                "id": route.task,
                "label": f"Routing: {route.task}",
                "status": route.mode,
                "lifecycle": "active",
                "current_state": route.as_dict(),
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": 1.0,
            "provenance": {"source": "router", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": 0},
            "created_at": _now(),
        }

    # ------------------------------------------------ 6. Intervention
    def explain_intervention(self, user_id: str, intervention_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        row = self.db.query_one("SELECT * FROM interventions WHERE id=? AND user_id=?", (intervention_id, user_id))
        if not row:
            return self._insufficient_evidence(
                exp_id, "INTERVENTION", intent, "intervention", intervention_id,
                f"Intervention '{intervention_id}' was not found."
            )

        dec = row["decision"]
        ev = float(row["expected_value"] or 0.0)
        cost = float(row["interruption_cost"] or 0.0)
        urgency = float(row["urgency"] or 0.0)
        confidence = float(row["confidence"] or 0.0)

        decisive_factors = [
            {"name": "decision", "value": dec, "impact": "positive" if dec in ("ACT", "ASK", "MENTION", "PREPARE") else "negative",
             "description": f"Intervention decision: {dec}."},
            {"name": "expected_value", "value": ev, "impact": "positive" if ev > cost else "negative",
             "description": f"Expected value: {ev:.2f}."},
            {"name": "interruption_cost", "value": cost, "impact": "negative",
             "description": f"Interruption cost: {cost:.2f}."},
            {"name": "urgency", "value": urgency, "impact": "positive" if urgency > 0.5 else "neutral",
             "description": f"Urgency: {urgency:.2f}."},
            {"name": "confidence", "value": confidence, "impact": "positive" if confidence >= 0.7 else "neutral",
             "description": f"Confidence: {confidence:.2f}."},
        ]

        summary = (
            f"Intervention on '{row['topic']}': decision was '{dec}'. "
            f"Expected value ({ev:.2f}) vs interruption cost ({cost:.2f}). Rationale: {row['rationale']}."
        )

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "INTERVENTION",
            "query_intent": intent,
            "subject": {
                "kind": "intervention",
                "id": intervention_id,
                "label": row["topic"],
                "status": dec,
                "lifecycle": dec,
                "current_state": dict(row),
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": confidence,
            "provenance": {"source": "attention_v2", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": 0},
            "created_at": _now(),
        }

    # ------------------------------------------------ 7. Attention / Suppression
    def explain_attention(self, user_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        policy = self.cognition.attention_v2.silence_policy(user_id) if hasattr(self.cognition, "attention_v2") else {}
        suppressions = self.cognition.attention_v2.suppressions(user_id) if hasattr(self.cognition, "attention_v2") else []

        decisive_factors = [
            {"name": "silence_verdict", "value": policy.get("verdict", "INSUFFICIENT EVIDENCE"), "impact": "neutral",
             "description": f"Learned silence policy: {policy.get('verdict')}."},
            {"name": "suppression_count", "value": len(suppressions), "impact": "neutral",
             "description": f"{len(suppressions)} recent suppressed intervention(s)."},
        ]

        summary = (
            f"Attention engine: {policy.get('detail', 'Silence policy active')}. "
            f"Total suppressions recorded: {len(suppressions)}."
        )

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "ATTENTION",
            "query_intent": intent,
            "subject": {
                "kind": "attention",
                "id": "attention_engine",
                "label": "Attention & Silence Policy",
                "status": policy.get("verdict", "active"),
                "lifecycle": "active",
                "current_state": policy,
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": s["id"], "kind": "suppression", "content": s["topic"], "confidence": 1.0,
                 "source": s.get("suppressed_because", "cost > value"), "created_at": s["created_at"], "relation": "suppression_record"}
                for s in suppressions[:5]
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": 0.85,
            "provenance": {"source": "attention_v2", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(suppressions)},
            "created_at": _now(),
        }

    # ------------------------------------------------ 8 & 9. Prediction & Outcome
    def explain_prediction(self, user_id: str, prediction_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        row = self.db.query_one("SELECT * FROM predictions WHERE id=? AND user_id=?", (prediction_id, user_id))
        if not row:
            return self._insufficient_evidence(
                exp_id, "PREDICTION", intent, "prediction", prediction_id,
                f"Prediction '{prediction_id}' was not found."
            )

        evidence = _loads(row["evidence"], [])
        status = row["status"]
        confidence = float(row["confidence"] or 0.5)

        decisive_factors = [
            {"name": "statement", "value": row["statement"], "impact": "neutral", "description": f"Statement: {row['statement']}."},
            {"name": "confidence", "value": confidence, "impact": "positive" if confidence >= 0.7 else "neutral", "description": f"Confidence: {confidence:.2f}."},
            {"name": "status", "value": status, "impact": "positive" if status == "correct" else ("negative" if status == "incorrect" else "neutral"), "description": f"Evaluation status: {status}."},
            {"name": "horizon", "value": row["horizon"], "impact": "neutral", "description": f"Horizon: {row['horizon']}."},
        ]

        if row["outcome"]:
            decisive_factors.append({
                "name": "outcome", "value": row["outcome"], "impact": "neutral",
                "description": f"Evaluated outcome: {row['outcome']}.",
            })

        obs_str = f"Outcome observed: {row['outcome']}" if row['outcome'] else "Open expectation."
        summary = (
            f"Prediction '{row['statement']}': confidence {confidence:.2f}, status '{status}'. "
            f"Evidence: {len(evidence)} item(s). {obs_str}"
        )

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "PREDICTION" if status == "open" else "PREDICTION_OUTCOME",
            "query_intent": intent,
            "subject": {
                "kind": "prediction",
                "id": prediction_id,
                "label": row["statement"],
                "status": status,
                "lifecycle": status,
                "current_state": dict(row),
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"ev_{i}", "kind": "observation", "content": ev, "confidence": confidence, "source": "prediction_engine", "created_at": row["created_at"], "relation": "prediction_evidence"}
                for i, ev in enumerate(evidence)
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": confidence,
            "provenance": {"source": "predictions", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(evidence)},
            "created_at": _now(),
        }

    def explain_prediction_outcome(self, user_id: str, prediction_id: str, intent: str = "why") -> dict[str, Any]:
        return self.explain_prediction(user_id, prediction_id, intent=intent)

    # ------------------------------------------------ 10. Experience / Learning
    def explain_experience(self, user_id: str, experience_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        exp = self.cognition.experiences.get(user_id, experience_id) if hasattr(self.cognition, "experiences") else None

        if not exp:
            return self._insufficient_evidence(
                exp_id, "LEARNING", intent, "experience", experience_id,
                f"Experience '{experience_id}' was not found."
            )

        evidence = exp.get("evidence") or []
        success = exp.get("success")

        decisive_factors = [
            {"name": "situation", "value": exp.get("situation"), "impact": "neutral", "description": f"Situation: {exp.get('situation')}."},
            {"name": "action", "value": exp.get("action"), "impact": "neutral", "description": f"Action taken: {exp.get('action')}."},
            {"name": "outcome", "value": exp.get("outcome"), "impact": "positive" if success else "negative", "description": f"Outcome observed: {exp.get('outcome')}."},
            {"name": "success", "value": success, "impact": "positive" if success else "negative", "description": f"Outcome success: {success}."},
            {"name": "confidence", "value": exp.get("confidence", 0.5), "impact": "neutral", "description": f"Observation confidence: {exp.get('confidence', 0.5):.2f}."},
        ]

        summary = (
            f"Experience '{exp.get('situation')}': action '{exp.get('action')}' led to '{exp.get('outcome')}' "
            f"(success: {success}). Supported by {len(evidence)} canonical observation(s)."
        )

        upstream = self.cognition.causal.upstream("experience", experience_id, user_id=user_id) if hasattr(self.cognition, "causal") else []
        downstream = self.cognition.causal.downstream("experience", experience_id, user_id=user_id) if hasattr(self.cognition, "causal") else []

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "LEARNING",
            "query_intent": intent,
            "subject": {
                "kind": "experience",
                "id": experience_id,
                "label": exp.get("situation", "")[:80],
                "status": exp.get("lifecycle", "observed"),
                "lifecycle": exp.get("lifecycle", "observed"),
                "current_state": exp,
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": ev["observation_id"], "kind": "observation", "content": ev["content"],
                 "confidence": ev["confidence"], "source": ev["source"], "created_at": _now(), "relation": ev["role"]}
                for ev in evidence
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": upstream, "downstream": downstream},
            "timeline": [],
            "correction": None,
            "confidence": exp.get("confidence", 0.5),
            "provenance": {"source": exp.get("source", "experience_store"), "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(evidence)},
            "created_at": _now(),
        }

    # ------------------------------------------------ 14. Causal Change
    def explain_causal_change(
        self, user_id: str, node_kind: str, node_id: str, intent: str = "what_caused_change"
    ) -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        upstream = self.cognition.causal.upstream(node_kind, node_id, user_id=user_id) if hasattr(self.cognition, "causal") else []
        downstream = self.cognition.causal.downstream(node_kind, node_id, user_id=user_id) if hasattr(self.cognition, "causal") else []
        events = self.bus.for_subject(node_kind, node_id, user_id=user_id)

        decisive_factors = [
            {"name": "upstream_causes", "value": len(upstream), "impact": "neutral",
             "description": f"{len(upstream)} upstream cause(s) influenced this node."},
            {"name": "downstream_effects", "value": len(downstream), "impact": "neutral",
             "description": f"{len(downstream)} downstream effect(s) were caused by this node."},
        ]

        if upstream:
            causes_summary = ", ".join(f"{u['cause_kind']}:{u['cause_id']} ({u['relation']})" for u in upstream[:3])
            summary = f"{node_kind}:{node_id} was influenced by {causes_summary}. Generated {len(downstream)} downstream effect(s)."
        else:
            summary = f"{node_kind}:{node_id} has no recorded upstream causes in the causal graph. Downstream effects: {len(downstream)}."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "CAUSAL_CHANGE",
            "query_intent": intent,
            "subject": {
                "kind": node_kind,
                "id": node_id,
                "label": f"{node_kind}:{node_id}",
                "status": "tracked",
                "lifecycle": "active",
                "current_state": {"upstream_count": len(upstream), "downstream_count": len(downstream)},
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"link_{u.get('id', i)}", "kind": "causal_link", "content": f"{u['cause_kind']}:{u['cause_id']} -> {node_kind}:{node_id}",
                 "confidence": float(u.get("weight", 0.5)), "source": u["relation"], "created_at": u.get("created_at", _now()), "relation": "upstream_cause"}
                for i, u in enumerate(upstream)
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": upstream, "downstream": downstream},
            "timeline": [
                {"id": e.id, "type": e.type, "summary": e.summary, "created_at": e.created_at, "correlation_id": e.correlation_id}
                for e in events
            ],
            "correction": None,
            "confidence": 0.85,
            "provenance": {"source": "causal_graph", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(upstream) + len(downstream)},
            "created_at": _now(),
        }

    # ------------------------------------------------ 15. Continuity Change
    def explain_continuity(self, user_id: str, sid: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        items = self.cognition.continuity.open_items(user_id, limit=20) if hasattr(self.cognition, "continuity") else []
        target = next((i for i in items if i.get("id") == sid), None) if sid else (items[0] if items else None)

        if not target:
            return self._insufficient_evidence(
                exp_id, "CONTINUITY_CHANGE", intent, "continuity", sid or "open_threads",
                "No open continuity items found."
            )

        decisive_factors = [
            {"name": "reason", "value": target.get("reason"), "impact": "neutral", "description": f"Continuity reason: {target.get('reason')}."},
            {"name": "status", "value": target.get("status"), "impact": "neutral", "description": f"Status: {target.get('status')}."},
            {"name": "summary", "value": target.get("summary"), "impact": "neutral", "description": f"Item: {target.get('summary')}."},
        ]

        summary = f"Continuity item '{target.get('summary')}': reason is '{target.get('reason')}' (status: {target.get('status')})."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "CONTINUITY_CHANGE",
            "query_intent": intent,
            "subject": {
                "kind": "continuity",
                "id": target.get("id", "item"),
                "label": target.get("summary", ""),
                "status": target.get("status", "open"),
                "lifecycle": "open",
                "current_state": target,
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": 0.8,
            "provenance": {"source": "continuity_engine", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": 0},
            "created_at": _now(),
        }

    # ------------------------------------------------ 16. World Change
    def explain_world_change(self, user_id: str, entity_id: str | None = None, intent: str = "what_changed") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        changes = self.cognition.world_v2.changes(user_id, entity_id=entity_id, limit=20) if hasattr(self.cognition, "world_v2") else []

        if entity_id:
            row = self.db.query_one("SELECT * FROM world_entities WHERE id=? AND user_id=?", (entity_id, user_id))
        else:
            row = None

        if not changes and not row:
            return self._insufficient_evidence(
                exp_id, "WORLD_CHANGE", intent, "world", entity_id or "world_state",
                "No world state changes recorded for that target."
            )

        decisive_factors = []
        if row:
            decisive_factors.append({
                "name": "state", "value": row["state"], "impact": "positive" if row["state"] == "active" else "neutral",
                "description": f"Entity state is '{row['state']}' with confidence {float(row['confidence'] or 0.6):.2f}.",
            })
            decisive_factors.append({
                "name": "freshness", "value": row["freshness_class"], "impact": "negative" if bool(row["stale"]) else "positive",
                "description": f"Freshness class: {row['freshness_class']} (stale: {bool(row['stale'])}).",
            })

        if changes:
            latest = changes[0]
            summary = (
                f"World entity change recorded: '{latest.get('change')}'. "
                f"Moved from '{latest.get('previous_state')}' to '{latest.get('new_state')}' on {latest.get('created_at')}."
            )
        else:
            summary = f"World entity '{row['label'] if row else entity_id}' is active with no recent state changes."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "WORLD_CHANGE",
            "query_intent": intent,
            "subject": {
                "kind": "world",
                "id": entity_id or (row["id"] if row else "world_state"),
                "label": row["label"] if row else (entity_id or "World State"),
                "status": row["state"] if row else "active",
                "lifecycle": row["state"] if row else "active",
                "current_state": dict(row) if row else {"changes_count": len(changes)},
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"chg_{c.get('id', i)}", "kind": "world_change", "content": c.get("change", ""),
                 "confidence": 0.9, "source": "world_v2", "created_at": c.get("created_at", _now()), "relation": "state_transition"}
                for i, c in enumerate(changes)
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": 0.9,
            "provenance": {"source": "world_v2", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(changes)},
            "created_at": _now(),
        }

    # ------------------------------------------------ 17. Intent Change
    def explain_intent_change(self, user_id: str, intent_id: str | None = None, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        transitions = self.cognition.intent_evolution.transitions(user_id, limit=10) if hasattr(self.cognition, "intent_evolution") else []
        current = self.cognition.intent.current(user_id) if hasattr(self.cognition, "intent") else None

        target_tr = next((t for t in transitions if t.get("intent_id") == intent_id), None) if intent_id else (transitions[0] if transitions else None)

        if not target_tr and not current:
            return self._insufficient_evidence(
                exp_id, "INTENT_CHANGE", intent, "intent", intent_id or "current_intent",
                "No intent transitions recorded."
            )

        decisive_factors = []
        if target_tr:
            decisive_factors.append({
                "name": "changed_because", "value": target_tr.get("changed_because"), "impact": "positive",
                "description": f"Trigger reason: {target_tr.get('changed_because')}.",
            })
            decisive_factors.append({
                "name": "confidence", "value": target_tr.get("confidence"), "impact": "positive",
                "description": f"Confidence: {target_tr.get('confidence', 0.5):.2f}.",
            })
            decisive_factors.append({
                "name": "uncertainty", "value": target_tr.get("uncertainty"), "impact": "negative",
                "description": f"Uncertainty: {target_tr.get('uncertainty', 0.0):.2f}.",
            })
            summary = (
                f"Intent transitioned to '{target_tr.get('to_status')}' because: {target_tr.get('changed_because')}. "
                f"Confidence: {target_tr.get('confidence', 0.5):.2f}, uncertainty: {target_tr.get('uncertainty', 0.0):.2f}."
            )
        else:
            summary = f"Current intent: '{current.get('label')}' (confidence: {current.get('confidence', 0.5):.2f})."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "INTENT_CHANGE",
            "query_intent": intent,
            "subject": {
                "kind": "intent",
                "id": intent_id or (current.get("id") if current else "current"),
                "label": current.get("label") if current else "Intent Evolution",
                "status": "current",
                "lifecycle": "current",
                "current_state": target_tr or current,
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"ev_{i}", "kind": "intent_evidence", "content": ev, "confidence": 0.8,
                 "source": "intent_v2", "created_at": target_tr.get("created_at", _now()), "relation": "intent_signal"}
                for i, ev in enumerate(target_tr.get("evidence", []) if target_tr else [])
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": target_tr.get("confidence", 0.7) if target_tr else 0.7,
            "provenance": {"source": "intent_evolution", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(transitions)},
            "created_at": _now(),
        }

    # ------------------------------------------------ 18 & 19. Autonomy Action / Refusal
    def explain_autonomy(
        self, user_id: str, capability: str | None = None, exp_type: str = "AUTONOMY_ACTION", intent: str = "why"
    ) -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        lvl = self.cognition.autonomy.level(user_id) if hasattr(self.cognition, "autonomy") else "SUGGEST"
        trust = self.cognition.trust.all(user_id) if hasattr(self.cognition, "trust") else []
        cap_trust = self.cognition.capability_trust.score(user_id, capability or "memory_retrieval") if hasattr(self.cognition, "capability_trust") else {}

        decisive_factors = [
            {"name": "autonomy_level", "value": lvl, "impact": "positive" if lvl in ("ACT", "AUTONOMOUS") else "neutral",
             "description": f"Global user autonomy level is '{lvl}'."},
            {"name": "capability_trust", "value": cap_trust.get("label", "INSUFFICIENT EVIDENCE"), "impact": "positive" if cap_trust.get("label") == "RELIABLE" else "neutral",
             "description": f"Capability trust label: '{cap_trust.get('label')}' ({cap_trust.get('successes', 0)} succ, {cap_trust.get('failures', 0)} fail)."},
        ]

        if exp_type == "AUTONOMY_REFUSAL" or intent == "why_not":
            summary = (
                f"Autonomous action for '{capability or 'action'}' was NOT executed directly because autonomy level is '{lvl}' "
                f"and capability trust is '{cap_trust.get('label', 'INSUFFICIENT EVIDENCE')}'. Explicit confirmation is required."
            )
        else:
            summary = (
                f"Autonomous behavior operates under level '{lvl}'. "
                f"Capability trust for '{capability or 'general'}': {cap_trust.get('label', 'INSUFFICIENT EVIDENCE')}."
            )

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": exp_type,
            "query_intent": intent,
            "subject": {
                "kind": "autonomy",
                "id": capability or "autonomy_policy",
                "label": f"Autonomy ({lvl})",
                "status": lvl,
                "lifecycle": lvl,
                "current_state": {"level": lvl, "trust": trust, "capability_trust": cap_trust},
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": 0.9,
            "provenance": {"source": "autonomy_governor", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": 0},
            "created_at": _now(),
        }

    # ------------------------------------------------ 20. Why Now
    def explain_why_now(
        self, user_id: str, subject_kind: str | None = None, subject_id: str | None = None, question: str | None = None
    ) -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        kind = subject_kind or "conversation"
        sid = subject_id or "current_turn"

        events = self.bus.for_subject(kind, sid, user_id=user_id) if subject_kind and subject_id else self.bus.recent(user_id, limit=20)
        latest = events[-1] if events else None

        correlated = []
        if latest and latest.correlation_id:
            correlated = [e for e in self.bus.for_correlation(latest.correlation_id) if e.id != latest.id]

        decisive_factors = [
            {"name": "trigger_event", "value": latest.type if latest else "none", "impact": "positive",
             "description": f"Most recent event: {latest.summary if latest else 'none'}."},
            {"name": "correlated_events", "value": len(correlated), "impact": "neutral",
             "description": f"{len(correlated)} correlated event(s) in the same turn."},
        ]

        if latest:
            summary = (
                f"Triggered by recent event '{latest.summary}' ({latest.type}) at {latest.created_at}. "
                f"Accompanied by {len(correlated)} related turn event(s)."
            )
        else:
            summary = "No recent trigger events found on record."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "WHY_NOW",
            "query_intent": "why_now",
            "subject": {
                "kind": kind,
                "id": sid,
                "label": f"Temporal trigger: {latest.summary if latest else sid}",
                "status": "triggered",
                "lifecycle": "active",
                "current_state": {"latest_event": latest.as_dict() if latest else None},
                "historical_state": None,
            },
            "summary": summary,
            "decisive_factors": decisive_factors,
            "supporting_evidence": [
                {"id": f"ev_{e.id}", "kind": "cognitive_event", "content": e.summary, "confidence": 1.0,
                 "source": e.type, "created_at": e.created_at, "relation": "turn_trigger"}
                for e in correlated[:6]
            ],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [
                {"id": e.id, "type": e.type, "summary": e.summary, "created_at": e.created_at, "correlation_id": e.correlation_id}
                for e in (events[-5:] if events else [])
            ],
            "correction": None,
            "confidence": 0.9,
            "provenance": {"source": "event_bus", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(correlated)},
            "created_at": _now(),
        }

    # ------------------------------------------------ Policy & Mission Helpers
    def explain_policy(self, user_id: str, key: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        try:
            pol = self.cognition.policy.explain(user_id, key) if hasattr(self.cognition, "policy") else {}
        except (ValueError, KeyError):
            pol = {}
        if not pol or pol.get("status") == "NOT_FOUND":
            return self._insufficient_evidence(
                exp_id, "POLICY_CHANGE", intent, "policy", key,
                f"No cognitive policy found for key '{key}'."
            )

        summary = pol.get("explanation") or f"Policy '{key}' value is '{pol.get('policy', {}).get('value')}'."
        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "POLICY_CHANGE",
            "query_intent": intent,
            "subject": {"kind": "policy", "id": key, "label": f"Policy: {key}", "status": "active", "lifecycle": "active", "current_state": pol.get("policy", {}), "historical_state": None},
            "summary": summary,
            "decisive_factors": [{"name": "evidence_count", "value": pol.get("policy", {}).get("evidence_count", 1), "impact": "positive", "description": "Derived from user utterance evidence."}],
            "supporting_evidence": [{"id": f"pev_{i}", "kind": "policy_evidence", "content": ev.get("evidence", ""), "confidence": 1.0, "source": "utterance", "created_at": ev.get("at", _now()), "relation": "policy_evidence"} for i, ev in enumerate(pol.get("evidence", []))],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": float(pol.get("policy", {}).get("confidence", 0.8)),
            "provenance": {"source": "policy_engine", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(pol.get("evidence", []))},
            "created_at": _now(),
        }

    def explain_mission(self, user_id: str, mission_id: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        mission = self.cognition.missions.get(user_id, mission_id) if hasattr(self.cognition, "missions") else None
        if not mission:
            return self._insufficient_evidence(
                exp_id, "MISSION_STATE_CHANGE", intent, "mission", mission_id,
                f"Mission '{mission_id}' was not found."
            )

        history = self.cognition.missions.history(user_id, mission_id, limit=10) if hasattr(self.cognition, "missions") else []
        summary = f"Mission '{mission['title']}' is in state '{mission['state']}' (progress: {mission.get('progress', 0)}%)."
        if mission.get("blocked_reason"):
            summary += f" Blocked reason: {mission['blocked_reason']}."

        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "MISSION_STATE_CHANGE",
            "query_intent": intent,
            "subject": {"kind": "mission", "id": mission_id, "label": mission["title"], "status": mission["state"], "lifecycle": mission["state"], "current_state": mission, "historical_state": None},
            "summary": summary,
            "decisive_factors": [{"name": "state", "value": mission["state"], "impact": "neutral", "description": f"State: {mission['state']}"}],
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [{"id": i, "type": "mission.transition", "summary": h.get("change", ""), "created_at": h.get("created_at", _now()), "correlation_id": None} for i, h in enumerate(history)],
            "correction": None,
            "confidence": 0.9,
            "provenance": {"source": "mission_registry", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(history)},
            "created_at": _now(),
        }

    # ------------------------------------------------ 21. Research Evidence
    def explain_research(self, user_id: str, kind: str, sid: str,
                         intent: str = "why") -> dict[str, Any]:
        """
        Explain a Connected Research (V8.4.3) subject: why a source was
        fetched, what evidence supports a claim, why claims conflict, why a
        source was rejected/blocked, or why research was blocked overall.

        `kind` may be 'research' (a session id), 'research_claim' (a claim
        id) or 'research_source' (a source id) — resolved against the real
        ResearchEngine tables, never fabricated.
        """
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        engine = getattr(self.cognition, "research_engine", None)
        if engine is None:
            return self._insufficient_evidence(
                exp_id, "RESEARCH_EVIDENCE", intent, kind, sid,
                "Connected Research engine is not available in this build.")

        # A claim id: explain its evidence, corroboration and any conflicts.
        claim = engine.get_claim(user_id, sid) if sid else None
        if claim is not None:
            session_id = claim["session_id"]
            evidence_rows = [e for e in engine.evidence(user_id, session_id)
                             if e["id"] in claim["evidence_ids"]]
            source_rows = [s for s in engine.sources(user_id, session_id)
                          if s["id"] in claim["source_ids"]]
            conflicts = [c for c in engine.conflicts(user_id, session_id)
                        if claim["id"] in c["claim_ids"]]

            decisive_factors = [
                {"name": "evidence_strength", "value": claim["evidence_strength"],
                 "impact": "positive" if claim["evidence_strength"] >= 0.5 else "neutral",
                 "description": f"Average evidence strength: {claim['evidence_strength']:.2f}."},
                {"name": "source_quality", "value": claim.get("source_quality"),
                 "impact": "neutral",
                 "description": f"Average source quality: {claim.get('source_quality')}."},
                {"name": "independent_domain_count", "value": claim["independent_domain_count"],
                 "impact": "positive" if claim["independent_domain_count"] > 1 else "neutral",
                 "description": (f"Corroborated by {claim['independent_domain_count']} "
                                f"independent domain(s) — repeated pages on the "
                                f"same domain do not count as independent "
                                f"corroboration.")},
                {"name": "claim_confidence", "value": claim["claim_confidence"],
                 "impact": "positive" if claim["claim_confidence"] >= 0.5 else "neutral",
                 "description": (f"Capped external-evidence confidence: "
                                f"{claim['claim_confidence']:.2f}. This is "
                                f"deliberately bounded — external evidence is "
                                f"never treated as certainty.")},
                {"name": "status", "value": claim["status"],
                 "impact": "negative" if claim["status"] == "contested" else "positive",
                 "description": f"Claim status: {claim['status']}."},
            ]

            if intent == "why_not" and conflicts:
                other_ids = [c for grp in conflicts for c in grp["claim_ids"] if c != claim["id"]]
                summary = (f"This claim is contested: {len(conflicts)} conflict "
                          f"group(s) preserve a competing claim rather than "
                          f"silently resolving it. Competing claim id(s): "
                          f"{', '.join(other_ids) or 'none'}.")
            elif intent == "what_evidence":
                summary = (f"Claim \"{claim['statement'][:100]}\" is backed by "
                          f"{len(evidence_rows)} evidence excerpt(s) from "
                          f"{len(source_rows)} source(s): "
                          + "; ".join(s["canonical_url"] for s in source_rows[:3]) + ".")
            else:
                summary = (f"Claim \"{claim['statement'][:100]}\" derives from "
                          f"{claim['corroboration_count']} piece(s) of evidence "
                          f"across {claim['independent_domain_count']} independent "
                          f"domain(s), giving it a capped confidence of "
                          f"{claim['claim_confidence']:.2f} (status: {claim['status']}).")

            return {
                "id": exp_id, "user_id": user_id,
                "explanation_type": "RESEARCH_EVIDENCE", "query_intent": intent,
                "subject": {"kind": "research_claim", "id": claim["id"],
                           "label": claim["statement"][:100], "status": claim["status"],
                           "lifecycle": claim["status"], "current_state": claim,
                           "historical_state": None},
                "summary": summary, "decisive_factors": decisive_factors,
                "supporting_evidence": [
                    {"id": e["id"], "kind": "research_evidence", "content": e["excerpt"],
                     "confidence": e["evidence_strength"], "source": e["locator"],
                     "created_at": e["retrieved_at"], "relation": "supports_claim"}
                    for e in evidence_rows],
                "counter_evidence": [
                    {"id": grp["id"], "kind": "research_conflict", "content": grp["reason"],
                     "confidence": None, "source": "conflict_detection",
                     "created_at": grp["created_at"], "relation": "conflicts_with"}
                    for grp in conflicts],
                "alternatives": [],
                "causality": {"upstream": [], "downstream": []},
                "timeline": [],
                "correction": None,
                "confidence": claim["claim_confidence"],
                "provenance": {"source": "research_engine", "schema_version": "8.4.3",
                              "generated_at": _now(),
                              "evidence_count": len(evidence_rows)},
                "created_at": _now(),
            }

        # A source id: explain why it was fetched / rejected.
        source = None
        for s in self.db.query("SELECT * FROM research_sources WHERE id=? AND user_id=?", (sid, user_id)):
            source = dict(s)
        if source is not None:
            fetches = [f for f in self.db.query(
                "SELECT * FROM research_fetches WHERE source_id=? AND user_id=?",
                (sid, user_id))]
            fetches = [dict(f) for f in fetches]
            latest = fetches[-1] if fetches else None
            summary = (f"Source '{source['canonical_url']}' is {source['availability']}."
                      + (f" Latest fetch: {latest['status']}"
                         + (f" ({latest['error_detail']})" if latest and latest.get('error_detail') else ".")
                         if latest else " No fetch has been attempted."))
            return {
                "id": exp_id, "user_id": user_id,
                "explanation_type": "RESEARCH_EVIDENCE", "query_intent": intent,
                "subject": {"kind": "research_source", "id": source["id"],
                           "label": source["canonical_url"], "status": source["availability"],
                           "lifecycle": source["availability"], "current_state": source,
                           "historical_state": None},
                "summary": summary,
                "decisive_factors": [
                    {"name": "availability", "value": source["availability"], "impact": "neutral",
                     "description": f"Availability: {source['availability']}."}],
                "supporting_evidence": [
                    {"id": f["id"], "kind": "fetch_attempt",
                     "content": f.get("error_detail") or f"HTTP {f.get('http_status')}",
                     "confidence": None, "source": f["status"],
                     "created_at": f["fetched_at"], "relation": "fetch_history"}
                    for f in fetches],
                "counter_evidence": [], "alternatives": [],
                "causality": {"upstream": [], "downstream": []}, "timeline": [],
                "correction": None, "confidence": None,
                "provenance": {"source": "research_engine", "schema_version": "8.4.3",
                              "generated_at": _now(), "evidence_count": len(fetches)},
                "created_at": _now(),
            }

        # Fall back to the session itself.
        session = engine.get(user_id, sid) if sid else None
        if session is None:
            return self._insufficient_evidence(
                exp_id, "RESEARCH_EVIDENCE", intent, kind, sid,
                f"No research session, claim or source found for id '{sid}'.")

        conflicts = engine.conflicts(user_id, sid)
        summary = (f"Research session '{session['question'][:80]}' is "
                  f"{session['state']}: {session['source_count']} source(s), "
                  f"{session['evidence_count']} evidence record(s), "
                  f"{session['claim_count']} claim(s), "
                  f"{session['conflict_count']} conflict(s). {session.get('detail') or ''}")
        return {
            "id": exp_id, "user_id": user_id,
            "explanation_type": "RESEARCH_EVIDENCE", "query_intent": intent,
            "subject": {"kind": "research", "id": session["id"],
                       "label": session["question"][:100], "status": session["state"],
                       "lifecycle": session["state"], "current_state": session,
                       "historical_state": None},
            "summary": summary,
            "decisive_factors": [
                {"name": "state", "value": session["state"], "impact": "neutral",
                 "description": f"Session state: {session['state']}."},
                {"name": "conflict_count", "value": session["conflict_count"],
                 "impact": "negative" if session["conflict_count"] else "positive",
                 "description": f"{session['conflict_count']} unresolved conflict(s)."},
            ],
            "supporting_evidence": [],
            "counter_evidence": [
                {"id": c["id"], "kind": "research_conflict", "content": c["reason"],
                 "confidence": None, "source": "conflict_detection",
                 "created_at": c["created_at"], "relation": "conflicts_with"}
                for c in conflicts],
            "alternatives": [], "causality": {"upstream": [], "downstream": []},
            "timeline": [], "correction": None, "confidence": None,
            "provenance": {"source": "research_engine", "schema_version": "8.4.3",
                          "generated_at": _now(), "evidence_count": session["evidence_count"]},
            "created_at": _now(),
        }

    # ------------------------------------------------ Generic / Fallback Subject
    def _explain_generic_subject(self, user_id: str, kind: str, sid: str, intent: str = "why") -> dict[str, Any]:
        exp_id = f"exp_{uuid.uuid4().hex[:12]}"
        events = self.bus.for_subject(kind or "subject", sid or "unknown", user_id=user_id) if (kind and sid) else []
        if not events:
            return self._insufficient_evidence(
                exp_id, "MEMORY_RECALL", intent, kind or "subject", sid or "unknown",
                "INSUFFICIENT EVIDENCE — No recorded history or state found for that subject."
            )
        latest = events[-1]
        summary = f"Subject '{kind}:{sid}' has {len(events)} recorded event(s). Most recent: '{latest.summary}' on {latest.created_at}."
        return {
            "id": exp_id,
            "user_id": user_id,
            "explanation_type": "MEMORY_RECALL",
            "query_intent": intent,
            "subject": {"kind": kind or "subject", "id": sid or "unknown", "label": f"{kind}:{sid}", "status": "active", "lifecycle": "active", "current_state": {}, "historical_state": None},
            "summary": summary,
            "decisive_factors": [],
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [{"id": e.id, "type": e.type, "summary": e.summary, "created_at": e.created_at, "correlation_id": e.correlation_id} for e in events],
            "correction": None,
            "confidence": 0.7,
            "provenance": {"source": "event_bus", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": len(events)},
            "created_at": _now(),
        }

    def _insufficient_evidence(
        self, exp_id: str, exp_type: str, intent: str, kind: str, sid: str, message: str
    ) -> dict[str, Any]:
        return {
            "id": exp_id,
            "user_id": "-",
            "explanation_type": exp_type,
            "query_intent": intent,
            "subject": {"kind": kind, "id": sid, "label": f"{kind}:{sid}", "status": "unknown", "lifecycle": "unknown", "current_state": {}, "historical_state": None},
            "summary": f"INSUFFICIENT EVIDENCE — {message}",
            "decisive_factors": [],
            "supporting_evidence": [],
            "counter_evidence": [],
            "alternatives": [],
            "causality": {"upstream": [], "downstream": []},
            "timeline": [],
            "correction": None,
            "confidence": None,
            "provenance": {"source": "explanation_engine", "schema_version": "8.4.2", "generated_at": _now(), "evidence_count": 0},
            "created_at": _now(),
        }

    # ------------------------------------------------ Persistence & Snapshots
    def save_snapshot(self, user_id: str, model: dict[str, Any]) -> str:
        """Persist an auditable explanation snapshot to SQLite."""
        exp_id = model.get("id") or f"exp_{uuid.uuid4().hex[:12]}"
        model["id"] = exp_id
        model["user_id"] = user_id
        subject = model.get("subject") or {}
        skind = subject.get("kind")
        sid = subject.get("id")
        etype = model.get("explanation_type", "MEMORY_RECALL")
        intent = model.get("query_intent", "why")
        summary = model.get("summary", "")
        cid = model.get("correlation_id")

        ev_ids = json.dumps([e.get("id") for e in model.get("supporting_evidence", []) if e.get("id")])
        evt_ids = json.dumps([e.get("id") for e in model.get("timeline", []) if e.get("id")])
        dec_ids = json.dumps([d.get("effect_id") for d in model.get("causality", {}).get("downstream", []) if d.get("effect_kind") == "decision"])
        caus_ids = json.dumps([c.get("cause_id") for c in model.get("causality", {}).get("upstream", []) if c.get("cause_id")])
        graph_data = json.dumps(model, default=str)
        created_at = model.get("created_at") or _now()

        self.db.execute(
            "INSERT OR REPLACE INTO explanation_snapshots "
            "(id, user_id, subject_kind, subject_id, explanation_type, query_intent, summary, graph_data, evidence_ids, event_ids, decision_ids, causality_ids, correlation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exp_id, user_id, skind, sid, etype, intent, summary, graph_data, ev_ids, evt_ids, dec_ids, caus_ids, cid, created_at),
        )
        return exp_id

    def get_snapshot(self, user_id: str, explanation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM explanation_snapshots WHERE id=? AND user_id=?",
            (explanation_id, user_id),
        )
        if not row:
            return None
        return _loads(row["graph_data"], dict(row))

    def list_snapshots(
        self, user_id: str, subject_kind: str | None = None, subject_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = "SELECT id, user_id, subject_kind, subject_id, explanation_type, query_intent, summary, created_at FROM explanation_snapshots WHERE user_id=?"
        params: list[Any] = [user_id]
        if subject_kind:
            sql += " AND subject_kind=?"
            params.append(subject_kind)
        if subject_id:
            sql += " AND subject_id=?"
            params.append(subject_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(200, limit)))
        rows = self.db.query(sql, params)
        return [dict(r) for r in rows]
