"""
V8.2 §5 / §16 / §21 — canonical context builder, context fabric honesty, and
object permanence through stable IDs.
"""
import uuid

import pytest


@pytest.fixture
def builder(runtime):
    return runtime.cognition.context


@pytest.fixture
def focus(runtime):
    return runtime.cognition.focus


@pytest.fixture
def u():
    return f"ctx-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------- context build
def test_bundle_is_bounded_and_ranked(runtime, builder, u):
    for i in range(40):
        runtime.memory.create(u, f"Prefers approach number {i} for deployments",
                              allow_duplicate=True)
    bundle = builder.build(u, "what deployment approach do I prefer?")
    data = bundle.as_dict()
    assert data["items"], "context must not be empty when memories exist"
    assert len(data["items"]) <= 40
    assert data["prompt_chars"] <= 4500
    scores = [i["relevance"] for i in data["items"]]
    assert scores == sorted(scores, reverse=True), "items must be relevance-ranked"


def test_every_context_item_declares_source_and_confidence(runtime, builder, u):
    runtime.memory.create(u, "Prefers Rust for systems work", allow_duplicate=True)
    bundle = builder.build(u, "what language do I prefer for systems work?")
    for item in bundle.as_dict()["items"]:
        assert item["source"], "every item must say where it came from"
        assert item["kind"]
        assert item["reason"], "every item must justify its inclusion"
        assert item["permission"] in ("OWNED", "NOT CONNECTED")
        # confidence may legitimately be None - that is honest for subsystems
        # that record no confidence, and must never be faked as a number.
        if item["confidence"] is not None:
            assert 0.0 <= item["confidence"] <= 1.0


def test_section_caps_keep_context_bounded(runtime, builder, u):
    """
    Per-section caps are the first line of defence: even with 60 long memories
    the bundle stays small, so global truncation should not normally fire.
    """
    for i in range(60):
        runtime.memory.create(
            u, f"Detail {i}: " + ("a long piece of remembered context " * 6),
            allow_duplicate=True)
    data = builder.build(u, "tell me everything about the context").as_dict()
    memories = data["sections"].get("memory", [])
    assert len(memories) <= 6, "the memory section must respect its cap"
    assert data["prompt_chars"] <= 4500


def test_truncation_is_declared_not_silent(builder, u):
    """When content genuinely overflows, the bundle must SAY it was trimmed."""
    huge = [{"id": f"m{i}", "content": "x" * 900, "score": 1.0 - i * 0.01,
             "confidence": 0.8, "source": "conversation", "reasons": ["test"],
             "category": "FACT", "status": "active"} for i in range(12)]
    data = builder.build(u, "everything", retrieved=huge).as_dict()
    assert data["truncated"] is True
    assert data["prompt_chars"] <= 4500
    # And the dropped content is genuinely absent, not silently summarised.
    assert len(data["items"]) < len(huge)


def test_empty_context_is_honest_not_padded(builder, u):
    bundle = builder.build(u, "anything at all?")
    data = bundle.as_dict()
    assert isinstance(data["items"], list)
    # No fabricated filler when the user is brand new.
    assert all(i["content"] for i in data["items"])


def test_prompt_rendering_is_plain_and_labelled(runtime, builder, u):
    runtime.memory.create(u, "Prefers concise written answers",
                          allow_duplicate=True)
    prompt = builder.build(u, "how should you answer me?").to_prompt()
    assert isinstance(prompt, str)
    if prompt.strip():
        assert "MEMORY" in prompt.upper() or "CONTEXT" in prompt.upper()


# ------------------------------------------------- §16 context fabric honesty
def test_external_sources_are_reported_unconnected_never_faked(builder, u):
    bundle = builder.build(u, "what is in my calendar and email?")
    unavailable = bundle.as_dict()["unavailable"]
    assert unavailable, "external sources must be declared, not omitted"
    assert {s["source"] for s in unavailable} >= {"calendar", "email", "files"}
    for source in unavailable:
        assert source["state"] in ("NOT CONNECTED", "NOT CONFIGURED",
                                   "UNCONNECTED")
        assert source["detail"], "an unconnected source must say why"


