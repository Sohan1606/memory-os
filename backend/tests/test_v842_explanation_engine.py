"""
Tests for V8.4.2 Advanced Explanation Engine.

Verifies:
- All 20+ explanation classes
- All 7 query intents (why, why_not, why_now, what_changed, what_evidence, what_alternatives, what_caused_change)
- Candidate alternative analysis with factual rejection reasons
- Historical truth vs current state for corrected/retired knowledge
- Causal upstream/downstream evidence assembly
- Persistence and auditable snapshots
- User namespace isolation
- HTTP API endpoints
- Conversational cognitive tool integration
"""

import json
import pytest
from fastapi.testclient import TestClient

from app.main import app, rt
from app.runtime import Runtime


@pytest.fixture
def client(runtime: Runtime):
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_explanation_memory_recall_and_alternatives(runtime: Runtime):
    user_a = "user_exp_a"
    mem_service = runtime.memory
    res1 = mem_service.create(user_a, "Deploy using docker compose up -d", category="PROJECT", importance=0.8)
    res2 = mem_service.create(user_a, "Deploy using kubectl apply -f k8s.yaml", category="PROJECT", importance=0.6)
    m1 = res1["memory"]
    m2 = res2["memory"]

    # Scored arbitration between m1 and m2
    arb_result = runtime.cognition.arbiter_v2.arbitrate(
        user_a,
        [
            {"id": m1["id"], "content": m1["content"], "confidence": 0.9, "source": "conversation", "updated_at": m1["updated_at"]},
            {"id": m2["id"], "content": m2["content"], "confidence": 0.6, "source": "conversation", "updated_at": m2["updated_at"]},
        ],
        query="how to deploy",
    )

    exp_engine = runtime.cognition.explanation_engine

    # 1. WHY
    exp_why = exp_engine.explain(user_a, subject_kind="memory", subject_id=m1["id"], query_intent="why")
    assert exp_why["explanation_type"] == "MEMORY_RECALL"
    assert exp_why["query_intent"] == "why"
    assert m1["id"] == exp_why["subject"]["id"]
    assert "docker compose" in exp_why["summary"]
    assert len(exp_why["decisive_factors"]) > 0

    # 2. WHAT_ALTERNATIVES
    exp_alts = exp_engine.explain(user_a, subject_kind="memory", subject_id=m1["id"], query_intent="what_alternatives")
    assert len(exp_alts["alternatives"]) >= 1
    alt = exp_alts["alternatives"][0]
    assert alt["id"] == m2["id"]
    assert "kubectl" in alt["label"]
    assert "Lower" in alt["rejection_reason"] or "score" in alt["rejection_reason"].lower()

    # 3. WHY_NOT on unselected memory
    exp_why_not = exp_engine.explain(user_a, subject_kind="memory", subject_id=m2["id"], query_intent="why_not")
    assert m2["id"] == exp_why_not["subject"]["id"]


