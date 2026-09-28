from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timedelta, timezone
import unittest

from zroq.domain_contracts import (
    Action,
    ActionSnapshotRef,
    Capability,
    Confirmation,
    ContractValidationError,
    Conversation,
    ConversationBranch,
    ConversationCheckpoint,
    DeletionBehavior,
    DeletionPropagationTarget,
    DeletionRequest,
    DeletionResult,
    DeletionStatus,
    DerivedViewReference,
    Device,
    DomainIndex,
    Entity,
    EntityType,
    Evidence,
    EvidenceStrength,
    EvolutionProposal,
    EvolutionProposalStatus,
    GenerationState,
    Goal,
    GovernanceStatus,
    Grant,
    InteractionCommandType,
    InteractionControlCommand,
    InteractionTarget,
    Lease,
    LifecycleState,
    Memory,
    MemoryCapturePolicy,
    MemoryFirewallRequest,
    MemoryFirewallResult,
    MemoryGovernanceDecision,
    MemoryGovernanceRequest,
    MemoryKind,
    MemoryRetrievalHit,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemorySource,
    Message,
    Outcome,
    OutcomeState,
    OwnerIdentity,
    PrivacyClass,
    Project,
    Provenance,
    ProviderTrustClass,
    Recommendation,
    Relationship,
    RelationshipType,
    Response,
    ResponseCursor,
    ResumePolicy,
    RetentionMode,
    RetentionOverride,
    DeletionBehavior,
    EgressPolicy,
    RetrievalMode,
    Session,
    SourceClassification,
    SourceType,
    SpeechState,
    TemporalExtent,
    TimelineEvent,
    User,
    VerificationState,
    CONTRACT_TYPES,
)


HEX = "a" * 64


