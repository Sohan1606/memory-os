"""V8.4.1 end-to-end acceptance scenarios A-H.

The scenarios deliberately traverse persisted services instead of constructing
final rows or asserting isolated helper output.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cognition.arbitration import ArbiterV2
from app.cognition.causality import CausalGraph
from app.cognition.events import EventBus
from app.cognition.experience import ExperienceStore
from app.cognition.knowledge import KnowledgeService
from app.cognition.observation import ObservationLog
from app.cognition.reputation import ReputationStore
from app.persistence.db import Database


def _stack(path: Path):
    db = Database(path)
    bus = EventBus(db)
    observations = ObservationLog(db, bus)
    causal = CausalGraph(db, bus)
    experiences = ExperienceStore(db, bus, observations, causal)
    reputation = ReputationStore(db, bus)
    arbiter = ArbiterV2(db, bus, reputation)
    knowledge = KnowledgeService(
        db, bus, experiences, reputation, causal, arbiter)
    return SimpleNamespace(
        db=db, bus=bus, observations=observations, causal=causal,
        experiences=experiences, reputation=reputation, arbiter=arbiter,
        knowledge=knowledge)


@pytest.fixture
def system(tmp_path):
    value = _stack(tmp_path / "acceptance.sqlite3")
    yield value
    value.db.close()


def _experience(system, user: str, index: int, *, pattern: str,
                situation: str, action: str,
                scope_kind: str = "user", scope_value: str | None = None,
                generalization_hint: str | None = None):
    observation = system.observations.record(
        user, f"Observed success {index} for {pattern}", source="outcome",
        origin=f"acceptance:{pattern}:{index}", confidence=0.95)
    episode = system.experiences.create(
        user, situation, evidence_ids=[observation["id"]], action=action,
        outcome="the real failure signal was identified", success=True,
        pattern_key=pattern, scope_kind=scope_kind, scope_value=scope_value,
        context=({"generalization_hint": generalization_hint}
                 if generalization_hint else {}), source="acceptance-scenario")
    system.experiences.enrich(user, episode["id"])
    assert system.experiences.validate(user, episode["id"])[
        "lifecycle"] == "validated"
    return system.experiences.activate(user, episode["id"])


def _skill(system, user: str, *, pattern: str,
           situation: str = "a deployment health check fails",
           action: str = "inspect the deployment logs before retrying",
           scope_kind: str = "user", scope_value: str | None = None,
           preconditions: list[str] | None = None,
           generalization_hint: str | None = None, start: int = 0):
    episodes = [_experience(
        system, user, start + i, pattern=pattern, situation=situation,
        action=action, scope_kind=scope_kind, scope_value=scope_value,
        generalization_hint=generalization_hint) for i in range(3)]
    candidate = system.knowledge.propose_skill(
        user, f"Skill {pattern}", f"When {situation}, {action}.",
        trigger=situation, preconditions=preconditions or [],
        procedure=[action, "identify the actual failure signal"],
        expected_outcome="the cause is known before another attempt",
        supporting_experience_ids=[item["id"] for item in episodes],
        scope_kind=scope_kind, scope_value=scope_value, pattern_key=pattern,
        generalization_hint=generalization_hint)
    validation = system.knowledge.validate(user, candidate["id"])
    assert validation["decision"] == "PASS"
    return system.knowledge.promote(user, candidate["id"]), episodes


def test_scenario_a_repeated_experience_becomes_validated_skill_candidate(system):
    """A: repeated outcomes create evidence, but automation never self-promotes."""
    user = "scenario-a"
    for index in range(3):
        _experience(
            system, user, index, pattern="inspect_worker_logs",
            situation="a scheduled worker fails",
            action="inspect worker logs before rerunning")

    maintenance = system.knowledge.maintain(user)

    assert maintenance["findings"][0]["kind"] == "skill_candidate"
    skill = system.knowledge.get(
        user, maintenance["findings"][0]["subject_id"])
    assert skill["validation_status"] == "passed"
    assert skill["lifecycle"] == "validating"
    assert skill["supporting_evidence_count"] == 3
    assert skill["reputation"]["score"] is None


def test_scenario_b_trusted_skill_influences_decision_and_earns_reputation(system):
    """B: trusted Skill → retrieval → influence → outcome → reputation."""
    user = "scenario-b"
    skill, _ = _skill(system, user, pattern="inspect_deployment_logs")
    retrieved = system.knowledge.retrieve(
        user, "The deployment health check failed; inspect its logs", kind="skill")
    usage = system.knowledge.record_use(
        user, skill["id"], influenced_kind="decision",
        influenced_id="scenario-b-decision",
        how="Won learned arbitration and changed the diagnostic plan.",
        arbitration_id=retrieved["arbitration"]["id"])
    result = system.knowledge.record_outcome(
        user, usage["id"], verdict="SUPPORTED",
        detail="The logs exposed a connection timeout.",
        evidence=["incident B-1"])

    assert retrieved["winner"]["subject_id"] == skill["id"]
    assert result["item"]["lifecycle"] == "reinforced"
    assert result["reputation"]["successes"] == 1
    assert any(edge["effect_id"] == "scenario-b-decision"
               for edge in system.causal.downstream(
                   "skill", skill["id"], user_id=user))


def test_scenario_c_failed_skill_is_weakened_contradicted_and_retired(system):
    """C: observed counter-outcomes refine, then stop, an unsafe Skill."""
    user = "scenario-c"
    skill, _ = _skill(system, user, pattern="retry_after_logs")
    uses = [system.knowledge.record_use(
        user, skill["id"], influenced_kind="decision",
        influenced_id=f"scenario-c-{index}", how="Applied to an actual run")
        for index in range(3)]
    states = []
    for index, usage in enumerate(uses):
        outcome = system.knowledge.record_outcome(
            user, usage["id"], verdict="CONTRADICTED",
            detail="The advised retry repeated the incident.",
            evidence=[f"incident C-{index}"])
        states.append(outcome["item"]["lifecycle"])

    assert states == ["weakened", "contradicted", "retired"]
    assert system.knowledge.retrieve(
        user, "deployment failed retry", kind="skill")["winner"] is None
    assert system.knowledge.get(user, skill["id"])["reputation"]["failures"] == 3


def test_scenario_d_multiple_skills_generalize_to_principle(system):
    """D: multiple Skill patterns, not frequency alone, support a Principle."""
    user = "scenario-d"
    statement = "Investigate the actual failure signal before repeating an operation."
    first, first_experiences = _skill(
        system, user, pattern="deployment_signal",
        generalization_hint=statement, start=10)
    second, second_experiences = _skill(
        system, user, pattern="migration_signal",
        situation="a database migration fails",
        action="inspect the database error before rerunning",
        generalization_hint=statement, start=20)

    maintenance = system.knowledge.maintain(user)
    findings = [finding for finding in maintenance["findings"]
                if finding["kind"] == "principle_candidate"]

    assert findings
    principle = system.knowledge.get(user, findings[0]["subject_id"])
    assert principle["lifecycle"] == "validating"
    assert principle["validation_status"] == "passed"
    principle = system.knowledge.promote(user, principle["id"])
    assert principle["lifecycle"] == "trusted"
    assert {edge["cause_id"] for edge in system.causal.upstream(
        "principle", principle["id"], user_id=user)}.issuperset(
            {first["id"], second["id"]})
    assert len(first_experiences + second_experiences) == 6


def test_scenario_e_trusted_principle_guides_cross_context_decision(system):
    """E: a promoted Principle participates in arbitration and outcome learning."""
    user = "scenario-e"
    statement = "Investigate the actual failure signal before repeating an operation."
    first, exps_a = _skill(
        system, user, pattern="service_signal",
        situation="a service deployment fails",
        generalization_hint=statement, start=30)
    second, exps_b = _skill(
        system, user, pattern="data_signal",
        situation="a data import fails",
        action="inspect the rejected-row signal before rerunning",
        generalization_hint=statement, start=40)
    candidate = system.knowledge.propose_principle(
        user, "Investigate before repeating", statement,
        supporting_skill_ids=[first["id"], second["id"]],
        supporting_experience_ids=[e["id"] for e in exps_a + exps_b],
        application=["inspect current evidence", "then decide whether to repeat"],
        expected_outcome="repeated actions are informed by their cause",
        pattern_key="investigate_before_repeat", generality=0.85)
    assert system.knowledge.validate(user, candidate["id"])["decision"] == "PASS"
    principle = system.knowledge.promote(user, candidate["id"])
    retrieved = system.knowledge.retrieve(
        user, "A new batch job failed; should we run it again?", kind="principle")
    use = system.knowledge.record_use(
        user, principle["id"], influenced_kind="decision",
        influenced_id="scenario-e-decision", how="Guided cross-context planning",
        arbitration_id=retrieved["arbitration"]["id"])
    learned = system.knowledge.record_outcome(
        user, use["id"], verdict="SUPPORTED",
        detail="Inspection found malformed input before a rerun.",
        evidence=["batch report E-1"])

    assert retrieved["winner"]["subject_id"] == principle["id"]
    assert learned["item"]["kind"] == "principle"
    assert learned["item"]["lifecycle"] == "reinforced"
    assert learned["reputation"]["successes"] == 1


def test_scenario_f_scope_and_current_world_prevent_wrong_use(system):
    """F: project scope and current-world preconditions are hard eligibility gates."""
    user = "scenario-f"
    skill, _ = _skill(
        system, user, pattern="atlas_cluster_logs",
        scope_kind="project", scope_value="Atlas",
        preconditions=["Kubernetes cluster is reachable"])

    wrong_project = system.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        scope={"project": "Nova"},
        current_world=["Kubernetes cluster is reachable"])
    stale_world = system.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        scope={"project": "Atlas"}, current_world=[])
    eligible = system.knowledge.retrieve(
        user, "deployment failed in Atlas", kind="skill",
        scope={"project": "Atlas"},
        current_world=["Kubernetes cluster is reachable"])

    assert wrong_project["winner"] is None
    assert stale_world["winner"] is None
    assert eligible["winner"]["subject_id"] == skill["id"]


def test_scenario_g_user_rescopes_then_forgets_without_erasing_audit(system):
    """G: user correction narrows future use and forgetting retires, not erases."""
    user = "scenario-g"
    skill, _ = _skill(system, user, pattern="environment_specific_logs")
    narrowed = system.knowledge.correct(
        user, skill["id"], action="rescope",
        reason="Only valid in Atlas production.",
        scope_kind="environment", scope_value="Atlas production")
    forgotten = system.knowledge.correct(
        user, skill["id"], action="forget",
        reason="Stop using that Skill.", evidence=["explicit user correction"])

    assert narrowed["scope_kind"] == "environment"
    assert forgotten["lifecycle"] == "retired"
    assert system.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        scope={"environment": "Atlas production"})["winner"] is None
    explanation = system.knowledge.explain(user, skill["id"])
    assert explanation["lifecycle_history"][-1]["lifecycle"] == "retired"
    assert len(explanation["supporting_evidence"]) == 3
    maintenance = system.knowledge.maintain(user)
    assert maintenance["changes"] == 0
    assert [item["id"] for item in system.knowledge.list(user, kind="skill")] == [
        skill["id"]]


def test_scenario_h_restart_and_user_isolation_preserve_objects(tmp_path):
    """H: durable learning survives restart and never crosses user namespaces."""
    path = tmp_path / "restart.sqlite3"
    first = _stack(path)
    skill, experiences = _skill(first, "scenario-h-owner", pattern="restart_skill")
    skill_id = skill["id"]
    experience_ids = {item["id"] for item in experiences}
    before_events = [event.type for event in first.bus.for_subject(
        "skill", skill_id, user_id="scenario-h-owner")]
    first.db.close()

    second = _stack(path)
    try:
        restored = second.knowledge.get("scenario-h-owner", skill_id)
        assert restored and restored["lifecycle"] == "trusted"
        assert {e["evidence_id"] for e in restored["evidence"]} == experience_ids
        assert [event.type for event in second.bus.for_subject(
            "skill", skill_id, user_id="scenario-h-owner")] == before_events
        assert second.knowledge.get("scenario-h-stranger", skill_id) is None
        assert second.causal.upstream(
            "skill", skill_id, user_id="scenario-h-stranger") == []
        with pytest.raises(KeyError):
            second.knowledge.correct(
                "scenario-h-stranger", skill_id, action="retire",
                reason="must not cross namespaces")
    finally:
        second.db.close()