def test_explanation_knowledge_correction_historical_truth(runtime: Runtime):
    user = "user_corr_exp"
    know = runtime.cognition.knowledge
    exp_store = runtime.cognition.experiences
    obs_log = runtime.cognition.observations

    # Seed 3 distinct observations and experiences to satisfy validation policy
    e_ids = []
    for i in range(3):
        o = obs_log.record(
            user, f"Build failed on missing lockfile {i}", source="outcome", origin=f"npm:test:{i}", confidence=0.9
        )
        e = exp_store.create(
            user,
            situation=f"npm ci fails {i}",
            evidence_ids=[o["id"]],
            action="regenerate package-lock.json",
            outcome="build succeeds",
            success=True,
            confidence=0.85,
        )
        exp_store.validate(user, e["id"])
        e_ids.append(e["id"])

    # Propose and promote a skill
    skill = know.propose_skill(
        user,
        name="Regenerate Lockfile on Failure",
        statement="When build fails, regenerate package-lock.json before retrying.",
        trigger="build failure with lockfile error",
        procedure=["npm install", "git add package-lock.json"],
        expected_outcome="clean build",
        supporting_experience_ids=e_ids,
    )
    val = know.validate(user, skill["id"])
    assert val["decision"] == "PASS"

    # User later corrects and retires the skill
    corrected = know.correct(
        user,
        skill["id"],
        action="retire",
        reason="Security policy forbids modifying lockfile in CI. Use frozen lockfile.",
    )
    assert corrected["lifecycle"] == "retired"

    # Explain knowledge
    exp_engine = runtime.cognition.explanation_engine
    exp = exp_engine.explain(user, subject_kind="skill", subject_id=skill["id"], query_intent="what_changed")

    assert exp["explanation_type"] in ("LEARNED_KNOWLEDGE_RETRIEVAL", "KNOWLEDGE_RETIREMENT", "KNOWLEDGE_CORRECTION")
    assert exp["correction"] is not None
    assert exp["correction"]["is_corrected"] is True
    assert exp["correction"]["current_lifecycle"] == "retired"
    assert "Security policy forbids" in exp["correction"]["reason"]
    assert exp["subject"]["lifecycle"] == "retired"
    assert "Security policy forbids" in exp["summary"]

    # WHY_NOT explains why the skill is no longer eligible for retrieval
    exp_not = exp_engine.explain(user, subject_kind="skill", subject_id=skill["id"], query_intent="why_not")
    assert "NOT trusted" in exp_not["summary"] or "retired" in exp_not["summary"]


def test_all_20_explanation_classes_supported(runtime: Runtime):
    user = "user_20_classes"
    exp_engine = runtime.cognition.explanation_engine

    classes_to_test = [
        ("MEMORY_RECALL", "memory", "mem_1"),
        ("LEARNED_KNOWLEDGE_RETRIEVAL", "skill", "sk_1"),
        ("ARBITRATION", "arbitration", "arb_1"),
        ("DECISION_INFLUENCE", "decision", "dec_1"),
        ("TOOL_SELECTION", "routing", "memory_retrieval"),
        ("INTERVENTION", "intervention", "int_1"),
        ("ATTENTION", "attention", "attention_engine"),
        ("PREDICTION", "prediction", "pred_1"),
        ("PREDICTION_OUTCOME", "prediction", "pred_1"),
        ("LEARNING", "experience", "exp_1"),
        ("KNOWLEDGE_VALIDATION", "skill", "sk_1"),
        ("KNOWLEDGE_CORRECTION", "skill", "sk_1"),
        ("KNOWLEDGE_RETIREMENT", "skill", "sk_1"),
        ("CAUSAL_CHANGE", "causal", "node_1"),
        ("CONTINUITY_CHANGE", "continuity", "item_1"),
        ("WORLD_CHANGE", "world", "entity_1"),
        ("INTENT_CHANGE", "intent", "intent_1"),
        ("AUTONOMY_ACTION", "autonomy", "general"),
        ("AUTONOMY_REFUSAL", "autonomy", "general"),
        ("WHY_NOW", "conversation", "current_turn"),
        ("EXPERIENCE_FORMATION", "experience", "exp_1"),
        ("POLICY_CHANGE", "policy", "response_depth"),
        ("MISSION_STATE_CHANGE", "mission", "mis_1"),
    ]

    for exp_type, kind, sid in classes_to_test:
        res = exp_engine.explain(user, subject_kind=kind, subject_id=sid, explanation_type=exp_type, persist=False)
        assert res is not None
        assert "summary" in res
        assert "decisive_factors" in res
        assert "supporting_evidence" in res
        assert "alternatives" in res
        assert "causality" in res
        assert "provenance" in res


