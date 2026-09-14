"""Authority is separate from confidence, and is never granted by default."""


def test_irreversible_action_always_asks(runtime, user):
    gov = runtime.cognition.autonomy
    gov.set_level(user, "act")
    decision = gov.authorize(user, "delete production data",
                             risk_class="irreversible", reversible=False)
    assert decision["allowed"] is False
    assert decision["decision"] == "ask"


def test_observe_mode_performs_nothing(runtime, user):
    gov = runtime.cognition.autonomy
    gov.set_level(user, "observe")
    assert gov.authorize(user, "save a memory", risk_class="remember")["allowed"] is False
    gov.set_level(user, "assist")


def test_low_risk_reversible_is_permitted_at_assist(runtime, user):
    gov = runtime.cognition.autonomy
    gov.set_level(user, "assist")
    assert gov.authorize(user, "save a memory", risk_class="remember")["allowed"] is True


def test_trust_requires_evidence(runtime, user):
    score = runtime.cognition.trust.score(user, "prediction")
    assert score["label"] == "INSUFFICIENT EVIDENCE"
    assert score["reliability"] is None


def test_demonstrated_failure_withdraws_authority(runtime, user):
    cog = runtime.cognition
    cog.autonomy.set_level(user, "assist")
    for _ in range(5):
        cog.trust.record(user, "action", False)
    assert cog.trust.score(user, "action")["label"] == "UNRELIABLE"
    decision = cog.autonomy.authorize(user, "save a memory", risk_class="remember")
    assert decision["allowed"] is False
    assert any("failure" in r for r in decision["reasons"])


def test_attention_can_decide_to_do_nothing(runtime, user):
    result = runtime.cognition.attention.consider(
        user, "trivial detail", importance=0.1, urgency=0.1, confidence=0.5)
    assert result["decision"] in ("ignore", "monitor")
    assert result["surface"] is False


def test_suppressed_interventions_are_logged(runtime, user):
    cog = runtime.cognition
    result = cog.attention.consider(user, "quiet thing", importance=0.1,
                                    urgency=0.1, confidence=0.4)
    events = cog.bus.for_subject("intervention", result["id"])
    assert any(e.type == "intervention.suppressed" for e in events)


def test_intervention_has_a_why_now(runtime, user):
    result = runtime.cognition.attention.consider(
        user, "deadline at risk", importance=0.9, urgency=0.9, confidence=0.85)
    assert result["surface"] is True
    assert result["why_now"]
