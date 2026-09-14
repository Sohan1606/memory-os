"""
Prediction engine and reality loop.

Predictions are structured, evidence-backed and always evaluated against what
actually happened. Accuracy is computed from resolved predictions only - with
zero resolved predictions the system reports INSUFFICIENT EVIDENCE rather than
a flattering 100%.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

STATUSES = ("open", "correct", "incorrect", "expired", "cancelled")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PredictionEngine:
    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def create(self, user_id: str, statement: str, confidence: float, *,
               evidence: list[str] | None = None, horizon: str | None = None,
               subject_id: str | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        confidence = max(0.01, min(0.99, float(confidence)))
        pid = f"p_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO predictions (id,user_id,subject_id,statement,confidence,"
            "evidence,horizon,status,created_at) VALUES (?,?,?,?,?,?,?, 'open', ?)",
            (pid, user_id, subject_id, statement, confidence,
             json.dumps(evidence or []), horizon, _now()))
        self.bus.emit(user_id, "prediction.created", statement,
                      subject_kind="prediction", subject_id=pid,
                      correlation_id=correlation_id,
                      payload={"confidence": confidence, "evidence": evidence or []})
        return self.get(pid)  # type: ignore[return-value]

    def evaluate(self, user_id: str, prediction_id: str, correct: bool,
                 outcome: str, correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Close a prediction against reality.

        A wrong prediction with high confidence is a genuine surprise and is
        recorded as one, because that is what drives learning.
        """
        row = self.db.query_one("SELECT * FROM predictions WHERE id=? AND user_id=?",
                                (prediction_id, user_id))
        if row is None or row["status"] != "open":
            return None

        status = "correct" if correct else "incorrect"
        self.db.execute(
            "UPDATE predictions SET status=?, outcome=?, evaluated_at=? WHERE id=?",
            (status, outcome, _now(), prediction_id))

        self.bus.emit(user_id, "prediction.evaluated",
                      f"Checked: {row['statement']}", subject_kind="prediction",
                      subject_id=prediction_id, correlation_id=correlation_id,
                      payload={"correct": correct, "outcome": outcome})
        self.bus.emit(user_id, "prediction.correct" if correct else "prediction.incorrect",
                      outcome, subject_kind="prediction", subject_id=prediction_id,
                      correlation_id=correlation_id,
                      payload={"confidence": row["confidence"]})

        # Confident and wrong => genuine surprise worth investigating.
        if not correct and float(row["confidence"]) >= 0.6:
            self.bus.emit(
                user_id, "surprise.detected",
                f"Expected '{row['statement']}' with {int(float(row['confidence']) * 100)}%"
                " confidence, but reality differed.",
                subject_kind="prediction", subject_id=prediction_id,
                correlation_id=correlation_id,
                payload={"magnitude": round(float(row["confidence"]), 2),
                         "outcome": outcome})
        return self.get(prediction_id)

    def get(self, prediction_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM predictions WHERE id=?", (prediction_id,))
        if row is None:
            return None
        d = dict(row)
        try:
            d["evidence"] = json.loads(d["evidence"]) if d["evidence"] else []
        except json.JSONDecodeError:
            d["evidence"] = []
        return d

    def list(self, user_id: str, status: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id FROM predictions WHERE user_id=?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY datetime(created_at) DESC"
        out = []
        for r in self.db.query(sql, params):
            item = self.get(r["id"])
            if item:
                out.append(item)
        return out

    def accuracy(self, user_id: str) -> dict[str, Any]:
        """
        Calibration report. Returns `insufficient_evidence` when nothing has been
        resolved yet - never a fabricated score.
        """
        rows = self.db.query(
            "SELECT status, confidence FROM predictions WHERE user_id=?"
            " AND status IN ('correct','incorrect')", (user_id,))
        resolved = len(rows)
        if resolved == 0:
            return {"resolved": 0, "accuracy": None, "brier": None,
                    "calibration": "INSUFFICIENT EVIDENCE",
                    "detail": "No predictions have been resolved yet."}

        correct = sum(1 for r in rows if r["status"] == "correct")
        # Brier score over the binary outcome: lower is better-calibrated.
        brier = sum((float(r["confidence"]) - (1.0 if r["status"] == "correct" else 0.0)) ** 2
                    for r in rows) / resolved
        accuracy = correct / resolved
        if resolved < 5:
            calibration = "INSUFFICIENT EVIDENCE"
        elif brier <= 0.15:
            calibration = "WELL CALIBRATED"
        elif brier <= 0.25:
            calibration = "REASONABLE"
        else:
            calibration = "POORLY CALIBRATED"
        return {"resolved": resolved, "correct": correct,
                "accuracy": round(accuracy, 3), "brier": round(brier, 3),
                "calibration": calibration,
                "detail": f"{correct}/{resolved} resolved predictions were correct."}

    # ------------------------------------------------------------- generation
    def assess_world(self, user_id: str, world, *,
                     correlation_id: str | None = None) -> list[dict[str, Any]]:
        """
        Generate real, evidence-backed predictions from current world state.

        Only produces a prediction when there is a concrete signal (an at-risk
        item, or a dated commitment). No signal means no prediction.
        """
        created: list[dict[str, Any]] = []
        open_statements = {p["statement"] for p in self.list(user_id, status="open")}

        for item in world.list(user_id, state="at_risk"):
            statement = f"'{item['label']}' is at risk of slipping."
            if statement in open_statements:
                continue
            created.append(self.create(
                user_id, statement, 0.55,
                evidence=[f"World entity {item['id']} is flagged at_risk.",
                          f"Confidence in that entity: {item['confidence']:.2f}"],
                horizon="short", subject_id=item["id"],
                correlation_id=correlation_id))

        for item in world.due_soon(user_id, days=7):
            statement = f"'{item['label']}' will be completed on time."
            if statement in open_statements:
                continue
            created.append(self.create(
                user_id, statement, 0.5,
                evidence=[f"Due at {item['due_at']}.",
                          f"Current state: {item['state']}."],
                horizon="week", subject_id=item["id"],
                correlation_id=correlation_id))
        return created