def test_explanation_decision_causal_chains(runtime: Runtime):
    user = "user_dec_causal"
    dec_log = runtime.cognition.decisions
    causal = runtime.cognition.causal

    # Record decision
    dec = dec_log.record(
        user,
        summary="Migrate DB to SQLite WAL mode",
        chosen="enable WAL mode",
        alternatives=["stay on default journal mode", "switch to postgres"],
        expected_outcome="reduce lock contention during concurrent queries",
    )

    # Link memory -> decision
    causal.link(user, "memory", "mem_wal_1", "decision", dec["id"], relation="influenced", weight=0.8)

    # Resolve decision outcome
    resolved = dec_log.resolve(
        user,
        dec["id"],
        actual_outcome="Concurrent reads succeeded with 0 lock errors",
        positive=True,
    )
    assert resolved["status"] == "resolved"

    exp_engine = runtime.cognition.explanation_engine
    exp = exp_engine.explain(user, subject_kind="decision", subject_id=dec["id"], query_intent="what_caused_change")

    assert exp["explanation_type"] == "DECISION_INFLUENCE"
    assert len(exp["causality"]["upstream"]) >= 1
    assert exp["causality"]["upstream"][0]["cause_id"] == "mem_wal_1"
    assert "enable WAL mode" in exp["summary"]
    assert "Concurrent reads succeeded" in exp["summary"]


def test_user_namespace_isolation_for_explanations(runtime: Runtime):
    user_a = "user_iso_a"
    user_b = "user_iso_b"

    res_a = runtime.memory.create(user_a, "Secret recipe of User A", category="FACT")
    mem_a_id = res_a["memory"]["id"]
    exp_engine = runtime.cognition.explanation_engine

    # User A generates explanation snapshot
    exp_a = exp_engine.explain(user_a, subject_kind="memory", subject_id=mem_a_id, persist=True)
    snapshot_id = exp_a["id"]

    # User A can get snapshot
    saved_a = exp_engine.get_snapshot(user_a, snapshot_id)
    assert saved_a is not None
    assert "Secret recipe" in saved_a["summary"]

    # User B cannot get User A's snapshot
    saved_b = exp_engine.get_snapshot(user_b, snapshot_id)
    assert saved_b is None

    # User B list snapshots is empty
    list_b = exp_engine.list_snapshots(user_b)
    assert len(list_b) == 0

    # User B explaining User A's memory returns INSUFFICIENT EVIDENCE
    exp_b = exp_engine.explain(user_b, subject_kind="memory", subject_id=mem_a_id, persist=False)
    assert "INSUFFICIENT EVIDENCE" in exp_b["summary"]


def test_explanation_api_endpoints(client: TestClient, runtime: Runtime):
    user = runtime.settings.demo_user_id
    res = runtime.memory.create(user, "API test memory explanation", category="FACT")
    mem_id = res["memory"]["id"]

    # 1. GET /api/explanations/subject/{subject_kind}/{subject_id}
    res_sub = client.get(f"/api/explanations/subject/memory/{mem_id}?intent=why")
    assert res_sub.status_code == 200
    data = res_sub.json()
    assert data["explanation_type"] == "MEMORY_RECALL"
    assert "API test memory explanation" in data["summary"]
    exp_id = data["id"]

    # 2. GET /api/explanations/{id}
    res_get = client.get(f"/api/explanations/{exp_id}")
    assert res_get.status_code == 200
    assert res_get.json()["id"] == exp_id

    # 3. GET /api/explanations
    res_list = client.get("/api/explanations")
    assert res_list.status_code == 200
    explanations = res_list.json()["explanations"]
    assert len(explanations) >= 1
    assert any(e["id"] == exp_id for e in explanations)

    # 4. POST /api/explanations/query
    res_query = client.post("/api/explanations/query", json={
        "subject_kind": "memory",
        "subject_id": mem_id,
        "query_intent": "what_evidence",
        "persist": True,
    })
    assert res_query.status_code == 200
    assert res_query.json()["query_intent"] == "what_evidence"


