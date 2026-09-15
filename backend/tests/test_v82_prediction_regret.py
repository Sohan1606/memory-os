"""
V8.2 §13 / §14 — the prediction -> reality -> learning loop, and the
consequence / regret model.

Two honesty rules dominate these tests:
  * A prediction is only scored when reality actually resolved it.
  * Regret is evidence-based. A bad outcome with no recorded expectation and no
    cited evidence is INSUFFICIENT EVIDENCE, not a number.
"""
import uuid

import pytest


@pytest.fixture
def predictions(runtime):
    return runtime.cognition.predictions


@pytest.fixture
def decisions(runtime):
    return runtime.cognition.decisions


@pytest.fixture
def u():
    return f"pred-{uuid.uuid4().hex[:8]}"


def open_prediction(predictions, u, statement="The migration will finish on time",
                    confidence=0.8):
    return predictions.create(u, statement, confidence)


# ------------------------------------------------- §13 prediction -> reality
def test_an_unrelated_observation_does_not_resolve_a_prediction(predictions, u):
    """The core guard: mentioning the topic is not evidence of the outcome."""
    p = open_prediction(predictions, u)
    result = predictions.observe(u, p["id"],
                                 "I was thinking about the migration today")
    assert result["resolved"] is False
    assert "INSUFFICIENT EVIDENCE" in result["reason"]
    assert predictions.get(p["id"])["status"] == "open"


def test_an_explicit_confirmation_resolves_the_prediction(predictions, u):
    p = open_prediction(predictions, u)
    result = predictions.observe(u, p["id"],
                                 "The migration came true, it finished on time")
    assert result["resolved"] is True
    assert result["correct"] is True
    assert predictions.get(p["id"])["status"] == "correct"


def test_an_explicit_refutation_resolves_the_prediction(predictions, u):
    p = open_prediction(predictions, u)
    result = predictions.observe(u, p["id"],
                                 "It slipped, we missed the deadline entirely")
    assert result["resolved"] is True
    assert result["correct"] is False
    assert predictions.get(p["id"])["status"] == "incorrect"


def test_an_explicit_supports_flag_overrides_text_parsing(predictions, u):
    p = open_prediction(predictions, u)
    result = predictions.observe(u, p["id"], "see the deploy log", supports=True)
    assert result["resolved"] is True and result["correct"] is True


def test_ambiguous_observation_mentioning_both_does_not_resolve(predictions, u):
    p = open_prediction(predictions, u)
    result = predictions.observe(
        u, p["id"], "Part of it came true but the rest missed the deadline")
    assert result["resolved"] is False


# ------------------------------------------------------- §13 learning signal
def test_confidently_wrong_produces_a_strong_learning_signal(predictions, u):
    p = open_prediction(predictions, u, confidence=0.9)
    result = predictions.observe(u, p["id"], "It didn't happen at all")
    assert result["error"] == pytest.approx(0.9, abs=0.01)
    assert result["surprise"] > 0.5
    assert "confidently wrong" in result["learning_signal"].lower()


def test_being_wrong_while_unsure_is_not_very_surprising(predictions, u):
    p = open_prediction(predictions, u, confidence=0.35)
    result = predictions.observe(u, p["id"], "It didn't happen")
    assert result["surprise"] < 0.2
    assert "not confident" in result["learning_signal"].lower()


def test_right_while_underconfident_is_flagged_for_calibration(predictions, u):
    p = open_prediction(predictions, u, confidence=0.3)
    result = predictions.observe(u, p["id"], "It came true")
    assert result["correct"] is True
    assert "under-confident" in result["learning_signal"].lower()


def test_right_and_confident_needs_no_change(predictions, u):
    p = open_prediction(predictions, u, confidence=0.85)
    result = predictions.observe(u, p["id"], "It came true, finished on time")
    assert "no change needed" in result["learning_signal"].lower()


def test_error_and_surprise_are_persisted(runtime, predictions, u):
    p = open_prediction(predictions, u, confidence=0.9)
    predictions.observe(u, p["id"], "It didn't happen")
    row = runtime.db.query_one("SELECT error, surprise, learning_signal FROM"
                               " predictions WHERE id=?", (p["id"],))
    assert row["error"] is not None
    assert row["surprise"] is not None
    assert row["learning_signal"]


def test_a_confident_miss_emits_surprise(runtime, predictions, u):
    p = open_prediction(predictions, u, confidence=0.9)
    predictions.observe(u, p["id"], "It didn't happen", correlation_id="sur-1")
    types = {e.type for e in runtime.cognition.bus.for_correlation("sur-1")}
    assert "surprise.detected" in types
    assert "prediction.incorrect" in types


