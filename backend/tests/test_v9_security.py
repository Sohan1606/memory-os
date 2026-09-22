"""V9 semantic object authorization and reference isolation."""
import json

import pytest

from conftest_v85 import (bearer, make_secure_runtime, register_and_login,
                          secure_client, teardown)


@pytest.fixture(scope="module")
def v9_secure():
    runtime, tmp = make_secure_runtime(disable_embeddings=True)
    client = secure_client(runtime)
    a, at, _ = register_and_login(client, "semantic-a@test.invalid", name="A")
    b, bt, _ = register_and_login(client, "semantic-b@test.invalid", name="B")
    yield runtime, client, (a, at), (b, bt)
    teardown(runtime, tmp)


def test_cross_user_semantic_reads_writes_and_ids_are_blocked(v9_secure):
    runtime, client, (a, at), (b, bt) = v9_secure
    created = client.post("/api/v9/cognitive-objects", headers=bearer(at), json={
        "type": "BOUNDARY", "content": "Never reveal Project Sable.",
        "modality": "NEGATED", "provenance": "USER_STATED"})
    assert created.status_code == 201, created.text
    oid = created.json()["id"]

    assert client.get(f"/api/v9/cognitive-objects/{oid}",
                      headers=bearer(bt)).status_code == 404
    assert client.patch(f"/api/v9/cognitive-objects/{oid}", headers=bearer(bt),
                        json={"content": "stolen", "reason": "attack"}).status_code == 404
    assert client.post(f"/api/v9/cognitive-objects/{oid}/retire",
                       headers=bearer(bt), json={}).status_code == 404
    listing = client.get("/api/v9/cognitive-objects", headers=bearer(bt)).json()
    assert "Sable" not in json.dumps(listing)
    assert runtime.cognition.personal_state.get(a["namespace"], oid)["content"].endswith("Sable.")


def test_live_surface_and_relationships_are_namespace_isolated(v9_secure):
    _, client, (a, at), (b, bt) = v9_secure
    turn = client.post("/api/v9/surface/turns", headers=bearer(at),
                       json={"thread_id": "secure-live-a"}).json()
    correlation = turn["conversation"]["correlation_id"]
    assert client.get(f"/api/v9/surface/turns/{correlation}",
                      headers=bearer(bt)).status_code == 404
    assert client.post("/api/chat", headers=bearer(bt), json={
        "message": "continue", "thread_id": "secure-live-a",
        "correlation_id": correlation}).status_code in (404, 409)

    a1 = client.post("/api/v9/cognitive-objects", headers=bearer(at), json={
        "type": "CLAIM", "content": "A-only one", "provenance": "USER_STATED"}).json()
    a2 = client.post("/api/v9/cognitive-objects", headers=bearer(at), json={
        "type": "CLAIM", "content": "A-only two", "provenance": "USER_STATED"}).json()
    assert client.post("/api/v9/relationships", headers=bearer(bt), json={
        "source_id": a1["id"], "target_id": a2["id"],
        "kind": "related_to", "provenance": "USER_STATED"}).status_code == 404
    assert "A-only" not in json.dumps(client.get(
        "/api/v9/relationships", headers=bearer(bt)).json())


def test_user_supplied_namespace_is_ignored_for_semantic_state(v9_secure):
    runtime, client, (a, at), (b, bt) = v9_secure
    response = client.post("/api/v9/cognitive-objects", headers=bearer(bt), json={
        "type": "FACT", "content": "This belongs to B.",
        "provenance": "USER_STATED", "user_id": a["namespace"]})
    assert response.status_code == 201
    row = runtime.db.query_one("SELECT user_id FROM cognitive_objects WHERE id=?",
                               (response.json()["id"],))
    assert row["user_id"] == b["namespace"]
