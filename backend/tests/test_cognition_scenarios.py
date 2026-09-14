"""
Scenario tests: the cognitive loop as a user actually exercises it.

These run against the real Runtime (real SQLite, real Chroma, real retrieval).
"""
import pytest

from app.cognition.prediction import PredictionEngine


@pytest.fixture(scope="module")
def cog(runtime):
    return runtime.cognition


# --- A: a turn produces a complete, correlated cognitive trace --------------
def test_scenario_a_turn_emits_correlated_trace(cog, user):
    trace = cog.process_turn(user, "I need to ship the billing rewrite by Friday")
    events = cog.bus.for_correlation(trace["correlation_id"])
    types = {e.type for e in events}
    assert "conversation.message" in types
    assert "need.detected" in types
    assert all(e.correlation_id == trace["correlation_id"] for e in events)


# --- B: statements build the world, questions do not ------------------------
def test_scenario_b_questions_do_not_pollute_the_world(cog, user):
    before = len(cog.world.list(user))
    cog.process_turn(user, "What did I say about the billing rewrite?")
    assert len(cog.world.list(user)) == before


# --- C: intent evolution preserves history ----------------------------------
def test_scenario_c_intent_change_preserves_trajectory(cog, user):
    cog.process_turn(user, "I want to migrate the database to Postgres")
    cog.process_turn(user, "Actually, I've changed my mind, I want to stay on MySQL")
    history = cog.intent.list(user)
    assert any(i["status"] == "historical" for i in history), \
        "superseded intent must be retained, not deleted"


# --- D: retrieval records real reputation evidence --------------------------
def test_scenario_d_retrieval_is_recorded_as_evidence(cog, user):
    trace = cog.process_turn(user, "What do you remember about my work style?")
    for candidate in trace["retrieval"]["candidates"]:
        assert cog.reputation.get(user, candidate["id"])["retrievals"] >= 1


# --- E: predictions are calibrated, and being wrong causes surprise ---------
def test_scenario_e_wrong_confident_prediction_creates_surprise(runtime, user):
    engine: PredictionEngine = runtime.cognition.predictions
    pred = engine.create(user, "The migration will finish on time", confidence=0.8,
                         evidence=["stated deadline"])
    engine.evaluate(user, pred["id"], False, "slipped two weeks")
    events = runtime.cognition.bus.for_subject("prediction", pred["id"])
    assert any(e.type == "surprise.detected" for e in events)


def test_scenario_f_accuracy_refuses_to_guess(runtime, user):
    acc = runtime.cognition.predictions.accuracy("user-with-no-history")
    assert acc["calibration"] == "INSUFFICIENT EVIDENCE"
    assert acc["accuracy"] is None


# --- G: causality is traversable in both directions -------------------------
def test_scenario_g_causal_chain_is_bidirectional(cog, user):
    cog.causal.link(user, "memory", "m_pref", "decision", "d_stack",
                    relation="influenced")
    cog.causal.link(user, "decision", "d_stack", "outcome", "o_shipped",
                    relation="caused")
    downstream = cog.causal.downstream("memory", "m_pref")
    upstream = cog.causal.upstream("outcome", "o_shipped")
    assert any(n["effect_id"] == "d_stack" for n in downstream)
    assert any(n["cause_id"] == "d_stack" for n in upstream)


def test_scenario_h_unmeasurable_impact_is_admitted(cog, user):
    impact = cog.causal.impact(user, "m_never_used")
    assert impact["evidence"] is False
    assert impact["detail"] == "INSUFFICIENT EVIDENCE"

    # A memory that HAS influenced a decision still refuses to invent ROI.
    cog.causal.link(user, "memory", "m_used", "decision", "d_used",
                    relation="influenced")
    measured = cog.causal.impact(user, "m_used")
    assert measured["time_saved"] == "INSUFFICIENT EVIDENCE"
    assert measured["cost_avoided"] == "INSUFFICIENT EVIDENCE"


# --- I: 'why' explains any object from recorded history ---------------------
def test_scenario_i_why_is_backed_by_events(cog, user):
    cog.bus.emit(user, "memory.created", "Prefers concise answers",
                 subject_kind="memory", subject_id="m_why")
    why = cog.why(user, "memory", "m_why")
    assert why["events"]
    assert "First recorded" in why["explanation"]


def test_scenario_j_why_admits_when_it_knows_nothing(cog, user):
    why = cog.why(user, "memory", "m_does_not_exist")
    assert "INSUFFICIENT EVIDENCE" in why["explanation"]


# --- K: continuity across sessions ------------------------------------------
def test_scenario_k_returning_user_gets_real_briefing(cog, user):
    resume = cog.continuity.resume(user)
    assert resume["first_time"] is False
    assert resume["briefing"]


def test_scenario_l_new_user_is_not_given_invented_history(cog):
    resume = cog.continuity.resume("brand-new-user-xyz")
    assert resume["first_time"] is True
    assert resume["open_commitments"] == []
    assert "first thing" in resume["briefing"]


# --- M: user isolation -------------------------------------------------------
def test_scenario_m_cognitive_state_is_user_scoped(cog):
    cog.process_turn("user-alpha", "I am building an alpha product")
    cog.process_turn("user-beta", "I am building a beta product")
    alpha = {e["label"] for e in cog.world.list("user-alpha")}
    beta = {e["label"] for e in cog.world.list("user-beta")}
    assert alpha and beta and not (alpha & beta)


# --- N: scoped forgetting ----------------------------------------------------
def test_scenario_n_forgetting_is_scoped_and_reported(cog, user):
    cog.bus.emit(user, "memory.created", "temp", subject_kind="memory",
                 subject_id="m_forget")
    removed = cog.bus.purge_subject("memory", "m_forget")
    assert removed >= 1
    assert cog.bus.for_subject("memory", "m_forget") == []


# --- O: status snapshot is complete and honest ------------------------------
def test_scenario_o_status_is_complete(cog, user):
    status = cog.status(user)
    for key in ("autonomy", "world", "predictions", "attention", "self",
                "policies", "events"):
        assert key in status
    assert status["self"]["not_configured"]


# --- P: the loop degrades rather than failing the conversation --------------
def test_scenario_p_cognitive_failure_does_not_break_retrieval(cog, user, monkeypatch):
    def broken_search(*_a, **_k):
        raise RuntimeError("vector store down")

    monkeypatch.setattr(cog.memory, "search", broken_search)
    trace = cog.process_turn(user, "Tell me what you remember")
    assert trace["retrieval"]["degraded"] is True
    assert trace["retrieval"]["count"] == 0
