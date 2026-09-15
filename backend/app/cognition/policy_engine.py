"""
V8.2 — Adaptive Cognitive Policy (§15).

A user-specific behavioural policy layer that changes only on evidence.

Guarantees:
  * Every policy has: current value, confidence, supporting evidence, last
    update, and a reason.
  * One weak signal never flips a policy. A change requires either an explicit
    user instruction (strong evidence, applied immediately) or repeated
    consistent implicit signals (weak evidence, accumulated).
  * Every change flows through the cognitive event bus — there is no separate
    policy store the panels read from.
  * Everything is reversible: "stop doing that" reverts through the same path.

Policies live in the existing `policies` table, extended in V8.2 with
confidence/evidence columns (migrated additively, so V8.1 rows still load).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# ----------------------------------------------------------------- dimensions
@dataclass(frozen=True)
class PolicyDimension:
    key: str
    values: tuple[str, ...]
    default: str
    description: str


DIMENSIONS: dict[str, PolicyDimension] = {
    "response_depth": PolicyDimension(
        "response_depth", ("brief", "balanced", "detailed"), "balanced",
        "How much detail to include in a reply."),
    "clarification_frequency": PolicyDimension(
        "clarification_frequency", ("low", "normal", "high"), "normal",
        "How often to ask clarifying questions before answering."),
    "interruption_tolerance": PolicyDimension(
        "interruption_tolerance", ("low", "normal", "high"), "normal",
        "How willing the user is to be interrupted proactively."),
    "planning_preference": PolicyDimension(
        "planning_preference", ("none", "light", "structured"), "light",
        "Whether to lay out a plan before acting."),
    "recommendation_preference": PolicyDimension(
        "recommendation_preference", ("options", "single", "none"), "options",
        "Whether to recommend one option or present several."),
    "autonomy_preference": PolicyDimension(
        "autonomy_preference", ("ask_first", "act_low_risk", "act"), "ask_first",
        "How much the user wants done without confirmation."),
    "explanation_density": PolicyDimension(
        "explanation_density", ("minimal", "normal", "verbose"), "normal",
        "How much reasoning-evidence to surface alongside answers."),
    "silence_tolerance": PolicyDimension(
        "silence_tolerance", ("low", "normal", "high"), "normal",
        "How comfortable the user is with the system staying quiet."),
}

# Evidence strengths.
STRONG = 0.9   # the user said it outright
WEAK = 0.25    # inferred from behaviour

# A weak signal must repeat before it changes anything.
WEAK_EVIDENCE_REQUIRED = 3
MIN_CONFIDENCE_TO_APPLY = 0.45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- explicit signals
# Natural-language controls (§20). These are EXPLICIT user instructions, so they
# carry strong evidence and apply immediately.
_EXPLICIT: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\b(just give me the answer|skip the (?:preamble|explanation)|"
                r"be (?:brief|concise|short)|less detail|shorter answers?|"
                r"stop explaining)\b", re.I), "response_depth", "brief"),
    (re.compile(r"\b(explain more|more detail|be (?:thorough|detailed)|"
                r"go deeper|elaborate)\b", re.I), "response_depth", "detailed"),
    (re.compile(r"\b(stop asking( me)?( so many)? questions|don'?t ask|"
                r"quit checking with me|stop checking in)\b", re.I),
     "clarification_frequency", "low"),
    (re.compile(r"\b(ask me (?:first|before)|check with me|confirm with me"
                r"(?: first)?)\b", re.I), "clarification_frequency", "high"),
    (re.compile(r"\b(don'?t interrupt( me)?|stop interrupting|leave me alone|"
                r"don'?t bother me)\b", re.I), "interruption_tolerance", "low"),
    (re.compile(r"\b(tell me when|flag (?:things|it) for me|keep me posted|"
                r"interrupt me if)\b", re.I), "interruption_tolerance", "high"),
    (re.compile(r"\b(stop doing that automatically|don'?t do that automatically|"
                r"stop acting on your own|ask before (?:you )?(?:do|act))\b", re.I),
     "autonomy_preference", "ask_first"),
    (re.compile(r"\b(go ahead without asking|just do it|you can act|"
                r"handle it yourself)\b", re.I), "autonomy_preference",
     "act_low_risk"),
    (re.compile(r"\b(give me a plan|lay out the steps|plan (?:it|this) out|"
                r"step by step)\b", re.I), "planning_preference", "structured"),
    (re.compile(r"\b(no plan(?:ning)? needed|skip the plan|don'?t plan)\b", re.I),
     "planning_preference", "none"),
    (re.compile(r"\b(just (?:pick|choose) one|give me (?:one|your) "
                r"recommendation|what should i do)\b", re.I),
     "recommendation_preference", "single"),
    (re.compile(r"\b(give me (?:the )?options|show me alternatives|"
                r"what are my choices)\b", re.I), "recommendation_preference",
     "options"),
    (re.compile(r"\b(stop (?:showing|telling) me (?:the )?(?:why|reasoning)|"
                r"i don'?t need the explanation)\b", re.I),
     "explanation_density", "minimal"),
    (re.compile(r"\b(show (?:me )?your (?:reasoning|evidence)|tell me why you|"
                r"explain your thinking)\b", re.I), "explanation_density",
     "verbose"),
)


class CognitivePolicyEngine:
    """Evidence-gated behavioural policy for one user."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------------ reads
    def get(self, user_id: str, key: str) -> dict[str, Any]:
        """
        Current policy for a dimension. Returns the default with zero evidence
        when nothing has been learned — never an invented preference.
        """
        dim = DIMENSIONS.get(key)
        if dim is None:
            raise ValueError(f"Unknown policy dimension: {key!r}")
        row = self.db.query_one(
            "SELECT * FROM policies WHERE user_id=? AND key=?", (user_id, key))
        if row is None:
            return {"key": key, "value": dim.default, "confidence": 0.0,
                    "evidence_count": 0, "evidence": [], "rationale":
                    "Default — nothing has been observed about this preference.",
                    "updated_at": None, "is_default": True,
                    "options": list(dim.values), "description": dim.description}
        d = dict(row)
        try:
            evidence = json.loads(d.get("evidence") or "[]")
        except (json.JSONDecodeError, TypeError):
            evidence = []
        return {"key": key, "value": d["value"],
                "confidence": float(d.get("confidence") or 0.0),
                "evidence_count": int(d.get("evidence_count") or 0),
                "evidence": evidence, "rationale": d.get("rationale") or "",
                "updated_at": d.get("updated_at"), "is_default": False,
                "options": list(dim.values), "description": dim.description}

    def list(self, user_id: str) -> list[dict[str, Any]]:
        """Every dimension, learned or default — a complete honest picture."""
        return [self.get(user_id, key) for key in DIMENSIONS]

    def learned(self, user_id: str) -> list[dict[str, Any]]:
        """Only the dimensions that carry real evidence."""
        return [p for p in self.list(user_id) if not p["is_default"]]

    def effective(self, user_id: str) -> dict[str, str]:
        """The values that should actually shape behaviour right now."""
        out: dict[str, str] = {}
        for p in self.list(user_id):
            if p["is_default"] or p["confidence"] >= MIN_CONFIDENCE_TO_APPLY:
                out[p["key"]] = p["value"]
            else:
                out[p["key"]] = DIMENSIONS[p["key"]].default
        return out

    # --------------------------------------------------------------- mutation
    def observe(self, user_id: str, key: str, value: str, *,
                strength: float, evidence: str,
                correlation_id: str | None = None) -> dict[str, Any]:
        """
        Feed one observation into a policy dimension.

        Strong evidence applies immediately. Weak evidence accumulates and only
        changes the value once it has repeated WEAK_EVIDENCE_REQUIRED times.
        """
        dim = DIMENSIONS.get(key)
        if dim is None:
            raise ValueError(f"Unknown policy dimension: {key!r}")
        if value not in dim.values:
            raise ValueError(
                f"Value {value!r} is not valid for {key!r}; expected one of "
                f"{list(dim.values)}")

        current = self.get(user_id, key)
        prior_evidence: list[dict[str, Any]] = list(current["evidence"])
        prior_evidence.append({"value": value, "strength": round(strength, 2),
                               "evidence": evidence[:240], "at": _now()})
        prior_evidence = prior_evidence[-12:]

        # Count only evidence pointing at the proposed value.
        agreeing = [e for e in prior_evidence if e["value"] == value]
        strong_hit = strength >= STRONG
        weak_support = sum(1 for e in agreeing if e["strength"] < STRONG)

        if strong_hit:
            new_value = value
            applied = True
            reason = f"You told me directly: \"{evidence[:160]}\""
        elif weak_support >= WEAK_EVIDENCE_REQUIRED:
            new_value = value
            applied = True
            reason = (f"Inferred from {weak_support} consistent observations, "
                      f"most recently: \"{evidence[:120]}\"")
        else:
            new_value = current["value"]
            applied = False
            reason = (f"Noted, but {WEAK_EVIDENCE_REQUIRED - weak_support} more "
                      f"consistent signal(s) are needed before I change "
                      f"'{key}'.")

        # Confidence is derived from the evidence actually held.
        confidence = min(0.95, sum(
            e["strength"] for e in prior_evidence if e["value"] == new_value))

        changed = applied and new_value != current["value"]
        self._write(user_id, key, new_value, confidence, prior_evidence, reason)

        if changed:
            self.bus.emit(
                user_id, "policy.updated",
                f"How I work with you: {key.replace('_', ' ')} → {new_value}",
                subject_kind="policy", subject_id=key,
                correlation_id=correlation_id,
                payload={"key": key, "from": current["value"], "to": new_value,
                         "confidence": round(confidence, 3), "reason": reason,
                         "evidence_count": len(prior_evidence)})
        elif not applied:
            self.bus.emit(
                user_id, "policy.proposed",
                f"Possible preference noted: {key.replace('_', ' ')} = {value}",
                subject_kind="policy", subject_id=key,
                correlation_id=correlation_id,
                payload={"key": key, "candidate": value, "reason": reason,
                         "applied": False})

        result = self.get(user_id, key)
        result["changed"] = changed
        result["applied"] = applied
        result["reason"] = reason
        return result

    def _write(self, user_id: str, key: str, value: str, confidence: float,
               evidence: list[dict[str, Any]], rationale: str) -> None:
        now = _now()
        blob = json.dumps(evidence, default=str)
        existing = self.db.query_one(
            "SELECT id FROM policies WHERE user_id=? AND key=?", (user_id, key))
        if existing:
            self.db.execute(
                "UPDATE policies SET value=?, rationale=?, confidence=?,"
                " evidence=?, evidence_count=?, updated_at=? WHERE id=?",
                (value, rationale[:400], confidence, blob, len(evidence), now,
                 existing["id"]))
        else:
            self.db.execute(
                "INSERT INTO policies (id,user_id,key,value,rationale,"
                "evidence_count,confidence,evidence,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"pol_{uuid.uuid4().hex[:10]}", user_id, key, value,
                 rationale[:400], len(evidence), confidence, blob, now, now))

    def revert(self, user_id: str, key: str,
               correlation_id: str | None = None) -> dict[str, Any]:
        """Drop a learned policy back to its default. Always available."""
        if key not in DIMENSIONS:
            raise ValueError(f"Unknown policy dimension: {key!r}")
        current = self.get(user_id, key)
        if current["is_default"]:
            return {**current, "reverted": False,
                    "reason": "Nothing learned to revert; already at default."}
        self.db.execute("DELETE FROM policies WHERE user_id=? AND key=?",
                        (user_id, key))
        self.bus.emit(user_id, "policy.reverted",
                      f"Reverted behaviour: {key.replace('_', ' ')}",
                      subject_kind="policy", subject_id=key,
                      correlation_id=correlation_id,
                      payload={"key": key, "was": current["value"],
                               "now": DIMENSIONS[key].default})
        return {**self.get(user_id, key), "reverted": True,
                "reason": f"Reverted '{key}' to default after your instruction."}

    # ------------------------------------------------------- utterance parsing
    def apply_utterance(self, user_id: str, text: str,
                        correlation_id: str | None = None) -> list[dict[str, Any]]:
        """
        Detect explicit behavioural instructions in a message and apply them.

        Only fires on unambiguous phrasing — ordinary conversation must not
        silently rewrite how the system behaves.
        """
        applied: list[dict[str, Any]] = []
        for pattern, key, value in _EXPLICIT:
            match = pattern.search(text)
            if not match:
                continue
            applied.append(self.observe(
                user_id, key, value, strength=STRONG,
                evidence=match.group(0), correlation_id=correlation_id))
        return applied

    # ------------------------------------------------------------ explanation
    def explain(self, user_id: str, key: str) -> dict[str, Any]:
        """'Why do you behave like that?' answered from stored evidence."""
        policy = self.get(user_id, key)
        if policy["is_default"]:
            return {**policy, "explanation": (
                f"I use the default '{policy['value']}' for "
                f"{key.replace('_', ' ')} because I have not observed anything "
                "about your preference here.")}
        lines = [f"- \"{e['evidence']}\" ({e['at']})"
                 for e in policy["evidence"][-4:]]
        return {**policy, "explanation": (
            f"I use '{policy['value']}' for {key.replace('_', ' ')} "
            f"(confidence {policy['confidence']:.2f}) based on "
            f"{policy['evidence_count']} observation(s):\n" + "\n".join(lines))}
