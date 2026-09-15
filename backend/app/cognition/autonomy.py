"""
Autonomy governor, trust calibration and attention/intervention intelligence.

Three separate dimensions that must never be conflated:
  CONFIDENCE  - how likely a memory is to be true
  REPUTATION  - how well acting on it has worked out
  AUTHORITY   - whether the system may act without asking

A high-confidence memory ("I prefer AWS") informs a recommendation. It does not
authorize a production deployment.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

# Escalating autonomy levels. Each includes the powers of those before it.
LEVELS = ("observe", "assist", "prepare", "act_low_risk", "act")

# V8.2 §20 — the governor's five possible dispositions. `decision` remains
# act/ask for backwards compatibility; `disposition` carries the finer answer.
ACT = "ACT"
ASK = "ASK"
WAIT = "WAIT"
DO_NOTHING = "DO_NOTHING"
BLOCKED = "BLOCKED"
DISPOSITIONS = (ACT, ASK, WAIT, DO_NOTHING, BLOCKED)
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}

DECISIONS = ("ignore", "monitor", "prepare", "mention", "ask", "act")

CAPABILITIES = ("memory", "planning", "recommendation", "prediction", "tool_use",
                "action")

# Risk classes for actions the system might take.
RISK = {
    "read": 0.05, "recommend": 0.1, "remember": 0.15, "update_memory": 0.3,
    "delete_memory": 0.6, "external_write": 0.85, "irreversible": 0.95,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TrustModel:
    """Evidence-based reliability per capability. No invented scores."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def record(self, user_id: str, capability: str, success: bool) -> dict[str, Any]:
        if capability not in CAPABILITIES:
            raise ValueError(f"Unknown capability: {capability!r}")
        row = self.db.query_one(
            "SELECT * FROM trust_records WHERE user_id=? AND capability=?",
            (user_id, capability))
        if row is None:
            self.db.execute(
                "INSERT INTO trust_records (user_id,capability,successes,failures,"
                "updated_at) VALUES (?,?,?,?,?)",
                (user_id, capability, 1 if success else 0, 0 if success else 1, _now()))
        else:
            col = "successes" if success else "failures"
            self.db.execute(
                f"UPDATE trust_records SET {col}={col}+1, updated_at=?"
                " WHERE user_id=? AND capability=?", (_now(), user_id, capability))

        score = self.score(user_id, capability)
        self.bus.emit(user_id, "trust.changed",
                      f"{capability} reliability: {score['label']}",
                      subject_kind="trust", subject_id=capability,
                      payload=score)
        return score

    def score(self, user_id: str, capability: str) -> dict[str, Any]:
        """
        Laplace-smoothed reliability with an explicit evidence threshold.

        Under 3 observations we refuse to publish a score - a single success is
        not evidence of reliability.
        """
        row = self.db.query_one(
            "SELECT * FROM trust_records WHERE user_id=? AND capability=?",
            (user_id, capability))
        s = int(row["successes"]) if row else 0
        f = int(row["failures"]) if row else 0
        total = s + f
        if total < 3:
            return {"capability": capability, "successes": s, "failures": f,
                    "total": total, "reliability": None,
                    "label": "INSUFFICIENT EVIDENCE"}
        reliability = (s + 1) / (total + 2)  # Laplace smoothing
        if reliability >= 0.85:
            label = "RELIABLE"
        elif reliability >= 0.6:
            label = "MIXED"
        else:
            label = "UNRELIABLE"
        return {"capability": capability, "successes": s, "failures": f,
                "total": total, "reliability": round(reliability, 3), "label": label}

    def all(self, user_id: str) -> list[dict[str, Any]]:
        return [self.score(user_id, c) for c in CAPABILITIES]