def test_a_resolved_prediction_cannot_be_resolved_twice(predictions, u):
    p = open_prediction(predictions, u)
    predictions.observe(u, p["id"], "It came true")
    assert predictions.observe(u, p["id"], "Actually it didn't happen") is None


def test_observing_an_unknown_prediction_raises(predictions, u):
    with pytest.raises(KeyError):
        predictions.observe(u, "nope", "It came true")


def test_accuracy_reflects_only_resolved_predictions(predictions, u):
    a = open_prediction(predictions, u, "A will happen", 0.8)
    b = open_prediction(predictions, u, "B will happen", 0.8)
    open_prediction(predictions, u, "C will happen", 0.8)  # stays open
    predictions.observe(u, a["id"], "It came true")
    predictions.observe(u, b["id"], "It didn't happen")
    report = predictions.accuracy(u)
    assert report["resolved"] == 2, "the open prediction must not be counted"
    assert report["correct"] == 1
    assert report["accuracy"] == 0.5
    # Two data points is not enough to claim calibration.
    assert report["calibration"] == "INSUFFICIENT EVIDENCE"


# ------------------------------------------------- §14 evidence-based regret
def test_a_positive_outcome_has_zero_regret(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL",
                         expected_outcome="Fewer migration problems")
    result = decisions.resolve(u, d["id"], "It went smoothly", positive=True)
    assert result["regret"] == 0.0
    assert "No regret" in result["regret_basis"]


def test_a_bad_outcome_with_no_evidence_is_not_scored(decisions, u):
    """A bad result is not by itself proof of a bad decision."""
    d = decisions.record(u, "Chose Postgres over MySQL")
    result = decisions.resolve(u, d["id"], "Things went badly", positive=False)
    assert result["regret"] is None
    assert result["regret_label"] == "INSUFFICIENT EVIDENCE"
    assert "INSUFFICIENT EVIDENCE" in result["regret_basis"]


def test_a_violated_explicit_expectation_grounds_regret(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL",
                         expected_outcome="Migration completes in one weekend")
    result = decisions.resolve(u, d["id"], "It took three weeks", positive=False)
    assert result["regret"] == pytest.approx(0.5, abs=0.01)
    assert "explicit expectation" in result["regret_basis"]


def test_cited_evidence_increases_regret(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL",
                         expected_outcome="Completes in one weekend")
    result = decisions.resolve(
        u, d["id"], "It took three weeks", positive=False,
        regret_evidence=["three weekends of downtime logged",
                         "two rollbacks recorded"])
    assert result["regret"] > 0.5
    assert len(result["regret_evidence"]) == 2
    assert "cited evidence" in result["regret_basis"]


def test_opportunity_cost_is_recorded_and_counted(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL")
    result = decisions.resolve(
        u, d["id"], "It took three weeks", positive=False,
        opportunity_cost="The API redesign was delayed by a month")
    assert result["regret"] is not None
    assert "API redesign" in result["opportunity_cost"]
    assert "opportunity cost" in result["regret_basis"]


def test_empty_evidence_strings_do_not_count_as_evidence(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL")
    result = decisions.resolve(u, d["id"], "Bad", positive=False,
                               regret_evidence=["", "   "])
    assert result["regret"] is None


def test_second_order_and_delayed_consequences_are_recorded(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL")
    result = decisions.resolve(
        u, d["id"], "Worked out", positive=True,
        second_order="The team had to learn new tooling",
        delayed_consequences="Backup costs rose three months later")
    assert "new tooling" in result["second_order"]
    assert "Backup costs" in result["delayed_consequences"]


def test_unobserved_consequences_stay_null_not_invented(decisions, u):
    d = decisions.record(u, "Chose Postgres over MySQL")
    result = decisions.resolve(u, d["id"], "Worked out", positive=True)
    assert result["second_order"] is None
    assert result["delayed_consequences"] is None
    assert result["opportunity_cost"] is None


def test_regret_emits_an_event_only_when_grounded(runtime, decisions, u):
    d = decisions.record(u, "Chose Postgres", expected_outcome="One weekend")
    decisions.resolve(u, d["id"], "Took three weeks", positive=False,
                      regret_evidence=["logged downtime"],
                      correlation_id="reg-1")
    types = {e.type for e in runtime.cognition.bus.for_correlation("reg-1")}
    assert "regret.detected" in types


def test_unscored_regret_emits_no_regret_event(runtime, decisions, u):
    d = decisions.record(u, "Chose Postgres")
    decisions.resolve(u, d["id"], "Went badly", positive=False,
                      correlation_id="reg-2")
    types = {e.type for e in runtime.cognition.bus.for_correlation("reg-2")}
    assert "regret.detected" not in types


def test_resolving_an_unknown_decision_returns_none(decisions, u):
    assert decisions.resolve(u, "nope", "x", positive=True) is None
