from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone
import unittest

from zroq.domain_contracts import (
    ContractValidationError,
    CurrentContextFrame,
    EgressPolicy,
    GovernanceStatus,
    LifecycleState,
    MemoryActivationCandidate,
    MemoryActivationDecision,
    MemoryActivationDecisionState,
    MemoryActivationPolicyDecision,
    MemoryActivationRequest,
    MemoryActivationThresholdPolicy,
    MemoryActivationTrigger,
    MemoryContextSelection,
    MemoryFirewallRequest,
    MemoryRelevanceLevel,
    MemoryRelevanceSignal,
    MemoryRelevanceSignalType,
    MemoryValidity,
    PrivacyClass,
    Provenance,
    ProviderTrustClass,
    SourceType,
    TemporalExtent,
    VerificationState,
)


def dt(year=2026, month=9, day=27, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def context(owner_id="owner-1", conversation_id="conv-2035"):
    return CurrentContextFrame(
        context_id="ctx-2035",
        owner_id=owner_id,
        conversation_id=conversation_id,
        branch_id="branch-architecture",
        created_at=dt(2035, 7, 30),
        current_topic="ZORQ architecture revision",
        active_question="How should we redesign continuity authority?",
        entity_ids=("entity-zorq",),
        project_ids=("project-zorq",),
        goal_ids=("goal-continuity",),
        decision_ids=("decision-action-authority",),
        current_assumptions=("Phase 2.6 remains action authority",),
        referenced_document_ids=("doc-master-architecture",),
        active_conversation_branch="architecture",
        temporal_context=TemporalExtent(event_time=dt(2035, 7, 30), created_at=dt(2035, 7, 30)),
    )


def provenance(source_id="source-2026", owner_id="owner-1"):
    return Provenance(
        provenance_id="prov-" + source_id,
        owner_id=owner_id,
        source_type=SourceType.CONVERSATION_MESSAGE,
        source_id=source_id,
        created_at=dt(2026, 9, 27),
        observed_at=dt(2026, 9, 27),
        conversation_id="conv-2026",
        message_id="msg-vision-1",
        confidence=0.95,
        verification_state=VerificationState.UNVERIFIED,
    )


def request(trigger=MemoryActivationTrigger.SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION, owner_id="owner-1"):
    return MemoryActivationRequest(
        activation_id="activation-1",
        owner_id=owner_id,
        principal_id="principal-1",
        created_at=dt(2035, 7, 30),
        conversation_id="conv-2035",
        current_context=context(owner_id=owner_id),
        purpose="contextual architecture reasoning",
        task_scope={"project_id": "project-zorq", "mode": "advisory"},
        requested_memory_scope={"project_id": "project-zorq", "modes": ["semantic", "temporal", "relational"]},
        privacy_class=PrivacyClass.PERSONAL,
        provider_trust_class=ProviderTrustClass.LOCAL_ONLY,
        trigger=trigger,
        temporal_context=TemporalExtent(event_time=dt(2035, 7, 30), created_at=dt(2035, 7, 30)),
    )


def signal(signal_id="signal-1", signal_type=MemoryRelevanceSignalType.SEMANTIC_SIMILARITY, relevance=MemoryRelevanceLevel.HIGH):
    return MemoryRelevanceSignal(
        signal_id=signal_id,
        owner_id="owner-1",
        created_at=dt(2035, 7, 30),
        signal_type=signal_type,
        relevance=relevance,
        explanation="Current architecture decision overlaps with the 2026 ZORQ vision discussion.",
        evidence_refs=("source-2026",),
        confidence=0.8,
    )


def candidate(candidate_id="candidate-1", relevance=MemoryRelevanceLevel.HIGH, sensitivity=PrivacyClass.PERSONAL, validity=MemoryValidity.HISTORICALLY_VALID, state=LifecycleState.HISTORICAL, conflict=LifecycleState.HISTORICAL):
    return MemoryActivationCandidate(
        candidate_id=candidate_id,
        activation_id="activation-1",
        owner_id="owner-1",
        source_id="source-2026",
        memory_id="mem-2026-vision",
        created_at=dt(2035, 7, 30),
        relevance=relevance,
        relevance_reasons=("semantic architecture overlap", "same ZORQ project", "historical decision context"),
        semantic_relation="same long-term ZORQ architecture theme",
        temporal_relation="historical source from 2026 relevant to 2035 decision",
        entity_relation_ids=("entity-zorq",),
        project_relation_ids=("project-zorq",),
        goal_relation_ids=("goal-continuity",),
        decision_relation_ids=("decision-action-authority",),
        historical_current_validity=validity,
        historical_current_state=state,
        conflict_state=conflict,
        sensitivity=sensitivity,
        provenance=provenance(),
        relevance_signals=(
            signal(),
            signal("signal-2", MemoryRelevanceSignalType.TEMPORAL_RELEVANCE, MemoryRelevanceLevel.HIGH),
            signal("signal-3", MemoryRelevanceSignalType.PROJECT_OVERLAP, MemoryRelevanceLevel.HIGH),
        ),
        temporal_extent=TemporalExtent(event_time=dt(2026, 9, 27), created_at=dt(2035, 7, 30), valid_from=dt(2026, 9, 27), valid_until=None),
        confidence=0.86,
        governance_status=GovernanceStatus.ALLOW,
    )


class Phase3A1ContextualMemoryActivationTests(unittest.TestCase):
    def test_contract_inventory_contains_activation_contracts(self):
        from zroq.domain_contracts import CONTRACT_TYPES
        names = {cls.__name__ for cls in CONTRACT_TYPES}
        self.assertTrue({
            "CurrentContextFrame",
            "MemoryRelevanceSignal",
            "MemoryActivationThresholdPolicy",
            "MemoryActivationRequest",
            "MemoryActivationCandidate",
            "MemoryActivationDecision",
            "MemoryContextSelection",
        }.issubset(names))

    def test_explicit_retrieval_vs_automatic_activation_are_distinct_triggers(self):
        explicit = request(MemoryActivationTrigger.USER_REQUESTED_SEARCH)
        automatic = request(MemoryActivationTrigger.SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION)
        self.assertNotEqual(explicit.trigger, automatic.trigger)
        self.assertEqual(explicit.current_context.current_topic, automatic.current_context.current_topic)

    def test_relevant_2026_memory_candidate_preserves_source_provenance(self):
        c = candidate()
        self.assertEqual(c.source_id, "source-2026")
        self.assertEqual(c.provenance.source_id, "source-2026")
        self.assertEqual(c.provenance.conversation_id, "conv-2026")
        self.assertEqual(c.temporal_extent.event_time, dt(2026, 9, 27))
        self.assertEqual(c.relevance, MemoryRelevanceLevel.HIGH)

    def test_activation_decision_allows_internal_context_without_user_visible_mention(self):
        c = candidate()
        d = MemoryActivationDecision(
            decision_id="decision-activation-1",
            activation_id="activation-1",
            owner_id="owner-1",
            candidate=c,
            created_at=dt(2035, 7, 30),
            decision_state=MemoryActivationDecisionState.ACTIVATED,
            policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
            governance_status=GovernanceStatus.ALLOW,
            reason="high relevance but no need to surface details",
            activated_for_internal_reasoning=True,
            user_visible_mention_allowed=False,
            explanation="Related to September 2026 architecture discussion.",
        )
        selection = MemoryContextSelection(
            selection_id="selection-1",
            activation_id="activation-1",
            owner_id="owner-1",
            created_at=dt(2035, 7, 30),
            decisions=(d,),
            selected_candidate_ids=(c.candidate_id,),
            internal_context_candidate_ids=(c.candidate_id,),
            reasoning_context={"summary": "Use as historical context only."},
            provenance=provenance(),
        )
        self.assertEqual(selection.user_visible_candidate_ids, ())
        self.assertEqual(selection.internal_context_candidate_ids, ("candidate-1",))

    def test_user_visible_mention_requires_show_policy_and_source_evidence(self):
        c = candidate()
        with self.assertRaises(ContractValidationError):
            MemoryActivationDecision(
                decision_id="decision-activation-2",
                activation_id="activation-1",
                owner_id="owner-1",
                candidate=c,
                created_at=dt(2035, 7, 30),
                decision_state=MemoryActivationDecisionState.ACTIVATED,
                policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
                governance_status=GovernanceStatus.ALLOW,
                reason="trying to mention without SHOW policy",
                activated_for_internal_reasoning=True,
                user_visible_mention_allowed=True,
            )
        shown = MemoryActivationDecision(
            decision_id="decision-activation-3",
            activation_id="activation-1",
            owner_id="owner-1",
            candidate=c,
            created_at=dt(2035, 7, 30),
            decision_state=MemoryActivationDecisionState.ACTIVATED,
            policy_decision=MemoryActivationPolicyDecision.SHOW,
            governance_status=GovernanceStatus.ALLOW,
            reason="source-backed visible contextual reference is permitted",
            activated_for_internal_reasoning=True,
            user_visible_mention_allowed=True,
        )
        self.assertTrue(shown.user_visible_mention_allowed)

    def test_semantically_similar_sensitive_memory_can_be_privacy_blocked(self):
        sensitive = candidate(candidate_id="candidate-sensitive", sensitivity=PrivacyClass.SECRET)
        blocked = MemoryActivationDecision(
            decision_id="decision-blocked-privacy",
            activation_id="activation-1",
            owner_id="owner-1",
            candidate=sensitive,
            created_at=dt(2035, 7, 30),
            decision_state=MemoryActivationDecisionState.BLOCKED_BY_PRIVACY,
            policy_decision=MemoryActivationPolicyDecision.BLOCK,
            governance_status=GovernanceStatus.ALLOW,
            reason="secret personal memory is semantically related but not safe to activate",
        )
        selection = MemoryContextSelection(
            selection_id="selection-blocked",
            activation_id="activation-1",
            owner_id="owner-1",
            created_at=dt(2035, 7, 30),
            decisions=(blocked,),
            blocked_candidate_ids=(sensitive.candidate_id,),
            reasoning_context={},
        )
        self.assertEqual(selection.selected_candidate_ids, ())
        self.assertEqual(blocked.decision_state, MemoryActivationDecisionState.BLOCKED_BY_PRIVACY)

    def test_governance_blocked_memory_is_not_activated(self):
        c = candidate(candidate_id="candidate-policy")
        d = MemoryActivationDecision(
            decision_id="decision-blocked-policy",
            activation_id="activation-1",
            owner_id="owner-1",
            candidate=c,
            created_at=dt(2035, 7, 30),
            decision_state=MemoryActivationDecisionState.BLOCKED_BY_POLICY,
            policy_decision=MemoryActivationPolicyDecision.BLOCK,
            governance_status=GovernanceStatus.DENY,
            reason="MEMORY//OS governance denies activation",
        )
        self.assertFalse(d.activated_for_internal_reasoning)
        with self.assertRaises(ContractValidationError):
            MemoryActivationDecision(
                decision_id="bad-policy-activation",
                activation_id="activation-1",
                owner_id="owner-1",
                candidate=c,
                created_at=dt(2035, 7, 30),
                decision_state=MemoryActivationDecisionState.ACTIVATED,
                policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
                governance_status=GovernanceStatus.DENY,
                reason="must not activate denied memory",
                activated_for_internal_reasoning=True,
            )

    def test_historical_relevance_can_be_currently_invalid_or_superseded(self):
        old = candidate(
            candidate_id="candidate-old-pref",
            validity=MemoryValidity.HISTORICALLY_RELEVANT_CURRENTLY_INVALID,
            state=LifecycleState.SUPERSEDED,
            conflict=LifecycleState.SUPERSEDED,
        )
        stale = MemoryActivationDecision(
            decision_id="decision-stale",
            activation_id="activation-1",
            owner_id="owner-1",
            candidate=old,
            created_at=dt(2035, 7, 30),
            decision_state=MemoryActivationDecisionState.STALE,
            policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
            governance_status=GovernanceStatus.ALLOW,
            reason="historically relevant but superseded by newer preference",
        )
        self.assertEqual(stale.candidate.historical_current_validity, MemoryValidity.HISTORICALLY_RELEVANT_CURRENTLY_INVALID)
        self.assertEqual(stale.candidate.historical_current_state, LifecycleState.SUPERSEDED)

    def test_multiple_conflicting_memories_are_exposed_as_conflicted_not_truth(self):
        c1 = candidate(candidate_id="candidate-conflict-1", validity=MemoryValidity.CONFLICTED, conflict=LifecycleState.CONFLICTED)
        c2 = candidate(candidate_id="candidate-conflict-2", validity=MemoryValidity.CONFLICTED, conflict=LifecycleState.CONFLICTED)
        d1 = MemoryActivationDecision(
            decision_id="decision-conflict-1", activation_id="activation-1", owner_id="owner-1", candidate=c1,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.CONFLICTED,
            policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY, governance_status=GovernanceStatus.ALLOW,
            reason="conflicts with another historical memory",
        )
        d2 = MemoryActivationDecision(
            decision_id="decision-conflict-2", activation_id="activation-1", owner_id="owner-1", candidate=c2,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.CONFLICTED,
            policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY, governance_status=GovernanceStatus.ALLOW,
            reason="conflicts with another historical memory",
        )
        selection = MemoryContextSelection(
            selection_id="selection-conflict", activation_id="activation-1", owner_id="owner-1", created_at=dt(2035, 7, 30),
            decisions=(d1, d2), selected_candidate_ids=(), reasoning_context={"state": "conflicted"}, explanation="conflicting memories require uncertainty",
        )
        self.assertEqual(selection.selected_candidate_ids, ())
        self.assertEqual(len(selection.decisions), 2)

    def test_no_supported_memory_path_continues_without_contextualization(self):
        empty = MemoryContextSelection(
            selection_id="selection-empty",
            activation_id="activation-1",
            owner_id="owner-1",
            created_at=dt(2035, 7, 30),
            decisions=(),
            selected_candidate_ids=(),
            reasoning_context={},
            explanation="No sufficiently supported historical memory exists.",
        )
        self.assertEqual(empty.decisions, ())
        self.assertEqual(empty.selected_candidate_ids, ())

    def test_irrelevant_memory_cannot_be_activated(self):
        c = candidate(candidate_id="candidate-low", relevance=MemoryRelevanceLevel.LOW)
        with self.assertRaises(ContractValidationError):
            MemoryActivationDecision(
                decision_id="decision-low", activation_id="activation-1", owner_id="owner-1", candidate=c,
                created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.ACTIVATED,
                policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY, governance_status=GovernanceStatus.ALLOW,
                reason="low relevance should not activate", activated_for_internal_reasoning=True,
            )
        not_relevant = MemoryActivationDecision(
            decision_id="decision-not-relevant", activation_id="activation-1", owner_id="owner-1", candidate=c,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.NOT_RELEVANT,
            policy_decision=MemoryActivationPolicyDecision.BLOCK, governance_status=GovernanceStatus.NOT_APPLICABLE,
            reason="low relevance; continue normally",
        )
        self.assertEqual(not_relevant.decision_state, MemoryActivationDecisionState.NOT_RELEVANT)

    def test_owner_isolation_is_enforced_for_requests_candidates_and_selection(self):
        with self.assertRaises(ContractValidationError):
            MemoryActivationRequest(
                activation_id="activation-cross-owner", owner_id="owner-1", principal_id="principal-1", created_at=dt(2035, 7, 30),
                conversation_id="conv-2035", current_context=context(owner_id="owner-2"), purpose="bad owner",
                task_scope={}, requested_memory_scope={}, privacy_class=PrivacyClass.PERSONAL,
                provider_trust_class=ProviderTrustClass.LOCAL_ONLY, trigger=MemoryActivationTrigger.SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION,
            )
        c = candidate()
        with self.assertRaises(ContractValidationError):
            MemoryActivationDecision(
                decision_id="decision-owner-bad", activation_id="activation-1", owner_id="owner-2", candidate=c,
                created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.RELEVANT,
                policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY, governance_status=GovernanceStatus.ALLOW,
                reason="owner mismatch",
            )

    def test_activation_requests_and_results_are_immutable_by_value(self):
        mutable_scope = {"projects": ["project-zorq"], "nested": {"value": "safe"}}
        req = MemoryActivationRequest(
            activation_id="activation-immutable", owner_id="owner-1", principal_id="principal-1", created_at=dt(2035, 7, 30),
            conversation_id="conv-2035", current_context=context(), purpose="immutability", task_scope=mutable_scope,
            requested_memory_scope={}, privacy_class=PrivacyClass.PERSONAL, provider_trust_class=ProviderTrustClass.LOCAL_ONLY,
            trigger=MemoryActivationTrigger.SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION,
        )
        mutable_scope["projects"].append("mutated")
        mutable_scope["nested"]["value"] = "mutated"
        self.assertEqual(req.task_scope["projects"], ("project-zorq",))
        self.assertEqual(req.task_scope["nested"]["value"], "safe")
        with self.assertRaises(TypeError):
            req.task_scope["new"] = "blocked"

    def test_serialization_roundtrip_preserves_activation_provenance(self):
        c = candidate()
        d = MemoryActivationDecision(
            decision_id="decision-roundtrip", activation_id="activation-1", owner_id="owner-1", candidate=c,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.ACTIVATED,
            policy_decision=MemoryActivationPolicyDecision.SHOW, governance_status=GovernanceStatus.ALLOW,
            reason="visible source-backed mention allowed", activated_for_internal_reasoning=True, user_visible_mention_allowed=True,
        )
        restored = MemoryActivationDecision.from_dict(d.to_dict())
        self.assertEqual(restored, d)
        self.assertEqual(restored.candidate.provenance.source_id, "source-2026")

    def test_activation_threshold_policy_is_configurable_without_scoring_engine(self):
        policy = MemoryActivationThresholdPolicy(
            activation_policy_id="activation-policy-1",
            owner_id="owner-1",
            created_at=dt(2035, 7, 30),
            high_relevance_policy=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
            medium_relevance_policy=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
            low_relevance_policy=MemoryActivationPolicyDecision.BLOCK,
            sensitive_requires_governance=True,
            user_visible_requires_show_policy=True,
        )
        self.assertEqual(policy.low_relevance_policy, MemoryActivationPolicyDecision.BLOCK)

    def test_memory_firewall_request_can_carry_activation_scope_without_retrieval_engine(self):
        fw = MemoryFirewallRequest(
            firewall_request_id="fw-activation-1", owner_id="owner-1", principal_id="principal-1", created_at=dt(2035, 7, 30),
            task_purpose="contextual memory activation for architecture reasoning",
            requested_memory_scope={"activation_id": "activation-1", "project_ids": ["project-zorq"]},
            provider_trust_class=ProviderTrustClass.LOCAL_ONLY,
            privacy_class=PrivacyClass.PERSONAL,
            egress_policy=EgressPolicy.NO_EGRESS,
            requested_memory_ids=("mem-2026-vision",),
        )
        self.assertEqual(fw.requested_memory_scope["activation_id"], "activation-1")
        self.assertEqual(fw.egress_policy, EgressPolicy.NO_EGRESS)


    def test_redacted_selection_requires_explicit_redact_policy(self):
        c = candidate(candidate_id="candidate-redact")
        not_redact = MemoryActivationDecision(
            decision_id="decision-not-redact", activation_id="activation-1", owner_id="owner-1", candidate=c,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.ACTIVATED,
            policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY, governance_status=GovernanceStatus.ALLOW,
            reason="internal use only is not redaction", activated_for_internal_reasoning=True,
        )
        with self.assertRaises(ContractValidationError):
            MemoryContextSelection(
                selection_id="selection-bad-redact", activation_id="activation-1", owner_id="owner-1", created_at=dt(2035, 7, 30),
                decisions=(not_redact,), selected_candidate_ids=(c.candidate_id,), redacted_candidate_ids=(c.candidate_id,),
                reasoning_context={"summary": "safe"},
            )
        redact = MemoryActivationDecision(
            decision_id="decision-redact", activation_id="activation-1", owner_id="owner-1", candidate=c,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.ACTIVATED,
            policy_decision=MemoryActivationPolicyDecision.REDACT, governance_status=GovernanceStatus.ALLOW,
            reason="redacted use permitted", activated_for_internal_reasoning=True,
        )
        selection = MemoryContextSelection(
            selection_id="selection-good-redact", activation_id="activation-1", owner_id="owner-1", created_at=dt(2035, 7, 30),
            decisions=(redact,), selected_candidate_ids=(c.candidate_id,), redacted_candidate_ids=(c.candidate_id,),
            reasoning_context={"redacted_summary": "A governed historical memory is relevant."},
        )
        self.assertEqual(selection.redacted_candidate_ids, ("candidate-redact",))

    def test_blocked_candidate_cannot_be_silently_treated_as_redacted_context(self):
        c = candidate(candidate_id="candidate-blocked-redact")
        blocked = MemoryActivationDecision(
            decision_id="decision-blocked-redact", activation_id="activation-1", owner_id="owner-1", candidate=c,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.BLOCKED_BY_PRIVACY,
            policy_decision=MemoryActivationPolicyDecision.BLOCK, governance_status=GovernanceStatus.ALLOW,
            reason="blocked by privacy",
        )
        with self.assertRaises(ContractValidationError):
            MemoryContextSelection(
                selection_id="selection-blocked-as-redacted", activation_id="activation-1", owner_id="owner-1", created_at=dt(2035, 7, 30),
                decisions=(blocked,), redacted_candidate_ids=(c.candidate_id,), blocked_candidate_ids=(c.candidate_id,),
                reasoning_context={"redacted_summary": "safe"},
            )

    def test_blocked_memory_markers_cannot_leak_through_reasoning_context(self):
        c = candidate(candidate_id="candidate-private-leak")
        blocked = MemoryActivationDecision(
            decision_id="decision-private-leak", activation_id="activation-1", owner_id="owner-1", candidate=c,
            created_at=dt(2035, 7, 30), decision_state=MemoryActivationDecisionState.BLOCKED_BY_PRIVACY,
            policy_decision=MemoryActivationPolicyDecision.BLOCK, governance_status=GovernanceStatus.ALLOW,
            reason="blocked by privacy",
        )
        with self.assertRaises(ContractValidationError):
            MemoryContextSelection(
                selection_id="selection-leaks-blocked", activation_id="activation-1", owner_id="owner-1", created_at=dt(2035, 7, 30),
                decisions=(blocked,), blocked_candidate_ids=(c.candidate_id,),
                reasoning_context={"debug": "do not expose source-2026"},
            )



if __name__ == "__main__":
    unittest.main()
