"""
V8.2 §6 / §7 — Memory Arbitration V2 and retrieval quality.

Confidence and reputation must stay separate, contradicted/quarantined memories
must not silently win, and every decision must carry real evidence.
"""
import pytest

from app.cognition.arbitration import AUTHORITY, ArbiterV2


@pytest.fixture
def arbiter(runtime):
    return runtime.cognition.arbiter_v2


def mem(mid, content, *, confidence=0.7, source="conversation",
        updated_at="2026-09-01T00:00:00+00:00", status="active",
        category="PREFERENCE"):
    return {"id": mid, "content": content, "confidence": confidence,
            "source": source, "updated_at": updated_at, "status": status,
            "category": category}


# ------------------------------------------------------------------- scoring
def test_scoring_exposes_every_factor(arbiter, user):
    cand = arbiter.score(user, mem("m1", "Prefers dark mode in the editor"),
                         "what theme do I prefer")
    for factor in ("confidence", "recency", "authority", "reputation_weight",
                   "specificity", "scope_match", "freshness",
                   "explicit_correction", "contradiction_penalty"):
        assert factor in cand.factors
    assert cand.evidence, "every candidate must carry stated evidence"


def test_confidence_and_reputation_are_separate_fields(arbiter, user):
    cand = arbiter.score(user, mem("m2", "Uses PostgreSQL in production",
                                   confidence=0.95))
    assert cand.confidence == 0.95
    # No outcomes recorded yet, so reputation must not inherit confidence.
    assert cand.reputation == "INSUFFICIENT EVIDENCE"


def test_correction_source_outranks_inference(arbiter, user):
    assert AUTHORITY["correction"] > AUTHORITY["inference"]
    corrected = arbiter.score(user, mem("m3", "Prefers TypeScript",
                                        source="correction"))
    inferred = arbiter.score(user, mem("m4", "Prefers TypeScript",
                                       source="inference"))
    assert corrected.score > inferred.score


def test_recent_memory_beats_an_old_identical_one(arbiter, user):
    fresh = arbiter.score(user, mem("m5", "Prefers tabs over spaces",
                                    updated_at="2026-09-13T00:00:00+00:00"))
    old = arbiter.score(user, mem("m6", "Prefers tabs over spaces",
                                  updated_at="2024-01-01T00:00:00+00:00"))
    assert fresh.score > old.score


def test_stale_memory_is_flagged_in_evidence(arbiter, user):
    old = arbiter.score(user, mem("m7", "Works at the old company",
                                  updated_at="2023-01-01T00:00:00+00:00"))
    assert any("stale" in e for e in old.evidence)


def test_superseded_memory_is_blocked(arbiter, user):
    cand = arbiter.score(user, mem("m8", "Old preference", status="superseded"))
    assert cand.blocked is True
    assert cand.score == 0.0
    assert "superseded" in cand.blocked_reason


def test_scope_match_rewards_relevant_memories(arbiter, user):
    on_topic = arbiter.score(user, mem("m9", "Prefers dark mode editor themes"),
                             "dark mode editor themes")
    off_topic = arbiter.score(user, mem("m10", "Owns a golden retriever"),
                              "dark mode editor themes")
    assert on_topic.factors["scope_match"] > off_topic.factors["scope_match"]


# --------------------------------------------------------------- arbitration
def test_arbitration_returns_winner_losers_and_reason(arbiter, user):
    result = arbiter.arbitrate(user, [
        mem("w1", "Prefers dark mode", source="correction",
            updated_at="2026-09-13T00:00:00+00:00"),
        mem("w2", "Prefers light mode", source="inference",
            updated_at="2024-02-01T00:00:00+00:00")],
        query="what theme do I prefer")
    assert result["winner"]["memory_id"] == "w1"
    assert len(result["losers"]) == 1
    assert result["reason"]
    assert 0.0 <= result["uncertainty"] <= 1.0
    assert result["candidates"], "all candidates must be reported"


def test_arbitration_record_is_persisted_and_readable(arbiter, user):
    result = arbiter.arbitrate(user, [mem("p1", "Prefers vim keybindings")],
                               query="editor keybindings")
    stored = arbiter.get(result["id"])
    assert stored is not None
    assert stored["winner_id"] == "p1"
    assert stored["reason"] == result["reason"]


