from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone, timedelta
import tempfile
import unittest

from zroq.domain_contracts import (
    EgressPolicy,
    LifecycleState,
    MemoryActivationDecisionState,
    MemoryActivationTrigger,
    MemoryActivationPolicyDecision,
    PrivacyClass,
    ProviderTrustClass,
)
from zroq.personal_continuity import (
    AdapterOutcome,
    ContinuityQuery,
    DeletionRuntimeStatus,
    DerivedMemoryKind,
    DocumentedMemoryOSAdapter,
    LocalConceptEmbeddingProvider,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityEngine,
    PersonalContinuityStore,
    RetrievalModeName,
)


def dt(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


class Phase3BPersonalContinuityTests(unittest.TestCase):
    def make_engine(self, db_path, owner="owner-1", *, available=True):
        store = PersonalContinuityStore(db_path)
        adapter = DocumentedMemoryOSAdapter(
            store,
            MemoryCapturePolicyEngine.default_retain(owner),
            memoryos_available=available,
            real_memoryos_verified=False,
        )
        return PersonalContinuityEngine(store, adapter), store, adapter

    def ctx(self, owner="owner-1", *, provider=ProviderTrustClass.LOCAL_ONLY, egress=EgressPolicy.NO_EGRESS):
        return MemoryAccessContext(
            owner_id=owner,
            principal_id=f"principal-{owner}",
            authenticated_owner_id=owner,
            purpose="phase3b personal continuity test",
            provider_trust_class=provider,
            egress_policy=egress,
        )

    def seed_2026_zorq(self, engine, context, conversation_id="conv-2026"):
        engine.start_conversation(
            context,
            conversation_id=conversation_id,
            started_at=dt(2026, 9, 27, 9),
            title="ZORQ long-term vision",
            topic="persistent ZORQ memory and architecture",
            project_id="project-zorq",
        )
        r1 = engine.record_message(
            context,
            conversation_id=conversation_id,
            role="user",
            content="ZORQ should have persistent personal memory so it can preserve long-term architecture continuity.",
            sequence=1,
            event_time=dt(2026, 9, 27, 9, 1),
            message_id="msg-2026-1",
            project_id="project-zorq",
            entity_ids=("entity-zorq",),
            goal_ids=("goal-continuity",),
            decision_ids=("decision-architecture",),
        )[1]
        r2 = engine.record_message(
            context,
            conversation_id=conversation_id,
            role="assistant",
            content="We discussed retaining source-backed conversations, provenance, and governed derived memory.",
            sequence=2,
            event_time=dt(2026, 9, 27, 9, 2),
            message_id="msg-2026-2",
            project_id="project-zorq",
            entity_ids=("entity-zorq",),
            goal_ids=("goal-continuity",),
            decision_ids=("decision-architecture",),
        )[1]
        engine.derive_from_conversation(context, conversation_id)
        return r1, r2

    def test_memoryos_search_result_is_environment_blocked_not_fabricated(self):
        # The real MEMORY//OS project is not present in this sandbox. Phase 3B
        # therefore uses the documented adapter harness and must not claim real verification.
        with tempfile.TemporaryDirectory() as td:
            engine, _store, adapter = self.make_engine(Path(td) / "memory.db")
            self.assertEqual(adapter.integration_status, "DOCUMENTED_CONTRACT_HARNESS_NOT_REAL_MEMORYOS")
            self.assertFalse(adapter.real_memoryos_verified)

    def test_persistent_source_survives_restart_and_exact_recall_uses_source(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            engine, _store, _adapter = self.make_engine(db)
            context = self.ctx()
            self.seed_2026_zorq(engine, context)

            # Simulated process restart: new store/adapter/engine over same SQLite file.
            engine2, _store2, _adapter2 = self.make_engine(db)
            answer = engine2.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            self.assertEqual(answer.status, AdapterOutcome.ALLOW)
            self.assertGreaterEqual(len(answer.exact_source_content), 2)
            self.assertIn("persistent personal memory", " ".join(r.content for r in answer.exact_source_content))
            self.assertIn("exact source messages", answer.inference)
            self.assertTrue(answer.evidence_source_ids)

    def test_duplicate_ingestion_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            engine, store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-dupe", started_at=dt(2026, 9, 27), project_id="project-zorq")
            first = engine.record_message(context, conversation_id="conv-dupe", role="user", content="Remember this once.", sequence=1, event_time=dt(2026, 9, 27, 1), message_id="same", project_id="project-zorq")
            second = engine.record_message(context, conversation_id="conv-dupe", role="user", content="Remember this once.", sequence=1, event_time=dt(2026, 9, 27, 1), message_id="same", project_id="project-zorq")
            self.assertTrue(first[2])
            self.assertFalse(second[2])
            self.assertEqual(store.table_counts("owner-1")["messages"], 1)

    def test_temporal_lexical_semantic_and_relational_retrieval_modes(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            self.seed_2026_zorq(engine, context)
            temporal = engine.retrieve(context, ContinuityQuery("during 2026", modes=(RetrievalModeName.TEMPORAL,), temporal_start=dt(2026, 1, 1), temporal_end=dt(2027, 1, 1), limit=10))
            self.assertGreaterEqual(len(temporal.source_records), 2)
            lexical = engine.retrieve(context, ContinuityQuery("source-backed conversations provenance", modes=(RetrievalModeName.LEXICAL,), limit=10))
            self.assertTrue(any("source-backed" in r.content for r in lexical.source_records))
            semantic = engine.retrieve(context, ContinuityQuery("keep ZORQ coherent as it grows", modes=(RetrievalModeName.SEMANTIC,), project_id="project-zorq", limit=10))
            self.assertIn("AVAILABLE:local-concept-vector-v1", semantic.mode_status[RetrievalModeName.SEMANTIC.value])
            self.assertTrue(any("long-term architecture continuity" in r.content for r in semantic.source_records))
            relational = engine.retrieve(context, ContinuityQuery("architecture", modes=(RetrievalModeName.RELATIONAL,), project_id="project-zorq", entity_ids=("entity-zorq",), goal_ids=("goal-continuity",), decision_ids=("decision-architecture",), limit=10))
            self.assertGreaterEqual(len(relational.source_records), 2)

    def test_contextual_activation_2026_to_2035_without_explicit_search(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            source, _ = self.seed_2026_zorq(engine, context)
            result = engine.activate_contextual_memory(
                context,
                current_message="How should we redesign ZORQ's architecture so it can stay coherent as it grows?",
                conversation_id="conv-2035",
                created_at=dt(2035, 7, 30, 12),
                project_id="project-zorq",
            )
            self.assertEqual(result.status, AdapterOutcome.ALLOW)
            self.assertIn(source.source_id, result.retrieved_source_ids)
            self.assertTrue(result.activated_candidate_ids)
            activated = [c for c in result.candidates if c.candidate_id in result.activated_candidate_ids]
            self.assertTrue(any(c.source_id == source.source_id for c in activated))
            self.assertTrue(all(c.provenance.source_id for c in activated))
            self.assertTrue(any(c.historical_current_state == LifecycleState.HISTORICAL for c in activated))
            self.assertTrue(result.minimized_reasoning_context["items"])
            self.assertTrue(result.mentioned_to_user_candidate_ids)
            self.assertNotIn("Search my September 2026 conversation", result.request.purpose)

    def test_explicit_historical_retrieval_is_separate_from_contextual_activation(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            self.seed_2026_zorq(engine, context)
            explicit = engine.activate_contextual_memory(
                context,
                current_message="What did we talk about on 27 September 2026?",
                conversation_id="conv-explicit",
                created_at=dt(2035, 7, 30),
                project_id="project-zorq",
                trigger=MemoryActivationTrigger.USER_REQUESTED_SEARCH,
            )
            self.assertEqual(explicit.request.trigger, MemoryActivationTrigger.USER_REQUESTED_SEARCH)
            answer = engine.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            self.assertGreaterEqual(len(answer.exact_source_content), 2)

    def test_false_associations_do_not_over_activate(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-other", started_at=dt(2026, 9, 27), project_id="project-cooking")
            engine.record_message(context, conversation_id="conv-other", role="user", content="I redesigned the kitchen architecture and memory wall for a cooking project.", sequence=1, event_time=dt(2026, 9, 27), message_id="cook-1", project_id="project-cooking")
            result = engine.activate_contextual_memory(context, current_message="How should we redesign ZORQ architecture?", conversation_id="conv-now", created_at=dt(2035, 7, 30), project_id="project-zorq")
            self.assertEqual(result.activated_candidate_ids, ())
            self.assertIn("No sufficiently supported", result.explanation)

    def test_sensitive_memory_is_blocked_from_activation_and_reasoning_context(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-secret", started_at=dt(2026, 9, 27), project_id="project-zorq", privacy_class=PrivacyClass.SECRET)
            secret = engine.record_message(context, conversation_id="conv-secret", role="user", content="ZORQ architecture secret credential is alpha beta gamma.", sequence=1, event_time=dt(2026, 9, 27), message_id="secret-1", privacy_class=PrivacyClass.SECRET, project_id="project-zorq", entity_ids=("entity-zorq",), decision_ids=("decision-architecture",))[1]
            result = engine.activate_contextual_memory(context, current_message="How should we redesign ZORQ architecture?", conversation_id="conv-now", created_at=dt(2035, 7, 30), project_id="project-zorq")
            blocked = [d for d in result.selection.decisions if d.candidate.source_id == secret.source_id]
            self.assertTrue(blocked)
            self.assertEqual(blocked[0].decision_state, MemoryActivationDecisionState.BLOCKED_BY_PRIVACY)
            self.assertNotIn(secret.source_id, str(result.minimized_reasoning_context))
            self.assertEqual(result.activated_candidate_ids, ())

    def test_redacted_sensitive_memory_uses_redact_policy_not_blocked_context(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-sensitive", started_at=dt(2026, 9, 27), project_id="project-zorq", privacy_class=PrivacyClass.SENSITIVE)
            source = engine.record_message(context, conversation_id="conv-sensitive", role="user", content="ZORQ architecture risk notes are sensitive but important to continuity.", sequence=1, event_time=dt(2026, 9, 27), message_id="sens-1", privacy_class=PrivacyClass.SENSITIVE, project_id="project-zorq", entity_ids=("entity-zorq",), goal_ids=("goal-continuity",), decision_ids=("decision-architecture",))[1]
            result = engine.activate_contextual_memory(context, current_message="How should ZORQ architecture preserve continuity?", conversation_id="conv-now", created_at=dt(2035, 7, 30), project_id="project-zorq")
            redacted_decisions = [d for d in result.selection.decisions if d.candidate.source_id == source.source_id]
            self.assertTrue(redacted_decisions)
            self.assertEqual(redacted_decisions[0].policy_decision, MemoryActivationPolicyDecision.REDACT)
            self.assertIn(redacted_decisions[0].candidate.candidate_id, result.selection.redacted_candidate_ids)

    def test_current_vs_historical_supersession_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            engine, store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-tech", started_at=dt(2027, 1, 1), project_id="project-tech")
            old = engine.record_message(context, conversation_id="conv-tech", role="user", content="I use technology X.", sequence=1, event_time=dt(2027, 1, 1), message_id="tech-old", project_id="project-tech")[1]
            engine.derive_from_conversation(context, "conv-tech")
            engine.start_conversation(context, conversation_id="conv-tech-2032", started_at=dt(2032, 1, 1), project_id="project-tech")
            new = engine.record_message(context, conversation_id="conv-tech-2032", role="user", content="I no longer use technology X.", sequence=1, event_time=dt(2032, 1, 1), message_id="tech-new", project_id="project-tech")[1]
            engine.derive_from_conversation(context, "conv-tech-2032")
            old_mem = store.derived_for_sources("owner-1", (old.source_id,), include_deleted=False)[0]
            new_mem = store.derived_for_sources("owner-1", (new.source_id,), include_deleted=False)[0]
            self.assertEqual(old_mem.lifecycle_state, LifecycleState.SUPERSEDED)
            self.assertEqual(new_mem.lifecycle_state, LifecycleState.CURRENT)
            current = engine.answer_current_or_historical_state(context, "What do I use now technology X?", "use:technology-x")
            self.assertIn("no longer", current.summary)
            historical = engine.retrieve(context, ContinuityQuery("technology X", modes=(RetrievalModeName.SEMANTIC,), temporal_start=dt(2027, 1, 1), temporal_end=dt(2028, 1, 1), limit=10))
            self.assertTrue(any(r.source_id == old.source_id for r in historical.source_records))

    def test_conflicting_derived_memory_preserves_both_and_marks_uncertainty(self):
        with tempfile.TemporaryDirectory() as td:
            engine, store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-conflict", started_at=dt(2028, 1, 1))
            a = engine.record_message(context, conversation_id="conv-conflict", role="user", content="I prefer editor Alpha.", sequence=1, event_time=dt(2028, 1, 1), message_id="pref-a")[1]
            b = engine.record_message(context, conversation_id="conv-conflict", role="user", content="I prefer editor Beta.", sequence=2, event_time=dt(2028, 1, 2), message_id="pref-b")[1]
            engine.store_derived_memory_from_source(a, DerivedMemoryKind.PREFERENCE, "User preferred editor Alpha", "preference:editor", None, (), (), ())
            engine.store_derived_memory_from_source(b, DerivedMemoryKind.PREFERENCE, "User preferred editor Beta", "preference:editor", None, (), (), ())
            memories = store.search_derived("owner-1", ContinuityQuery("editor", modes=(RetrievalModeName.LEXICAL,), limit=10), limit=10)
            states = {m.lifecycle_state for m in memories}
            self.assertIn(LifecycleState.CONFLICTED, states)
            self.assertEqual(len([m for m in memories if m.claim_key == "preference:editor"]), 2)

    def test_owner_isolation_blocks_cross_owner_retrieval_export_and_context_spoofing(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            engine_a, _store_a, _adapter_a = self.make_engine(db, owner="owner-a")
            ctx_a = self.ctx("owner-a")
            engine_a.start_conversation(ctx_a, conversation_id="conv-a", started_at=dt(2026, 1, 1))
            engine_a.record_message(ctx_a, conversation_id="conv-a", role="user", content="Owner A private memory.", sequence=1, event_time=dt(2026, 1, 1), message_id="a1")

            engine_b, _store_b, _adapter_b = self.make_engine(db, owner="owner-b")
            ctx_b = self.ctx("owner-b")
            retrieved_b = engine_b.retrieve(ctx_b, ContinuityQuery("Owner A private memory", modes=(RetrievalModeName.LEXICAL,), limit=10))
            self.assertEqual(retrieved_b.source_records, ())
            export_b = engine_b.export_owner_memory(ctx_b)
            self.assertEqual(export_b["source_records"], [])
            with self.assertRaises(PermissionError):
                MemoryAccessContext(owner_id="owner-a", principal_id="bad", authenticated_owner_id="owner-b", purpose="spoof")

    def test_deletion_propagates_to_source_derived_indexes_timeline_and_audit(self):
        with tempfile.TemporaryDirectory() as td:
            engine, store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            self.seed_2026_zorq(engine, context, conversation_id="conv-delete")
            before = engine.retrieve(context, ContinuityQuery("persistent personal memory", modes=(RetrievalModeName.LEXICAL,), limit=10))
            self.assertTrue(before.source_records)
            report = engine.delete_memory(context, scope_type="conversation", conversation_id="conv-delete")
            self.assertEqual(report.status, DeletionRuntimeStatus.COMPLETED)
            self.assertTrue(report.deleted_source_ids)
            after = engine.retrieve(context, ContinuityQuery("persistent personal memory", modes=(RetrievalModeName.LEXICAL,), limit=10))
            self.assertEqual(after.source_records, ())
            export = engine.export_owner_memory(context)
            self.assertTrue(export["deletion_states"])
            audit = export["deletion_states"][0]
            self.assertEqual(audit["content_included"], 0)
            self.assertNotIn("persistent personal memory", str(audit))
            self.assertGreaterEqual(store.table_counts("owner-1")["deletion_audit"], 1)

    def test_delete_derived_memory_can_preserve_source_record(self):
        with tempfile.TemporaryDirectory() as td:
            engine, store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx()
            source, _ = self.seed_2026_zorq(engine, context)
            derived = store.derived_for_sources("owner-1", (source.source_id,))
            self.assertTrue(derived)
            report = engine.delete_memory(context, scope_type="derived_memory", memory_ids=(derived[0].memory_id,))
            self.assertEqual(report.status, DeletionRuntimeStatus.COMPLETED)
            self.assertIsNotNone(store.get_source("owner-1", source.source_id))
            remaining_derived = store.derived_for_sources("owner-1", (source.source_id,))
            self.assertTrue(all(m.memory_id != derived[0].memory_id for m in remaining_derived))

    def test_memoryos_unavailable_fails_closed_for_storage_and_degrades_retrieval(self):
        with tempfile.TemporaryDirectory() as td:
            engine, store, adapter = self.make_engine(Path(td) / "memory.db", available=False)
            context = self.ctx()
            decision, conv = engine.start_conversation(context, conversation_id="conv-unavailable", started_at=dt(2026, 1, 1))
            self.assertEqual(decision.outcome, AdapterOutcome.UNAVAILABLE)
            self.assertIsNone(conv)
            retrieved = engine.retrieve(context, ContinuityQuery("anything", modes=(RetrievalModeName.LEXICAL,), limit=10))
            self.assertEqual(retrieved.status, AdapterOutcome.UNAVAILABLE)
            self.assertEqual(store.table_counts("owner-1")["messages"], 0)
            self.assertEqual(adapter.integration_status, "UNAVAILABLE")

    def test_crash_during_derivation_does_not_corrupt_source_truth(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            engine, _store, _adapter = self.make_engine(db)
            context = self.ctx()
            self.seed_2026_zorq(engine, context, conversation_id="conv-crash")
            with self.assertRaises(RuntimeError):
                engine.derive_from_conversation(context, "conv-crash", fail_after=1)
            restarted, _store2, _adapter2 = self.make_engine(db)
            answer = restarted.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            self.assertTrue(answer.exact_source_content)
            self.assertIn("persistent personal memory", " ".join(r.content for r in answer.exact_source_content))

    def test_provider_egress_policy_denies_external_memory_leakage(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, adapter = self.make_engine(Path(td) / "memory.db")
            external_ctx = self.ctx(provider=ProviderTrustClass.EXTERNAL_UNTRUSTED, egress=EgressPolicy.NO_EGRESS)
            decision = adapter.govern(external_ctx, operation="retrieve", purpose="external prompt", scope={"query": "private memory"})
            self.assertEqual(decision.outcome, AdapterOutcome.DENY)

    def test_memory_cannot_authorize_actions(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            self.assertFalse(engine.memory_cannot_authorize_action("Usually do X for me"))

    def test_basic_owner_export_includes_schema_provenance_and_no_other_owner(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            engine_a, _store_a, _adapter_a = self.make_engine(db, owner="owner-a")
            ctx_a = self.ctx("owner-a")
            engine_a.start_conversation(ctx_a, conversation_id="conv-a", started_at=dt(2026, 1, 1))
            engine_a.record_message(ctx_a, conversation_id="conv-a", role="user", content="Exportable owner A memory.", sequence=1, event_time=dt(2026, 1, 1), message_id="a1")
            engine_b, _store_b, _adapter_b = self.make_engine(db, owner="owner-b")
            ctx_b = self.ctx("owner-b")
            engine_b.start_conversation(ctx_b, conversation_id="conv-b", started_at=dt(2026, 1, 1))
            engine_b.record_message(ctx_b, conversation_id="conv-b", role="user", content="Exportable owner B memory.", sequence=1, event_time=dt(2026, 1, 1), message_id="b1")
            export_a = engine_a.export_owner_memory(ctx_a)
            self.assertEqual(export_a["schema_version"], "zorq.phase3b.personal-continuity.v1")
            self.assertTrue(all(row["owner_id"] == "owner-a" for row in export_a["source_records"]))
            self.assertNotIn("owner B", str(export_a))


if __name__ == "__main__":
    unittest.main()