class AutonomyGovernor:
    """Decides what the system may do on its own, and what needs approval."""

    def __init__(self, db, bus, trust: TrustModel) -> None:
        self.db = db
        self.bus = bus
        self.trust = trust
        self._level: dict[str, str] = {}

    def level(self, user_id: str) -> str:
        if user_id in self._level:
            return self._level[user_id]
        row = self.db.query_one(
            "SELECT value FROM policies WHERE user_id=? AND key='autonomy_level'",
            (user_id,))
        level = row["value"] if row and row["value"] in LEVEL_INDEX else "assist"
        self._level[user_id] = level
        return level

    def set_level(self, user_id: str, level: str, reason: str = "") -> str:
        if level not in LEVEL_INDEX:
            raise ValueError(f"Unknown autonomy level: {level!r}")
        previous = self.level(user_id)
        now = _now()
        existing = self.db.query_one(
            "SELECT id FROM policies WHERE user_id=? AND key='autonomy_level'", (user_id,))
        if existing:
            self.db.execute("UPDATE policies SET value=?, rationale=?, updated_at=?"
                            " WHERE id=?", (level, reason, now, existing["id"]))
        else:
            self.db.execute(
                "INSERT INTO policies (id,user_id,key,value,rationale,created_at,"
                "updated_at) VALUES (?,?,?,?,?,?,?)",
                (f"pol_{uuid.uuid4().hex[:10]}", user_id, "autonomy_level", level,
                 reason, now, now))
        self._level[user_id] = level
        self.bus.emit(user_id, "autonomy.changed",
                      f"Autonomy: {previous} → {level}", subject_kind="autonomy",
                      subject_id="autonomy_level",
                      payload={"from": previous, "to": level, "reason": reason})
        return level

    def authorize(self, user_id: str, action: str, *, risk_class: str = "read",
                  confidence: float = 0.7, reversible: bool = True,
                  correlation_id: str | None = None) -> dict[str, Any]:
        """
        Decide whether an action may proceed.

        Combines the user's autonomy level, the action's risk, reversibility and
        the system's own demonstrated reliability. Irreversible or high-risk work
        always requires approval regardless of autonomy level.
        """
        risk = RISK.get(risk_class, 0.5)
        level = self.level(user_id)
        level_idx = LEVEL_INDEX[level]
        reliability = self.trust.score(user_id, "action")

        reasons: list[str] = [f"Autonomy level is '{level}'.",
                              f"Risk class '{risk_class}' ({risk:.2f})."]

        if not reversible or risk >= RISK["external_write"]:
            allowed, decision = False, "ask"
            reasons.append("Action is irreversible or high-impact, so it needs approval.")
        elif level_idx <= LEVEL_INDEX["observe"]:
            allowed, decision = False, "ask"
            reasons.append("Observe-only mode: nothing is performed automatically.")
        elif risk <= RISK["remember"] and level_idx >= LEVEL_INDEX["assist"]:
            allowed, decision = True, "act"
            reasons.append("Low-risk and reversible.")
        elif risk <= RISK["update_memory"] and level_idx >= LEVEL_INDEX["act_low_risk"]:
            allowed, decision = True, "act"
            reasons.append("Moderate risk permitted at this autonomy level.")
        else:
            allowed, decision = False, "ask"
            reasons.append("Risk exceeds what this autonomy level permits.")

        # Demonstrated unreliability withdraws automatic action.
        if allowed and reliability["label"] == "UNRELIABLE":
            allowed, decision = False, "ask"
            reasons.append("Recent action failures reduced automatic authority.")

        if confidence < 0.4 and allowed:
            allowed, decision = False, "ask"
            reasons.append(f"Confidence is only {confidence:.2f}.")

        # ---- V8.2 §20: refine the binary act/ask into a five-state disposition.
        disposition, why = self._disposition(
            allowed=allowed, risk=risk, risk_class=risk_class, level=level,
            level_idx=level_idx, reversible=reversible, confidence=confidence,
            reliability=reliability)
        reasons.append(why)

        self.bus.emit(user_id,
                      "action.authorized" if allowed else "action.denied",
                      f"{action}: {disposition}",
                      subject_kind="action", subject_id=action,
                      correlation_id=correlation_id,
                      payload={"allowed": allowed, "decision": decision,
                               "disposition": disposition,
                               "risk": risk, "reasons": reasons})
        return {"action": action, "allowed": allowed,
                # `decision` stays act/ask for V8/V8.1 compatibility.
                "decision": decision,
                "disposition": disposition, "disposition_reason": why,
                "risk": risk, "level": level, "reasons": reasons,
                "reliability": reliability}

    def _disposition(self, *, allowed: bool, risk: float, risk_class: str,
                     level: str, level_idx: int, reversible: bool,
                     confidence: float,
                     reliability: dict[str, Any]) -> tuple[str, str]:
        """
        Map the authorisation outcome onto ACT / ASK / WAIT / DO_NOTHING / BLOCKED.

        The distinction matters: ASK means "I need your approval", WAIT means
        "I need more evidence before I can even ask", DO_NOTHING means "acting
        here would be noise", and BLOCKED means "a hard rule forbids this".
        """
        if not reversible or risk >= RISK["external_write"]:
            return (BLOCKED,
                    "BLOCKED: this is irreversible or externally visible, so it "
                    "can never run without your explicit instruction.")

        if reliability["label"] == "UNRELIABLE":
            return (BLOCKED,
                    "BLOCKED: my recent track record on this kind of action is "
                    "demonstrably poor, so I have withdrawn my own authority.")

        if allowed:
            return (ACT, "ACT: low risk, reversible, and within your autonomy "
                         "level.")

        if level == "observe":
            return (DO_NOTHING,
                    "DO_NOTHING: you have me in observe-only mode, so the "
                    "correct behaviour is to stay out of the way.")

        if confidence < 0.4:
            return (WAIT,
                    f"WAIT: at {confidence:.2f} confidence I do not yet have "
                    "enough evidence to act or even to ask a useful question.")

        if reliability["label"] == "INSUFFICIENT EVIDENCE":
            return (WAIT,
                    "WAIT: I have no track record for this capability yet, so I "
                    "am holding until there is evidence either way.")

        return (ASK, "ASK: I could do this, but the risk is above what you have "
                     "authorised me to take unilaterally.")