def test_empty_candidates_do_not_fabricate_a_winner(arbiter, user):
    result = arbiter.arbitrate(user, [], query="anything")
    assert result["winner"] is None
    assert "No competing memories" in result["reason"]


def test_all_blocked_candidates_yield_insufficient_evidence(arbiter, user):
    result = arbiter.arbitrate(user, [
        mem("b1", "Outdated one", status="superseded"),
        mem("b2", "Another outdated one", status="superseded")],
        query="anything")
    assert result["winner"] is None
    assert "INSUFFICIENT EVIDENCE" in result["reason"]
    assert len(result["blocked"]) == 2


def test_contradiction_history_penalises_a_memory(runtime, arbiter, user):
    """A memory contradicted in recorded history must score lower."""
    runtime.cognition.reputation.record_contradiction(user, "c1")
    runtime.cognition.reputation.record_contradiction(user, "c1")
    penalised = arbiter.score(user, mem("c1", "Prefers MySQL databases"))
    clean = arbiter.score(user, mem("c2", "Prefers MySQL databases"))
    assert penalised.factors["contradiction_penalty"] > 0
    assert penalised.score < clean.score
    assert any("Contradicted" in e for e in penalised.evidence)


def test_contradicted_memory_is_excluded_from_winning(runtime, arbiter, user):
    """Lifecycle 'contradicted' must not silently win arbitration."""
    rep = runtime.cognition.reputation
    for _ in range(3):
        rep.record_contradiction(user, "x1")
    state = rep.get(user, "x1")
    assert state["reputation"] == "CONTRADICTED"
    result = arbiter.arbitrate(user, [
        mem("x1", "Prefers the old deployment process"),
        mem("x2", "Prefers the new deployment process")],
        query="deployment process")
    assert result["winner"]["memory_id"] == "x2"


def test_close_scores_are_reported_as_a_conflict(arbiter, user):
    result = arbiter.arbitrate(user, [
        mem("q1", "Prefers morning standups"),
        mem("q2", "Prefers evening standups")],
        query="standup timing")
    # Identical factors except wording -> genuinely close.
    assert result["uncertainty"] > 0.5


def test_uncertainty_is_high_when_evidence_is_thin(arbiter, user):
    result = arbiter.arbitrate(user, [mem("t1", "Maybe", confidence=0.1)],
                               query="unrelated topic entirely")
    assert result["uncertainty"] > 0.5


def test_arbitration_for_memory_lookup(arbiter, user):
    arbiter.arbitrate(user, [mem("lookup1", "Prefers concise answers")],
                      query="answer length")
    records = arbiter.for_memory("lookup1")
    assert records and records[0]["winner_id"] == "lookup1"


# ----------------------------------------------- retrieval quality (§7)
def test_retrieval_explanations_come_from_real_metadata(runtime, user):
    runtime.memory.create(user, "Prefers FastAPI for backend services",
                          category="PREFERENCE", allow_duplicate=True)
    trace = runtime.cognition.process_turn(user, "what do I prefer for backend?")
    for candidate in trace["retrieval"]["candidates"]:
        assert candidate["reasons"], "every candidate needs a real reason"
        assert "semantic" in candidate and "keyword" in candidate
        assert candidate["lifecycle"]
        assert candidate["reputation"]


def test_quarantined_memory_does_not_re_enter_retrieval(runtime, user):
    """A quarantined memory must be excluded and reported, not silently used."""
    created = runtime.memory.create(
        user, "Deploys with the legacy Jenkins pipeline every Thursday",
        category="HABIT", allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.db.execute(
        "INSERT OR REPLACE INTO memory_reputation (memory_id,user_id,lifecycle,"
        "updated_at) VALUES (?,?,?,datetime('now'))", (mid, user, "quarantined"))

    trace = runtime.cognition.process_turn(user, "legacy Jenkins pipeline Thursday")
    ids = [c["id"] for c in trace["retrieval"]["candidates"]]
    assert mid not in ids
    excluded = [e["id"] for e in trace["retrieval"]["excluded"]]
    assert mid in excluded
