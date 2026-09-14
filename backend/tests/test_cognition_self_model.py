"""The system must describe its own limits truthfully."""


def test_unconfigured_integrations_are_not_claimed(runtime):
    caps = runtime.cognition.self_model.capabilities()
    assert caps["external_context"]["state"] == "NOT CONFIGURED"
    assert caps["external_actions"]["state"] == "NOT CONFIGURED"
    assert "NOT CONNECTED" in caps["external_context"]["detail"]


def test_capability_states_are_from_a_known_vocabulary(runtime):
    allowed = {"ACTIVE", "DEGRADED", "NOT CONFIGURED"}
    for cap in runtime.cognition.self_model.capabilities().values():
        assert cap["state"] in allowed


def test_langmem_state_matches_runtime_truth(runtime):
    caps = runtime.cognition.self_model.capabilities()
    assert caps["langmem"]["state"] == (
        "ACTIVE" if runtime.langmem.status().active else "NOT CONFIGURED")


def test_limitations_are_stated_plainly(runtime):
    limits = runtime.cognition.self_model.limitations()
    assert any("calendar" in line for line in limits)
    assert any("outside my own memory store" in line for line in limits)


def test_can_answers_unknown_capability_honestly(runtime):
    answer = runtime.cognition.self_model.can("time_travel")
    assert answer["able"] is False


def test_recovery_reports_degradation_rather_than_pretending(runtime, user):
    rec = runtime.cognition.recovery

    def boom():
        raise RuntimeError("primary failed")

    result = rec.attempt(user, "memory retrieval", boom, fallback=lambda: ["ok"])
    assert result["ok"] is True
    assert result["degraded"] is True
    assert result["path"] == "fallback"

    failed = rec.attempt(user, "memory retrieval", boom)
    assert failed["ok"] is False
    assert "haven't changed anything" in failed["message"]