class AttentionEngine:
    """
    Decides what deserves attention - including deciding to stay silent.

    Suppressed interventions are recorded, because "the system chose not to
    interrupt" is a real decision worth inspecting.
    """

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def consider(self, user_id: str, topic: str, *, importance: float,
                 urgency: float, confidence: float, interruption_cost: float = 0.4,
                 correlation_id: str | None = None) -> dict[str, Any]:
        expected_value = importance * urgency * confidence
        net = expected_value - interruption_cost

        if confidence < 0.35:
            decision = "monitor"
        elif net <= 0:
            decision = "ignore" if expected_value < 0.15 else "monitor"
        elif net < 0.15:
            decision = "prepare"
        elif net < 0.3:
            decision = "mention"
        elif confidence < 0.7:
            decision = "ask"
        else:
            decision = "act"

        rationale = (
            f"importance {importance:.2f} × urgency {urgency:.2f} × confidence "
            f"{confidence:.2f} = {expected_value:.2f}; interruption cost "
            f"{interruption_cost:.2f}; net {net:.2f}")

        iid = f"iv_{uuid.uuid4().hex[:10]}"
        self.db.execute(
            "INSERT INTO interventions (id,user_id,topic,decision,expected_value,"
            "urgency,confidence,interruption_cost,rationale,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (iid, user_id, topic, decision, expected_value, urgency, confidence,
             interruption_cost, rationale, _now()))

        self.bus.emit(user_id, "intervention.considered", f"{topic} → {decision}",
                      subject_kind="intervention", subject_id=iid,
                      correlation_id=correlation_id,
                      payload={"decision": decision, "rationale": rationale,
                               "expected_value": round(expected_value, 3)})
        if decision in ("ignore", "monitor"):
            self.bus.emit(user_id, "intervention.suppressed",
                          f"Chose not to interrupt about: {topic}",
                          subject_kind="intervention", subject_id=iid,
                          correlation_id=correlation_id,
                          payload={"rationale": rationale})
        elif decision in ("mention", "ask", "act"):
            self.bus.emit(user_id, "intervention.presented", topic,
                          subject_kind="intervention", subject_id=iid,
                          correlation_id=correlation_id,
                          payload={"decision": decision, "why_now": rationale})

        return {"id": iid, "topic": topic, "decision": decision,
                "expected_value": round(expected_value, 3), "net": round(net, 3),
                "why_now": rationale, "surface": decision in ("mention", "ask", "act")}

    def recent(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM interventions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit))]

    def precision(self, user_id: str) -> dict[str, Any]:
        """How often surfaced interventions were actually useful."""
        rows = self.db.query(
            "SELECT decision, COUNT(*) AS n FROM interventions WHERE user_id=?"
            " GROUP BY decision", (user_id,))
        counts = {r["decision"]: r["n"] for r in rows}
        surfaced = sum(counts.get(d, 0) for d in ("mention", "ask", "act"))
        suppressed = sum(counts.get(d, 0) for d in ("ignore", "monitor"))
        total = surfaced + suppressed
        if total == 0:
            return {"total": 0, "surfaced": 0, "suppressed": 0,
                    "restraint": None, "detail": "INSUFFICIENT EVIDENCE"}
        return {"total": total, "surfaced": surfaced, "suppressed": suppressed,
                "restraint": round(suppressed / total, 3),
                "detail": f"{suppressed}/{total} considerations stayed silent."}
