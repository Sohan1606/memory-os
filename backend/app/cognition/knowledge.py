"""Evidence-backed Skill and Principle learning for MEMORY//OS V8.4.1.

This module is the domain layer for actionable learned knowledge. It extends the
existing canonical systems rather than replacing them:

* ExperienceStore and ObservationLog supply evidence.
* ReputationStore records only performance after actual use.
* ArbiterV2 chooses between relevant abstractions.
* CausalGraph records experience -> skill/principle -> decision -> outcome.
* EventBus remains the only cognitive event stream.

An LLM or background task may propose structure, but only deterministic evidence
checks may validate it, and promotion is a separate explicit operation.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

KINDS = ("skill", "principle")
SCOPES = ("user", "task", "project", "domain", "environment", "global")
LIFECYCLES = ("candidate", "validating", "trusted", "used", "reinforced",
              "weakened", "outdated", "contradicted", "retired")
LIVE_LIFECYCLES = ("trusted", "used", "reinforced", "weakened")
STANCES = ("supporting", "counterexample")
USAGE_VERDICTS = ("SUPPORTED", "CONTRADICTED", "NEUTRAL",
                  "INSUFFICIENT EVIDENCE")
INFLUENCE_KINDS = ("decision", "action", "policy", "intent")

SKILL_MIN_EXPERIENCES = 3
SKILL_MIN_CONSISTENCY = 0.75
PRINCIPLE_MIN_SKILLS = 2
PRINCIPLE_MIN_EXPERIENCES = 4
PRINCIPLE_MIN_PATTERNS = 2
PRINCIPLE_MIN_CONSISTENCY = 0.80

_ALLOWED: dict[str, tuple[str, ...]] = {
    "candidate": ("validating", "weakened", "outdated", "contradicted", "retired"),
    "validating": ("candidate", "trusted", "weakened", "contradicted", "retired"),
    "trusted": ("used", "reinforced", "weakened", "outdated", "contradicted", "retired"),
    "used": ("used", "reinforced", "weakened", "outdated", "contradicted", "retired"),
    "reinforced": ("used", "reinforced", "weakened", "outdated", "contradicted", "retired"),
    "weakened": ("used", "validating", "reinforced", "outdated", "contradicted", "retired"),
    "outdated": ("validating", "retired"),
    "contradicted": ("validating", "retired"),
    "retired": (),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


_WORLD_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when",
    "then", "are", "was", "were", "been", "being", "must", "should", "can",
    "could", "would", "may", "might", "has", "have", "had", "not", "never",
    "without", "none", "no",
}
_NEGATORS = {"no", "not", "never", "without", "none"}


def _words(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]{2,}", str(value).lower())


def _tokens(value: Any) -> set[str]:
    return {word for word in _words(value)
            if len(word) >= 3 and word not in _WORLD_STOPWORDS}


def _polarities(value: Any) -> dict[str, set[bool]]:
    words = _words(value)
    result: dict[str, set[bool]] = defaultdict(set)
    for index, word in enumerate(words):
        if len(word) < 3 or word in _WORLD_STOPWORDS:
            continue
        window = words[max(0, index - 3):index]
        result[word].add(any(previous in _NEGATORS for previous in window))
    return result


def _polarity_conflict(requirement: Any, current_world: Any) -> bool:
    required = _polarities(requirement)
    observed = _polarities(current_world)
    return any(required[token].isdisjoint(observed[token])
               for token in required.keys() & observed.keys())


def _key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").lower()))[:160]


class KnowledgeService:
    """Skill/Principle persistence, evidence validation, use and refinement."""

    def __init__(self, db, bus, experiences, reputation, causal, arbiter) -> None:
        self.db = db
        self.bus = bus
        self.experiences = experiences
        self.reputation = reputation
        self.causal = causal
        self.arbiter = arbiter

    # --------------------------------------------------------------- proposal
    def propose_skill(
        self, user_id: str, name: str, statement: str, *, trigger: str,
        procedure: list[str], expected_outcome: str,
        supporting_experience_ids: list[str],
        counterexample_experience_ids: list[str] | None = None,
        context: list[str] | None = None, preconditions: list[str] | None = None,
        scope_kind: str = "user", scope_value: str | None = None,
        pattern_key: str | None = None, generalization_hint: str | None = None,
        source: str = "learning-engine", provenance: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if not trigger.strip() or not [p for p in procedure if p.strip()]:
            raise ValueError("A Skill requires a trigger and an actionable procedure.")
        if not expected_outcome.strip():
            raise ValueError("A Skill requires an expected outcome.")
        return self._propose(
            user_id, "skill", name, statement, trigger=trigger,
            procedure=procedure, expected_outcome=expected_outcome,
            supporting=("experience", supporting_experience_ids),
            counterexamples=("experience", counterexample_experience_ids or []),
            context=context, preconditions=preconditions,
            scope_kind=scope_kind, scope_value=scope_value,
            pattern_key=pattern_key, generalization_hint=generalization_hint,
            generality=0.0, source=source, provenance=provenance,
            correlation_id=correlation_id)

    def propose_principle(
        self, user_id: str, name: str, statement: str, *,
        supporting_skill_ids: list[str],
        supporting_experience_ids: list[str] | None = None,
        counterexample_experience_ids: list[str] | None = None,
        application: list[str] | None = None, expected_outcome: str = "",
        scope_kind: str = "user", scope_value: str | None = None,
        pattern_key: str | None = None, generality: float = 0.6,
        source: str = "learning-engine", provenance: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if not statement.strip():
            raise ValueError("A Principle requires a statement.")
        item = self._propose(
            user_id, "principle", name, statement, trigger="Relevant decisions",
            procedure=application or [statement], expected_outcome=expected_outcome,
            supporting=("skill", supporting_skill_ids),
            counterexamples=("experience", counterexample_experience_ids or []),
            context=None, preconditions=None, scope_kind=scope_kind,
            scope_value=scope_value, pattern_key=pattern_key,
            generalization_hint=None, generality=generality, source=source,
            provenance=provenance, correlation_id=correlation_id)
        for exp_id in supporting_experience_ids or []:
            self.link_evidence(user_id, item["id"], "experience", exp_id,
                               stance="supporting", relation="supports")
        return self.get(user_id, item["id"])  # type: ignore[return-value]

    def _propose(
        self, user_id: str, kind: str, name: str, statement: str, *,
        trigger: str, procedure: list[str], expected_outcome: str,
        supporting: tuple[str, list[str]],
        counterexamples: tuple[str, list[str]], context: list[str] | None,
        preconditions: list[str] | None, scope_kind: str,
        scope_value: str | None, pattern_key: str | None,
        generalization_hint: str | None, generality: float, source: str,
        provenance: dict[str, Any] | None, correlation_id: str | None,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"Unknown learned knowledge kind: {kind!r}")
        name, statement = name.strip(), statement.strip()
        if not name or not statement:
            raise ValueError(f"A {kind.title()} requires name and statement.")
        self._validate_scope(scope_kind, scope_value)
        if not supporting[1]:
            raise ValueError(f"A {kind.title()} candidate requires linked evidence.")
        item_id = f"{'sk' if kind == 'skill' else 'pr'}_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO knowledge_items (id,user_id,kind,name,statement,"
            "trigger_text,context,preconditions,procedure,expected_outcome,"
            "scope_kind,scope_value,pattern_key,generalization_hint,generality,"
            "confidence,lifecycle,validation_status,source,provenance,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, user_id, kind, name[:200], statement[:1000], trigger[:500],
             json.dumps(context or []), json.dumps(preconditions or []),
             json.dumps([p.strip() for p in procedure if p.strip()]),
             expected_outcome[:1000], scope_kind, scope_value,
             (pattern_key or "").strip() or None,
             (generalization_hint or "").strip() or None,
             max(0.0, min(1.0, float(generality))), 0.0, "candidate", "pending",
             source[:80], json.dumps(provenance or {}), now, now))
        self.reputation.get_subject(user_id, kind, item_id)
        for evidence_id in supporting[1]:
            self.link_evidence(user_id, item_id, supporting[0], evidence_id,
                               stance="supporting", relation="derived_from")
        for evidence_id in counterexamples[1]:
            self.link_evidence(user_id, item_id, counterexamples[0], evidence_id,
                               stance="counterexample", relation="contradicted_by")
        self._record_transition(user_id, item_id, kind, None, "candidate",
                                "Evidence-linked candidate proposed.",
                                supporting[1], correlation_id)
        self.bus.emit(
            user_id, f"{kind}.candidate_created", statement[:160],
            subject_kind=kind, subject_id=item_id, correlation_id=correlation_id,
            payload={"supporting_evidence": len(supporting[1]),
                     "counterexamples": len(counterexamples[1]),
                     "scope": {"kind": scope_kind, "value": scope_value},
                     "source": source})
        return self.get(user_id, item_id)  # type: ignore[return-value]

    # --------------------------------------------------------------- evidence
    def link_evidence(self, user_id: str, item_id: str, evidence_kind: str,
                      evidence_id: str, *, stance: str = "supporting",
                      relation: str = "derived_from", quality: float | None = None,
                      note: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, item_id, include_details=False)
        if item is None:
            raise KeyError(item_id)
        if stance not in STANCES:
            raise ValueError(f"Unknown evidence stance: {stance!r}")
        evidence_quality = self._verify_evidence(
            user_id, item["kind"], evidence_kind, evidence_id)
        if quality is not None:
            evidence_quality = min(evidence_quality,
                                   max(0.0, min(1.0, float(quality))))
        self.db.execute(
            "INSERT OR IGNORE INTO knowledge_evidence (id,user_id,item_id,item_kind,"
            "evidence_kind,evidence_id,stance,relation,quality,note,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"kev_{uuid.uuid4().hex[:12]}", user_id, item_id, item["kind"],
             evidence_kind, evidence_id, stance, relation,
             round(evidence_quality, 4), note, _now()))
        return self.get(user_id, item_id)  # type: ignore[return-value]

    def _verify_evidence(self, user_id: str, item_kind: str,
                         evidence_kind: str, evidence_id: str) -> float:
        if evidence_kind == "experience":
            exp = self.experiences.get(user_id, evidence_id)
            if exp is None:
                raise ValueError(
                    f"Experience {evidence_id!r} does not exist in this user namespace.")
            if not exp["evidence"]:
                raise ValueError("An experience without evidence cannot support learning.")
            return float(exp.get("confidence") or 0.0)
        if evidence_kind in KINDS:
            linked = self.get(user_id, evidence_id, include_details=False)
            if linked is None or linked["kind"] != evidence_kind:
                raise ValueError(
                    f"{evidence_kind.title()} {evidence_id!r} does not exist in this user namespace.")
            if item_kind == "skill":
                raise ValueError("A Skill must be supported by Experiences, not another abstraction.")
            return float(linked.get("confidence") or 0.0)
        if evidence_kind == "observation":
            row = self.db.query_one(
                "SELECT confidence FROM observations WHERE id=? AND user_id=?",
                (evidence_id, user_id))
            if row is None:
                raise ValueError(
                    f"Observation {evidence_id!r} does not exist in this user namespace.")
            return float(row["confidence"])
        if evidence_kind == "usage":
            row = self.db.query_one(
                "SELECT outcome_verdict FROM knowledge_usages WHERE id=? AND user_id=?",
                (evidence_id, user_id))
            if row is None or row["outcome_verdict"] is None:
                raise ValueError("Only a resolved usage can be learning evidence.")
            return 0.9 if row["outcome_verdict"] in ("SUPPORTED", "CONTRADICTED") else 0.4
        raise ValueError(f"Unknown evidence kind: {evidence_kind!r}")

    # ------------------------------------------------------------- validation
    def validate(self, user_id: str, item_id: str, *,
                 validator: str = "deterministic-evidence-policy",
                 correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        if item is None:
            raise KeyError(item_id)
        if item["lifecycle"] == "retired":
            raise ValueError("A retired abstraction cannot be validated.")
        if item["lifecycle"] not in ("candidate", "validating", "weakened",
                                     "outdated", "contradicted"):
            raise ValueError(
                f"Validation is not applicable in lifecycle {item['lifecycle']!r}.")
        if item["lifecycle"] != "validating":
            self._transition(user_id, item, "validating",
                             "Deterministic evidence validation started.", [],
                             correlation_id)
        self.bus.emit(
            user_id, f"{item['kind']}.validation_started",
            f"Checking evidence for {item['name']}", subject_kind=item["kind"],
            subject_id=item_id, correlation_id=correlation_id)

        item = self.get(user_id, item_id) or item
        if item["kind"] == "skill":
            passed, confidence, metrics, reason = self._validate_skill(user_id, item)
        else:
            passed, confidence, metrics, reason = self._validate_principle(user_id, item)
        decision = "PASS" if passed else "REJECT"
        self.db.execute(
            "INSERT INTO knowledge_validations (id,user_id,item_id,item_kind,"
            "decision,confidence,metrics,reason,validator,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"val_{uuid.uuid4().hex[:12]}", user_id, item_id, item["kind"],
             decision, round(confidence, 4), json.dumps(metrics), reason,
             validator, correlation_id, _now()))
        self.db.execute(
            "UPDATE knowledge_items SET confidence=?,validation_status=?,updated_at=?"
            " WHERE id=? AND user_id=?",
            (round(confidence, 4), "passed" if passed else "rejected", _now(),
             item_id, user_id))
        if passed:
            self.bus.emit(
                user_id, f"{item['kind']}.validated", reason,
                subject_kind=item["kind"], subject_id=item_id,
                correlation_id=correlation_id,
                payload={"confidence": round(confidence, 4), "metrics": metrics})
        else:
            # A failed check is not promotion. Strongly contrary evidence is a
            # contradiction; otherwise it remains a candidate for more evidence.
            target = ("contradicted" if metrics.get("contradiction_ratio", 0) >= 0.5
                      else "candidate")
            current = self.get(user_id, item_id, include_details=False) or item
            if current["lifecycle"] != target:
                self._transition(user_id, current, target, reason,
                                 metrics.get("evidence_ids", []), correlation_id)
            self.bus.emit(
                user_id, f"{item['kind']}.validation_failed", reason,
                subject_kind=item["kind"], subject_id=item_id,
                correlation_id=correlation_id,
                payload={"confidence": round(confidence, 4), "metrics": metrics})
        return {"item": self.get(user_id, item_id), "decision": decision,
                "confidence": round(confidence, 4), "metrics": metrics,
                "reason": reason}

    def _validate_skill(self, user_id: str, item: dict[str, Any]):
        linked = [e for e in item["evidence"] if e["evidence_kind"] == "experience"]
        supporting_ids = {e["evidence_id"] for e in linked
                          if e["stance"] == "supporting"}
        counter_ids = {e["evidence_id"] for e in linked
                       if e["stance"] == "counterexample"}
        experiences = [self.experiences.get(user_id, eid) for eid in supporting_ids]
        experiences = [e for e in experiences if e is not None]
        valid = [e for e in experiences
                 if e["lifecycle"] in ("validated", "active")
                 and e.get("success") is not None]
        successes = [e for e in valid if e["success"]]
        failures = [e for e in valid if not e["success"]]
        recorded_contradictions = int(
            item.get("reputation", {}).get("evidence_contradictions") or 0)
        counter_count = len(counter_ids) + len(failures) + recorded_contradictions
        judged = len(successes) + counter_count
        consistency = len(successes) / judged if judged else 0.0
        direct_origins = {ev["origin"] for exp in valid for ev in exp["evidence"]
                          if ev.get("epistemic_status") == "OBSERVED"}
        quality_values = [float(e.get("confidence") or 0.0) for e in valid]
        quality = sum(quality_values) / len(quality_values) if quality_values else 0.0
        pattern_values = {_key(str(e.get("pattern_key") or e.get("action") or ""))
                          for e in successes}
        pattern_values.discard("")
        requested_pattern = _key(str(item.get("pattern_key") or ""))
        pattern_consistent = bool(pattern_values) and (
            len(pattern_values) == 1 or
            (requested_pattern and all(requested_pattern in p or p in requested_pattern
                                       for p in pattern_values)))
        operational = bool(item["trigger_text"] and item["procedure"]
                           and item["expected_outcome"])
        scope_ok = self._evidence_scope_ok(item, successes)
        confidence = max(0.0, min(1.0,
            0.10 + 0.25 * min(1.0, len(successes) / SKILL_MIN_EXPERIENCES)
            + 0.25 * consistency + 0.20 * quality
            + 0.10 * min(1.0, len(direct_origins) / SKILL_MIN_EXPERIENCES)
            + (0.10 if pattern_consistent and operational and scope_ok else 0.0)
            - 0.20 * (counter_count / max(1, judged))))
        passed = bool(
            len(successes) >= SKILL_MIN_EXPERIENCES
            and len(direct_origins) >= SKILL_MIN_EXPERIENCES
            and consistency >= SKILL_MIN_CONSISTENCY
            and pattern_consistent and operational and scope_ok and quality >= 0.55)
        metrics = {
            "supporting_experiences": len(successes),
            "counterexamples": counter_count,
            "consistency": round(consistency, 4),
            "contradiction_ratio": round(counter_count / max(1, judged), 4),
            "distinct_evidence_origins": len(direct_origins),
            "evidence_quality": round(quality, 4),
            "pattern_consistent": pattern_consistent,
            "operational_structure": operational, "scope_consistent": scope_ok,
            "evidence_ids": sorted(supporting_ids | counter_ids),
        }
        reason = (
            f"Skill evidence {'passed' if passed else 'did not pass'}: "
            f"{len(successes)} successful validated experience(s), "
            f"{counter_count} counterexample(s), consistency {consistency:.2f}, "
            f"{len(direct_origins)} distinct observed origin(s).")
        return passed, confidence, metrics, reason

    def _validate_principle(self, user_id: str, item: dict[str, Any]):
        linked_skills = [e for e in item["evidence"]
                         if e["evidence_kind"] == "skill"
                         and e["stance"] == "supporting"]
        skills = [self.get(user_id, e["evidence_id"]) for e in linked_skills]
        skills = [s for s in skills if s is not None]
        trusted = [s for s in skills
                   if s["lifecycle"] in ("trusted", "used", "reinforced")
                   and s["validation_status"] == "passed"]
        experience_ids: set[str] = {
            e["evidence_id"] for skill in trusted for e in skill["evidence"]
            if e["evidence_kind"] == "experience" and e["stance"] == "supporting"}
        experience_ids.update(
            e["evidence_id"] for e in item["evidence"]
            if e["evidence_kind"] == "experience" and e["stance"] == "supporting")
        experiences = [self.experiences.get(user_id, eid) for eid in experience_ids]
        experiences = [e for e in experiences if e is not None and
                       e["lifecycle"] in ("validated", "active")]
        successful = [e for e in experiences if e.get("success") is True]
        unsuccessful = [e for e in experiences if e.get("success") is False]
        explicit_counter = [e for e in item["evidence"]
                            if e["stance"] == "counterexample"]
        recorded_contradictions = int(
            item.get("reputation", {}).get("evidence_contradictions") or 0)
        counters = len(unsuccessful) + len(explicit_counter) + recorded_contradictions
        judged = len(successful) + counters
        consistency = len(successful) / judged if judged else 0.0
        patterns = {_key(str(s.get("pattern_key") or s["statement"])) for s in trusted}
        patterns.discard("")
        scopes = {(s["scope_kind"], s.get("scope_value")) for s in trusted}
        quality_values = [float(s.get("confidence") or 0.0) for s in trusted]
        quality = sum(quality_values) / len(quality_values) if quality_values else 0.0
        generality_ok = float(item.get("generality") or 0.0) >= 0.5
        breadth_ok = len(patterns) >= PRINCIPLE_MIN_PATTERNS
        scope_ok = self._principle_scope_ok(item, scopes)
        confidence = max(0.0, min(1.0,
            0.05 + 0.20 * min(1.0, len(trusted) / PRINCIPLE_MIN_SKILLS)
            + 0.20 * min(1.0, len(successful) / PRINCIPLE_MIN_EXPERIENCES)
            + 0.20 * consistency + 0.15 * quality
            + (0.10 if breadth_ok else 0.0) + (0.10 if scope_ok else 0.0)
            - 0.20 * counters / max(1, judged)))
        passed = bool(
            len(trusted) >= PRINCIPLE_MIN_SKILLS
            and len(successful) >= PRINCIPLE_MIN_EXPERIENCES
            and consistency >= PRINCIPLE_MIN_CONSISTENCY
            and breadth_ok and generality_ok and scope_ok and quality >= 0.60)
        metrics = {
            "supporting_skills": len(trusted),
            "supporting_experiences": len(successful),
            "counterexamples": counters, "consistency": round(consistency, 4),
            "contradiction_ratio": round(counters / max(1, judged), 4),
            "distinct_patterns": len(patterns), "scope_breadth": len(scopes),
            "evidence_quality": round(quality, 4),
            "generality_ok": generality_ok, "scope_consistent": scope_ok,
            "evidence_ids": sorted(experience_ids),
        }
        reason = (
            f"Principle evidence {'passed' if passed else 'did not pass'}: "
            f"{len(trusted)} validated skill(s), {len(successful)} successful "
            f"experience(s), {len(patterns)} distinct pattern(s), consistency "
            f"{consistency:.2f}.")
        return passed, confidence, metrics, reason

    def _evidence_scope_ok(self, item: dict[str, Any],
                           experiences: list[dict[str, Any]]) -> bool:
        kind, value = item["scope_kind"], item.get("scope_value")
        if kind == "user":
            return True
        if kind == "global":
            # Per-user evidence may never silently become globally authoritative.
            return bool(item["provenance"].get("global_authorized"))
        return bool(experiences and all(
            e["scope_kind"] == kind and e.get("scope_value") == value
            for e in experiences))

    @staticmethod
    def _principle_scope_ok(item: dict[str, Any],
                            scopes: set[tuple[str, Any]]) -> bool:
        kind, value = item["scope_kind"], item.get("scope_value")
        if kind == "user":
            return True
        if kind == "global":
            return bool(item["provenance"].get("global_authorized"))
        return bool(scopes and all(k == kind and v == value for k, v in scopes))

    def promote(self, user_id: str, item_id: str, *,
                reason: str = "Latest deterministic validation passed.",
                correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        if item is None:
            raise KeyError(item_id)
        latest = item["validations"][0] if item["validations"] else None
        if item["lifecycle"] != "validating" or item["validation_status"] != "passed":
            raise ValueError("Only a currently validating candidate with PASS evidence can be promoted.")
        if latest is None or latest["decision"] != "PASS":
            raise ValueError("Promotion requires a recorded PASS validation.")
        self._transition(user_id, item, "trusted", reason,
                         [e["evidence_id"] for e in item["evidence"]], correlation_id)
        promoted = self.get(user_id, item_id) or item
        self.bus.emit(
            user_id, f"{item['kind']}.promoted", reason,
            subject_kind=item["kind"], subject_id=item_id,
            correlation_id=correlation_id,
            payload={"confidence": promoted["confidence"],
                     "supporting_evidence": promoted["supporting_evidence_count"]})
        self._link_provenance_edges(user_id, promoted, correlation_id)
        return promoted

    def _link_provenance_edges(self, user_id: str, item: dict[str, Any],
                               correlation_id: str | None) -> None:
        for evidence in item["evidence"]:
            if evidence["stance"] != "supporting":
                continue
            source_kind = evidence["evidence_kind"]
            if source_kind not in ("experience", "skill", "principle"):
                continue
            try:
                self.causal.link(
                    user_id, source_kind, evidence["evidence_id"], item["kind"],
                    item["id"], relation="supported", weight=evidence["quality"],
                    correlation_id=correlation_id)
            except ValueError:
                continue

    # ---------------------------------------------------------- pattern mining
    def detect_skill_candidates(self, user_id: str, *,
                                correlation_id: str | None = None) -> list[dict[str, Any]]:
        experiences = self.experiences.list(user_id, limit=500)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for exp in experiences:
            if (exp["lifecycle"] in ("validated", "active")
                    and exp.get("success") is True and exp.get("pattern_key")
                    and exp.get("action") and exp.get("outcome")):
                groups[str(exp["pattern_key"])].append(exp)
        created: list[dict[str, Any]] = []
        for pattern, members in groups.items():
            if len(members) < SKILL_MIN_EXPERIENCES:
                continue
            existing = self.db.query_one(
                "SELECT id FROM knowledge_items WHERE user_id=? AND kind='skill'"
                " AND pattern_key=?", (user_id, pattern))
            if existing:
                continue
            actions = Counter(_key(str(e["action"])) for e in members)
            action_key, _ = actions.most_common(1)[0]
            coherent = [e for e in members if _key(str(e["action"])) == action_key]
            if len(coherent) < SKILL_MIN_EXPERIENCES:
                continue
            action = str(coherent[0]["action"])
            situation = str(coherent[0]["situation"])
            outcome = Counter(str(e["outcome"]) for e in coherent).most_common(1)[0][0]
            scopes = {(e["scope_kind"], e.get("scope_value")) for e in coherent}
            scope_kind, scope_value = (next(iter(scopes)) if len(scopes) == 1
                                       else ("user", None))
            generalization = next((e["context"].get("generalization_hint")
                                   for e in coherent
                                   if isinstance(e.get("context"), dict)
                                   and e["context"].get("generalization_hint")), None)
            candidate = self.propose_skill(
                user_id, name=f"Respond to {pattern.replace('_', ' ')}",
                statement=f"When {situation}, {action}.", trigger=situation,
                procedure=[action], expected_outcome=outcome,
                supporting_experience_ids=[e["id"] for e in coherent],
                scope_kind=scope_kind, scope_value=scope_value,
                pattern_key=pattern, generalization_hint=generalization,
                source="background-pattern-detector",
                provenance={"detector": "exact-action/outcome-consistency",
                            "experience_ids": [e["id"] for e in coherent]},
                correlation_id=correlation_id)
            self.bus.emit(
                user_id, "learning.pattern_detected",
                f"{len(coherent)} successful experiences share pattern {pattern}",
                subject_kind="skill", subject_id=candidate["id"],
                correlation_id=correlation_id,
                payload={"pattern_key": pattern, "experiences": len(coherent)})
            created.append(candidate)
        return created

    def detect_principle_candidates(self, user_id: str, *,
                                    correlation_id: str | None = None) -> list[dict[str, Any]]:
        skills = self.list(user_id, kind="skill", limit=500)
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for skill in skills:
            if (skill["lifecycle"] in ("trusted", "used", "reinforced")
                    and skill["validation_status"] == "passed"
                    and skill.get("generalization_hint")):
                groups[str(skill["generalization_hint"])].append(skill)
        created: list[dict[str, Any]] = []
        for statement, members in groups.items():
            patterns = {m.get("pattern_key") for m in members if m.get("pattern_key")}
            if len(members) < PRINCIPLE_MIN_SKILLS or len(patterns) < PRINCIPLE_MIN_PATTERNS:
                continue
            pattern_key = _key(statement)
            existing = self.db.query_one(
                "SELECT id FROM knowledge_items WHERE user_id=? AND kind='principle'"
                " AND pattern_key=?", (user_id, pattern_key))
            if existing:
                continue
            item = self.propose_principle(
                user_id, name=statement[:120], statement=statement,
                supporting_skill_ids=[m["id"] for m in members],
                application=[statement], pattern_key=pattern_key,
                generality=0.7, source="background-pattern-detector",
                provenance={"detector": "cross-skill-generalization",
                            "skill_ids": [m["id"] for m in members]},
                correlation_id=correlation_id)
            self.bus.emit(
                user_id, "learning.pattern_detected",
                f"{len(members)} trusted skills support a higher-order pattern",
                subject_kind="principle", subject_id=item["id"],
                correlation_id=correlation_id,
                payload={"skills": len(members), "patterns": len(patterns)})
            created.append(item)
        return created

    def maintain(self, user_id: str, *,
                 correlation_id: str | None = None) -> dict[str, Any]:
        """Safe background work: create and validate candidates, never promote."""
        skills = self.detect_skill_candidates(user_id, correlation_id=correlation_id)
        principles = self.detect_principle_candidates(
            user_id, correlation_id=correlation_id)
        validations = []
        for item in skills + principles:
            validations.append(self.validate(user_id, item["id"],
                                             correlation_id=correlation_id))
        findings = [{"kind": f"{i['kind']}_candidate", "subject_id": i["id"],
                     "summary": f"Evidence-backed {i['kind']} candidate: {i['name']}"}
                    for i in skills + principles]
        return {"candidates": skills + principles, "validations": validations,
                "findings": findings, "changes": len(skills) + len(principles),
                "promoted": 0,
                "note": "Background maintenance never promotes candidates."}

    # -------------------------------------------------------- retrieval / use
    def retrieve(self, user_id: str, query: str, *, kind: str | None = None,
                 scope: dict[str, str] | None = None,
                 current_world: list[str] | None = None, limit: int = 5,
                 correlation_id: str | None = None) -> dict[str, Any]:
        if kind is not None and kind not in KINDS:
            raise ValueError(f"Unknown learned knowledge kind: {kind!r}")
        candidates = self.list(user_id, kind=kind, limit=500)
        live = [item for item in candidates if item["lifecycle"] in LIVE_LIFECYCLES]
        world_text = " ".join(current_world or [])
        world_tokens = _tokens(world_text)
        for item in live:
            item["context_text"] = " ".join(item["context"])
            item["procedure_text"] = " ".join(item["procedure"])
            checks = []
            for precondition in item.get("preconditions") or []:
                required = _tokens(precondition)
                lexical_support = len(required & world_tokens) / max(1, len(required))
                checks.append(0.0 if _polarity_conflict(
                    precondition, world_text) else lexical_support)
            # Preconditions are claims about recorded current state, not about
            # lexical hints in the user's question. Every precondition must be
            # substantially present with matching polarity; an empty or
            # explicitly contradicted current-world bundle cannot pass.
            item["world_match"] = (sum(checks) / len(checks) if checks else 1.0)
            item["world_eligible"] = all(check >= 0.8 for check in checks)
        arbitration = self.arbiter.arbitrate_knowledge(
            user_id, live, query=query, scope=scope, correlation_id=correlation_id)
        eligible = [c for c in arbitration["candidates"] if not c["blocked"]]
        for candidate in eligible[:max(1, min(20, limit))]:
            self.reputation.record_subject_retrieval(
                user_id, candidate["subject_kind"], candidate["subject_id"])
        winner = arbitration.get("winner")
        if winner:
            self.bus.emit(
                user_id, f"{winner['subject_kind']}.retrieved",
                f"Retrieved {winner['item']['name']} for the current context",
                subject_kind=winner["subject_kind"], subject_id=winner["subject_id"],
                correlation_id=correlation_id,
                payload={"arbitration_id": arbitration["id"],
                         "score": winner["score"], "scope": scope or {}})
        return {"winner": winner, "candidates": eligible[:limit],
                "blocked": arbitration["blocked"],
                "arbitration": arbitration, "count": len(eligible)}

    def record_use(self, user_id: str, item_id: str, *,
                   influenced_kind: str, influenced_id: str, how: str,
                   context: dict[str, Any] | None = None, weight: float = 0.5,
                   arbitration_id: str | None = None,
                   correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        if item is None:
            raise KeyError(item_id)
        if item["lifecycle"] not in LIVE_LIFECYCLES:
            raise ValueError(f"A {item['lifecycle']} {item['kind']} cannot be used.")
        if influenced_kind not in INFLUENCE_KINDS:
            raise ValueError(f"Unsupported learned influence kind: {influenced_kind!r}.")
        if not influenced_id.strip() or not how.strip():
            raise ValueError("A learned use requires a target id and influence summary.")
        if arbitration_id:
            arbitration = self.db.query_one(
                "SELECT winner_id FROM arbitration_records WHERE id=? AND user_id=?",
                (arbitration_id, user_id))
            if arbitration is None or arbitration["winner_id"] != item_id:
                raise ValueError("The item did not win the referenced arbitration.")
        usage_id = f"use_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO knowledge_usages (id,user_id,item_id,item_kind,"
            "influenced_kind,influenced_id,context,how,weight,status,correlation_id,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,'awaiting_outcome',?,?)",
            (usage_id, user_id, item_id, item["kind"], influenced_kind,
             influenced_id, json.dumps(context or {}), how[:500],
             max(0.0, min(1.0, float(weight))), correlation_id, now))
        self.db.execute(
            "UPDATE knowledge_items SET last_used_at=?,updated_at=? WHERE id=?"
            " AND user_id=?", (now, now, item_id, user_id))
        self.reputation.record_subject_influence(user_id, item["kind"], item_id)
        if item["lifecycle"] != "used":
            self._transition(user_id, item, "used", "Entered a recorded decision path.",
                             [arbitration_id] if arbitration_id else [], correlation_id)
        self.causal.link(
            user_id, item["kind"], item_id, influenced_kind, influenced_id,
            relation="influenced", weight=weight, correlation_id=correlation_id)
        self.bus.emit(
            user_id, f"{item['kind']}.used", how[:160],
            subject_kind=item["kind"], subject_id=item_id,
            correlation_id=correlation_id,
            payload={"usage_id": usage_id, "influenced_kind": influenced_kind,
                     "influenced_id": influenced_id,
                     "arbitration_id": arbitration_id})
        return self.get_usage(user_id, usage_id)  # type: ignore[return-value]

    def record_outcome(self, user_id: str, usage_id: str, *, verdict: str,
                       detail: str, evidence: list[str] | None = None,
                       correlation_id: str | None = None) -> dict[str, Any]:
        verdict = verdict.upper()
        if verdict not in USAGE_VERDICTS:
            raise ValueError(f"Unknown learned-knowledge outcome: {verdict!r}")
        usage = self.get_usage(user_id, usage_id)
        if usage is None:
            raise KeyError(usage_id)
        if usage["status"] != "awaiting_outcome":
            raise ValueError("This usage already has an outcome.")
        evidence = [str(e).strip() for e in (evidence or []) if str(e).strip()]
        if verdict in ("SUPPORTED", "CONTRADICTED") and not evidence:
            verdict = "INSUFFICIENT EVIDENCE"
            detail += " (no evidence supplied; reputation was not changed)"
        self.db.execute(
            "UPDATE knowledge_usages SET status='resolved',outcome_verdict=?,"
            "outcome_detail=?,outcome_evidence=?,resolved_at=? WHERE id=? AND user_id=?",
            (verdict, detail[:1000], json.dumps(evidence), _now(), usage_id, user_id))
        kind, item_id = usage["item_kind"], usage["item_id"]
        rep = self.reputation.record_subject_outcome(user_id, kind, item_id, verdict)
        item = self.get(user_id, item_id) or {}
        target = item.get("lifecycle")
        if verdict == "SUPPORTED":
            target = "reinforced"
            confidence = min(1.0, float(item.get("confidence") or 0.0) + 0.02)
        elif verdict == "CONTRADICTED":
            confidence = max(0.0, float(item.get("confidence") or 0.0) - 0.08)
            if rep["failures"] >= 3 and rep["successes"] == 0:
                target = "retired"
            elif rep["failures"] >= 2 and rep["failures"] > rep["successes"]:
                target = "contradicted"
            else:
                target = "weakened"
        else:
            confidence = float(item.get("confidence") or 0.0)
        self.db.execute(
            "UPDATE knowledge_items SET confidence=?,updated_at=? WHERE id=? AND user_id=?",
            (round(confidence, 4), _now(), item_id, user_id))
        refreshed = self.get(user_id, item_id, include_details=False) or item
        if target and refreshed.get("lifecycle") != target:
            self._transition(user_id, refreshed, target,
                             f"Observed usage outcome: {verdict}.", evidence,
                             correlation_id)
        if verdict in ("SUPPORTED", "CONTRADICTED"):
            event = (f"{kind}.reinforced" if verdict == "SUPPORTED"
                     else f"{kind}.retired" if target == "retired"
                     else f"{kind}.contradicted" if target == "contradicted"
                     else f"{kind}.weakened")
            self.bus.emit(
                user_id, event, detail[:160], subject_kind=kind, subject_id=item_id,
                correlation_id=correlation_id,
                payload={"usage_id": usage_id, "verdict": verdict,
                         "reputation": rep, "confidence": round(confidence, 4)})
            self.causal.link(
                user_id, "outcome", usage_id, kind, item_id,
                relation="supported" if verdict == "SUPPORTED" else "contradicted",
                weight=0.8, correlation_id=correlation_id)
        if kind == "skill" and verdict == "CONTRADICTED":
            self._revalidate_dependent_principles(
                user_id, item_id, correlation_id=correlation_id)
        result = self.get_usage(user_id, usage_id) or usage
        result["reputation"] = rep
        result["item"] = self.get(user_id, item_id)
        return result

    def _revalidate_dependent_principles(
        self, user_id: str, skill_id: str, *,
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Withdraw Principles whose supporting Skill has materially changed."""
        rows = self.db.query(
            "SELECT DISTINCT ki.id FROM knowledge_items ki "
            "JOIN knowledge_evidence ke ON ke.item_id=ki.id AND ke.user_id=ki.user_id "
            "WHERE ki.user_id=? AND ki.kind='principle' AND ke.evidence_kind='skill' "
            "AND ke.evidence_id=? AND ke.stance='supporting'",
            (user_id, skill_id))
        results: list[dict[str, Any]] = []
        for row in rows:
            principle = self.get(user_id, row["id"])
            if principle is None or principle["lifecycle"] not in LIVE_LIFECYCLES:
                continue
            if principle["lifecycle"] != "weakened":
                self._transition(
                    user_id, principle, "weakened",
                    f"Supporting Skill {skill_id} materially changed.",
                    [skill_id], correlation_id)
                self.bus.emit(
                    user_id, "principle.weakened",
                    "A supporting Skill changed; revalidation is required.",
                    subject_kind="principle", subject_id=principle["id"],
                    correlation_id=correlation_id,
                    payload={"supporting_skill_id": skill_id})
            results.append(self.validate(
                user_id, principle["id"], correlation_id=correlation_id))
        return results

    # ------------------------------------------------------------- correction
    def correct(self, user_id: str, item_id: str, *, action: str, reason: str,
                scope_kind: str | None = None, scope_value: str | None = None,
                evidence: list[str] | None = None,
                correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        if item is None:
            raise KeyError(item_id)
        action = action.lower().strip()
        if not reason.strip():
            raise ValueError("A correction requires a reason for the audit trail.")
        if action in ("forget", "retire", "stop_using"):
            target = "retired"
        elif action in ("weaken", "doesnt_work", "doesn't_work"):
            target = "weakened"
        elif action in ("contradict", "invalid"):
            target = "contradicted"
            self.reputation.record_subject_contradiction(
                user_id, item["kind"], item_id)
        elif action == "outdated":
            target = "outdated"
        elif action in ("rescope", "narrow"):
            if not scope_kind:
                raise ValueError("Rescoping requires scope_kind.")
            self._validate_scope(scope_kind, scope_value)
            previous = {"kind": item["scope_kind"], "value": item.get("scope_value")}
            self.db.execute(
                "UPDATE knowledge_items SET scope_kind=?,scope_value=?,updated_at=?"
                " WHERE id=? AND user_id=?",
                (scope_kind, scope_value, _now(), item_id, user_id))
            self.bus.emit(
                user_id, f"{item['kind']}.rescoped", reason,
                subject_kind=item["kind"], subject_id=item_id,
                correlation_id=correlation_id,
                payload={"from": previous,
                         "to": {"kind": scope_kind, "value": scope_value},
                         "evidence": evidence or []})
            if item["kind"] == "skill":
                self._revalidate_dependent_principles(
                    user_id, item_id, correlation_id=correlation_id)
            return self.get(user_id, item_id)  # type: ignore[return-value]
        else:
            raise ValueError(
                "action must be weaken, contradict, outdated, retire/forget, or rescope")

        if item["lifecycle"] != target:
            self._transition(user_id, item, target, reason, evidence or [],
                             correlation_id)
        if target in ("weakened", "contradicted"):
            reduction = 0.10 if target == "weakened" else 0.20
            self.db.execute(
                "UPDATE knowledge_items SET confidence=max(0,confidence-?),updated_at=?"
                " WHERE id=? AND user_id=?", (reduction, _now(), item_id, user_id))
        self.bus.emit(
            user_id, f"{item['kind']}.{target}", reason,
            subject_kind=item["kind"], subject_id=item_id,
            correlation_id=correlation_id,
            payload={"evidence": evidence or [], "correction": True})
        if item["kind"] == "skill":
            self._revalidate_dependent_principles(
                user_id, item_id, correlation_id=correlation_id)
        return self.get(user_id, item_id)  # type: ignore[return-value]

    # --------------------------------------------------------------- lifecycle
    def _transition(self, user_id: str, item: dict[str, Any], target: str,
                    reason: str, evidence: list[str],
                    correlation_id: str | None) -> None:
        if target not in LIFECYCLES:
            raise ValueError(f"Unknown learned lifecycle: {target!r}")
        previous = str(item["lifecycle"])
        if target == previous:
            return
        if target not in _ALLOWED.get(previous, ()):
            raise ValueError(f"Invalid {item['kind']} transition: {previous} -> {target}.")
        self.db.execute(
            "UPDATE knowledge_items SET lifecycle=?,updated_at=? WHERE id=? AND user_id=?",
            (target, _now(), item["id"], user_id))
        self._record_transition(user_id, item["id"], item["kind"], previous,
                                target, reason, evidence, correlation_id)

    def _record_transition(self, user_id: str, item_id: str, kind: str,
                           previous: str | None, target: str, reason: str,
                           evidence: list[str], correlation_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO knowledge_transitions (id,user_id,item_id,item_kind,"
            "previous_lifecycle,lifecycle,reason,evidence,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ktr_{uuid.uuid4().hex[:12]}", user_id, item_id, kind, previous,
             target, reason[:500], json.dumps(evidence), correlation_id, _now()))

    @staticmethod
    def _validate_scope(scope_kind: str, scope_value: str | None) -> None:
        if scope_kind not in SCOPES:
            raise ValueError(f"Unknown learned knowledge scope: {scope_kind!r}")
        if scope_kind not in ("user", "global") and not (scope_value or "").strip():
            raise ValueError(f"Scope {scope_kind!r} requires scope_value.")

    # ------------------------------------------------------------------ reads
    def get(self, user_id: str, item_id: str, *,
            include_details: bool = True) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM knowledge_items WHERE id=? AND user_id=?",
            (item_id, user_id))
        return self._row(row, include_details=include_details) if row else None

    def list(self, user_id: str, *, kind: str | None = None,
             lifecycle: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if kind is not None and kind not in KINDS:
            raise ValueError(f"Unknown learned knowledge kind: {kind!r}")
        sql = "SELECT * FROM knowledge_items WHERE user_id=?"
        params: list[Any] = [user_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if lifecycle:
            sql += " AND lifecycle=?"
            params.append(lifecycle)
        sql += " ORDER BY updated_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        return [self._row(r) for r in self.db.query(sql, params)]

    def get_usage(self, user_id: str, usage_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM knowledge_usages WHERE id=? AND user_id=?",
            (usage_id, user_id))
        if row is None:
            return None
        usage = dict(row)
        usage["context"] = _loads(usage.get("context"), {})
        usage["outcome_evidence"] = _loads(usage.get("outcome_evidence"), [])
        return usage

    def usages(self, user_id: str, *, item_id: str | None = None,
               pending: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM knowledge_usages WHERE user_id=?"
        params: list[Any] = [user_id]
        if item_id:
            sql += " AND item_id=?"
            params.append(item_id)
        if pending:
            sql += " AND status='awaiting_outcome'"
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        return [u for u in (self.get_usage(user_id, r["id"])
                            for r in self.db.query(sql, params)) if u]

    def explain(self, user_id: str, item_id: str) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        if item is None:
            raise KeyError(item_id)
        arbitrations = self.arbiter.for_subject(user_id, item_id, limit=10)
        causal = {
            "upstream": self.causal.upstream(item["kind"], item_id, user_id=user_id),
            "downstream": self.causal.downstream(item["kind"], item_id,
                                                   user_id=user_id),
        }
        latest_validation = item["validations"][0] if item["validations"] else None
        summary = (
            f"{item['name']} is {item['lifecycle']} with confidence "
            f"{item['confidence']:.2f}. It has {item['supporting_evidence_count']} "
            f"supporting item(s) and {item['counterexample_count']} "
            f"counterexample(s). Reputation is {item['reputation']['reputation']} "
            f"from {item['reputation']['evidence']} observed use outcome(s).")
        if latest_validation:
            summary += " Latest validation: " + latest_validation["reason"]
        return {"item": item, "supporting_evidence": [
                    e for e in item["evidence"] if e["stance"] == "supporting"],
                "counterexamples": [e for e in item["evidence"]
                                    if e["stance"] == "counterexample"],
                "validation_history": item["validations"],
                "lifecycle_history": item["transitions"],
                "usage_history": item["usages"], "arbitrations": arbitrations,
                "causal": causal, "provenance": item["provenance"],
                "summary": summary,
                "note": "Evidence summary only; no private chain-of-thought."}

    def stats(self, user_id: str) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT kind,lifecycle,COUNT(*) AS n FROM knowledge_items WHERE user_id=?"
            " GROUP BY kind,lifecycle", (user_id,))
        by_kind = {kind: {state: 0 for state in LIFECYCLES} for kind in KINDS}
        for row in rows:
            by_kind[row["kind"]][row["lifecycle"]] = int(row["n"])
        return {"by_kind": by_kind,
                "skills": sum(by_kind["skill"].values()),
                "principles": sum(by_kind["principle"].values()),
                "trusted_skills": sum(by_kind["skill"][s]
                                      for s in LIVE_LIFECYCLES),
                "trusted_principles": sum(by_kind["principle"][s]
                                          for s in LIVE_LIFECYCLES)}

    def _row(self, row, *, include_details: bool = True) -> dict[str, Any]:
        item = dict(row)
        item["context"] = _loads(item.get("context"), [])
        item["preconditions"] = _loads(item.get("preconditions"), [])
        item["procedure"] = _loads(item.get("procedure"), [])
        item["provenance"] = _loads(item.get("provenance"), {})
        item["reputation"] = self.reputation.get_subject(
            item["user_id"], item["kind"], item["id"])
        if not include_details:
            return item
        evidence = [dict(r) for r in self.db.query(
            "SELECT * FROM knowledge_evidence WHERE item_id=? AND user_id=?"
            " ORDER BY created_at", (item["id"], item["user_id"]))]
        validations = [dict(r) for r in self.db.query(
            "SELECT * FROM knowledge_validations WHERE item_id=? AND user_id=?"
            " ORDER BY created_at DESC, rowid DESC",
            (item["id"], item["user_id"]))]
        for validation in validations:
            validation["metrics"] = _loads(validation.get("metrics"), {})
        transitions = [dict(r) for r in self.db.query(
            "SELECT * FROM knowledge_transitions WHERE item_id=? AND user_id=?"
            " ORDER BY created_at, rowid", (item["id"], item["user_id"]))]
        for transition in transitions:
            transition["evidence"] = _loads(transition.get("evidence"), [])
        item["evidence"] = evidence
        item["validations"] = validations
        item["transitions"] = transitions
        item["usages"] = self.usages(item["user_id"], item_id=item["id"], limit=50)
        item["supporting_evidence_count"] = sum(
            1 for e in evidence if e["stance"] == "supporting")
        item["counterexample_count"] = sum(
            1 for e in evidence if e["stance"] == "counterexample")
        return item
