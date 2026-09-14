"""Audit trail must record every real mutation."""


def test_event_types_recorded(runtime, user):
    created = runtime.memory.create(user, "Volunteers at a local coding club.",
                                    category="HABIT", allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.memory.update(mid, content="Mentors at a local coding club.",
                          reason="User correction")
    runtime.memory.reinforce(mid)
    runtime.memory.search(user, "coding club")
    runtime.memory.delete(mid)

    types = [e["event_type"] for e in runtime.memory.events(user, limit=500)]
    for expected in ("MEMORY_CREATED", "MEMORY_UPDATED", "MEMORY_REINFORCED",
                     "MEMORY_RETRIEVED", "MEMORY_DELETED"):
        assert expected in types, f"missing audit event {expected}"


def test_events_carry_metadata_and_timestamps(runtime, user):
    events = runtime.memory.events(user, limit=10)
    assert events
    for e in events:
        assert e["created_at"]
        assert isinstance(e["metadata"], dict)


def test_version_history_matches_updates(runtime, user):
    m = runtime.memory.create(user, "Learning Portuguese on weekends.",
                              category="GOAL", allow_duplicate=True)["memory"]["id"]
    runtime.memory.update(m, content="Learning Spanish on weekends.", reason="Changed language")
    versions = runtime.memory.versions(m)
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["reason"] == "Changed language"
    assert runtime.memory.get(m).version == 2
    runtime.memory.delete(m)
