"""The headline demo: long-term memory crosses threads, short-term does not."""


def test_preference_learned_in_thread_a_is_available_in_thread_b(runtime, user):
    runtime.agent.run(user, "xthread-A", "I prefer concise technical explanations.")

    stored = [m for m in runtime.memory.list(user) if "concise" in m.content.lower()]
    assert stored, "durable preference was not stored"

    result = runtime.agent.run(user, "xthread-B",
                               "What do you know about how I like explanations?")
    recalled = " ".join(r["memory"]["content"].lower() for r in result["recalled"])
    assert "concise" in recalled or "concise" in result["answer"].lower()


def test_short_term_context_does_not_leak(runtime, user):
    runtime.agent.run(user, "xthread-C", "My lucky number is seventeen.")
    runtime.agent.run(user, "xthread-D", "Hello.")
    d_history = " ".join(m["content"] for m in runtime.agent.history("xthread-D"))
    assert "seventeen" not in d_history.lower()
