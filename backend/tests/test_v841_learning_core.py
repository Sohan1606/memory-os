"""Deterministic V8.4.1 core scenarios: Experience -> Skill -> Principle."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.cognition.arbitration import ArbiterV2
from app.cognition.background import BackgroundCognition
from app.cognition.causality import CausalGraph
from app.cognition.events import EventBus
from app.cognition.experience import ExperienceStore
from app.cognition.knowledge import KnowledgeService
from app.cognition.observation import ObservationLog
from app.cognition.reputation import ReputationStore
from app.persistence.db import Database


@pytest.fixture
def core():
    tmp = tempfile.TemporaryDirectory(prefix="memoryos-v841-")
    db = Database(Path(tmp.name) / "memory.db")
    bus = EventBus(db)
    observations = ObservationLog(db, bus)
    causal = CausalGraph(db, bus)
    experiences = ExperienceStore(db, bus, observations, causal)
    reputation = ReputationStore(db, bus)
    arbiter = ArbiterV2(db, bus, reputation)
    knowledge = KnowledgeService(
        db, bus, experiences, reputation, causal, arbiter)
    yield SimpleNamespace(
        db=db, bus=bus, observations=observations, causal=causal,
        experiences=experiences, reputation=reputation, arbiter=arbiter,
        knowledge=knowledge)
    db.close()
    tmp.cleanup()


def make_experience(core, user: str, index: int, *,
                    pattern: str = "inspect_failure_signal",
                    action: str = "inspect the relevant logs before retrying",
                    situation: str = "a deployment health check fails",
                    outcome: str = "the underlying failure signal is identified",
                    success: bool = True, scope_kind: str = "user",
                    scope_value: str | None = None,
                    generalization: str | None = None):
    observation = core.observations.record(
        user, f"Observed outcome {index}: {outcome}", source="outcome",
        origin=f"scenario:{pattern}:{index}", confidence=0.92,
        provenance={"scenario_index": index})
    experience = core.experiences.create(
        user, situation, evidence_ids=[observation["id"]], action=action,
        outcome=outcome, success=success, pattern_key=pattern,
        scope_kind=scope_kind, scope_value=scope_value,
        context=({"generalization_hint": generalization}
                 if generalization else {}),
        source="scenario", provenance={"observation_id": observation["id"]})
    core.experiences.enrich(user, experience["id"])
    core.experiences.validate(user, experience["id"])
    return core.experiences.activate(user, experience["id"])


def make_skill(core, user: str, *, pattern: str = "inspect_failure_signal",
               name: str = "Inspect failure logs before retrying",
               action: str = "inspect the relevant logs before retrying",
               situation: str = "a deployment health check fails",
               scope_kind: str = "user", scope_value: str | None = None,
               generalization: str | None = None, promote: bool = True,
               start: int = 0):
    exps = [make_experience(
        core, user, start + i, pattern=pattern, action=action,
        situation=situation, scope_kind=scope_kind, scope_value=scope_value,
        generalization=generalization) for i in range(3)]
    skill = core.knowledge.propose_skill(
        user, name, f"When {situation}, {action}.", trigger=situation,
        procedure=[action, "identify the failure signal", "retry after diagnosis"],
        expected_outcome="the cause is identified before another attempt",
        supporting_experience_ids=[e["id"] for e in exps],
        scope_kind=scope_kind, scope_value=scope_value, pattern_key=pattern,
        generalization_hint=generalization,
        provenance={"proposal": "deterministic scenario"})
    validation = core.knowledge.validate(user, skill["id"])
    assert validation["decision"] == "PASS"
    if promote:
        skill = core.knowledge.promote(user, skill["id"])
    else:
        skill = core.knowledge.get(user, skill["id"])
    return skill, exps


def test_experience_creation_persistence_provenance_and_lifecycle(core):
    user = "experience-owner"
    observed = core.observations.record(
        user, "The deployment logs showed a timeout", source="outcome",
        origin="run:deploy-17", confidence=0.95,
        provenance={"log": "deploy-17"})
    experience = core.experiences.create(
        user, "deployment health check failed", evidence_ids=[observed["id"]],
        action="inspect logs", outcome="timeout identified", success=True,
        pattern_key="inspect_failure", source="decision-outcome",
        provenance={"decision": "d17"}, thread_id="thread-17")

    assert experience["lifecycle"] == "observed"
    assert experience["evidence"][0]["origin"] == "run:deploy-17"
    assert experience["provenance"] == {"decision": "d17"}
    persisted = core.experiences.get(user, experience["id"])
    assert persisted and persisted["thread_id"] == "thread-17"
    upstream = core.causal.upstream(
        "experience", experience["id"], user_id=user)
    assert upstream[0]["cause_kind"] == "observation"
    assert upstream[0]["cause_id"] == observed["id"]

    core.experiences.enrich(user, experience["id"])
    core.experiences.validate(user, experience["id"])
    active = core.experiences.activate(user, experience["id"])
    assert active["lifecycle"] == "active"
    audit = core.experiences.provenance(user, experience["id"])
    assert [t["lifecycle"] for t in audit["transitions"]] == [
        "observed", "enriched", "validated", "active"]
    archived = core.experiences.archive(
        user, experience["id"], reason="No longer used for active learning.")
    assert archived["lifecycle"] == "archived"
    types = [e.type for e in core.bus.for_subject("experience", experience["id"])]
    assert {"experience.created", "experience.enriched",
            "experience.validated", "experience.activated",
            "experience.archived"}.issubset(types)


def test_experience_rejects_fabricated_or_cross_user_evidence(core):
    observation = core.observations.record(
        "alice", "Observed fact", source="conversation", origin="message:1")
    with pytest.raises(ValueError, match="requires at least one real observation"):
        core.experiences.create("alice", "something happened", evidence_ids=[])
    with pytest.raises(ValueError, match="user namespace"):
        core.experiences.create(
            "bob", "something happened", evidence_ids=[observation["id"]])
    assert core.experiences.get("bob", "not-real") is None


def test_skill_learning_validation_promotion_evidence_and_causality(core):
    user = "skill-learning"
    skill, experiences = make_skill(core, user)

    assert skill["lifecycle"] == "trusted"
    assert skill["validation_status"] == "passed"
    assert skill["confidence"] > 0.8
    assert skill["reputation"]["reputation"] == "INSUFFICIENT EVIDENCE"
    assert skill["supporting_evidence_count"] == 3
    assert len({e["evidence_id"] for e in skill["evidence"]}) == 3
    validation = skill["validations"][0]
    assert validation["metrics"]["distinct_evidence_origins"] == 3
    assert validation["metrics"]["pattern_consistent"] is True
    upstream = core.causal.upstream("skill", skill["id"], user_id=user)
    assert {link["cause_id"] for link in upstream} == {e["id"] for e in experiences}
    events = [e.type for e in core.bus.for_subject("skill", skill["id"])]
    assert events.index("skill.validated") < events.index("skill.promoted")


def test_skill_validation_rejects_frequency_without_sufficient_quality(core):
    user = "skill-rejection"
    one = make_experience(core, user, 1)
    skill = core.knowledge.propose_skill(
        user, "Retry immediately", "Retry immediately after failure.",
        trigger="operation fails", procedure=["retry"],
        expected_outcome="operation succeeds",
        supporting_experience_ids=[one["id"]], pattern_key="retry")
    result = core.knowledge.validate(user, skill["id"])
    assert result["decision"] == "REJECT"
    assert result["item"]["lifecycle"] == "candidate"
    assert result["item"]["validation_status"] == "rejected"
    with pytest.raises(ValueError, match="PASS evidence"):
        core.knowledge.promote(user, skill["id"])
    assert "skill.validation_failed" in {
        e.type for e in core.bus.for_subject("skill", skill["id"])}


def test_skill_use_updates_reputation_separately_from_confidence(core):
    user = "skill-use"
    skill, _ = make_skill(core, user)
    initial_confidence = skill["confidence"]
    unrelated = core.knowledge.retrieve(
        user, "plan a birthday dinner menu", kind="skill")
    assert unrelated["winner"] is None
    assert "No relevant" in unrelated["blocked"][0]["blocked_reason"]
    retrieval = core.knowledge.retrieve(
        user, "The deployment health check failed; inspect logs", kind="skill")
    assert retrieval["winner"]["subject_id"] == skill["id"]
    usage = core.knowledge.record_use(
        user, skill["id"], influenced_kind="decision", influenced_id="decision-1",
        how="Won arbitration and shaped the diagnostic plan.",
        arbitration_id=retrieval["arbitration"]["id"])
    assert usage["status"] == "awaiting_outcome"
    pending = core.knowledge.get(user, skill["id"])
    assert pending["lifecycle"] == "used"
    assert pending["reputation"]["evidence"] == 0
    assert pending["confidence"] == initial_confidence

    outcome = core.knowledge.record_outcome(
        user, usage["id"], verdict="SUPPORTED",
        detail="Logs revealed the timeout before another deployment.",
        evidence=["deployment run 18 log excerpt"])
    assert outcome["reputation"]["successes"] == 1
    assert outcome["item"]["lifecycle"] == "reinforced"
    assert outcome["item"]["reputation"]["score"] == 1.0
    assert outcome["item"]["confidence"] > initial_confidence
    downstream = core.causal.downstream("skill", skill["id"], user_id=user)
    assert any(l["effect_kind"] == "decision" for l in downstream)


def test_unsupported_usage_outcomes_weaken_contradict_then_retire(core):
    user = "skill-failure"
    skill, _ = make_skill(core, user)
    confidence = skill["confidence"]
    states = []
    for index in range(3):
        retrieval = core.knowledge.retrieve(
            user, "deployment health failure logs", kind="skill")
        # After the second poor result the contradicted skill is excluded from
        # normal retrieval, but an already-attributed real usage can still be
        # resolved. Create each usage while it remains eligible.
        if retrieval["winner"]:
            usage = core.knowledge.record_use(
                user, skill["id"], influenced_kind="decision",
                influenced_id=f"failure-{index}", how="Applied diagnostic skill",
                arbitration_id=retrieval["arbitration"]["id"])
        else:
            # Credible repeated poor outcomes already contradicted it; explicit
            # retirement is the safe auditable final step.
            retired = core.knowledge.correct(
                user, skill["id"], action="retire",
                reason="Repeated observed failures make continued use unsafe.",
                evidence=["two contradictory observed outcomes"])
            states.append(retired["lifecycle"])
            break
        result = core.knowledge.record_outcome(
            user, usage["id"], verdict="CONTRADICTED",
            detail=f"Attempt {index} repeated the failure.",
            evidence=[f"deployment failure report {index}"])
        states.append(result["item"]["lifecycle"])
    assert states[:2] == ["weakened", "contradicted"]
    assert states[-1] == "retired"
    final = core.knowledge.get(user, skill["id"])
    assert final["confidence"] < confidence
    assert final["reputation"]["failures"] == 2
    assert core.knowledge.retrieve(
        user, "deployment health failure logs", kind="skill")["winner"] is None


def test_three_already_attributed_failures_trigger_auditable_retirement(core):
    user = "automatic-retirement"
    skill, _ = make_skill(core, user)
    # Three decisions can be pending before their outcomes arrive. Resolving all
    # three as contradicted exercises the automatic evidence policy.
    uses = [core.knowledge.record_use(
        user, skill["id"], influenced_kind="decision",
        influenced_id=f"pending-{i}", how="Applied before outcomes arrived")
        for i in range(3)]
    states = []
    for i, use in enumerate(uses):
        result = core.knowledge.record_outcome(
            user, use["id"], verdict="CONTRADICTED",
            detail="Observed repeated failure", evidence=[f"failure-{i}"])
        states.append(result["item"]["lifecycle"])
    assert states == ["weakened", "contradicted", "retired"]
    final = core.knowledge.get(user, skill["id"])
    assert final["reputation"]["failures"] == 3
    assert final["reputation"]["reputation"] == "CONTRADICTED"


def test_current_world_preconditions_are_checked_before_use(core):
    user = "world-check"
    exps = [make_experience(core, user, i, pattern="kubernetes_logs")
            for i in range(3)]
    skill = core.knowledge.propose_skill(
        user, "Inspect Kubernetes logs", "Inspect pod logs before retrying.",
        trigger="deployment fails", procedure=["inspect pod logs"],
        preconditions=["Kubernetes cluster is reachable"],
        expected_outcome="failure signal is found",
        supporting_experience_ids=[e["id"] for e in exps],
        pattern_key="kubernetes_logs")
    assert core.knowledge.validate(user, skill["id"])["decision"] == "PASS"
    skill = core.knowledge.promote(user, skill["id"])
    blocked = core.knowledge.retrieve(
        user, "deployment failed", kind="skill", current_world=[])
    assert blocked["winner"] is None
    assert "precondition" in blocked["blocked"][0]["blocked_reason"]
    contradicted_world = core.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        current_world=["Kubernetes cluster is unreachable"])
    assert contradicted_world["winner"] is None
    explicitly_negated = core.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        current_world=["Kubernetes cluster is not reachable"])
    assert explicitly_negated["winner"] is None
    assert explicitly_negated["blocked"][0]["factors"]["current_world_match"] == 0.0
    usable = core.knowledge.retrieve(
        user, "deployment failed in Kubernetes", kind="skill",
        current_world=["Kubernetes cluster is reachable"])
    assert usable["winner"]["subject_id"] == skill["id"]


def test_outcome_without_evidence_does_not_move_reputation(core):
    user = "insufficient-outcome"
    skill, _ = make_skill(core, user)
    use = core.knowledge.record_use(
        user, skill["id"], influenced_kind="decision", influenced_id="d-empty",
        how="Explicit use")
    result = core.knowledge.record_outcome(
        user, use["id"], verdict="SUPPORTED", detail="It seemed fine", evidence=[])
    assert result["outcome_verdict"] == "INSUFFICIENT EVIDENCE"
    assert result["reputation"]["evidence"] == 0
    assert result["item"]["lifecycle"] == "used"


@pytest.mark.parametrize(
    ("action", "expected_lifecycle"), [
        ("forget", "retired"),
        ("stop_using", "retired"),
        ("doesnt_work", "weakened"),
        ("doesn't_work", "weakened"),
        ("invalid", "contradicted"),
        ("narrow", "trusted"),
    ])
def test_canonical_store_retains_correction_alias_compatibility(
        core, action, expected_lifecycle):
    user = f"alias-{action.replace(chr(39), '').replace('_', '-')}"
    skill, _ = make_skill(core, user, pattern=f"alias_{len(action)}_{action[0]}")
    kwargs = ({"scope_kind": "project", "scope_value": "Atlas"}
              if action == "narrow" else {})

    corrected = core.knowledge.correct(
        user, skill["id"], action=action,
        reason="Compatibility alias correction", **kwargs)

    assert corrected["lifecycle"] == expected_lifecycle
    if action == "narrow":
        assert corrected["scope_kind"] == "project"
        assert corrected["scope_value"] == "Atlas"


def test_scope_enforcement_and_rescoping_affect_future_retrieval(core):
    user = "scope-owner"
    skill, _ = make_skill(
        core, user, scope_kind="project", scope_value="Project Atlas")
    outside = core.knowledge.retrieve(
        user, "deployment failed", kind="skill", scope={"project": "Project Nova"})
    assert outside["winner"] is None
    assert outside["blocked"][0]["factors"]["scope_match"] == 0.0
    inside = core.knowledge.retrieve(
        user, "deployment failed", kind="skill", scope={"project": "Project Atlas"})
    assert inside["winner"]["subject_id"] == skill["id"]

    narrowed = core.knowledge.correct(
        user, skill["id"], action="rescope",
        reason="This is only valid in the Atlas production environment.",
        scope_kind="environment", scope_value="Atlas production")
    assert narrowed["scope_kind"] == "environment"
    assert core.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        scope={"project": "Project Atlas"})["winner"] is None
    assert core.knowledge.retrieve(
        user, "deployment failed", kind="skill",
        scope={"environment": "Atlas production"})["winner"] is not None


def test_reputation_can_outweigh_slight_similarity_advantage(core):
    user = "arbitration-owner"
    bad, _ = make_skill(core, user, pattern="inspect_deploy_logs",
                        name="Inspect deployment logs", start=10)
    good, _ = make_skill(
        core, user, pattern="inspect_runtime_signal",
        name="Investigate runtime failure signal", start=20,
        action="inspect the actual runtime failure signal",
        situation="an operation fails")
    # Build real usage evidence: bad has one success and one failure (contextual),
    # good has three successes (trusted). Both remain retrievable.
    for item, verdicts in ((bad, ["SUPPORTED", "CONTRADICTED"]),
                           (good, ["SUPPORTED", "SUPPORTED", "SUPPORTED"])):
        for index, verdict in enumerate(verdicts):
            use = core.knowledge.record_use(
                user, item["id"], influenced_kind="decision",
                influenced_id=f"{item['id']}-{index}", how="Historical use")
            core.knowledge.record_outcome(
                user, use["id"], verdict=verdict, detail="Observed",
                evidence=[f"outcome-{item['id']}-{index}"])
    result = core.knowledge.retrieve(
        user, "deployment logs failed retry", kind="skill")
    assert result["winner"]["subject_id"] == good["id"]
    candidates = {c["subject_id"]: c for c in result["candidates"]}
    assert candidates[bad["id"]]["factors"]["relevance"] >= candidates[good["id"]]["factors"]["relevance"]
    assert candidates[good["id"]]["factors"]["reputation_weight"] > candidates[bad["id"]]["factors"]["reputation_weight"]


def test_user_correction_weakens_contradicts_forgets_and_preserves_audit(core):
    user = "correction-owner"
    skill, _ = make_skill(core, user)
    weakened = core.knowledge.correct(
        user, skill["id"], action="weaken",
        reason="That skill does not work reliably anymore.",
        evidence=["explicit user correction"])
    assert weakened["lifecycle"] == "weakened"
    contradicted = core.knowledge.correct(
        user, skill["id"], action="contradict",
        reason="The deployment system changed.", evidence=["new deployment policy"])
    assert contradicted["lifecycle"] == "contradicted"
    assert contradicted["reputation"]["evidence_contradictions"] == 1
    forgotten = core.knowledge.correct(
        user, skill["id"], action="forget",
        reason="Do not use that skill.")
    assert forgotten["lifecycle"] == "retired"
    assert core.knowledge.retrieve(
        user, "deployment failed", kind="skill")["winner"] is None
    assert [t["lifecycle"] for t in forgotten["transitions"]][-3:] == [
        "weakened", "contradicted", "retired"]


def test_user_isolation_for_knowledge_and_causality(core):
    alice_skill, _ = make_skill(core, "alice-v841")
    assert core.knowledge.get("bob-v841", alice_skill["id"]) is None
    assert core.knowledge.list("bob-v841", kind="skill") == []
    with pytest.raises(KeyError):
        core.knowledge.correct(
            "bob-v841", alice_skill["id"], action="retire", reason="not mine")
    assert core.causal.upstream(
        "skill", alice_skill["id"], user_id="bob-v841") == []


def test_principle_requires_multiple_validated_skills_and_broad_evidence(core):
    user = "principle-learning"
    generalization = "Investigate the underlying failure signal before repeating a failed operation."
    skill_a, exps_a = make_skill(
        core, user, pattern="inspect_deploy_logs", start=100,
        generalization=generalization)
    skill_b, exps_b = make_skill(
        core, user, pattern="inspect_database_error", start=200,
        name="Inspect database error before rerun",
        action="inspect the database error before rerunning the migration",
        situation="a database migration fails", generalization=generalization)

    one_skill = core.knowledge.propose_principle(
        user, "Investigate before repeat", generalization,
        supporting_skill_ids=[skill_a["id"]], generality=0.8)
    rejected = core.knowledge.validate(user, one_skill["id"])
    assert rejected["decision"] == "REJECT"
    assert rejected["metrics"]["supporting_skills"] == 1

    principle = core.knowledge.propose_principle(
        user, "Investigate before repeating failures", generalization,
        supporting_skill_ids=[skill_a["id"], skill_b["id"]],
        supporting_experience_ids=[e["id"] for e in exps_a + exps_b],
        application=["inspect the actual failure signal", "then choose whether to retry"],
        expected_outcome="repeated attempts are informed by the real cause",
        pattern_key="investigate_before_repeat", generality=0.8)
    validation = core.knowledge.validate(user, principle["id"])
    assert validation["decision"] == "PASS"
    assert validation["metrics"]["supporting_skills"] == 2
    assert validation["metrics"]["supporting_experiences"] == 6
    assert validation["metrics"]["distinct_patterns"] == 2
    promoted = core.knowledge.promote(user, principle["id"])
    assert promoted["lifecycle"] == "trusted"
    upstream = core.causal.upstream("principle", principle["id"], user_id=user)
    assert {u["cause_kind"] for u in upstream} == {"skill", "experience"}

    core.knowledge.correct(
        user, skill_a["id"], action="retire",
        reason="This supporting Skill is no longer valid.")
    refined = core.knowledge.get(user, principle["id"])
    assert refined["lifecycle"] == "candidate"
    assert refined["validation_status"] == "rejected"
    assert refined["validations"][0]["metrics"]["supporting_skills"] == 1
    assert core.knowledge.retrieve(
        user, "investigate a failed operation", kind="principle")["winner"] is None


def test_principle_retrieval_use_reputation_contradiction_and_retirement(core):
    user = "principle-use"
    generalization = "Investigate the real signal before repeating a failed operation."
    a, ea = make_skill(core, user, pattern="pattern_a", start=300,
                       generalization=generalization)
    b, eb = make_skill(core, user, pattern="pattern_b", start=400,
                       name="Inspect operation signal", action="inspect operation signal",
                       situation="a batch operation fails", generalization=generalization)
    principle = core.knowledge.propose_principle(
        user, "Investigate before repeat", generalization,
        supporting_skill_ids=[a["id"], b["id"]],
        supporting_experience_ids=[e["id"] for e in ea + eb], generality=0.8,
        pattern_key="principle_signal_first")
    assert core.knowledge.validate(user, principle["id"])["decision"] == "PASS"
    principle = core.knowledge.promote(user, principle["id"])
    retrieval = core.knowledge.retrieve(
        user, "The operation failed; should I repeat it?", kind="principle")
    use = core.knowledge.record_use(
        user, principle["id"], influenced_kind="decision", influenced_id="pd-1",
        how="Guided failure investigation",
        arbitration_id=retrieval["arbitration"]["id"])
    supported = core.knowledge.record_outcome(
        user, use["id"], verdict="SUPPORTED", detail="Cause found",
        evidence=["incident report"])
    assert supported["item"]["lifecycle"] == "reinforced"
    assert supported["reputation"]["successes"] == 1
    contradicted = core.knowledge.correct(
        user, principle["id"], action="contradict",
        reason="New evidence shows immediate failover is required.",
        evidence=["new incident protocol"])
    assert contradicted["lifecycle"] == "contradicted"
    retired = core.knowledge.correct(
        user, principle["id"], action="retire",
        reason="Stop using that principle.")
    assert retired["lifecycle"] == "retired"


def test_background_learning_creates_and_validates_but_never_promotes(core):
    user = "background-learning"
    for i in range(3):
        make_experience(core, user, i, pattern="background_pattern")
    background = BackgroundCognition(
        core.db, core.bus, learning=core.knowledge)
    result = background.run_cycle(user, force=True)
    assert result["state"] == "completed"
    assert "learning_patterns" in result["tasks_run"]
    assert result["findings"]
    created = core.knowledge.list(user, kind="skill")
    assert len(created) == 1
    assert created[0]["validation_status"] == "passed"
    assert created[0]["lifecycle"] == "validating"
    assert not any(e.type == "skill.promoted" for e in core.bus.recent(user, 100))


def test_explanation_is_evidence_backed_not_private_reasoning(core):
    user = "explanation-owner"
    skill, experiences = make_skill(core, user)
    report = core.knowledge.explain(user, skill["id"])
    assert {e["evidence_id"] for e in report["supporting_evidence"]} == {
        e["id"] for e in experiences}
    assert report["validation_history"][0]["decision"] == "PASS"
    assert "confidence" in report["summary"].lower()
    assert "reputation" in report["summary"].lower()
    assert report["note"] == "Evidence summary only; no private chain-of-thought."
