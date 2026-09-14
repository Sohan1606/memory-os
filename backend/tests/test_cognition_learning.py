"""Learning is evidence-gated and reversible; self-evaluation is honest."""


def test_self_evaluation_admits_missing_evidence(runtime, user):
    ev = runtime.cognition.learning.self_evaluation(user)
    assert ev["predictions_calibrated"]["answer"] == "INSUFFICIENT EVIDENCE"


def test_no_pattern_below_threshold(runtime, user):
    cog = runtime.cognition
    cog.bus.emit(user, "intervention.rejected", "declined once")
    result = cog.learning.consolidate(user)
    assert all(p["kind"] != "principle" or p["status"] != "validated"
               for p in result["proposed"])


def test_repeated_evidence_promotes_a_principle(runtime, user):
    cog = runtime.cognition
    for _ in range(4):
        cog.bus.emit(user, "intervention.rejected", "declined")
    result = cog.learning.consolidate(user)
    principles = [p for p in result["proposed"] if p["kind"] == "principle"]
    assert principles
    assert principles[0]["status"] == "validated"
    assert cog.learning.get_policy(user, "interruption_tolerance")["value"] == "low"


def test_policies_are_reversible(runtime, user):
    le = runtime.cognition.learning
    le.set_policy(user, "tone", "concise", rationale="asked for brevity")
    assert le.get_policy(user, "tone")["value"] == "concise"
    assert le.revert_policy(user, "tone") is True
    assert le.get_policy(user, "tone") is None


def test_policy_change_is_announced_as_an_event(runtime, user):
    cog = runtime.cognition
    pol = cog.learning.set_policy(user, "detail_level", "high", rationale="asked")
    events = cog.bus.for_subject("policy", pol["id"])
    assert any(e.type in ("policy.proposed", "policy.updated") for e in events)
