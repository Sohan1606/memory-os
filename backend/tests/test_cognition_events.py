"""The event bus is the single source of truth; it must not drift or lie."""
import pytest

from app.cognition.events import EVENT_TYPES, LABELS


def test_event_vocabulary_is_closed(runtime, user):
    bus = runtime.cognition.bus
    with pytest.raises(ValueError):
        bus.emit(user, "totally.madeup", "nope")


def test_every_event_type_has_a_human_label():
    missing = [t for t in EVENT_TYPES if t not in LABELS]
    assert missing == [], f"event types without human labels: {missing}"


def test_emit_is_retrievable_and_correlated(runtime, user):
    bus = runtime.cognition.bus
    ev = bus.emit(user, "memory.created", "test memory", subject_kind="memory",
                  subject_id="m_test", correlation_id="corr_test")
    assert ev.id > 0
    assert [e.id for e in bus.for_correlation("corr_test")] == [ev.id]
    assert [e.id for e in bus.for_subject("memory", "m_test")] == [ev.id]


def test_subscriber_failure_cannot_break_emission(runtime, user):
    bus = runtime.cognition.bus
    seen = []
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
    bus.subscribe(seen.append)
    ev = bus.emit(user, "memory.created", "still works")
    assert ev.id > 0
    assert seen and seen[-1].id == ev.id


def test_since_is_incremental(runtime, user):
    bus = runtime.cognition.bus
    first = bus.emit(user, "memory.created", "one")
    second = bus.emit(user, "memory.created", "two")
    ids = [e.id for e in bus.since(user, first.id)]
    assert second.id in ids and first.id not in ids
