"""Retrieval paths must only ever reference memories that currently exist."""


def test_path_nodes_all_exist(runtime, user):
    results = runtime.memory.search(user, "What do you remember about my projects?")
    path = runtime.memory.retrieval_path(user, "my projects", results)
    assert path[0]["kind"] == "query"
    for step in path:
        if step.get("id"):
            assert runtime.memory.get(step["id"]) is not None


def test_path_is_empty_for_no_match(runtime, user):
    results = runtime.memory.search(user, "qwertyuiop nonsense token")
    path = runtime.memory.retrieval_path(user, "qwertyuiop nonsense token", results)
    assert len([s for s in path if s["kind"] == "memory"]) == 0


def test_path_recalculates_after_delete(runtime, user):
    created = runtime.memory.create(user, "Prototyping a mesh networking project.",
                                    category="PROJECT", allow_duplicate=True)
    mid = created["memory"]["id"]
    results = runtime.memory.search(user, "mesh networking project")
    assert any(r.memory.id == mid for r in results)

    runtime.memory.delete(mid)
    results2 = runtime.memory.search(user, "mesh networking project")
    path2 = runtime.memory.retrieval_path(user, "mesh networking project", results2)
    assert all(s.get("id") != mid for s in path2)