def test_agent_explain_tool_focus_resolution(runtime: Runtime):
    from app.agent.cognitive_tools import build_cognitive_tools

    user = "user_tool_explain"
    res = runtime.memory.create(user, "Focus target memory for tool explain", category="FACT")
    mem_id = res["memory"]["id"]
    mem_content = res["memory"]["content"]

    # Set focus on this memory
    runtime.cognition.focus.set_focus(user, "memory", mem_id, label=mem_content[:40], session_id="test_sess")

    tools = build_cognitive_tools(runtime.cognition, user, thread_id="test_sess")
    explain_tool = next(t for t in tools if t.name in ("explain_cognition", "explain"))

    # Call with no args (resolves single focused item)
    res_json = explain_tool.func(intent="why")
    res_tool = json.loads(res_json)

    assert res_tool["status"] == "OK"
    assert res_tool["subject"]["id"] == mem_id
    assert "Focus target memory" in res_tool["summary"]
    assert isinstance(res_tool["state_now"], dict)
    assert res_tool["state_now"]["lifecycle"] in ("active", "candidate", "trusted")
    assert res_tool["state_now"]["status"] == "active"


def test_tool_registry_and_schema_for_explanation(runtime: Runtime):
    from app.agent.cognitive_tools import (
        build_cognitive_tools,
        EXPLANATION_INTENTS,
    )
    from app.agent.graph import SYSTEM_PROMPT

    user = "user_tool_registry"
    tools = build_cognitive_tools(runtime.cognition, user)
    tool_names = [t.name for t in tools]

    assert "explain_cognition" in tool_names
    assert "inspect_learned" in tool_names

    exp_tool = next(t for t in tools if t.name == "explain_cognition")
    assert "WHY" in exp_tool.description
    assert "WHY_NOT" in exp_tool.description
    assert "WHAT_CHANGED" in exp_tool.description
    assert "inspect_learned" in exp_tool.description

    inspect_tool = next(t for t in tools if t.name == "inspect_learned")
    assert "DO NOT use for 'why did you use that skill'" in inspect_tool.description
    assert "explain_cognition" in inspect_tool.description

    # Test args schema validation and normalization
    schema = exp_tool.args_schema
    fields = schema.model_fields
    assert "intent" in fields
    assert "subject_id" in fields
    assert "subject_kind" in fields
    assert "question" in fields

    for intent in EXPLANATION_INTENTS:
        parsed = schema(intent=intent)
        assert parsed.intent == intent

    # Null tolerance
    parsed_null = schema(intent=None, subject_id=None, subject_kind=None)
    assert parsed_null.intent == "why"
    assert parsed_null.subject_id == ""
    assert parsed_null.subject_kind == ""

    # Legacy question_kind alias
    parsed_alias = schema.model_validate({"question_kind": "why_not"})
    assert parsed_alias.intent == "why_not"


def test_system_prompt_explanation_required_guidance():
    from app.agent.graph import SYSTEM_PROMPT

    assert "EXPLANATION REQUIRED (explain_cognition):" in SYSTEM_PROMPT
    assert "the agent MUST call explain_cognition before answering" in SYSTEM_PROMPT
    assert "The model MUST NOT answer such questions from conversational intuition" in SYSTEM_PROMPT
    assert "inspect_learned: inspects the object's current recorded static state" in SYSTEM_PROMPT
    assert "explain_cognition: explains WHY / WHY_NOT / WHY_NOW / WHAT_CHANGED" in SYSTEM_PROMPT
    for pattern in ["why", "why not", "why now", "what changed", "what evidence", "what alternatives", "what caused this change"]:
        assert pattern in SYSTEM_PROMPT.lower()


def test_deterministic_routing_for_all_seven_intents(runtime: Runtime):
    from app.agent.cognitive_tools import build_cognitive_tools

    user = "user_det_seven_intents"
    res = runtime.memory.create(user, "Deterministic intent test memory", category="FACT")
    mem_id = res["memory"]["id"]
    runtime.cognition.focus.set_focus(user, "memory", mem_id, label="Deterministic intent", session_id="det_sess")

    tools = build_cognitive_tools(runtime.cognition, user, thread_id="det_sess")
    exp_tool = next(t for t in tools if t.name == "explain_cognition")

    intents = [
        "why",
        "why_not",
        "why_now",
        "what_changed",
        "what_evidence",
        "what_alternatives",
        "what_caused_change",
    ]

    for intent in intents:
        raw = exp_tool.func(intent=intent)
        payload = json.loads(raw)
        assert payload["status"] == "OK", f"Failed for intent {intent}: {payload}"
        assert payload["query_intent"] == intent
        assert "summary" in payload
        assert "decisive_factors" in payload
        assert "state_now" in payload
        assert isinstance(payload["state_now"], dict)


