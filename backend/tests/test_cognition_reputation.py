"""Reputation is earned from evidence and is distinct from confidence."""
from datetime import datetime, timedelta, timezone

from app.cognition.reputation import MemoryArbiter


def test_reputation_requires_evidence(runtime, user):
    rep = runtime.cognition.reputation
    state = rep.get(user, "mem_unknown")
    assert state["reputation"] == "INSUFFICIENT EVIDENCE"
    assert state["evidence"] == 0


def test_reputation_is_earned_not_assigned(runtime, user):
    rep = runtime.cognition.reputation
    mid = "mem_rep_earned"
    for _ in range(3):
        rep.record_outcome(user, mid, True)
    state = rep.get(user, mid)
    assert state["reputation"] == "TRUSTED"
    assert state["lifecycle"] == "reinforced"
    assert state["supporting"] == 3


def test_contradiction_degrades_reputation(runtime, user):
    rep = runtime.cognition.reputation
    mid = "mem_rep_bad"
    rep.record_outcome(user, mid, False)
    rep.record_outcome(user, mid, False)
    state = rep.get(user, mid)
    assert state["reputation"] == "CONTRADICTED"
    assert state["lifecycle"] == "contradicted"


def test_lifecycle_never_jumps_straight_to_trusted(runtime, user):
    rep = runtime.cognition.reputation
    mid = "mem_rep_new"
    rep.record_retrieval(user, mid)
    assert rep.reevaluate(user, mid)["lifecycle"] == "candidate"
    rep.record_retrieval(user, mid)
    assert rep.reevaluate(user, mid)["lifecycle"] == "validating"


def test_arbitration_prefers_recent_correction_over_stale_general(runtime, user):
    arbiter = MemoryArbiter(runtime.cognition.reputation)
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    result = arbiter.arbitrate(user, [
        {"id": "m_old", "content": "Prefers dark mode", "confidence": 0.9,
         "updated_at": old, "source": "conversation"},
        {"id": "m_new", "content": "Switched to light mode for the client demo",
         "confidence": 0.7, "updated_at": new, "source": "correction"},
    ])
    assert result["winner"]["memory"]["id"] == "m_new"
    assert "more recent" in result["explanation"]
    assert len(result["considered"]) == 2


def test_arbitration_is_inspectable(runtime, user):
    arbiter = MemoryArbiter(runtime.cognition.reputation)
    result = arbiter.arbitrate(user, [
        {"id": "m_solo", "content": "Only memory", "confidence": 0.8,
         "updated_at": datetime.now(timezone.utc).isoformat(), "source": "manual"}])
    factors = result["winner"]["factors"]
    for key in ("confidence", "recency", "authority", "reputation_weight",
                "specificity"):
        assert key in factors
