"""
V8.2 §8 — the memory -> influence -> outcome -> reputation causal chain.

The central rule under test: reputation changes ONLY on real evidence.
Retrieval is not influence, and an outcome with no evidence is INSUFFICIENT
EVIDENCE, not a reputation change.
"""
import pytest


@pytest.fixture
def ledger(runtime):
    return runtime.cognition.influence


@pytest.fixture
def memory_id(runtime, user):
    created = runtime.memory.create(
        user, "Prefers to deploy on Tuesday mornings",
        category="PREFERENCE", allow_duplicate=True)
    return created["memory"]["id"]


# ------------------------------------------------------- retrieval != influence
def test_retrieval_alone_does_not_create_influence(runtime, user, memory_id):
    before = len(runtime.cognition.influence.recent(user))
    runtime.cognition._retrieve(user, "when do I deploy", "corr-retrieval")
    after = len(runtime.cognition.influence.recent(user))
    assert after == before, "merely retrieving a memory is not influence"


def test_retrieval_alone_does_not_change_reputation(runtime, user, memory_id):
    runtime.cognition._retrieve(user, "when do I deploy", "corr-retrieval")
    state = runtime.cognition.reputation.get(user, memory_id)
    assert state["reputation"] == "INSUFFICIENT EVIDENCE"
    assert state["positive_outcomes"] == 0
    assert state["negative_outcomes"] == 0
    # The retrieval counter may move, but that is a usage count, never a
    # quality judgement.
    assert state["influences"] == 0


def test_only_the_arbitration_winner_is_recorded_as_influence(runtime, user):
    runtime.memory.create(user, "Prefers Tuesday releases for the API",
                          category="PREFERENCE", allow_duplicate=True)
    runtime.memory.create(user, "Prefers Wednesday releases for the API",
                          category="PREFERENCE", allow_duplicate=True)
    trace = runtime.cognition.process_turn(user, "when should I release the API?")
    if trace["arbitration"].get("winner"):
        winner = trace["arbitration"]["winner"]["memory_id"]
        recorded = [i["memory_id"] for i in trace["influences"]]
        assert recorded == [winner]


# ------------------------------------------------------------ outcome recording
def test_influence_then_supported_outcome_raises_reputation(runtime, ledger,
                                                            user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d1",
        how="Advised a Tuesday deploy", correlation_id="corr-1")
    result = ledger.record_outcome(
        user, inf["id"], verdict="SUPPORTED",
        detail="The Tuesday deploy went ahead and the user confirmed it",
        evidence=["user said 'yes, that worked'"])
    assert result["outcome_verdict"] == "SUPPORTED"
    assert result["reputation_effect"] == "INCREASE"
    state = runtime.cognition.reputation.get(user, memory_id)
    assert state["positive_outcomes"] == 1


def test_contradicted_outcome_lowers_reputation(runtime, ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d2",
        how="Advised a Tuesday deploy")
    ledger.record_outcome(
        user, inf["id"], verdict="CONTRADICTED",
        detail="User said they moved to Thursday deploys months ago",
        evidence=["user: 'we deploy Thursdays now'"])
    state = runtime.cognition.reputation.get(user, memory_id)
    assert state["negative_outcomes"] == 1


def test_outcome_without_evidence_does_not_move_reputation(runtime, ledger,
                                                           user, memory_id):
    """The core honesty guard: no evidence, no reputation change."""
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d3",
        how="Advised a Tuesday deploy")
    result = ledger.record_outcome(user, inf["id"], verdict="SUPPORTED",
                                   detail="It seemed to go fine", evidence=[])
    assert result["outcome_verdict"] == "INSUFFICIENT EVIDENCE"
    assert result["reputation_effect"] == "UNCHANGED"
    assert "no supporting evidence" in result["outcome_detail"]
    state = runtime.cognition.reputation.get(user, memory_id)
    assert state["positive_outcomes"] == 0
    assert state["reputation"] == "INSUFFICIENT EVIDENCE"


