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
from datetime import datetime, timedelta, timezone
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
               evaluation_window_days: float | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Record a prediction.

        `evaluation_window_days` (v8.3 §19) declares when this should be
        checked. When the window passes with no evidence the prediction becomes
        UNRESOLVED - it is never scored correct or incorrect by assumption.
        """
        confidence = max(0.01, min(0.99, float(confidence)))
        pid = f"p_{uuid.uuid4().hex[:12]}"
        expected_at = None
        if evaluation_window_days is not None:
            expected_at = (datetime.now(timezone.utc)
                           + timedelta(days=float(evaluation_window_days))
                           ).isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO predictions (id,user_id,subject_id,statement,confidence,"
            "evidence,horizon,status,created_at,evaluation_window_days,"
            "expected_evaluation_at) VALUES (?,?,?,?,?,?,?, 'open', ?,?,?)",
            (pid, user_id, subject_id, statement, confidence,
             json.dumps(evidence or []), horizon, _now(),
             evaluation_window_days, expected_at))
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

    # ------------------------------------------------------- v8.2 observation
    def observe(self, user_id: str, prediction_id: str, observation: str, *,
                supports: bool | None = None, evidence: list[str] | None = None,
                correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Evaluate a prediction against an OBSERVED reality (§13).

        The critical guard: a prediction is NOT marked correct merely because
        the user said something related. When `supports` is not supplied we
        require the observation to carry an explicit resolution signal; if it
        does not, the prediction stays open and we say why.

        On a genuine evaluation we compute error, surprise and a learning
        signal, and store them alongside the outcome.
        """
        row = self.db.query_one("SELECT * FROM predictions WHERE id=? AND user_id=?",
                                (prediction_id, user_id))
        if row is None:
            raise KeyError(prediction_id)
        if row["status"] != "open":
            return None

        verdict = supports
        if verdict is None:
            verdict = self._resolution_signal(observation)

        if verdict is None:
            # Not defensible as evidence either way — record the observation and
            # leave the prediction open rather than inventing a result.
            self.bus.emit(
                user_id, "prediction.evaluated",
                f"Observation did not resolve: {row['statement']}",
                subject_kind="prediction", subject_id=prediction_id,
                correlation_id=correlation_id,
                payload={"resolved": False, "observation": observation[:200],
                         "reason": "INSUFFICIENT EVIDENCE"})
            return {**self.get(prediction_id),  # type: ignore[dict-item]
                    "resolved": False,
                    "reason": ("INSUFFICIENT EVIDENCE — that observation does not "
                               "clearly confirm or refute this prediction, so it "
                               "remains open.")}

        confidence = float(row["confidence"])
        # Error is the distance between what we asserted and what happened.
        error = abs(confidence - (1.0 if verdict else 0.0))
        # Surprise is error weighted by how sure we were: confidently wrong is
        # the only genuinely surprising outcome.
        surprise = round(error * confidence if not verdict else error * (1 - confidence), 3)

        if not verdict and confidence >= 0.7:
            learning = ("Was confidently wrong — lower confidence for this class "
                        "of prediction until calibration improves.")
        elif not verdict:
            learning = "Was wrong but not confident; calibration looks reasonable."
        elif confidence < 0.4:
            learning = ("Was right while under-confident — this class of "
                        "prediction may deserve more confidence.")
        else:
            learning = "Was right and appropriately confident; no change needed."

        self.db.execute(
            "UPDATE predictions SET status=?, outcome=?, evaluated_at=?,"
            " error=?, surprise=?, learning_signal=? WHERE id=?",
            ("correct" if verdict else "incorrect", observation[:400], _now(),
             round(error, 3), surprise, learning, prediction_id))

        self.bus.emit(user_id, "prediction.evaluated",
                      f"Checked against reality: {row['statement']}",
                      subject_kind="prediction", subject_id=prediction_id,
                      correlation_id=correlation_id,
                      payload={"correct": verdict, "observation": observation[:200],
                               "error": round(error, 3), "surprise": surprise,
                               "evidence": evidence or []})
        self.bus.emit(user_id,
                      "prediction.correct" if verdict else "prediction.incorrect",
                      observation[:200], subject_kind="prediction",
                      subject_id=prediction_id, correlation_id=correlation_id,
                      payload={"confidence": confidence,
                               "learning_signal": learning})

        if not verdict and confidence >= 0.6:
            self.bus.emit(
                user_id, "surprise.detected",
                f"Expected '{row['statement']}' with {int(confidence * 100)}% "
                "confidence, but reality differed.",
                subject_kind="prediction", subject_id=prediction_id,
                correlation_id=correlation_id,
                payload={"magnitude": surprise, "outcome": observation[:200],
                         "learning_signal": learning})

        result = self.get(prediction_id)
        assert result is not None
        return {**result, "resolved": True, "correct": verdict,
                "error": round(error, 3), "surprise": surprise,
                "learning_signal": learning}

    @staticmethod
    def _resolution_signal(observation: str) -> bool | None:
        """
        Does this observation actually resolve a prediction?

        Returns True/False only for explicit confirmation or refutation, and
        None when the text merely mentions the topic.
        """
        import re
        text = observation.lower()
        confirmed = re.search(
            r"\b(it happened|that happened|came true|was right|were right|"
            r"confirmed|did (?:finish|ship|complete|happen)|finished on time|"
            r"shipped on time|completed as expected|turned out (?:to be )?"
            r"(?:right|correct|true))\b", text)
        refuted = re.search(
            r"\b(didn'?t happen|did not happen|never happened|was wrong|"
            r"were wrong|refuted|slipped|missed (?:the )?deadline|fell through|"
            r"cancelled|canceled|failed to (?:finish|ship|complete)|"
            r"turned out (?:to be )?(?:wrong|false|incorrect))\b", text)
        if confirmed and not refuted:
            return True
        if refuted and not confirmed:
            return False
        return None

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

    # ------------------------------------------------- v8.3 §19 windows
    def due_for_evaluation(self, user_id: str) -> list[dict[str, Any]]:
        """
        Open predictions whose declared evaluation window has passed.

        These are *candidates for resolution*, not resolutions. Silence is not
        evidence: the caller must supply a real observation, or the prediction
        stays UNRESOLVED.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = self.db.query(
            "SELECT * FROM predictions WHERE user_id=? AND status='open'"
            " AND expected_evaluation_at IS NOT NULL"
            " AND datetime(expected_evaluation_at) <= datetime(?)"
            " ORDER BY expected_evaluation_at", (user_id, now))
        return [self.get(r["id"]) for r in rows]  # type: ignore[misc]

    def mark_unresolved(self, user_id: str, prediction_id: str, *,
                        reason: str = "No evidence was observed within the "
                                      "evaluation window.",
                        correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Close a prediction as UNRESOLVED. This is a real, honest outcome and is
        deliberately excluded from accuracy scoring - guessing either way would
        corrupt calibration.
        """
        row = self.db.query_one(
            "SELECT * FROM predictions WHERE id=? AND user_id=?",
            (prediction_id, user_id))
        if row is None or row["status"] != "open":
            return None
        self.db.execute(
            "UPDATE predictions SET status='unresolved', outcome=?,"
            " evaluated_at=? WHERE id=?",
            (f"UNRESOLVED — {reason}",
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             prediction_id))
        self.bus.emit(user_id, "outcome.unresolved", row["statement"],
                      subject_kind="prediction", subject_id=prediction_id,
                      correlation_id=correlation_id,
                      payload={"reason": reason})
        return self.get(prediction_id)

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
