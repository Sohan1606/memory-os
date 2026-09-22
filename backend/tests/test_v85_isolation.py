"""V8.5 USER ISOLATION.

Proves User A cannot access User B's memories, events, world state, research,
evidence, portability packages, restore history, explanations, plans/goals/
commitments, skills/principles, causal/decision records — through the API,
through bare-object-id routes (IDOR), through the cognitive tools the model
calls, and through recovery/import.
"""
from __future__ import annotations

import json

import pytest

from conftest_v85 import (bearer, make_secure_runtime, register_and_login,
                          secure_client, teardown)


@pytest.fixture(scope="module")
def env():
    runtime, tmp = make_secure_runtime()
    client = secure_client(runtime)
    # Two self-registered users → two tenants, two namespaces.
    a_user, a_token, a_csrf = register_and_login(client, "a@iso.test", name="A")
    b_user, b_token, b_csrf = register_and_login(client, "b@iso.test", name="B")
    yield runtime, client, (a_user, a_token), (b_user, b_token)
    teardown(runtime, tmp)


# ------------------------------------------------------------------ memories
def test_memories_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    r = client.post("/api/memories", headers=bearer(a_token), json={
        "content": "A's secret project is called Nightjar.", "category": "FACT"})
    assert r.status_code == 201, r.text
    a_mem = r.json()["memory"]["id"]

    # B's listing never contains A's memory.
    b_list = client.get("/api/memories", headers=bearer(b_token)).json()
    assert all(m["id"] != a_mem for m in b_list["memories"])
    assert "Nightjar" not in json.dumps(b_list)

    # Direct-id fetch (IDOR) is indistinguishable from absence.
    assert client.get(f"/api/memories/{a_mem}",
                      headers=bearer(b_token)).status_code == 404
    # And B cannot mutate or delete it.
    assert client.patch(f"/api/memories/{a_mem}", headers=bearer(b_token),
                        json={"content": "corrupted"}).status_code == 404
    assert client.delete(f"/api/memories/{a_mem}",
                         headers=bearer(b_token)).status_code == 404
    # A still sees the memory untouched.
    got = client.get(f"/api/memories/{a_mem}", headers=bearer(a_token)).json()
    assert got["memory"]["content"].endswith("Nightjar.")