def test_neutral_outcome_never_changes_reputation(runtime, ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d4",
        how="Mentioned the deploy window")
    result = ledger.record_outcome(user, inf["id"], verdict="NEUTRAL",
                                   detail="No bearing either way",
                                   evidence=["user changed the subject"])
    assert result["reputation_effect"] == "UNCHANGED"
    state = runtime.cognition.reputation.get(user, memory_id)
    assert state["positive_outcomes"] == 0 and state["negative_outcomes"] == 0


def test_outcome_cannot_be_recorded_twice(ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d5",
        how="Advised a Tuesday deploy")
    ledger.record_outcome(user, inf["id"], verdict="SUPPORTED", detail="Confirmed",
                          evidence=["explicit user confirmation"])
    with pytest.raises(ValueError):
        ledger.record_outcome(user, inf["id"], verdict="CONTRADICTED",
                              detail="Changed my mind", evidence=["later message"])


def test_unknown_influence_id_is_rejected(ledger, user):
    with pytest.raises(KeyError):
        ledger.record_outcome(user, "does-not-exist", verdict="SUPPORTED",
                              detail="x", evidence=["y"])


def test_unknown_verdict_is_rejected(ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d6",
        how="Used in an answer")
    with pytest.raises(ValueError):
        ledger.record_outcome(user, inf["id"], verdict="PROBABLY_FINE",
                              detail="x", evidence=["y"])


# ------------------------------------------------------------------ inspection
def test_pending_influences_await_outcomes(ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d7",
        how="Used in an answer")
    pending_ids = [p["id"] for p in ledger.pending(user)]
    assert inf["id"] in pending_ids
    ledger.record_outcome(user, inf["id"], verdict="SUPPORTED", detail="Confirmed",
                          evidence=["user confirmation"])
    assert inf["id"] not in [p["id"] for p in ledger.pending(user)]


def test_impact_report_traces_the_full_causal_chain(ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d8",
        how="Advised a Tuesday deploy")
    ledger.record_outcome(user, inf["id"], verdict="SUPPORTED", detail="It worked",
                          evidence=["user confirmed the deploy succeeded"])
    impact = ledger.impact(user, memory_id)
    assert impact["influence_count"] == 1
    assert impact["resolved_count"] == 1
    assert impact["supported"] == 1
    assert impact["contradicted"] == 0
    assert impact["influences"][0]["influenced_kind"] == "decision"
    assert "1 outcome(s) supported it" in impact["summary"]


def test_impact_of_an_uninfluential_memory_is_honest(ledger, user, memory_id):
    impact = ledger.impact(user, memory_id)
    assert impact["influence_count"] == 0
    assert "has not been recorded as influencing" in impact["summary"]
    assert impact["reputation"]["reputation"] == "INSUFFICIENT EVIDENCE"


def test_influence_without_an_observed_outcome_stays_unjudged(ledger, user,
                                                              memory_id):
    ledger.record_influence(user, memory_id, influenced_kind="decision",
                            influenced_id="d9", how="Used in an answer")
    impact = ledger.impact(user, memory_id)
    assert impact["unresolved"] == 1
    assert "INSUFFICIENT EVIDENCE to judge it" in impact["summary"]


def test_influence_emits_events(runtime, ledger, user, memory_id):
    inf = ledger.record_influence(
        user, memory_id, influenced_kind="decision", influenced_id="d10",
        how="Used in an answer", correlation_id="corr-events")
    ledger.record_outcome(user, inf["id"], verdict="SUPPORTED", detail="Confirmed",
                          evidence=["user confirmation"],
                          correlation_id="corr-events")
    types = {e.type for e in runtime.cognition.bus.for_correlation("corr-events")}
    assert "memory.influenced" in types
    assert "outcome.recorded" in types
    assert "memory.impact" in types
