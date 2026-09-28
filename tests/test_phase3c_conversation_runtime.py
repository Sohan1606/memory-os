from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone
import json
import tempfile
import unittest

from zroq.domain_contracts import (
    ContractValidationError,
    ConversationCheckpoint,
    ConversationState,
    GenerationState,
    PrivacyClass,
    ProviderTrustClass,
    EgressPolicy,
)
from zroq.personal_continuity import (
    AdapterOutcome,
    DocumentedMemoryOSAdapter,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityEngine,
    PersonalContinuityStore,
    RetrievalModeName,
    ContinuityQuery,
)
from zroq.conversation_runtime import (
    ConversationRuntime,
    DeterministicConversationProvider,
    ResponseStreamEventType,
    RuntimeCommandKind,
    UnavailableConversationProvider,
)


def dt(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


class RaisingProvider(DeterministicConversationProvider):
    provider_id = "raising-test-provider"

    def stream_response(self, request, cancellation_signal):
        raise RuntimeError("boom")


class Phase3CConversationRuntimeTests(unittest.TestCase):
    def make_engine(self, db_path, owner="owner-1", *, memoryos_available=True):
        store = PersonalContinuityStore(db_path)
        adapter = DocumentedMemoryOSAdapter(
            store,
            MemoryCapturePolicyEngine.default_retain(owner),
            memoryos_available=memoryos_available,
            real_memoryos_verified=False,
        )
        return PersonalContinuityEngine(store, adapter), store, adapter

    def ctx(self, owner="owner-1", *, purpose="phase3c conversation runtime test"):
        return MemoryAccessContext(
            owner_id=owner,
            principal_id=f"principal-{owner}",
            authenticated_owner_id=owner,
            purpose=purpose,
            provider_trust_class=ProviderTrustClass.LOCAL_ONLY,
            egress_policy=EgressPolicy.NO_EGRESS,
            owner_calendar_timezone="Asia/Kolkata",
        )

    def runtime(self, db_path, owner="owner-1", provider=None):
        engine, store, adapter = self.make_engine(db_path, owner=owner)
        provider = provider or DeterministicConversationProvider(("Alpha ", "Beta ", "Gamma ", "Delta"))
        return ConversationRuntime(engine, provider), engine, store, adapter

    def seed_zorq_memory(self, engine, context, conversation_id="conv-2026-zorq"):
        engine.start_conversation(
            context,
            conversation_id=conversation_id,
            started_at=dt(2026, 9, 27, 1),
            title="ZORQ memory architecture",
            topic="source-backed personal continuity",
            project_id="project-zorq",
        )
        r1 = engine.record_message(
            context,
            conversation_id=conversation_id,
            role="user",
            content="ZORQ architecture memory continuity vision for long-term provenance.",
            sequence=1,
            event_time=dt(2026, 9, 27, 1, 15),
            message_id="msg-zorq-1",
            project_id="project-zorq",
            entity_ids=("entity-zorq",),
            goal_ids=("goal-continuity",),
            decision_ids=("decision-architecture",),
        )[1]
        r2 = engine.record_message(
            context,
            conversation_id=conversation_id,
            role="assistant",
            content="We said Phase 2.6 Action Plane authority stays separate from memory and conversation state.",
            sequence=2,
            event_time=dt(2026, 9, 27, 1, 16),
            message_id="msg-zorq-2",
            project_id="project-zorq",
            entity_ids=("entity-zorq",),
            goal_ids=("goal-continuity",),
            decision_ids=("decision-architecture",),
        )[1]
        engine.derive_from_conversation(context, conversation_id)
        return r1, r2

    def test_stream_event_vocabulary_is_provider_neutral(self):
        expected = {
            "RESPONSE_STARTED",
            "TEXT_DELTA",
            "MEMORY_REFERENCE",
            "EVIDENCE_REFERENCE",
            "RESPONSE_PAUSED",
            "RESPONSE_INTERRUPTED",
            "RESPONSE_COMPLETED",
            "RESPONSE_FAILED",
            "RESPONSE_CANCELED",
        }
        self.assertEqual({item.value for item in ResponseStreamEventType}, expected)

    def test_normal_text_turn_persists_user_and_assistant_through_continuity_engine(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-runtime")
            result = rt.receive_user_message(context, "conv-runtime", "Hello ZORQ memory architecture")
            self.assertEqual(result.state, ConversationState.COMPLETED)
            self.assertEqual(result.response_text, "Alpha Beta Gamma Delta")
            sources = store.list_sources_for_conversation(context.owner_id, "conv-runtime")
            self.assertEqual([s.role for s in sources], ["user", "assistant"])
            self.assertIn("Hello ZORQ", sources[0].content)
            self.assertIn("Alpha Beta", sources[1].content)

    def test_stop_interrupts_generation_preserves_prefix_cursor_and_does_not_discard_response(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-stop")
            partial = rt.receive_user_message(context, "conv-stop", "Please stream slowly", max_deltas=1)
            self.assertEqual(partial.state, ConversationState.SPEAKING)
            self.assertEqual(partial.response_text, "Alpha ")
            stopped = rt.receive_user_message(context, "conv-stop", "STOP")
            self.assertEqual(stopped.command, RuntimeCommandKind.STOP)
            self.assertEqual(stopped.state, ConversationState.INTERRUPTED)
            self.assertEqual(stopped.response_text, "Alpha ")
            row = rt.store.latest_response(context.owner_id, "conv-stop")
            self.assertEqual(row["generation_state"], GenerationState.INTERRUPTED.value)
            self.assertEqual(row["text_position"], len("Alpha "))
            checkpoint = rt.store.latest_checkpoint(context.owner_id, "conv-stop")
            self.assertIsNotNone(checkpoint)
            self.assertEqual(checkpoint.response_cursor.text_position, len("Alpha "))
            sources = store.list_sources_for_conversation(context.owner_id, "conv-stop")
            self.assertEqual([s.role for s in sources], ["user", "assistant"])
            self.assertEqual(sources[-1].content, "Alpha ")
            self.assertFalse(rt.action_plane_available_from_conversation)

    def test_what_were_you_saying_resumes_from_preserved_prefix_without_duplicate_prefix_source(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-resume")
            rt.receive_user_message(context, "conv-resume", "stream", max_deltas=2)
            rt.receive_user_message(context, "conv-resume", "stop")
            resumed = rt.receive_user_message(context, "conv-resume", "What were you saying?")
            self.assertEqual(resumed.command, RuntimeCommandKind.RESUME)
            self.assertEqual(resumed.state, ConversationState.COMPLETED)
            self.assertEqual(resumed.response_text, "Alpha Beta Gamma Delta")
            latest_request = rt.provider.requests[-1]
            self.assertEqual(latest_request.generated_prefix, "Alpha Beta ")
            self.assertEqual(latest_request.resume_from_position, len("Alpha Beta "))
            sources = store.list_sources_for_conversation(context.owner_id, "conv-resume")
            assistant_contents = [s.content for s in sources if s.role == "assistant"]
            self.assertEqual(assistant_contents, ["Alpha Beta ", "Gamma Delta"])

    def test_pause_and_continue_are_response_generation_only_controls(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-pause")
            rt.receive_user_message(context, "conv-pause", "stream", max_deltas=1)
            paused = rt.receive_user_message(context, "conv-pause", "pause")
            self.assertEqual(paused.state, ConversationState.PAUSED)
            continued = rt.receive_user_message(context, "conv-pause", "continue")
            self.assertEqual(continued.response_text, "Alpha Beta Gamma Delta")
            self.assertEqual(continued.state, ConversationState.COMPLETED)
            self.assertFalse(rt.action_plane_available_from_conversation)

    def test_cancel_makes_response_non_resumable_and_does_not_cancel_actions(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-cancel")
            rt.receive_user_message(context, "conv-cancel", "stream", max_deltas=1)
            canceled = rt.receive_user_message(context, "conv-cancel", "cancel")
            self.assertEqual(canceled.state, ConversationState.CANCELED)
            row = rt.store.latest_response(context.owner_id, "conv-cancel")
            self.assertEqual(row["resumable"], 0)
            resumed = rt.receive_user_message(context, "conv-cancel", "continue")
            self.assertEqual(resumed.message, "nothing resumable")
            self.assertFalse(rt.action_plane_available_from_conversation)

    def test_skip_discards_remaining_generation_without_action_plane_side_effect(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-skip")
            rt.receive_user_message(context, "conv-skip", "stream", max_deltas=1)
            skipped = rt.receive_user_message(context, "conv-skip", "skip")
            self.assertEqual(skipped.command, RuntimeCommandKind.SKIP)
            self.assertEqual(skipped.state, ConversationState.WAITING)
            row = rt.store.latest_response(context.owner_id, "conv-skip")
            self.assertEqual(row["generation_state"], GenerationState.CANCELED.value)
            self.assertEqual(row["resumable"], 0)
            self.assertFalse(rt.action_plane_available_from_conversation)

    def test_unavailable_provider_reports_failure_without_fabricating_assistant_answer(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            engine, store, _adapter = self.make_engine(db)
            rt = ConversationRuntime(engine, UnavailableConversationProvider())
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-unavailable")
            result = rt.receive_user_message(context, "conv-unavailable", "Answer using a model")
            self.assertIn(ResponseStreamEventType.RESPONSE_FAILED, [e.event_type for e in result.events])
            self.assertEqual(result.response_text, "")
            sources = store.list_sources_for_conversation(context.owner_id, "conv-unavailable")
            self.assertEqual([s.role for s in sources], ["user"])

    def test_provider_exception_is_recorded_as_failed_not_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            engine, store, _adapter = self.make_engine(db)
            rt = ConversationRuntime(engine, RaisingProvider())
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-provider-fail")
            result = rt.receive_user_message(context, "conv-provider-fail", "please answer")
            self.assertIn(ResponseStreamEventType.RESPONSE_FAILED, [e.event_type for e in result.events])
            self.assertEqual(result.response_text, "")
            self.assertEqual([s.role for s in store.list_sources_for_conversation(context.owner_id, "conv-provider-fail")], ["user"])

    def test_explicit_historical_recall_uses_existing_source_records_and_evidence_ids(self):
        with tempfile.TemporaryDirectory() as td:
            rt, engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            self.seed_zorq_memory(engine, context)
            rt.start_conversation(context, conversation_id="conv-recall")
            result = rt.receive_user_message(context, "conv-recall", "What did we discuss on 27 September 2026?")
            self.assertEqual(result.command, RuntimeCommandKind.EXPLICIT_RECALL)
            self.assertEqual(result.historical_answer.status, AdapterOutcome.ALLOW)
            self.assertTrue(result.historical_answer.evidence_source_ids)
            self.assertIn("Source-backed summary", result.response_text)
            self.assertIn("exact source messages", result.response_text)

    def test_automatic_context_activation_is_distinct_from_explicit_recall_and_preserves_refs(self):
        with tempfile.TemporaryDirectory() as td:
            rt, engine, _store, _adapter = self.runtime(Path(td) / "memory.db", provider=DeterministicConversationProvider(("ok",)))
            context = self.ctx()
            seeded_source, _ = self.seed_zorq_memory(engine, context)
            rt.start_conversation(context, conversation_id="conv-activation")
            result = rt.receive_user_message(context, "conv-activation", "Why should ZORQ architecture preserve memory continuity?")
            self.assertEqual(result.command, RuntimeCommandKind.NONE)
            self.assertIsNotNone(result.memory_activation)
            self.assertTrue(result.memory_activation.activated_candidate_ids)
            self.assertNotEqual(result.memory_activation.retrieved_source_ids, ())
            self.assertIn(ResponseStreamEventType.MEMORY_REFERENCE, [e.event_type for e in result.events])
            row = rt.store.latest_response(context.owner_id, "conv-activation")
            evidence_ids = json.loads(row["referenced_evidence_ids_json"])
            self.assertIn(seeded_source.source_id, evidence_ids)
            explanation = rt.explain_last_memory_activation(context, "conv-activation")
            self.assertTrue("related" in explanation.lower() or "memory" in explanation.lower())

    def test_local_only_context_passes_minimized_no_egress_memory_frame_to_provider(self):
        with tempfile.TemporaryDirectory() as td:
            rt, engine, _store, _adapter = self.runtime(Path(td) / "memory.db", provider=DeterministicConversationProvider(("ok",)))
            context = self.ctx()
            self.seed_zorq_memory(engine, context)
            rt.start_conversation(context, conversation_id="conv-egress")
            rt.receive_user_message(context, "conv-egress", "Why should ZORQ architecture preserve memory continuity?")
            req = rt.provider.requests[-1]
            self.assertEqual(req.memory_context.get("provider_egress"), "NO_EGRESS")
            self.assertEqual(req.runtime_context["conversation_id"], "conv-egress")
            self.assertEqual(req.runtime_context["branch_id"], "branch-main")
            self.assertEqual(req.runtime_context["memory_firewall"]["injection"], "minimized_reasoning_context_only")
            self.assertIn("latest_messages", req.runtime_context)
            self.assertIn("activated_candidate_ids", req.runtime_context)
            self.assertEqual(context.provider_trust_class, ProviderTrustClass.LOCAL_ONLY)
            self.assertNotIn("ZORQ architecture memory continuity vision", json.dumps(dict(req.memory_context), default=str))

    def test_memory_commands_remember_do_not_remember_and_forget_conversation(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-memory-cmd")
            remember = rt.receive_user_message(context, "conv-memory-cmd", "remember this: my launch color is cobalt")
            self.assertEqual(remember.command, RuntimeCommandKind.REMEMBER_THIS)
            before = len(store.list_sources_for_conversation(context.owner_id, "conv-memory-cmd"))
            dn = rt.receive_user_message(context, "conv-memory-cmd", "do not remember this")
            self.assertEqual(dn.command, RuntimeCommandKind.DO_NOT_REMEMBER)
            rt.receive_user_message(context, "conv-memory-cmd", "secret transient sentence")
            after = store.list_sources_for_conversation(context.owner_id, "conv-memory-cmd")
            self.assertEqual(len([s for s in after if "secret transient" in s.content]), 0)
            forget = rt.receive_user_message(context, "conv-memory-cmd", "forget this conversation")
            self.assertEqual(forget.command, RuntimeCommandKind.FORGET_CONVERSATION)
            self.assertEqual(store.list_sources_for_conversation(context.owner_id, "conv-memory-cmd"), ())
            latest = rt.store.latest_response(context.owner_id, "conv-memory-cmd")
            if latest:
                self.assertEqual(latest["generated_text"], "")
            self.assertGreaterEqual(before, 2)

    def test_show_memory_is_source_backed_and_owner_scoped(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            rt, engine, _store, _adapter = self.runtime(db)
            context = self.ctx()
            seeded_source, _ = self.seed_zorq_memory(engine, context)
            rt.start_conversation(context, conversation_id="conv-show")
            result = rt.receive_user_message(context, "conv-show", "show me what you remember about ZORQ architecture")
            self.assertEqual(result.command, RuntimeCommandKind.SHOW_MEMORY)
            self.assertIsNotNone(result.retrieval)
            self.assertIn(seeded_source.source_id, [r.source_id for r in result.retrieval.source_records])
            self.assertIn("source-backed", result.response_text)
            other_ctx = self.ctx("owner-2")
            other_engine, _other_store, _ = self.make_engine(db, owner="owner-2")
            other_rt = ConversationRuntime(other_engine, DeterministicConversationProvider(("ok",)), db_path=db)
            other_rt.start_conversation(other_ctx, conversation_id="conv-other")
            other_result = other_rt.receive_user_message(other_ctx, "conv-other", "show me what you remember about ZORQ architecture")
            self.assertNotIn(seeded_source.source_id, other_result.response_text)

    def test_branch_topic_change_checkpoint_and_restart_continuity(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            rt, engine, _store, _adapter = self.runtime(db)
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-branch", topic="first")
            changed = rt.receive_user_message(context, "conv-branch", "change topic to orbital design")
            self.assertEqual(changed.command, RuntimeCommandKind.CHANGE_TOPIC)
            new_branch = changed.branch_id
            self.assertNotEqual(new_branch, "branch-main")
            cp_result = rt.receive_user_message(context, "conv-branch", "checkpoint")
            self.assertIsNotNone(cp_result.checkpoint)
            engine2, _store2, _adapter2 = self.make_engine(db)
            rt2 = ConversationRuntime(engine2, DeterministicConversationProvider(("ok",)), db_path=db)
            reopened = rt2.reopen_conversation(context, "conv-branch")
            self.assertEqual(reopened.branch_id, new_branch)
            self.assertIsNotNone(reopened.checkpoint)
            self.assertEqual(reopened.checkpoint.branch_id, new_branch)

    def test_owner_isolation_blocks_cross_owner_conversation_access(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            rt, _engine, _store, _adapter = self.runtime(db, owner="owner-1")
            context = self.ctx("owner-1")
            rt.start_conversation(context, conversation_id="conv-private")
            other_engine, _other_store, _ = self.make_engine(db, owner="owner-2")
            other_rt = ConversationRuntime(other_engine, DeterministicConversationProvider(("ok",)), db_path=db)
            other_ctx = self.ctx("owner-2")
            with self.assertRaises(PermissionError):
                other_rt.reopen_conversation(other_ctx, "conv-private")
            with self.assertRaises(PermissionError):
                other_rt.start_conversation(other_ctx, conversation_id="conv-private")

    def test_checkpoint_may_reference_historical_authority_but_cannot_restore_executable_authority(self):
        with tempfile.TemporaryDirectory() as td:
            rt, _engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-checkpoint-authority")
            checkpoint = rt.create_checkpoint_with_historical_references(context, "conv-checkpoint-authority")
            self.assertEqual(checkpoint.referenced_lease_ids, ("lease-historical",))
            self.assertIsNone(checkpoint.active_lease_id)
            with self.assertRaises(ContractValidationError):
                ConversationCheckpoint(
                    checkpoint_id="checkpoint-bad",
                    conversation_id="conv-checkpoint-authority",
                    owner_id=context.owner_id,
                    created_at=dt(2026, 9, 27),
                    active_lease_id="lease-live",
                )

    def test_static_conversation_runtime_does_not_import_or_call_action_plane_kernels(self):
        module_text = (Path(__file__).resolve().parents[1] / "src" / "zroq" / "conversation_runtime.py").read_text()
        forbidden = ("from .action_kernel", "import zroq.action_kernel", "DeviceAgent(", "LeaseIssuer(", "ActionKernel(")
        self.assertEqual([token for token in forbidden if token in module_text], [])
        self.assertIn("action_plane_available_from_conversation", module_text)

    def test_date_only_recall_keeps_owner_timezone_contract_through_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            rt, engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            engine.start_conversation(context, conversation_id="conv-date-source", started_at=dt(2026, 9, 26, 20))
            inside_kolkata_day = engine.record_message(
                context,
                conversation_id="conv-date-source",
                role="user",
                content="Late-night India calendar memory for 27 September 2026.",
                sequence=1,
                event_time=dt(2026, 9, 26, 20, 45),
                message_id="msg-date-1",
            )[1]
            rt.start_conversation(context, conversation_id="conv-date-runtime")
            result = rt.receive_user_message(context, "conv-date-runtime", "What did we discuss on 27 September 2026?")
            self.assertIn(inside_kolkata_day.source_id, result.historical_answer.evidence_source_ids)
            self.assertEqual(result.historical_answer.mode_status.get("calendar_timezone"), "Asia/Kolkata")

    def test_retrieval_status_truthfully_reports_semantic_availability(self):
        with tempfile.TemporaryDirectory() as td:
            rt, engine, _store, _adapter = self.runtime(Path(td) / "memory.db")
            context = self.ctx()
            rt.start_conversation(context, conversation_id="conv-semantic-status")
            response = engine.retrieve(context, ContinuityQuery("anything", modes=(RetrievalModeName.SEMANTIC,)))
            self.assertIn(response.mode_status[RetrievalModeName.SEMANTIC.value].split(":")[0], {"AVAILABLE", "UNAVAILABLE"})


if __name__ == "__main__":
    unittest.main()
