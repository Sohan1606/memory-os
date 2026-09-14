"""Create / reinforce / conflict / consolidate / delete integrity."""
import pytest


def test_create_and_version_one(runtime, user):
    r = runtime.memory.create(user, "Runs a half marathon every spring.", category="HABIT")
    assert r["action"] == "created"
    mem_id = r["memory"]["id"]
    assert runtime.memory.versions(mem_id)[0]["version"] == 1
    runtime.memory.delete(mem_id)


def test_duplicate_is_reinforced_not_duplicated(runtime, user):
    first = runtime.memory.create(user, "Uses Neovim as the main editor.", category="PREFERENCE")
    before = len(runtime.memory.list(user))
    second = runtime.memory.create(user, "Uses Neovim as the main editor.")
    assert second["action"] == "reinforced"
    assert second["memory"]["id"] == first["memory"]["id"]
    assert len(runtime.memory.list(user)) == before
    assert second["memory"]["reinforcement_count"] >= 1
    runtime.memory.delete(first["memory"]["id"])


def test_conflict_updates_existing_memory(runtime, user):
    original = runtime.memory.create(
        user, "Prefers dark interfaces over light ones.",
        category="PREFERENCE", allow_duplicate=True)["memory"]
    count = len(runtime.memory.list(user))

    result = runtime.memory.create(user, "I have switched to light mode.")
    assert result["action"] == "updated"
    assert result.get("conflict") is True
    assert result["memory"]["id"] == original["id"]
    assert result["memory"]["version"] == 2
    assert len(runtime.memory.list(user)) == count, "conflict must not create a duplicate"

    versions = runtime.memory.versions(original["id"])
    assert [v["version"] for v in versions] == [1, 2]
    assert "dark" in versions[0]["content"].lower()
    assert "light" in versions[1]["content"].lower()

    if runtime.vectors.available:
        assert "light" in runtime.vectors.get(original["id"])["document"].lower()

    types = [e["event_type"] for e in runtime.memory.events(user)]
    assert "MEMORY_SUPERSEDED" in types
    runtime.memory.delete(original["id"])


def test_unrelated_statement_does_not_trigger_conflict(runtime, user):
    r = runtime.memory.create(user, "I am learning to sail a small dinghy.")
    assert r["action"] == "created"
    runtime.memory.delete(r["memory"]["id"])


def test_consolidation_merges_and_supersedes(runtime, user):
    a = runtime.memory.create(user, "Works on cloud provisioning scripts.",
                              category="PROJECT", allow_duplicate=True)["memory"]["id"]
    b = runtime.memory.create(user, "Automates DevOps release workflows.",
                              category="PROJECT", allow_duplicate=True)["memory"]["id"]
    result = runtime.memory.consolidate(user, [a, b])
    new_id = result["memory"]["id"]
    assert set(result["source_memory_ids"]) == {a, b}
    assert runtime.memory.get(a).status == "superseded"
    assert runtime.memory.get(b).status == "superseded"
    assert new_id in [m.id for m in runtime.memory.list(user)]
    types = [e["event_type"] for e in runtime.memory.events(user)]
    assert "MEMORY_CONSOLIDATED" in types
    runtime.memory.delete(new_id)


def test_consolidation_requires_two(runtime, user):
    one = runtime.memory.create(user, "Only one memory here.", allow_duplicate=True)["memory"]["id"]
    with pytest.raises(ValueError):
        runtime.memory.consolidate(user, [one])
    runtime.memory.delete(one)


def test_empty_content_rejected(runtime, user):
    with pytest.raises(ValueError):
        runtime.memory.create(user, "   ")