def test_no_external_content_is_ever_invented(builder, u):
    """An unconnected source must contribute zero context items."""
    bundle = builder.build(u, "check my calendar for tomorrow's meetings")
    data = bundle.as_dict()
    names = {s["source"].lower() for s in data["unavailable"]}
    for item in data["items"]:
        assert item["source"].lower() not in names
    # The prompt declares the gap rather than hiding it.
    assert "Not available to me" in bundle.to_prompt()


def test_a_failing_section_degrades_only_that_section(runtime, builder, u,
                                                      monkeypatch):
    """One broken subsystem must not take down the whole context build."""
    monkeypatch.setattr(
        builder, "_predictions",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    bundle = builder.build(u, "what do you know?")
    data = bundle.as_dict()
    assert data["degraded"] is True
    assert any("prediction" in n for n in data["notes"])


# ------------------------------------------- §21 object permanence via focus
def test_focus_is_recorded_and_retrievable(focus, u):
    focus.set_focus(u, "memory", "mem_abc123", session_id="s1", label="Redis")
    current = focus.current(u, session_id="s1")
    assert current[0]["subject_id"] == "mem_abc123"
    assert current[0]["label"] == "Redis"


def test_unknown_focus_kind_is_rejected(focus, u):
    with pytest.raises(ValueError):
        focus.set_focus(u, "wormhole", "x1")


def test_focus_is_scoped_per_session(focus, u):
    focus.set_focus(u, "memory", "mem_a", session_id="s1")
    focus.set_focus(u, "memory", "mem_b", session_id="s2")
    assert focus.current(u, session_id="s1")[0]["subject_id"] == "mem_a"
    assert focus.current(u, session_id="s2")[0]["subject_id"] == "mem_b"


def test_refocusing_replaces_rather_than_duplicates(focus, u):
    focus.set_focus(u, "memory", "mem_a", session_id="s1")
    focus.set_focus(u, "memory", "mem_b", session_id="s1")
    entries = [e for e in focus.current(u, session_id="s1")
               if e["subject_kind"] == "memory"]
    assert len(entries) == 1
    assert entries[0]["subject_id"] == "mem_b"


def test_clear_removes_focus(focus, u):
    focus.set_focus(u, "memory", "mem_a", session_id="s1")
    assert focus.clear(u, session_id="s1") >= 1
    assert focus.current(u, session_id="s1") == []


def test_typed_reference_resolves_to_the_focused_object(focus, u):
    focus.set_focus(u, "memory", "mem_abc123", session_id="s1")
    result = focus.resolve(u, "why do you trust that memory?", session_id="s1")
    assert result["resolved"] is True
    assert result["subject_id"] == "mem_abc123"
    assert result["reason"]


def test_a_reference_with_nothing_focused_does_not_resolve(focus, u):
    result = focus.resolve(u, "forget that memory", session_id="s1")
    assert result["resolved"] is False
    assert result["reason"]


def test_no_referential_phrase_means_no_resolution(focus, u):
    focus.set_focus(u, "memory", "mem_abc123", session_id="s1")
    result = focus.resolve(u, "tell me about deployment strategy",
                           session_id="s1")
    assert result["resolved"] is False
    assert "No referential phrase" in result["reason"]


def test_bare_deictic_is_ambiguous_with_two_focused_objects(focus, u):
    """'that' with two things focused must NOT guess."""
    focus.set_focus(u, "memory", "mem_a", session_id="s1")
    focus.set_focus(u, "decision", "dec_b", session_id="s1")
    result = focus.resolve(u, "forget that", session_id="s1")
    assert result["resolved"] is False
    assert "ambiguous" in result["reason"].lower() or "more than one" in \
        result["reason"].lower()


def test_bare_deictic_resolves_with_exactly_one_focused_object(focus, u):
    focus.set_focus(u, "memory", "mem_only", session_id="s1")
    result = focus.resolve(u, "why do you believe that?", session_id="s1")
    assert result["resolved"] is True
    assert result["subject_id"] == "mem_only"


def test_stable_ids_survive_across_turns(runtime, u):
    """Object permanence: the same memory keeps its id across the session."""
    created = runtime.memory.create(u, "Uses pnpm as the package manager",
                                    allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.cognition.process_turn(u, "what package manager do I use?")
    runtime.cognition.process_turn(u, "and what about the deploy process?")
    assert runtime.memory.get(mid) is not None
    assert runtime.memory.get(mid).id == mid
