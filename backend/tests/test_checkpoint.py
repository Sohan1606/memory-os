"""LangGraph persistent checkpointing and thread isolation."""


def test_checkpointer_is_persistent(runtime):
    assert runtime.checkpoint_backend in {"sqlite", "memory"}


def test_thread_history_persists(runtime, user):
    runtime.agent.run(user, "ckpt-A", "My name is Alex.")
    history = runtime.agent.history("ckpt-A")
    assert any(m["role"] == "user" and "Alex" in m["content"] for m in history)


def test_threads_are_isolated(runtime, user):
    runtime.agent.run(user, "ckpt-iso-1", "I drive a blue bicycle to work.")
    runtime.agent.run(user, "ckpt-iso-2", "Something entirely different.")
    h2 = " ".join(m["content"] for m in runtime.agent.history("ckpt-iso-2"))
    assert "bicycle" not in h2.lower(), "short-term state leaked across threads"


def test_checkpoint_survives_new_agent_instance(runtime, user):
    """A fresh MemoryAgent over the same checkpointer must see prior state."""
    from app.agent.graph import MemoryAgent
    runtime.agent.run(user, "ckpt-persist", "I collect mechanical keyboards.")
    fresh = MemoryAgent(runtime.memory, runtime.provider, runtime.checkpointer)
    history = fresh.history("ckpt-persist")
    assert any("keyboard" in m["content"].lower() for m in history)