def dt(year=2026, month=9, day=27, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def prov(source_id="source-1", owner_id="owner-1"):
    return Provenance(
        provenance_id="prov-" + source_id.replace("source-", ""),
        owner_id=owner_id,
        source_type=SourceType.CONVERSATION_MESSAGE,
        source_id=source_id,
        created_at=dt(),
        observed_at=dt(),
        conversation_id="conv-1",
        message_id="msg-1",
        confidence=0.9,
        verification_state=VerificationState.UNVERIFIED,
    )


def source(source_id="source-1", owner_id="owner-1"):
    return MemorySource(
        source_id=source_id,
        owner_id=owner_id,
        source_owner_id=owner_id,
        source_type=SourceType.CONVERSATION_MESSAGE,
        created_at=dt(),
        observed_at=dt(),
        ingested_at=dt(),
        privacy_class=PrivacyClass.PERSONAL,
        retention_mode=RetentionMode.DEFAULT_RETAIN,
        retention_policy_id="retain-default",
        content_ref="conversation://conv-1/messages/msg-1",
        conversation_id="conv-1",
        message_id="msg-1",
    )


def memory(memory_id="mem-1", source_id="source-1", owner_id="owner-1", state=LifecycleState.CURRENT):
    return Memory(
        memory_id=memory_id,
        owner_id=owner_id,
        source_id=source_id,
        memory_kind=MemoryKind.PERSONAL,
        created_at=dt(),
        observed_at=dt(),
        valid_from=dt(),
        valid_until=None,
        privacy_class=PrivacyClass.PERSONAL,
        retention_policy_id="retain-default",
        lifecycle_state=state,
        provenance=prov(source_id, owner_id),
        content={"fact": "user prefers exact historical recall", "nested": {"safe": True}},
        confidence=0.8,
    )


class Phase3ADomainContractTests(unittest.TestCase):
    def test_contract_inventory_contains_required_schema_families(self):
        names = {cls.__name__ for cls in CONTRACT_TYPES}
        required = {
            "User", "OwnerIdentity", "Session", "Device",
            "Conversation", "ConversationBranch", "Message", "Response", "ResponseCursor", "ConversationCheckpoint",
            "MemorySource", "Memory", "TimelineEvent", "Entity", "Relationship",
            "Goal", "Project", "Decision", "Plan", "Observation", "Evidence", "Recommendation",
            "Action", "ActionSnapshotRef", "Outcome", "Capability", "Grant", "Confirmation", "Lease",
            "Experiment", "EvolutionProposal", "MemoryCapturePolicy", "MemoryRetrievalRequest", "DeletionRequest",
            "MemoryFirewallRequest", "MemoryGovernanceRequest",
        }
        self.assertTrue(required.issubset(names))
        self.assertGreaterEqual(len(CONTRACT_TYPES), 40)

    def test_serialization_round_trip_preserves_provenance_and_memory(self):
        m = memory()
        round_tripped = Memory.from_dict(m.to_dict())
        self.assertEqual(round_tripped, m)
        self.assertEqual(round_tripped.provenance.source_id, "source-1")
        self.assertEqual(round_tripped.content["nested"]["safe"], True)

    def test_unknown_field_and_unknown_enum_fail_closed(self):
        data = prov().to_dict()
        data["unexpected"] = "bad"
        with self.assertRaises(ContractValidationError):
            Provenance.from_dict(data)
        data = prov().to_dict()
        data["source_type"] = "NOT_A_SOURCE_TYPE"
        with self.assertRaises(ContractValidationError):
            Provenance.from_dict(data)

    def test_version_compatibility_rejects_unsupported_schema(self):
        data = prov().to_dict()
        data["schema_version"] = "zorq.phase99.future"
        with self.assertRaises(ContractValidationError):
            Provenance.from_dict(data)

    def test_id_timestamp_and_ownership_validation(self):
        with self.assertRaises(ContractValidationError):
            User(user_id="bad id with spaces", owner_id="owner-1", created_at=dt())
        with self.assertRaises(ContractValidationError):
            User(user_id="user-1", owner_id="owner-1", created_at=datetime(2026, 9, 27))
        with self.assertRaises(ContractValidationError):
            Memory(
                memory_id="mem-1", owner_id="owner-1", source_id="source-1", memory_kind=MemoryKind.PERSONAL,
                created_at=dt(), observed_at=dt(), valid_from=dt(), valid_until=None,
                privacy_class=PrivacyClass.PERSONAL, retention_policy_id="retain", lifecycle_state=LifecycleState.CURRENT,
                provenance=prov("source-1", "owner-2"), content={}, confidence=1.0,
            )

    def test_temporal_validity_supports_current_and_historical_queries(self):
        extent = TemporalExtent(
            event_time=dt(2026, 9, 27),
            created_at=dt(2026, 9, 27),
            valid_from=dt(2027, 1, 1),
            valid_until=dt(2031, 1, 1),
            local_display_time="27-09-2026 00:00",
            timezone_name="Asia/Calcutta",
            utc_offset_minutes=330,
        )
        self.assertTrue(extent.includes(dt(2026, 9, 27)))
        self.assertTrue(extent.is_current_at(dt(2028, 1, 1)))
        self.assertFalse(extent.is_current_at(dt(2035, 7, 30)))
        with self.assertRaises(ContractValidationError):
            TemporalExtent(created_at=dt(), valid_from=dt(2031, 1, 1), valid_until=dt(2027, 1, 1))

    def test_source_and_derived_memory_are_distinct_and_linked(self):
        s = source()
        m = memory(source_id=s.source_id)
        self.assertEqual(m.source_id, s.source_id)
        self.assertEqual(m.provenance.source_id, s.source_id)
        self.assertNotEqual(m.memory_id, s.source_id)

    def test_historical_current_conflict_and_deletion_states_are_executable(self):
        current = memory("mem-current", state=LifecycleState.CURRENT)
        historical = memory("mem-historical", state=LifecycleState.HISTORICAL)
        conflicted = memory("mem-conflicted", state=LifecycleState.CONFLICTED)
        deleted = memory("mem-deleted", state=LifecycleState.DELETED)
        self.assertEqual(current.lifecycle_state, LifecycleState.CURRENT)
        self.assertEqual(historical.lifecycle_state, LifecycleState.HISTORICAL)
        self.assertEqual(conflicted.lifecycle_state, LifecycleState.CONFLICTED)
        self.assertEqual(deleted.lifecycle_state, LifecycleState.DELETED)

    def test_memory_capture_policy_supports_defaults_and_overrides(self):
        policy = MemoryCapturePolicy(
            policy_id="policy-1",
            owner_id="owner-1",
            created_at=dt(),
            default_conversation_retention=RetentionMode.DEFAULT_RETAIN,
            explicit_remember=RetentionOverride.RETAIN,
            explicit_do_not_remember=RetentionOverride.DO_NOT_RETAIN,
            temporary_conversation=RetentionOverride.TEMPORARY,
            sensitive_memory=RetentionOverride.DO_NOT_RETAIN,
            project_scoped_memory=RetentionOverride.RETAIN,
            deletion_behavior=DeletionBehavior.DELETE_ALL_REPRESENTATIONS,
            retention_period_days=3650,
            per_conversation_overrides={"conv-1": RetentionOverride.RETAIN},
            per_message_overrides={"msg-1": RetentionOverride.DO_NOT_RETAIN},
        )
        self.assertEqual(policy.per_conversation_overrides["conv-1"], RetentionOverride.RETAIN)
        with self.assertRaises(ContractValidationError):
            MemoryCapturePolicy(
                policy_id="policy-2", owner_id="owner-1", created_at=dt(),
                default_conversation_retention=RetentionMode.EXPLICIT_REMEMBER,
                explicit_remember=RetentionOverride.RETAIN,
                explicit_do_not_remember=RetentionOverride.DO_NOT_RETAIN,
                temporary_conversation=RetentionOverride.TEMPORARY,
                sensitive_memory=RetentionOverride.DO_NOT_RETAIN,
                project_scoped_memory=RetentionOverride.RETAIN,
                deletion_behavior=DeletionBehavior.DELETE_ALL_REPRESENTATIONS,
            )

    def test_conversation_checkpoint_cannot_restore_security_authority(self):
        cursor = ResponseCursor(
            cursor_id="cursor-1", response_id="resp-1", conversation_id="conv-1", branch_id="branch-1", owner_id="owner-1",
            created_at=dt(), updated_at=dt(), generation_state=GenerationState.INTERRUPTED, speech_state=SpeechState.INTERRUPTED,
            text_position=10, semantic_position="reason", last_spoken_boundary="The main reason is",
        )
        cp = ConversationCheckpoint(
            checkpoint_id="checkpoint-1", conversation_id="conv-1", owner_id="owner-1", created_at=dt(), branch_id="branch-1",
            topic="architecture", response_cursor=cursor, referenced_session_ids=("session-1",), referenced_lease_ids=("lease-1",),
            historical_action_state={"last_action_id": "action-1"},
        )
        self.assertEqual(cp.referenced_lease_ids, ("lease-1",))
        with self.assertRaises(ContractValidationError):
            ConversationCheckpoint(
                checkpoint_id="checkpoint-2", conversation_id="conv-1", owner_id="owner-1", created_at=dt(),
                active_session_id="session-1",
            )

    def test_response_cursor_state_transitions(self):
        cursor = ResponseCursor(
            cursor_id="cursor-1", response_id="resp-1", conversation_id="conv-1", branch_id="branch-1", owner_id="owner-1",
            created_at=dt(), updated_at=dt(), generation_state=GenerationState.GENERATING, speech_state=SpeechState.SPEAKING,
            text_position=23, semantic_position="paragraph-1", last_spoken_boundary="that",
            referenced_evidence_ids=("evidence-1",), referenced_memory_ids=("mem-1",), resume_policy=ResumePolicy.RESUME_EXACT,
        )
        interrupted = cursor.interrupt("user_stop", dt(2026, 9, 27, 0, 1))
        self.assertEqual(interrupted.speech_state, SpeechState.INTERRUPTED)
        self.assertEqual(interrupted.text_position, 23)
        resumed = interrupted.resume(dt(2026, 9, 27, 0, 2))
        self.assertEqual(resumed.speech_state, SpeechState.RESUMING)
        canceled = resumed.cancel("user_cancel", dt(2026, 9, 27, 0, 3))
        self.assertEqual(canceled.resume_policy, ResumePolicy.DO_NOT_RESUME)
        completed = cursor.complete(dt(2026, 9, 27, 0, 4), text_position=100)
        self.assertEqual(completed.generation_state, GenerationState.COMPLETED)

    def test_stop_semantics_distinguish_speech_from_action(self):
        speech_stop = InteractionControlCommand(
            command_id="cmd-1", owner_id="owner-1", conversation_id="conv-1", created_at=dt(),
            command_type=InteractionCommandType.STOP, target=InteractionTarget.SPEECH, response_id="resp-1",
        )
        self.assertTrue(speech_stop.is_speech_stop)
        self.assertFalse(speech_stop.requires_action_plane_cancellation)
        action_stop = InteractionControlCommand(
            command_id="cmd-2", owner_id="owner-1", conversation_id="conv-1", created_at=dt(),
            command_type=InteractionCommandType.STOP, target=InteractionTarget.ACTION, action_id="action-1",
        )
        self.assertTrue(action_stop.requires_action_plane_cancellation)
        with self.assertRaises(ContractValidationError):
            InteractionControlCommand(
                command_id="cmd-3", owner_id="owner-1", conversation_id="conv-1", created_at=dt(),
                command_type=InteractionCommandType.STOP, target=InteractionTarget.UNKNOWN,
            )

    def test_evidence_contract_serializes_without_web_research_runtime(self):
        ev = Evidence(
            evidence_id="evidence-1", owner_id="owner-1", source_identifier="source-1", source_type=SourceType.EXTERNAL_EVIDENCE,
            accessed_at=dt(), provenance=prov(), publisher="Example", author="Analyst", uri="https://example.invalid/report",
            published_at=dt(2026, 9, 1), updated_source_at=dt(2026, 9, 2), jurisdiction_context="IN",
            source_classification=SourceClassification.SECONDARY, evidence_strength=EvidenceStrength.MODERATE,
            limitations=("example source only",), claim_refs=("claim-1",), verification_state=VerificationState.UNVERIFIED,
        )
        self.assertEqual(Evidence.from_dict(ev.to_dict()), ev)

    def test_retrieval_contract_supports_exact_semantic_temporal_relational_causal(self):
        req = MemoryRetrievalRequest(
            retrieval_id="retrieval-1", owner_id="owner-1", principal_id="principal-1", created_at=dt(),
            modes=(RetrievalMode.EXACT, RetrievalMode.SEMANTIC, RetrievalMode.TEMPORAL, RetrievalMode.RELATIONAL, RetrievalMode.CAUSAL_HISTORICAL),
            query_text="What did we discuss on 27 September 2026?", temporal_start=dt(2026, 9, 27), temporal_end=dt(2026, 9, 28),
            entity_ids=("entity-1",), project_id="project-1", purpose="historical_recall",
        )
        hit = MemoryRetrievalHit(
            hit_id="hit-1", owner_id="owner-1", record_id="mem-1", source_id="source-1", created_at=dt(), relevance=0.95,
            temporal_metadata=TemporalExtent(event_time=dt(2026, 9, 27), created_at=dt()), governance_status=GovernanceStatus.ALLOW,
            provenance=prov(), conflict_state=LifecycleState.HISTORICAL,
        )
        result = MemoryRetrievalResult(
            result_id="result-1", retrieval_id=req.retrieval_id, owner_id="owner-1", created_at=dt(),
            governance_status=GovernanceStatus.ALLOW, hits=(hit,), message="exact historical source available",
        )
        self.assertEqual(result.hits[0].conflict_state, LifecycleState.HISTORICAL)
        with self.assertRaises(ContractValidationError):
            MemoryRetrievalRequest(
                retrieval_id="retrieval-2", owner_id="owner-1", principal_id="principal-1", created_at=dt(), modes=(), query_text="x"
            )

    def test_delete_contract_does_not_falsely_report_complete_deletion(self):
        req = DeletionRequest(
            deletion_id="delete-1", owner_id="owner-1", principal_id="principal-1", created_at=dt(),
            scope_type="conversation", authentication_context="session-1+a2", conversation_id="conv-1",
            requested_propagation=(DeletionPropagationTarget.SOURCE, DeletionPropagationTarget.DERIVED_MEMORY, DeletionPropagationTarget.BACKUPS),
        )
        partial = DeletionResult(
            result_id="delete-result-1", deletion_id=req.deletion_id, owner_id="owner-1", created_at=dt(), status=DeletionStatus.PARTIAL,
            propagated={"SOURCE": DeletionStatus.COMPLETED, "BACKUPS": DeletionStatus.UNKNOWN}, unknown_targets=("BACKUPS",),
        )
        self.assertEqual(partial.status, DeletionStatus.PARTIAL)
        with self.assertRaises(ContractValidationError):
            DeletionResult(
                result_id="delete-result-2", deletion_id=req.deletion_id, owner_id="owner-1", created_at=dt(), status=DeletionStatus.COMPLETED,
                propagated={"SOURCE": DeletionStatus.COMPLETED, "BACKUPS": DeletionStatus.UNKNOWN}, unknown_targets=("BACKUPS",),
            )

    def test_memoryos_boundary_distinguishes_governance_storage_views_retrieval(self):
        gov_req = MemoryGovernanceRequest(
            request_id="memgov-1", owner_id="owner-1", principal_id="principal-1", created_at=dt(),
            operation="store", purpose="remember_this", privacy_class=PrivacyClass.PERSONAL, retention_policy_id="retain-default", source_ids=("source-1",),
        )
        decision = MemoryGovernanceDecision(
            decision_id="memgov-decision-1", request_id=gov_req.request_id, owner_id="owner-1", created_at=dt(),
            status=GovernanceStatus.ALLOW, reason="owner requested remember", policy_version="MEMORYOS-adapter-v1",
        )
        storage = __import__("zroq.domain_contracts", fromlist=["MemoryStorageRequest"]).MemoryStorageRequest(
            storage_request_id="storage-1", owner_id="owner-1", created_at=dt(), source=source(), derived_memory=memory(), governance_decision_id=decision.decision_id,
        )
        view = DerivedViewReference(view_id="view-1", owner_id="owner-1", created_at=dt(), source_ids=("source-1",), view_type="timeline")
        self.assertEqual(storage.source.source_id, "source-1")
        self.assertTrue(view.governance_inherited)
        with self.assertRaises(ContractValidationError):
            DerivedViewReference(view_id="view-2", owner_id="owner-1", created_at=dt(), source_ids=("source-1",), view_type="index", governance_inherited=False)

    def test_memory_firewall_contract_minimizes_and_denies_provider_context_when_not_allowed(self):
        req = MemoryFirewallRequest(
            firewall_request_id="fw-1", owner_id="owner-1", principal_id="principal-1", created_at=dt(),
            task_purpose="answer scoped project question", requested_memory_scope={"project_id": "project-1"},
            provider_trust_class=ProviderTrustClass.EXTERNAL_CONTRACTED, privacy_class=PrivacyClass.SENSITIVE,
            egress_policy=EgressPolicy.REDACTED, requested_memory_ids=("mem-1",),
        )
        allowed = MemoryFirewallResult(
            firewall_result_id="fw-result-1", firewall_request_id=req.firewall_request_id, owner_id="owner-1", created_at=dt(),
            allowed_memory_ids=("mem-1",), denied_fields=("secret",), redacted_fields=("phone",), minimization_summary="one redacted memory",
            provider_context={"summary": "redacted"}, provenance=prov(), governance_status=GovernanceStatus.ALLOW,
        )
        self.assertEqual(allowed.redacted_fields, ("phone",))
        with self.assertRaises(ContractValidationError):
            MemoryFirewallResult(
                firewall_result_id="fw-result-2", firewall_request_id=req.firewall_request_id, owner_id="owner-1", created_at=dt(),
                allowed_memory_ids=(), denied_fields=("all",), redacted_fields=(), minimization_summary="denied",
                provider_context={"leak": "not allowed"}, provenance=prov(), governance_status=GovernanceStatus.DENY,
            )

    def test_world_model_relationships_and_outcome_references_are_consistent(self):
        p = prov()
        goal = Goal(goal_id="goal-1", owner_id="owner-1", created_at=dt(), title="Continuity", description="historical recall", provenance=p)
        project = Project(project_id="project-1", owner_id="owner-1", created_at=dt(), title="ZORQ", provenance=p, goal_ids=(goal.goal_id,))
        decision = __import__("zroq.domain_contracts", fromlist=["Decision"]).Decision(
            decision_id="decision-1", owner_id="owner-1", project_id=project.project_id, created_at=dt(), decided_at=dt(),
            title="Use source archive", rationale="summaries cannot replace source", provenance=p,
        )
        entity_a = Entity(entity_id="entity-1", owner_id="owner-1", entity_type=EntityType.PROJECT, name="ZORQ", created_at=dt(), provenance=p)
        entity_b = Entity(entity_id="entity-2", owner_id="owner-1", entity_type=EntityType.GOAL, name="Recall", created_at=dt(), provenance=p)
        rel = Relationship(
            relationship_id="rel-1", owner_id="owner-1", subject_entity_id=entity_a.entity_id, object_entity_id=entity_b.entity_id,
            relationship_type=RelationshipType.RELATES_TO, created_at=dt(), provenance=p,
        )
        action = Action(action_id="action-1", owner_id="owner-1", principal_id="principal-1", created_at=dt(), capability_id="cap-1", operation="op", parameters_ref="params://1", proposed_by="planner")
        snap = ActionSnapshotRef(action_snapshot_ref_id="snap-1", owner_id="owner-1", action_id=action.action_id, action_digest=HEX, created_at=dt())
        outcome = Outcome(outcome_id="outcome-1", owner_id="owner-1", action_id=action.action_id, created_at=dt(), completed_at=dt(), verified_at=None, outcome_state=OutcomeState.UNKNOWN, verification_state=VerificationState.UNKNOWN, action_snapshot_ref=snap)
        index = DomainIndex(
            index_id="index-1", owner_id="owner-1", created_at=dt(), memory_sources={"source-1": source()}, memories={"mem-1": memory()},
            entities={entity_a.entity_id: entity_a, entity_b.entity_id: entity_b}, relationships={rel.relationship_id: rel},
            projects={project.project_id: project}, decisions={decision.decision_id: decision}, actions={action.action_id: action}, outcomes={outcome.outcome_id: outcome},
        )
        self.assertEqual(index.relationships["rel-1"].object_entity_id, "entity-2")

    def test_cross_entity_consistency_rejects_missing_references(self):
        with self.assertRaises(ContractValidationError):
            DomainIndex(index_id="index-1", owner_id="owner-1", created_at=dt(), memories={"mem-1": memory()})
        p = prov()
        entity_a = Entity(entity_id="entity-1", owner_id="owner-1", entity_type=EntityType.PROJECT, name="ZORQ", created_at=dt(), provenance=p)
        rel = Relationship(
            relationship_id="rel-1", owner_id="owner-1", subject_entity_id=entity_a.entity_id, object_entity_id="missing-entity",
            relationship_type=RelationshipType.RELATES_TO, created_at=dt(), provenance=p,
        )
        with self.assertRaises(ContractValidationError):
            DomainIndex(index_id="index-2", owner_id="owner-1", created_at=dt(), entities={entity_a.entity_id: entity_a}, relationships={rel.relationship_id: rel})

    def test_ownership_isolation_rejects_cross_owner_domain_index(self):
        with self.assertRaises(ContractValidationError):
            DomainIndex(index_id="index-1", owner_id="owner-1", created_at=dt(), memory_sources={"source-2": source("source-2", owner_id="owner-2")})

    def test_action_governance_contracts_validate_digest_and_time(self):
        cap = Capability(capability_id="cap-1", owner_id="owner-1", created_at=dt(), version="1.0", operations=("op",), risk_class="R1")
        grant = Grant(grant_id="grant-1", owner_id="owner-1", principal_id="principal-1", capability_id=cap.capability_id, created_at=dt(), valid_from=dt(), valid_until=None, permission_scope={"scope": "bounded"}, risk_ceiling="R1")
        confirmation = Confirmation(confirmation_id="confirm-1", owner_id="owner-1", principal_id="principal-1", session_id="session-1", action_id="action-1", action_digest=HEX, issued_at=dt(), expires_at=dt() + timedelta(minutes=5), assurance_level="A2")
        lease = Lease(lease_id="lease-1", owner_id="owner-1", principal_id="principal-1", device_id="device-1", action_id="action-1", action_digest=HEX, issued_at=dt(), expires_at=dt() + timedelta(minutes=5), security_epoch=0)
        self.assertEqual(grant.capability_id, cap.capability_id)
        self.assertEqual(confirmation.action_digest, lease.action_digest)
        with self.assertRaises(ContractValidationError):
            ActionSnapshotRef(action_snapshot_ref_id="snap-2", owner_id="owner-1", action_id="action-1", action_digest="not-hex", created_at=dt())

    def test_identity_session_device_temporal_contracts(self):
        user = User(user_id="user-1", owner_id="owner-1", created_at=dt())
        owner = OwnerIdentity(owner_id="owner-1", user_id=user.user_id, created_at=dt(), valid_from=dt())
        session = Session(session_id="session-1", owner_id=owner.owner_id, principal_id="principal-1", device_id="device-1", issued_at=dt(), expires_at=dt() + timedelta(hours=1), security_epoch=0, created_at=dt())
        device = Device(device_id="device-1", owner_id=owner.owner_id, created_at=dt(), enrolled_at=dt())
        self.assertEqual(session.device_id, device.device_id)
        with self.assertRaises(ContractValidationError):
            Session(session_id="session-2", owner_id="owner-1", principal_id="principal-1", device_id="device-1", issued_at=dt(), expires_at=dt() - timedelta(seconds=1), security_epoch=0, created_at=dt())

    def test_conversation_message_ordering_does_not_depend_only_on_wall_clock(self):
        conv = Conversation(conversation_id="conv-1", owner_id="owner-1", created_at=dt(), started_at=dt(), title="ZORQ")
        branch = ConversationBranch(branch_id="branch-1", conversation_id=conv.conversation_id, owner_id="owner-1", created_at=dt(), topic="architecture")
        m1 = Message(message_id="msg-1", conversation_id=conv.conversation_id, branch_id=branch.branch_id, owner_id="owner-1", principal_id="principal-1", sequence=1, role="user", content="first", created_at=dt(), observed_at=dt())
        m2 = Message(message_id="msg-2", conversation_id=conv.conversation_id, branch_id=branch.branch_id, owner_id="owner-1", principal_id="principal-1", sequence=2, role="assistant", content="second", created_at=dt(), observed_at=dt())
        self.assertLess(m1.sequence, m2.sequence)
        with self.assertRaises(ContractValidationError):
            Message(message_id="msg-3", conversation_id=conv.conversation_id, branch_id=branch.branch_id, owner_id="owner-1", principal_id="principal-1", sequence=-1, role="user", content="bad", created_at=dt(), observed_at=dt())

    def test_mutable_alias_prevention_for_memory_response_cursor_and_firewall(self):
        content = {"items": ["safe"], "nested": {"value": "safe"}}
        m = Memory(
            memory_id="mem-alias", owner_id="owner-1", source_id="source-1", memory_kind=MemoryKind.PERSONAL,
            created_at=dt(), observed_at=dt(), valid_from=dt(), valid_until=None,
            privacy_class=PrivacyClass.PERSONAL, retention_policy_id="retain", lifecycle_state=LifecycleState.CURRENT,
            provenance=prov(), content=content, confidence=1.0,
        )
        content["items"].append("mutated")
        content["nested"]["value"] = "mutated"
        self.assertEqual(m.content["items"], ("safe",))
        self.assertEqual(m.content["nested"]["value"], "safe")
        with self.assertRaises(TypeError):
            m.content["new"] = "blocked"
        cursor_memory_ids = ["mem-1"]
        cursor = ResponseCursor(cursor_id="cursor-1", response_id="resp-1", conversation_id="conv-1", branch_id="branch-1", owner_id="owner-1", created_at=dt(), updated_at=dt(), generation_state=GenerationState.GENERATED, speech_state=SpeechState.IDLE, text_position=0, semantic_position="start", last_spoken_boundary="", referenced_memory_ids=cursor_memory_ids)
        cursor_memory_ids.append("mem-2")
        self.assertEqual(cursor.referenced_memory_ids, ("mem-1",))
        provider_context = {"allowed": ["one"]}
        fw = MemoryFirewallResult(firewall_result_id="fw-1", firewall_request_id="fwreq-1", owner_id="owner-1", created_at=dt(), allowed_memory_ids=("mem-1",), denied_fields=(), redacted_fields=(), minimization_summary="min", provider_context=provider_context, provenance=prov(), governance_status=GovernanceStatus.ALLOW)
        provider_context["allowed"].append("two")
        self.assertEqual(fw.provider_context["allowed"], ("one",))

    def test_evolution_contract_forbids_silent_security_mutation(self):
        ok = EvolutionProposal(evolution_proposal_id="evo-1", owner_id="owner-1", created_at=dt(), target_component="routing", rationale="improve quality", status=EvolutionProposalStatus.PROPOSED, test_plan="canary", rollback_plan="rollback", approval_required=True)
        self.assertEqual(ok.status, EvolutionProposalStatus.PROPOSED)
        with self.assertRaises(ContractValidationError):
            EvolutionProposal(evolution_proposal_id="evo-2", owner_id="owner-1", created_at=dt(), target_component="ActionKernel", rationale="unsafe", status=EvolutionProposalStatus.PROPOSED, approval_required=False)
        with self.assertRaises(ContractValidationError):
            EvolutionProposal(evolution_proposal_id="evo-3", owner_id="owner-1", created_at=dt(), target_component="security_policy", rationale="unsafe", status=EvolutionProposalStatus.PROPOSED, forbidden_security_mutation=True)

    def test_timeline_event_references_valid_source_and_memory(self):
        s = source()
        m = memory(source_id=s.source_id)
        event = TimelineEvent(event_id="event-1", owner_id="owner-1", event_time=dt(), created_at=dt(), title="Architecture decision", provenance=prov(), source_ids=(s.source_id,), memory_ids=(m.memory_id,), conversation_ids=("conv-1",))
        DomainIndex(index_id="index-1", owner_id="owner-1", created_at=dt(), memory_sources={s.source_id: s}, memories={m.memory_id: m}, timeline_events={event.event_id: event})


if __name__ == "__main__":
    unittest.main()