def test_search_never_crosses_namespaces(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    r = client.post("/api/memories/search", headers=bearer(b_token),
                    json={"query": "secret project Nightjar"})
    assert r.status_code == 200
    body = r.json()
    # The echoed query naturally contains the word; the RESULTS must not.
    assert "Nightjar" not in json.dumps(body.get("results", []))
    assert "Nightjar" not in json.dumps(body.get("path", []))


def test_user_id_substitution_is_ignored(env):
    """The IDOR the handoff names: supplying another user's id must not work."""
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    a_ns = a_user["namespace"]
    # Query-param substitution.
    r = client.get(f"/api/memories?user_id={a_ns}", headers=bearer(b_token))
    assert "Nightjar" not in json.dumps(r.json())
    # Body substitution.
    r = client.post("/api/memories/search", headers=bearer(b_token),
                    json={"query": "Nightjar", "user_id": a_ns})
    assert "Nightjar" not in json.dumps(r.json().get("results", []))
    # Even creating data "as" someone else lands in B's own namespace.
    r = client.post("/api/memories", headers=bearer(b_token), json={
        "content": "B tries to plant data into A's namespace.",
        "category": "FACT", "user_id": a_ns})
    assert r.status_code == 201
    planted = r.json()["memory"]["id"]
    row = runtime.db.query_one("SELECT user_id FROM memories WHERE id=?", (planted,))
    assert row["user_id"] == b_user["namespace"]


# ------------------------------------------------------------------- events
def test_events_and_turn_replay_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    r = client.post("/api/chat", headers=bearer(a_token), json={
        "message": "Remember my cat is named Biscuit.", "thread_id": "iso-a-1"})
    assert r.status_code == 200
    corr = r.json().get("correlation_id")

    b_events = client.get("/api/cognition/events", headers=bearer(b_token)).json()
    assert "Biscuit" not in json.dumps(b_events)
    if corr:
        # Turn replay by correlation id is owner-checked.
        assert client.get(f"/api/cognition/turn/{corr}",
                          headers=bearer(b_token)).status_code == 404
        assert client.get(f"/api/execution/{corr}",
                          headers=bearer(b_token)).status_code in (200, 404)
        # (200 only if the trace exists AND belongs to B — prove it doesn't)
        r2 = client.get(f"/api/execution/{corr}", headers=bearer(b_token))
        assert r2.status_code == 404


def test_conversations_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    b_conv = client.get("/api/conversations", headers=bearer(b_token)).json()
    conv_list = b_conv["conversations"] if isinstance(b_conv, dict) else b_conv
    assert all(c["id"] != "iso-a-1" for c in conv_list)
    msgs = client.get("/api/conversations/iso-a-1/messages",
                      headers=bearer(b_token)).json()
    assert "Biscuit" not in json.dumps(msgs)


def test_thread_id_collision_is_refused(env):
    """B cannot resume A's conversation by reusing A's thread id — the
    LangGraph checkpoint would otherwise leak A's dialogue state."""
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    r = client.post("/api/chat", headers=bearer(b_token), json={
        "message": "What did we talk about before?", "thread_id": "iso-a-1"})
    assert r.status_code == 409
    # And B's checkpoint read of that thread is empty.
    msgs = client.get("/api/conversations/iso-a-1/messages",
                      headers=bearer(b_token)).json()
    assert msgs["messages"] == []
    assert msgs["checkpoint_messages"] == []


# ------------------------------------------------------------------ world
def test_world_state_is_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    runtime.cognition.world.upsert(
        a_user["namespace"], "project", "Operation Nightjar", detail="secret")
    b_world = client.get("/api/world", headers=bearer(b_token)).json()
    assert "Nightjar" not in json.dumps(b_world)
    b_graph = client.get("/api/world/graph", headers=bearer(b_token)).json()
    assert "Nightjar" not in json.dumps(b_graph)


# --------------------------------------------------------------- portability
def test_portability_exports_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    r = client.post("/api/portability/v1/exports", headers=bearer(a_token), json={})
    assert r.status_code == 200, r.text
    export_id = r.json()["export"]["id"]

    # B cannot list, inspect, verify or download A's package.
    b_exports = client.get("/api/portability/v1/exports",
                           headers=bearer(b_token)).json()["exports"]
    assert all(e["id"] != export_id for e in b_exports)
    for path in (f"/api/portability/v1/exports/{export_id}",
                 f"/api/portability/v1/exports/{export_id}/manifest",
                 f"/api/portability/v1/exports/{export_id}/download"):
        assert client.get(path, headers=bearer(b_token)).status_code == 404, path
    assert client.post(f"/api/portability/v1/exports/{export_id}/verify",
                       headers=bearer(b_token)).status_code == 404


def test_import_of_foreign_package_is_rejected(env):
    """Recovery cannot be used to smuggle another user's data across."""
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    export_id = client.get("/api/portability/v1/exports",
                           headers=bearer(a_token)).json()["exports"][0]["id"]
    package = client.get(f"/api/portability/v1/exports/{export_id}/download",
                         headers=bearer(a_token)).content
    # B stages A's package: staging succeeds (untrusted input is accepted for
    # inspection) but validation MUST reject the ownership mismatch.
    r = client.post("/api/portability/v1/imports", headers=bearer(b_token),
                    files={"file": ("a-package.zip", package, "application/zip")})
    assert r.status_code == 200, r.text
    import_id = r.json()["import"]["id"]
    v = client.post(f"/api/portability/v1/imports/{import_id}/validate",
                    headers=bearer(b_token)).json()
    assert v["status"] in ("INVALID", "REJECTED")
    assert any("owner" in e.lower() for e in v["validation"]["errors"])
    # And restore refuses outright.
    rr = client.post(f"/api/portability/v1/imports/{import_id}/restore",
                     headers=bearer(b_token), json={"confirm": True})
    assert rr.status_code in (400, 409)


def test_restore_history_is_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    a_hist = client.get("/api/portability/v1/restore-history",
                        headers=bearer(a_token)).json()
    b_hist = client.get("/api/portability/v1/restore-history",
                        headers=bearer(b_token)).json()
    a_ids = {op["id"] for op in a_hist.get("operations", [])}
    b_ids = {op["id"] for op in b_hist.get("operations", [])}
    assert not (a_ids & b_ids) or (not a_ids and not b_ids)


# ------------------------------------------------------------------ research
def test_research_sessions_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    session = runtime.cognition.research_engine.start(
        a_user["namespace"], "What is A researching in secret?")
    sid = session["id"]
    b_sessions = client.get("/api/research/v2", headers=bearer(b_token)).json()
    assert sid not in json.dumps(b_sessions)
    assert client.get(f"/api/research/v2/{sid}",
                      headers=bearer(b_token)).status_code == 404
    # Sub-resources are namespace-scoped: a foreign session id yields the same
    # empty result as a nonexistent one (no existence oracle, no data).
    src = client.get(f"/api/research/v2/{sid}/sources", headers=bearer(b_token))
    assert src.status_code in (200, 404)
    if src.status_code == 200:
        assert src.json()["sources"] == []
    fet = client.get(f"/api/research/v2/{sid}/fetches", headers=bearer(b_token))
    assert fet.status_code in (200, 404)
    if fet.status_code == 200:
        assert fet.json()["fetches"] == []


# -------------------------------------------------------------- explanations
def test_explanations_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    a_expl = client.get("/api/explanations", headers=bearer(a_token)).json()
    b_expl = client.get("/api/explanations", headers=bearer(b_token)).json()
    a_ids = {e["id"] for e in a_expl["explanations"]}
    b_ids = {e["id"] for e in b_expl["explanations"]}
    assert not (a_ids & b_ids) or (not a_ids and not b_ids)
    for eid in a_ids:
        assert client.get(f"/api/explanations/{eid}",
                          headers=bearer(b_token)).status_code == 404


# ------------------------------------------------- skills/principles/decisions
def test_knowledge_and_decisions_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    a_ns = a_user["namespace"]
    # Create a real experience then a skill candidate in A's namespace through
    # the actual learning core (no fixtures, no parallel path).
    obs = runtime.cognition.observations.record(
        a_ns, "Nightjar deploy used a feature flag and rolled out cleanly.",
        source="conversation", origin="user")
    exp = runtime.cognition.experiences.create(
        a_ns, "Deployed Nightjar behind a feature flag",
        evidence_ids=[obs["id"]],
        action="used a flag", outcome="clean rollout", success=True)
    skill = runtime.cognition.knowledge.propose_skill(
        a_ns, "Nightjar flag rollout",
        "Always deploy Nightjar behind a feature flag.",
        trigger="deploying Nightjar", procedure=["create flag", "deploy"],
        expected_outcome="clean rollout",
        supporting_experience_ids=[exp["id"]])
    skill_id = skill["id"] if "id" in skill else skill.get("skill", {}).get("id")

    b_skills = client.get("/api/skills", headers=bearer(b_token)).json()
    assert "Nightjar" not in json.dumps(b_skills)
    if skill_id:
        assert client.get(f"/api/skills/{skill_id}",
                          headers=bearer(b_token)).status_code == 404

    r = client.post("/api/decisions", headers=bearer(a_token), json={
        "question": "When should Nightjar roll out?", "chosen": "Tuesday"})
    assert r.status_code == 200, r.text
    b_decisions = client.get("/api/decisions", headers=bearer(b_token)).json()
    assert "Nightjar" not in json.dumps(b_decisions)


def test_missions_goals_are_isolated(env):
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    mission = runtime.cognition.missions.create(
        a_user["namespace"], "Ship Nightjar v1")
    b_missions = client.get("/api/missions", headers=bearer(b_token)).json()
    assert "Nightjar" not in json.dumps(b_missions)


# -------------------------------------------------------------- cognitive tools
def test_cognitive_tools_bound_to_principal_namespace(env):
    """The conversational tools are constructed server-side with the OWNER's
    namespace; the model cannot pass a different user id — the parameter does
    not exist in any tool signature."""
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    from app.agent.cognitive_tools import build_cognitive_tools
    tools = build_cognitive_tools(runtime.cognition, b_user["namespace"])
    by_name = {t.name: t for t in tools}
    # No tool accepts a user/namespace/tenant argument.
    for tool in tools:
        schema = tool.args_schema.model_json_schema() if tool.args_schema else {}
        props = json.dumps(schema.get("properties", {})).lower()
        assert "user_id" not in props, tool.name
        assert "namespace" not in props, tool.name
        assert "tenant" not in props, tool.name
    # B's mission listing tool cannot see A's mission.
    listing = by_name["list_missions"].func()
    assert "Nightjar" not in listing


def test_direct_db_rows_remain_owner_scoped(env):
    """Direct database check: every cognitive row created above carries the
    correct owner namespace — nothing leaked into the wrong namespace."""
    runtime, client, (a_user, a_token), (b_user, b_token) = env
    a_ns, b_ns = a_user["namespace"], b_user["namespace"]
    for table in ("memories", "cognitive_events", "world_entities", "missions",
                  "decisions", "knowledge_items", "portability_exports"):
        rows = runtime.db.query(f"SELECT user_id FROM {table}")
        owners = {r["user_id"] for r in rows}
        # No row may be owned by a namespace that is neither A, B, demo (seeds)
        # nor the security audit scope.
        assert owners <= {a_ns, b_ns, runtime.settings.demo_user_id, "security"}, table
    a_rows = runtime.db.query("SELECT content FROM memories WHERE user_id=?", (b_ns,))
    assert all("Nightjar" not in r["content"] for r in a_rows)
