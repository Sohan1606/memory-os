from pathlib import Path

import pytest

from app.cognition.events import EventBus
from app.cognition.governance import (
    LearningSignalDeriver,
    PolicyEvidenceAssembler,
    PolicyGovernanceService,
    PolicyRuntimeCoordinator,
)
from app.cognition.policy_engine import CognitivePolicyEngine
from app.persistence.db import Database


def services(tmp_path):
    db = Database(Path(tmp_path) / "memory.db")
    bus = EventBus(db)
    policy = CognitivePolicyEngine(db, bus)
    governance = PolicyGovernanceService(db, bus, policy)
    return db, bus, policy, governance


def test_lifecycle_history_and_canonical_mutation(tmp_path):
    db, bus, policy, governance = services(tmp_path)
    candidate = governance.create("u1", domain="DECISION", target="planning_preference",
                                  proposed_value="structured", reason="observed need",
                                  evidence_refs=["event:1"], correlation_id="turn-1")
    assert candidate["state"] == "VALIDATING"
    for state in ("PROPOSED", "ACCEPTED", "ACTIVE"):
        candidate = governance.transition("u1", candidate["id"], state,
                                          reason="confirmed", evidence_refs=["event:1"],
                                          correlation_id="turn-1")
    assert candidate["state"] == "ACTIVE"
    assert policy.get("u1", "planning_preference")["value"] == "structured"
    assert len(governance.history("u1", candidate["id"])) == 4
    assert bus.for_correlation("turn-1")[-1].type == "governance.transition"


def test_invalid_transition_and_user_isolation(tmp_path):
    _, _, _, governance = services(tmp_path)
    candidate = governance.create("u1", domain="ATTENTION", target="silence_tolerance",
                                  proposed_value="high", reason="r", evidence_refs=["e"],
                                  correlation_id="c")
    with pytest.raises(ValueError):
        governance.transition("u2", candidate["id"], "PROPOSED", reason="r",
                              evidence_refs=["e"], correlation_id="c")
    with pytest.raises(ValueError):
        governance.transition("u1", candidate["id"], "ACTIVE", reason="r",
                              evidence_refs=["e"], correlation_id="c")


def test_evidence_provenance_and_bounded_signal(tmp_path):
    db, bus, _, _ = services(tmp_path)
    bus.emit("u1", "policy.updated", "adapted", correlation_id="turn-2")
    assembler = PolicyEvidenceAssembler(db, bus)
    evidence = assembler.assemble("u1", {"target": "planning_preference"}, correlation_id="turn-2")
    assert evidence[0].provenance == "EventBus"
    assert evidence[0].kind == "adaptation"
    assert LearningSignalDeriver().derive(evidence)["signal"] == "insufficient_evidence"


def test_runtime_coordinator_is_bounded(tmp_path):
    _, _, _, governance = services(tmp_path)
    result = PolicyRuntimeCoordinator(governance).run_once("u1", "turn-3")
    assert result == {"invoked": True, "status": "no_candidate"}