def test_regression_inspect_learned_does_not_replace_explain_cognition(runtime: Runtime):
    from app.agent.cognitive_tools import build_cognitive_tools

    user = "user_reg_inspect_vs_explain"
    know = runtime.cognition.knowledge
    exp_store = runtime.cognition.experiences
    obs_log = runtime.cognition.observations

    e_ids = []
    for i in range(3):
        o = obs_log.record(user, f"Database lock timeout {i}", source="outcome", origin=f"db:test:{i}", confidence=0.9)
        e = exp_store.create(
            user,
            situation=f"db lock {i}",
            evidence_ids=[o["id"]],
            action="retry with exponential backoff",
            outcome="query succeeds",
            success=True,
            confidence=0.85,
        )
        exp_store.validate(user, e["id"])
        e_ids.append(e["id"])

    skill = know.propose_skill(
        user,
        name="Retry with backoff on DB lock",
        statement="When database locks occur, retry with exponential backoff.",
        trigger="db lock timeout",
        procedure=["pause 100ms", "retry transaction"],
        expected_outcome="successful query execution",
        supporting_experience_ids=e_ids,
    )
    know.validate(user, skill["id"])
    know.promote(user, skill["id"])

    runtime.cognition.focus.set_focus(user, "skill", skill["id"], label=skill["name"], session_id="comp_sess")
    tools = build_cognitive_tools(runtime.cognition, user, thread_id="comp_sess")

    inspect_tool = next(t for t in tools if t.name == "inspect_learned")
    explain_tool = next(t for t in tools if t.name == "explain_cognition")

    # 1. inspect_learned returns static object view only
    inspect_raw = inspect_tool.func()
    inspect_payload = json.loads(inspect_raw)
    assert inspect_payload["status"] == "OK"
    assert "item" in inspect_payload
    assert "procedure" in inspect_payload["item"]
    assert "query_intent" not in inspect_payload
    assert "decisive_factors" not in inspect_payload
    assert "alternatives" not in inspect_payload

    # 2. explain_cognition returns dynamic multi-angle explanation graphs
    explain_raw = explain_tool.func(intent="why")
    explain_payload = json.loads(explain_raw)
    assert explain_payload["status"] == "OK"
    assert explain_payload["query_intent"] == "why"
    assert "decisive_factors" in explain_payload
    assert "alternatives" in explain_payload
    assert "causality" in explain_payload
    assert isinstance(explain_payload["state_now"], dict)
    assert explain_payload["state_now"]["lifecycle"] == "trusted"


