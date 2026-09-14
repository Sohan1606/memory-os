"""Deleting a memory must purge SQLite, Chroma, relationships and retrieval."""


def test_delete_purges_every_store(runtime, user):
    created = runtime.memory.create(user, "Keeps a saltwater aquarium at home.",
                                    category="HABIT", allow_duplicate=True)
    mid = created["memory"]["id"]
    assert runtime.memory.search(user, "saltwater aquarium")

    assert runtime.memory.delete(mid) is True
    assert runtime.memory.get(mid) is None
    if runtime.vectors.available:
        assert runtime.vectors.get(mid) is None
    assert all(r.memory.id != mid for r in runtime.memory.search(user, "saltwater aquarium"))
    assert all(n["id"] != mid for n in runtime.memory.graph(user)["nodes"])
    assert "MEMORY_DELETED" in [e["event_type"] for e in runtime.memory.events(user)]


def test_no_dangling_relationships_after_delete(runtime, user):
    a = runtime.memory.create(user, "Builds a rust powered CLI tool.",
                              category="PROJECT", allow_duplicate=True)["memory"]["id"]
    b = runtime.memory.create(user, "Publishes the rust CLI tool as open source.",
                              category="PROJECT", allow_duplicate=True)["memory"]["id"]
    runtime.memory.delete(a)
    graph = runtime.memory.graph(user)
    ids = {n["id"] for n in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
    assert a not in [r for n in graph["nodes"] for r in n["related_memory_ids"]]
    runtime.memory.delete(b)


def test_delete_missing_memory_is_safe(runtime):
    assert runtime.memory.delete("does-not-exist") is False


def test_graph_never_emits_dangling_edges(runtime, user):
    graph = runtime.memory.graph(user)
    ids = {n["id"] for n in graph["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])
