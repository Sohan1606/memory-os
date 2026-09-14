"""Simulations must never touch real state."""


def _snapshot(runtime, user):
    world = runtime.cognition.world
    return {
        "entities": sorted((e["kind"], e["label"], e["state"])
                           for e in world.list(user)),
        "memories": len(runtime.memory.list(user)),
    }


def test_simulation_never_mutates_world_state(runtime, user):
    cog = runtime.cognition
    cog.world.observe(user, "I am working on the payments migration")
    before = _snapshot(runtime, user)
    cog.sandbox.simulate(user, "What if I drop the payments migration entirely?")
    cog.sandbox.simulate(user, "What if I focus only on billing?")
    assert _snapshot(runtime, user) == before


def test_simulation_is_clearly_labelled(runtime, user):
    result = runtime.cognition.sandbox.simulate(user, "What if I delay everything?")
    assert result["simulation"] is True
    assert "SIMULATION ONLY" in result["banner"]


def test_simulation_states_assumptions_and_risks(runtime, user):
    result = runtime.cognition.sandbox.simulate(user, "What if I delay the migration?")
    assert result["assumptions"]
    assert result["projected_effects"]
    assert 0.0 <= result["confidence"] <= 1.0


def test_simulation_history_is_recorded(runtime, user):
    cog = runtime.cognition
    run = cog.sandbox.simulate(user, "What if I cancel the rewrite?")
    assert any(h["id"] == run["id"] for h in cog.sandbox.history(user))