def test_explain_cognition_structured_state_contract(runtime: Runtime):
    from app.agent.cognitive_tools import build_cognitive_tools

    user = "user_state_contract"
    know = runtime.cognition.knowledge
    exp_store = runtime.cognition.experiences
    obs_log = runtime.cognition.observations

    e_ids = []
    for i in range(3):
        o = obs_log.record(user, f"Observation {i}", source="outcome", origin=f"obs:{i}", confidence=0.9)
        e = exp_store.create(
            user,
            situation=f"sit {i}",
            evidence_ids=[o["id"]],
            action="action",
            outcome="outcome",
            success=True,
            confidence=0.85,
        )
        exp_store.validate(user, e["id"])
        e_ids.append(e["id"])

    skill = know.propose_skill(
        user,
        name="Contract Validation Skill",
        statement="Procedure statement for validation",
        trigger="trigger",
        procedure=["step 1", "step 2"],
        expected_outcome="expected",
        supporting_experience_ids=e_ids,
    )
    know.validate(user, skill["id"])
    know.promote(user, skill["id"])

    # Retire the skill with explicit reason
    retirement_reason = "Security policy forbids modifying lockfile in CI. Use frozen lockfile."
    corrected = know.correct(user, skill["id"], action="retire", reason=retirement_reason)
    assert corrected["lifecycle"] == "retired"

    runtime.cognition.focus.set_focus(user, "skill", skill["id"], label=skill["name"], session_id="contract_sess")
    tools = build_cognitive_tools(runtime.cognition, user, thread_id="contract_sess")
    explain_tool = next(t for t in tools if t.name == "explain_cognition")

    raw = explain_tool.func(intent="why_not")
    payload = json.loads(raw)

    assert payload["status"] == "OK"
    assert payload["explanation_type"] == "KNOWLEDGE_RETIREMENT"

    # Canonical structured state_now contract
    assert isinstance(payload["state_now"], dict)
    assert payload["state_now"]["lifecycle"] == "retired"
    assert payload["state_now"]["eligible_for_retrieval"] is False
    assert "scope" in payload["state_now"]

    # Canonical structured state_then contract
    assert isinstance(payload["state_then"], dict)
    assert payload["state_then"]["lifecycle"] == "trusted"
    assert payload["state_then"]["eligible_for_retrieval"] is True
    assert "scope" in payload["state_then"]

    # Change reason
    assert payload.get("change_reason") == retirement_reason or (payload.get("correction") and payload["correction"].get("reason") == retirement_reason)
    assert "security policy forbids" in (payload.get("change_reason") or payload.get("correction", {}).get("reason", "")).lower()


def test_legacy_explain_tool_compatibility(runtime: Runtime):
    from app.agent.cognitive_tools import build_cognitive_tools

    user = "user_legacy_compat"
    mis = runtime.cognition.missions.create(user, "Build payment integration", description="Stripe checkout")
    runtime.cognition.missions.set_state(user, mis["id"], "active", reason="Starting execution")
    runtime.cognition.missions.set_state(user, mis["id"], "paused", reason="Awaiting API credentials")
    runtime.cognition.missions.set_state(user, mis["id"], "active", reason="Resumed after credentials added")
    runtime.cognition.focus.set_focus(user, "mission", mis["id"], label="Build payment integration", session_id="leg_sess")

    tools = build_cognitive_tools(runtime.cognition, user, thread_id="leg_sess")
    legacy_tool = next(t for t in tools if t.name == "explain")
    canonical_tool = next(t for t in tools if t.name == "explain_cognition")

    # 1. Legacy explain why
    why_raw = legacy_tool.func(question_kind="why")
    why_res = json.loads(why_raw)
    assert why_res["status"] == "OK"
    assert "subjects" in why_res
    assert len(why_res["subjects"]) == 1
    sub = why_res["subjects"][0]
    assert sub["kind"] == "mission"
    assert sub["id"] == mis["id"]
    assert sub["label"] == "Build payment integration"
    assert "mission" in sub
    assert "history" in sub

    # 2. Legacy explain what_changed
    changed_raw = legacy_tool.func(question_kind="what_changed")
    changed_res = json.loads(changed_raw)
    assert changed_res["status"] == "OK"
    assert "mission_history" in changed_res
    hist = changed_res["mission_history"]
    assert any(h["from"] == "active" and h["to"] == "paused" for h in hist)
    assert any(h["from"] == "paused" and h["to"] == "active" for h in hist)
    assert any("resumed" in (h["reason"] or "").lower() for h in hist)
    for h in hist:
        assert "change" in h
        assert "from" in h
        assert "to" in h
        assert "reason" in h
        assert "when" in h

    # 3. Canonical tool preserves V8.4.2 structured envelope
    canon_raw = canonical_tool.func(intent="why")
    canon_res = json.loads(canon_raw)
    assert canon_res["status"] == "OK"
    assert "explanation_id" in canon_res
    assert "explanation_type" in canon_res
    assert "decisive_factors" in canon_res
    assert "state_now" in canon_res
    assert "summary" in canon_res


