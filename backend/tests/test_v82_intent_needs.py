"""
V8.2 §10 / §11 — probabilistic intent evolution and need detection V2.

Hard rule under test: a QUESTION never creates an intent fact. Intent changes
are probabilistic, carry uncertainty, and always state why they changed.
"""
import pytest


@pytest.fixture
def intents(runtime):
    return runtime.cognition.intent_evolution


@pytest.fixture
def needs(runtime):
    return runtime.cognition.needs


@pytest.fixture
def u(runtime):
    """A user id unique per test, so intent history never leaks between tests."""
    import uuid
    return f"intent-{uuid.uuid4().hex[:8]}"


# ------------------------------------------------- questions never assert intent
@pytest.mark.parametrize("question", [
    "should I migrate to Postgres?",
    "what am I working on?",
    "do you think I want to learn Rust?",
    "is my goal still to ship by Friday?",
])
def test_a_question_never_creates_an_intent(intents, u, question):
    report = intents.observe(u, question)
    assert report["is_question"] is True
    assert report["changed"] is False
    assert report["current_intent"] is None
    assert "question" in report["note"].lower()


def test_a_question_does_not_overwrite_an_existing_intent(intents, u):
    intents.observe(u, "I want to migrate the database to Postgres")
    before = intents.observe(u, "what am I working on?")["current_intent"]
    after = intents.observe(u, "should I switch to MySQL instead?")["current_intent"]
    assert before["label"] == after["label"]


# --------------------------------------------------------------- intent capture
def test_a_statement_of_purpose_creates_an_intent(intents, u):
    report = intents.observe(u, "I want to migrate the database to Postgres")
    assert report["current_intent"] is not None
    assert "postgres" in report["current_intent"]["label"].lower()
    assert 0.0 < report["confidence"] <= 1.0
    assert report["uncertainty"] == pytest.approx(1 - report["confidence"], abs=0.01)


def test_intent_carries_uncertainty_not_false_certainty(intents, u):
    report = intents.observe(u, "I'm trying to learn Rust properly")
    assert report["confidence"] < 1.0
    assert report["uncertainty"] > 0.0


def test_no_statement_of_purpose_leaves_intent_untouched(intents, u):
    intents.observe(u, "I want to migrate the database to Postgres")
    report = intents.observe(u, "The weather is quite nice today.")
    assert report["changed"] is False
    assert "No explicit statement of purpose" in report["note"]


# -------------------------------------------------------------- intent evolution
def test_explicit_change_supersedes_the_previous_intent(intents, u):
    intents.observe(u, "I want to migrate the database to Postgres")
    report = intents.observe(
        u, "actually, I've changed my mind, I want to focus on the API redesign")
    assert report["changed"] is True
    assert report["changed_because"]
    assert "api redesign" in report["current_intent"]["label"].lower()
    assert report["previous_intent"]["label"] != report["current_intent"]["label"]


def test_a_competing_objective_without_a_change_signal_only_emerges(intents, u):
    """
    Design decision: mentioning a second goal does NOT demote a confirmed one.
    It is recorded as competition, not as a replacement.
    """
    first = intents.observe(u, "I want to migrate the database to Postgres")
    report = intents.observe(u, "I want to improve the test coverage")
    # The confirmed goal stays current...
    assert report["current_intent"]["label"] == first["current_intent"]["label"]
    assert report["changed"] is False
    # ...and the competing one is surfaced as emerging, not hidden.
    assert report["emerging_intent"]["label"] == "improve the test coverage"
    assert report["emerging_intent"]["status"] == "emerging"
    assert "emerging" in report["note"]


def test_abandonment_is_explicit_and_recorded(intents, u):
    intents.observe(u, "I want to migrate the database to Postgres")
    report = intents.observe(u, "I'm giving up on that, forget the migration")
    assert report["changed"] is True
    assert report["current_intent"] is None
    assert "dropping" in report["changed_because"].lower()


def test_resuming_a_dormant_intent_is_recognised(intents, u):
    intents.observe(u, "I want to migrate the database to Postgres")
    intents.observe(u, "I'm giving up on that, forget the migration")
    report = intents.observe(u, "I'm getting back to the migration to Postgres")
    assert report["current_intent"] is not None


def test_transitions_are_recorded_with_evidence(intents, u):
    intents.observe(u, "I want to migrate the database to Postgres")
    intents.observe(u, "actually I want to focus on the API redesign instead")
    history = intents.transitions(u)
    assert history, "transitions must be persisted"
    latest = history[0]
    assert latest["changed_because"]
    assert latest["evidence"], "a transition must cite what was said"
    assert 0.0 <= latest["uncertainty"] <= 1.0


def test_explain_describes_the_trajectory_without_chain_of_thought(intents, u):
    report = intents.observe(u, "I want to migrate the database to Postgres")
    explanation = intents.explain(u, report["current_intent"]["id"])
    assert explanation["intent"]
    assert explanation["transitions"]
    assert explanation["explanation"]
    # Evidence-based narration only - never internal deliberation.
    assert "→" in explanation["explanation"]


def test_explain_unknown_intent_is_honest(intents, u):
    explanation = intents.explain(u, "does-not-exist")
    assert explanation.get("intent") is None


# ---------------------------------------------------------------- need detection
@pytest.mark.parametrize("text,expected", [
    ("should I use Postgres or MySQL for this?", "decision_support"),
    ("help me break this down into steps", "planning"),
    ("I'm really worried I won't finish in time", "reassurance"),
    ("just venting, no advice needed", "listening"),
    ("please remember my API key rotates monthly", "action"),
    ("leave me alone for now", "silence"),
    ("what are my options for caching?", "exploration"),
])
def test_need_signals_are_detected(needs, u, text, expected):
    hypothesis = needs.detect(u, text, persist=False)
    assert hypothesis["need"] == expected
    assert hypothesis["signals"]
    assert hypothesis["reason"]


def test_needs_are_always_labelled_as_hypotheses(needs, u):
    hypothesis = needs.detect(u, "should I use Postgres?", persist=False)
    assert hypothesis["hypothesis"] is True
    assert hypothesis["confidence"] < 1.0


def test_a_weak_signal_defaults_to_low_confidence(needs, u):
    hypothesis = needs.detect(u, "mm ok sure", persist=False)
    assert hypothesis["need"] == "information"
    assert hypothesis["confidence"] <= 0.3
    assert "No strong signal" in hypothesis["reason"]


def test_repeated_need_is_reinforced_by_context(needs, u):
    plain = needs.detect(u, "help me plan the next steps", persist=False)
    reinforced = needs.detect(u, "help me plan the next steps",
                              recent_needs=["planning"], persist=False)
    assert reinforced["confidence"] > plain["confidence"]
    assert "Reinforced" in reinforced["reason"]


def test_need_accuracy_is_insufficient_evidence_without_feedback(needs, u):
    needs.detect(u, "should I use Postgres?")
    report = needs.accuracy(u)
    assert report["accuracy"] is None
    assert "INSUFFICIENT EVIDENCE" in report["detail"]


def test_need_accuracy_reflects_real_feedback(needs, u):
    first = needs.detect(u, "should I use Postgres?")
    second = needs.detect(u, "help me plan the next steps")
    needs.evaluate(u, first["id"], True)
    needs.evaluate(u, second["id"], False)
    report = needs.accuracy(u)
    assert report["evaluated"] == 2
    assert report["accuracy"] == 0.5
    assert report["correct"] == 1


def test_evaluating_an_unknown_hypothesis_returns_none(needs, u):
    assert needs.evaluate(u, "nope", True) is None
