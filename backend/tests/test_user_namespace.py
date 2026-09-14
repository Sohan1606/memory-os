"""Long-term memory is namespaced per user_id (separation, not authentication)."""


def test_users_do_not_share_memories(runtime):
    runtime.memory.create("alice", "Alice prefers espresso in the afternoon.",
                          category="PREFERENCE", allow_duplicate=True)
    runtime.memory.create("bob", "Bob prefers herbal tea at night.",
                          category="PREFERENCE", allow_duplicate=True)

    alice = " ".join(m.content for m in runtime.memory.list("alice"))
    bob = " ".join(m.content for m in runtime.memory.list("bob"))
    assert "espresso" in alice and "herbal tea" not in alice
    assert "herbal tea" in bob and "espresso" not in bob

    results = runtime.memory.search("bob", "what drink do I prefer?")
    assert all("espresso" not in r.memory.content.lower() for r in results)


def test_demo_user_unaffected_by_other_namespaces(runtime, user):
    assert all(m.user_id == user for m in runtime.memory.list(user))
