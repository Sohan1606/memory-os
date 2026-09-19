"""V8.4.1 integration: API, decisions, context, events and conversation tools."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent.cognitive_tools import build_cognitive_tools
from app.main import app


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def client(runtime):
    import app.main as main
    app.dependency_overrides[main.rt] = lambda: runtime
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def create_experience(cog, user: str, index: int, *, pattern: str,
                      action: str, situation: str,
                      generalization: str | None = None):
    observed = cog.observations.record(
        user, f"Observed successful result {index}", source="outcome",
        origin=f"integration:{pattern}:{index}", confidence=0.95)
    item = cog.experiences.create(
        user, situation, evidence_ids=[observed["id"]], action=action,
        outcome="the failure cause was found before another attempt", success=True,
        pattern_key=pattern,
        context=({"generalization_hint": generalization}
                 if generalization else {}), source="integration-test")
    cog.experiences.enrich(user, item["id"])
    cog.experiences.validate(user, item["id"])
    return cog.experiences.activate(user, item["id"])


def create_skill(cog, user: str, *, pattern: str = "inspect_failure",
                 action: str = "inspect relevant logs before retrying",
                 situation: str = "a deployment health check fails",
                 generalization: str | None = None):
    experiences = [create_experience(
        cog, user, i, pattern=pattern, action=action, situation=situation,
        generalization=generalization) for i in range(3)]
    item = cog.knowledge.propose_skill(
        user, f"Skill {pattern}", f"When {situation}, {action}.",
        trigger=situation, procedure=[action, "identify the failure signal"],
        expected_outcome="the cause is found before another attempt",
        supporting_experience_ids=[e["id"] for e in experiences],
        pattern_key=pattern, generalization_hint=generalization)
    assert cog.knowledge.validate(user, item["id"])["decision"] == "PASS"
    return cog.knowledge.promote(user, item["id"]), experiences


def _tools(cog, user: str, thread: str = "learning-thread"):
    return {t.name: t for t in build_cognitive_tools(
        cog, user, thread_id=thread, correlation_id="corr-v841")}


def _call(tools, name: str, **kwargs):
    return json.loads(tools[name].invoke(kwargs))


def test_api_experience_lifecycle_and_provenance(client):
    user = _u("api-exp")
    observation = client.post("/api/observations", json={
        "content": "Logs showed a connection timeout", "source": "outcome",
        "origin": "api-run:1", "confidence": 0.9, "user_id": user})
    assert observation.status_code == 200
    oid = observation.json()["observation"]["id"]
    created = client.post("/api/experiences", json={
        "situation": "deployment failed its health check",
        "evidence_ids": [oid], "action": "inspect logs",
        "outcome": "connection timeout identified", "success": True,
        "pattern_key": "inspect_logs", "provenance": {"run": "api-run:1"},
        "user_id": user})
    assert created.status_code == 201
    experience = created.json()
    assert experience["lifecycle"] == "observed"
    for lifecycle in ("enriched", "validated", "active"):
        moved = client.post(
            f"/api/experiences/{experience['id']}/lifecycle", json={
                "lifecycle": lifecycle, "reason": f"Move to {lifecycle}",
                "user_id": user})
        assert moved.status_code == 200, moved.text
        assert moved.json()["lifecycle"] == lifecycle
    provenance = client.get(
        f"/api/experiences/{experience['id']}/provenance",
        params={"user_id": user}).json()
    assert provenance["provenance"] == {"run": "api-run:1"}
    assert provenance["evidence"][0]["origin"] == "api-run:1"


def test_api_skill_candidate_validation_usage_outcome_and_correction(client, runtime):
    user = _u("api-skill")
    skill, experiences = create_skill(runtime.cognition, user)
    listed = client.get("/api/skills", params={"user_id": user})
    assert listed.status_code == 200
    assert listed.json()["skills"][0]["id"] == skill["id"]

    retrieved = client.post("/api/skills/retrieve", json={
        "query": "deployment health check failed; inspect logs",
        "user_id": user}).json()
    assert retrieved["winner"]["subject_id"] == skill["id"]
    use = client.post(f"/api/skills/{skill['id']}/use", json={
        "influenced_kind": "decision", "influenced_id": "api-decision",
        "how": "Won arbitration and changed the diagnostic plan",
        "arbitration_id": retrieved["arbitration"]["id"],
        "user_id": user})
    assert use.status_code == 200
    outcome = client.post(
        f"/api/learning/usages/{use.json()['id']}/outcome", json={
            "verdict": "SUPPORTED", "detail": "The logs revealed the cause",
            "evidence": ["incident report API-1"], "user_id": user})
    assert outcome.status_code == 200
    assert outcome.json()["reputation"]["successes"] == 1

    explanation = client.get(
        f"/api/skills/{skill['id']}/explanation",
        params={"user_id": user}).json()
    assert len(explanation["supporting_evidence"]) == len(experiences)
    assert explanation["validation_history"][0]["decision"] == "PASS"
    assert explanation["usage_history"][0]["outcome_verdict"] == "SUPPORTED"

    corrected = client.post(f"/api/skills/{skill['id']}/correction", json={
        "action": "outdated", "reason": "The deployment platform changed",
        "evidence": ["user correction"], "user_id": user})
    assert corrected.status_code == 200
    assert corrected.json()["lifecycle"] == "outdated"
    no_longer_used = client.post("/api/skills/retrieve", json={
        "query": "deployment failed", "user_id": user}).json()
    assert no_longer_used["winner"] is None


def test_api_principle_requires_multiple_skills_and_exposes_real_data(client, runtime):
    user = _u("api-principle")
    statement = "Investigate the underlying signal before repeating a failed operation."
    first, exps_a = create_skill(
        runtime.cognition, user, pattern="deploy_logs",
        generalization=statement)
    second, exps_b = create_skill(
        runtime.cognition, user, pattern="database_error",
        action="inspect the database error before rerunning",
        situation="a database migration fails", generalization=statement)
    created = client.post("/api/principles/candidates", json={
        "name": "Investigate before repeating", "statement": statement,
        "supporting_skill_ids": [first["id"], second["id"]],
        "supporting_experience_ids": [e["id"] for e in exps_a + exps_b],
        "application": ["inspect the real signal", "then decide whether to repeat"],
        "generality": 0.8, "pattern_key": "investigate_before_repeat",
        "user_id": user})
    assert created.status_code == 201
    pid = created.json()["id"]
    validation = client.post(
        f"/api/principles/{pid}/validate", params={"user_id": user})
    assert validation.status_code == 200
    assert validation.json()["decision"] == "PASS"
    promoted = client.post(
        f"/api/principles/{pid}/promote", params={"user_id": user})
    assert promoted.status_code == 200
    assert promoted.json()["lifecycle"] == "trusted"
    assert client.get("/api/principles", params={"user_id": user}).json()[
        "principles"][0]["id"] == pid


def test_api_user_isolation_for_experience_skill_and_explanation(client, runtime):
    owner = _u("owner")
    stranger = _u("stranger")
    skill, experiences = create_skill(runtime.cognition, owner)
    assert client.get(
        f"/api/skills/{skill['id']}", params={"user_id": stranger}).status_code == 404
    assert client.get(
        f"/api/skills/{skill['id']}/explanation",
        params={"user_id": stranger}).status_code == 404
    assert client.get(
        f"/api/experiences/{experiences[0]['id']}",
        params={"user_id": stranger}).status_code == 404


def test_conversational_tools_inspect_correct_and_resolve_focus(runtime):
    user = _u("tools")
    skill, _ = create_skill(runtime.cognition, user)
    tools = _tools(runtime.cognition, user)
    listed = _call(tools, "list_learned", kind="skills")
    assert listed["status"] == "OK"
    assert listed["items"][0]["confidence"] != listed["items"][0]["reputation"]
    focused = runtime.cognition.focus.current(user, session_id="learning-thread")
    assert focused[0]["subject_kind"] == "skill"
    assert focused[0]["subject_id"] == skill["id"]

    inspected = _call(tools, "inspect_learned")
    assert inspected["status"] == "OK"
    assert inspected["validation_history"][0]["decision"] == "PASS"
    assert "private chain-of-thought" in inspected["note"]

    correction_tool = tools["correct_learned"]
    schema = correction_tool.args_schema.model_json_schema()
    assert schema["properties"]["action"]["enum"] == [
        "retire", "weaken", "outdated", "contradict", "rescope"]
    for semantic in ("retire", "weaken", "outdated", "contradict", "rescope",
                     "focused", "omit item_id"):
        assert semantic in correction_tool.description.lower()

    corrected = _call(
        tools, "correct_learned", action="retire",
        reason="The user said to forget that skill")
    assert corrected["status"] == "UPDATED"
    assert corrected["action"] == "retire"
    assert corrected["previous_lifecycle"] == "trusted"
    assert corrected["resulting_lifecycle"] == "retired"
    assert corrected["lifecycle"] == "retired"
    assert runtime.cognition.knowledge.retrieve(
        user, "deployment failed", kind="skill")["winner"] is None
    retired_events = [
        event for event in runtime.cognition.bus.for_subject(
            "skill", skill["id"], user_id=user)
        if event.type == "skill.retired"]
    assert retired_events and retired_events[-1].payload["correction"] is True

    unchanged = _call(
        tools, "correct_learned", action="retire",
        reason="The user repeated the retirement correction")
    assert unchanged["status"] == "NO_CHANGE"
    assert unchanged["previous_lifecycle"] == "retired"
    assert unchanged["resulting_lifecycle"] == "retired"

    resolved = runtime.cognition.focus.resolve(
        user, "why did you stop using that skill?", session_id="learning-thread")
    assert resolved["resolved"] is True
    assert resolved["subject_id"] == skill["id"]


@pytest.mark.parametrize(
    ("action", "expected_lifecycle", "expected_event"), [
        ("retire", "retired", "skill.retired"),
        ("weaken", "weakened", "skill.weakened"),
        ("outdated", "outdated", "skill.outdated"),
        ("contradict", "contradicted", "skill.contradicted"),
        ("rescope", "trusted", "skill.rescoped"),
    ])
def test_conversational_correction_operations_are_truthful(
        runtime, action, expected_lifecycle, expected_event):
    user = _u(f"correct-{action}")
    skill, _ = create_skill(
        runtime.cognition, user, pattern=f"correct_{action}")
    runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], label=skill["name"],
        session_id="learning-thread")
    tools = _tools(runtime.cognition, user)
    kwargs = ({"scope_kind": "project", "scope_value": "Atlas"}
              if action == "rescope" else {})

    result = _call(
        tools, "correct_learned", action=action,
        reason=f"Explicit user {action} correction", **kwargs)

    assert result["status"] == "UPDATED"
    assert result["action"] == action
    assert result["previous_lifecycle"] == "trusted"
    assert result["resulting_lifecycle"] == expected_lifecycle
    assert result["lifecycle"] == expected_lifecycle
    if action == "rescope":
        assert result["scope"] == {"kind": "project", "value": "Atlas"}
    events = runtime.cognition.bus.for_subject(
        "skill", skill["id"], user_id=user)
    assert expected_event in {event.type for event in events}


def test_conversational_correction_rejects_unsupported_action(runtime):
    user = _u("correct-invalid")
    skill, _ = create_skill(runtime.cognition, user, pattern="correct_invalid")
    runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], label=skill["name"],
        session_id="learning-thread")
    tools = _tools(runtime.cognition, user)

    result = _call(
        tools, "correct_learned", action="delete",
        reason="Unsupported correction must not look successful")

    assert result["status"] == "INVALID"
    assert result["action"] is None
    assert runtime.cognition.knowledge.get(
        user, skill["id"])["lifecycle"] == "trusted"
    assert not any(
        event.type in {"skill.retired", "skill.weakened", "skill.outdated",
                       "skill.contradicted", "skill.rescoped"}
        for event in runtime.cognition.bus.for_subject(
            "skill", skill["id"], user_id=user))


def test_conversational_experience_listing_returns_only_real_episodes(runtime):
    user = _u("tool-exp")
    tools = _tools(runtime.cognition, user)
    assert _call(tools, "list_experiences")["status"] == "NO_EXPERIENCES_RECORDED"
    created = create_experience(
        runtime.cognition, user, 1, pattern="real_episode",
        action="inspect evidence", situation="an operation failed")
    result = _call(tools, "list_experiences")
    assert result["status"] == "OK"
    assert result["experiences"][0]["id"] == created["id"]
    assert result["experiences"][0]["evidence_count"] == 1


def test_context_builder_and_turn_record_real_skill_decision_influence(runtime):
    user = _u("turn")
    skill, _ = create_skill(runtime.cognition, user)
    trace = runtime.cognition.process_turn(
        user, "A deployment health check failed, so inspect relevant logs before retrying",
        conversation_id="turn-thread", correlation_id=f"turn_{uuid.uuid4().hex[:12]}")
    winner = trace["learned"]["skills"]["winner"]
    assert winner and winner["subject_id"] == skill["id"]
    section = trace["context"]["sections"]["skill"]
    assert section[0]["id"] == skill["id"]
    assert section[0]["extra"]["reputation"] == "INSUFFICIENT EVIDENCE"
    assert trace["learned_decision"]["status"] == "open"
    assert trace["learned_decision"]["chosen"] == skill["procedure"][0]
    assert trace["learned_decision"]["expected_outcome"] == skill["expected_outcome"]
    assert trace["learned_influences"][0]["item_id"] == skill["id"]
    assert trace["learned_influences"][0]["status"] == "awaiting_outcome"
    assert trace["learned_influences"][0]["influenced_id"] == trace[
        "learned_decision"]["id"]
    assert runtime.cognition.decisions.get(trace["learned_decision"]["id"])
    causal = runtime.cognition.causal.downstream(
        "skill", skill["id"], user_id=user)
    assert any(link["effect_kind"] == "decision"
               and link["effect_id"] == trace["learned_decision"]["id"]
               for link in causal)


def test_decision_outcome_creates_experience_and_updates_skill_reputation(runtime):
    user = _u("decision")
    skill, _ = create_skill(runtime.cognition, user)
    decision = runtime.cognition.decisions.record(
        user, "How should we respond to the failed deployment?",
        chosen="inspect logs before retrying",
        expected_outcome="identify the cause", influenced_by=[skill["id"]])
    before = len(runtime.cognition.experiences.list(user))
    resolved = runtime.cognition.decisions.resolve(
        user, decision["id"], "The timeout was identified in the logs",
        positive=True, regret_evidence=["deployment incident report"])
    assert resolved and resolved["status"] == "resolved"
    experiences = runtime.cognition.experiences.list(user)
    assert len(experiences) == before + 1
    assert experiences[0]["lifecycle"] == "active"
    assert experiences[0]["provenance"]["decision_id"] == decision["id"]
    updated = runtime.cognition.knowledge.get(user, skill["id"])
    assert updated["reputation"]["successes"] == 1
    assert updated["lifecycle"] == "reinforced"


def test_turn_decision_outcome_closes_the_genuine_skill_learning_loop(runtime):
    user = _u("turn-outcome")
    skill, _ = create_skill(runtime.cognition, user)
    before = len(runtime.cognition.experiences.list(user))
    trace = runtime.cognition.process_turn(
        user, "The deployment health check failed; inspect relevant logs before retrying",
        conversation_id="loop-thread",
        correlation_id=f"loop_{uuid.uuid4().hex[:12]}")
    decision = trace["learned_decision"]
    usage = trace["learned_influences"][0]
    assert decision and usage["influenced_id"] == decision["id"]

    resolved = runtime.cognition.decisions.resolve(
        user, decision["id"], "The logs exposed the timeout before another attempt",
        positive=True)

    assert resolved and resolved["status"] == "resolved"
    closed_usage = runtime.cognition.knowledge.get_usage(user, usage["id"])
    assert closed_usage["outcome_verdict"] == "SUPPORTED"
    assert any(evidence_id.startswith("obs_")
               for evidence_id in closed_usage["outcome_evidence"])
    learned = runtime.cognition.knowledge.get(user, skill["id"])
    assert learned["lifecycle"] == "reinforced"
    assert learned["reputation"]["successes"] == 1
    episodes = runtime.cognition.experiences.list(user)
    assert len(episodes) == before + 1
    assert episodes[0]["provenance"]["decision_id"] == decision["id"]


def test_background_cycle_reports_learning_findings_without_promotion(runtime):
    user = _u("background-int")
    for i in range(3):
        create_experience(
            runtime.cognition, user, i, pattern="background_integration",
            action="inspect the failure signal", situation="a scheduled job fails")
    result = runtime.cognition.background.run_cycle(user, force=True)
    assert "learning_patterns" in result["tasks_run"]
    assert any(f["kind"] == "skill_candidate" for f in result["findings"])
    skill = runtime.cognition.knowledge.list(user, kind="skill")[0]
    assert skill["validation_status"] == "passed"
    assert skill["lifecycle"] == "validating"


def test_v841_events_share_the_canonical_event_bus(runtime):
    user = _u("events")
    skill, _ = create_skill(runtime.cognition, user)
    events = runtime.cognition.bus.recent(user, limit=200)
    types = {event.type for event in events}
    assert {"experience.created", "experience.validated",
            "skill.candidate_created", "skill.validation_started",
            "skill.validated", "skill.promoted", "causal.linked"}.issubset(types)
    assert all(event.user_id == user for event in events)
    history = runtime.cognition.bus.for_subject(
        "skill", skill["id"], user_id=user)
    assert history and all(event.subject_id == skill["id"] for event in history)


def test_learning_consolidate_preserves_contract_and_adds_v841(client):
    body = client.post(
        "/api/learning/consolidate", params={"user_id": _u("consolidate")})
    assert body.status_code == 200
    result = body.json()
    assert {"proposed", "event_sample", "note", "experience_learning"} <= result.keys()
    assert result["experience_learning"]["promoted"] == 0


def test_demo_reset_removes_v841_state_and_focus(runtime):
    user = runtime.settings.demo_user_id
    skill, _ = create_skill(runtime.cognition, user)
    runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], session_id="default")
    runtime.cognition.knowledge.retrieve(
        user, "deployment health check failed inspect logs", kind="skill")
    assert runtime.cognition.knowledge.list(user, kind="skill")
    assert runtime.cognition.experiences.list(user)
    assert runtime.cognition.knowledge.arbiter.recent(user, limit=10)

    runtime.reset_demo()

    assert runtime.cognition.knowledge.list(user) == []
    assert runtime.cognition.experiences.list(user) == []
    assert runtime.cognition.knowledge.arbiter.recent(user, limit=10) == []
    assert not any(item["subject_kind"] in {"experience", "skill", "principle"}
                   for item in runtime.cognition.focus.current(
                       user, session_id="default"))
