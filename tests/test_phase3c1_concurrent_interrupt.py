from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tempfile
import threading
import unittest

from zroq.domain_contracts import ConversationState, GenerationState
from zroq.personal_continuity import (
    DocumentedMemoryOSAdapter,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityEngine,
    PersonalContinuityStore,
)
from zroq.conversation_runtime import (
    ConversationRuntime,
    DeterministicConversationProvider,
    ResponseControlState,
    ResponseStreamEvent,
    ResponseStreamEventType,
    RuntimeCommandKind,
    utc_now,
)


class BlockingLateProvider:
    provider_id = "phase3c1-blocking-late-provider"
    is_test_provider = True

    def __init__(self, late_events=None):
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.requests = []
        self.late_events = list(late_events or [
            {"event_type": ResponseStreamEventType.TEXT_DELTA, "text_delta": "Beta "},
            {"event_type": ResponseStreamEventType.RESPONSE_COMPLETED},
        ])

    def stream_response(self, request, cancellation_signal):
        self.requests.append(request)
        yield ResponseStreamEvent(ResponseStreamEventType.RESPONSE_STARTED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now())
        yield ResponseStreamEvent(ResponseStreamEventType.TEXT_DELTA, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), text_delta="Alpha ")
        # This code resumes only after the runtime has accepted/persisted the first delta and asks for the next event.
        self.blocked.set()
        self.release.wait(5)
        # Intentionally ignore cancellation and emit whatever the test requested.
        for spec in self.late_events:
            event_type = spec["event_type"]
            yield ResponseStreamEvent(
                event_type,
                request.response_id,
                request.owner_id,
                request.conversation_id,
                request.branch_id,
                utc_now(),
                text_delta=spec.get("text_delta", ""),
                memory_id=spec.get("memory_id"),
                evidence_id=spec.get("evidence_id"),
                message=spec.get("message", ""),
                metadata=spec.get("metadata", {}),
            )


class Phase3C1ConcurrentInterruptTests(unittest.TestCase):
    def make_runtime(self, db_path, provider, owner="owner-1"):
        store = PersonalContinuityStore(db_path)
        adapter = DocumentedMemoryOSAdapter(
            store,
            MemoryCapturePolicyEngine.default_retain(owner),
            memoryos_available=True,
            real_memoryos_verified=False,
        )
        engine = PersonalContinuityEngine(store, adapter)
        return ConversationRuntime(engine, provider), engine, store

    def ctx(self, owner="owner-1"):
        return MemoryAccessContext(
            owner_id=owner,
            principal_id=f"principal-{owner}",
            authenticated_owner_id=owner,
            purpose="phase3c1 concurrent interruption test",
        )

    def start_blocked_stream(self, rt, provider, ctx, conversation_id="conv-c1"):
        rt.start_conversation(ctx, conversation_id=conversation_id)
        holder = {}
        thread = threading.Thread(
            target=lambda: holder.setdefault(
                "result",
                rt.receive_user_message(
                    ctx,
                    conversation_id,
                    "stream concurrently",
                ),
            )
        )
        thread.start()

        if not provider.blocked.wait(15):
            # Never leave a provider thread behind when the fixture setup
            # itself fails. On Windows an unfinished stream can retain the
            # SQLite file handle and make TemporaryDirectory cleanup fail
            # with WinError 32.
            provider.release.set()
            thread.join(10)
            self.assertFalse(
                thread.is_alive(),
                "provider stream thread did not finish after fixture timeout",
            )
            self.fail(
                "provider did not block after first delta within 15 seconds"
            )

        row = rt.store.latest_response(ctx.owner_id, conversation_id)
        self.assertEqual(row["generated_text"], "Alpha ")
        self.assertEqual(row["generation_state"], GenerationState.GENERATING.value)
        return thread, holder

    def release_and_join(self, provider, thread, holder):
        provider.release.set()
        thread.join(5)
        self.assertFalse(thread.is_alive(), "stream thread did not finish")
        return holder.get("result")

    def test_concurrent_stop_cannot_be_overwritten_by_late_completion(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            control = {}
            cthread = threading.Thread(target=lambda: control.setdefault("result", rt.interrupt_response(ctx, "conv-c1")))
            cthread.start(); cthread.join(5)
            self.assertEqual(control["result"].state, ConversationState.INTERRUPTED)
            after_stop = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(after_stop["generation_state"], GenerationState.INTERRUPTED.value)
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.INTERRUPTED.value)
            self.assertEqual(final["generated_text"], "Alpha ")

    def test_concurrent_stop_blocks_late_text_delta_commit(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider([{"event_type": ResponseStreamEventType.TEXT_DELTA, "text_delta": "Beta "}, {"event_type": ResponseStreamEventType.RESPONSE_COMPLETED}])
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            rt.interrupt_response(ctx, "conv-c1")
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generated_text"], "Alpha ")
            self.assertNotIn("Beta", final["generated_text"])

    def test_concurrent_pause_cannot_be_overwritten_by_completion(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            paused = rt.pause_response(ctx, "conv-c1")
            self.assertEqual(paused.state, ConversationState.PAUSED)
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.PAUSED.value)
            self.assertEqual(final["generated_text"], "Alpha ")

    def test_concurrent_cancel_cannot_be_overwritten_by_completion(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            canceled = rt.cancel_response(ctx, "conv-c1")
            self.assertEqual(canceled.state, ConversationState.CANCELED)
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.CANCELED.value)
            self.assertEqual(final["resumable"], 0)
            self.assertEqual(final["generated_text"], "Alpha ")

    def test_late_memory_reference_after_stop_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider([{"event_type": ResponseStreamEventType.MEMORY_REFERENCE, "memory_id": "mem-late", "evidence_id": "src-late"}, {"event_type": ResponseStreamEventType.RESPONSE_COMPLETED}])
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            rt.interrupt_response(ctx, "conv-c1")
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["referenced_memory_ids_json"], "[]")
            self.assertNotIn("src-late", final["referenced_evidence_ids_json"])

    def test_late_evidence_reference_after_cancel_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider([{"event_type": ResponseStreamEventType.EVIDENCE_REFERENCE, "evidence_id": "src-late"}, {"event_type": ResponseStreamEventType.RESPONSE_COMPLETED}])
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            rt.cancel_response(ctx, "conv-c1")
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertNotIn("src-late", final["referenced_evidence_ids_json"])

    def test_stop_preserves_exact_committed_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            rt.interrupt_response(ctx, "conv-c1")
            self.release_and_join(provider, thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            checkpoint = rt.store.latest_checkpoint(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generated_text"], "Alpha ")
            self.assertEqual(checkpoint.response_cursor.text_position, len("Alpha "))

    def test_resume_uses_only_authoritative_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            rt.interrupt_response(ctx, "conv-c1")
            self.release_and_join(provider, thread, holder)
            rt.provider = DeterministicConversationProvider(("Alpha ", "Beta ", "Gamma"))
            resumed = rt.resume_response(ctx, "conv-c1")
            self.assertEqual(resumed.response_text, "Alpha Beta Gamma")
            self.assertEqual(rt.provider.requests[-1].generated_prefix, "Alpha ")
            assistant_sources = [s.content for s in store.list_sources_for_conversation(ctx.owner_id, "conv-c1") if s.role == "assistant"]
            self.assertEqual(assistant_sources, ["Alpha ", "Beta Gamma"])

    def test_out_of_band_interrupt_does_not_create_user_message(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            rt.interrupt_response(ctx, "conv-c1")
            self.release_and_join(provider, thread, holder)
            sources = store.list_sources_for_conversation(ctx.owner_id, "conv-c1")
            self.assertEqual([s.role for s in sources], ["user", "assistant"])
            self.assertEqual(sources[0].content, "stream concurrently")

    def test_stop_and_cancel_race_has_deterministic_result(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            stream_thread, holder = self.start_blocked_stream(rt, provider, ctx)
            start = threading.Barrier(3)
            results = []
            def stop():
                start.wait(); results.append(rt.interrupt_response(ctx, "conv-c1"))
            def cancel():
                start.wait(); results.append(rt.cancel_response(ctx, "conv-c1"))
            t1 = threading.Thread(target=stop); t2 = threading.Thread(target=cancel)
            t1.start(); t2.start(); start.wait(); t1.join(5); t2.join(5)
            provider.release.set(); stream_thread.join(5)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.CANCELED.value)
            self.assertEqual(final["control_state"], ResponseControlState.CANCEL_REQUESTED.value)
            self.assertLessEqual(len([s for s in store.list_sources_for_conversation(ctx.owner_id, "conv-c1") if s.role == "assistant"]), 1)

    def test_pause_and_resume_race_has_deterministic_result(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            stream_thread, holder = self.start_blocked_stream(rt, provider, ctx)
            start = threading.Barrier(3)
            def pause():
                start.wait(); rt.pause_response(ctx, "conv-c1")
            def resume():
                start.wait(); rt.resume_response(ctx, "conv-c1")
            t1 = threading.Thread(target=pause); t2 = threading.Thread(target=resume)
            t1.start(); t2.start(); start.wait(); t1.join(5); t2.join(5)
            provider.release.set(); stream_thread.join(5)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.PAUSED.value)
            rt.provider = DeterministicConversationProvider(("Alpha ", "Beta"))
            resumed = rt.continue_response(ctx, "conv-c1")
            self.assertEqual(resumed.state, ConversationState.COMPLETED)
            self.assertEqual(resumed.response_text, "Alpha Beta")

    def test_persisted_control_state_survives_runtime_restart(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            provider = DeterministicConversationProvider(("Alpha ", "Beta"))
            rt, _engine, _store = self.make_runtime(db, provider)
            ctx = self.ctx()
            rt.start_conversation(ctx, conversation_id="conv-c1")
            partial = rt.receive_user_message(ctx, "conv-c1", "stream restart", max_deltas=1)
            self.assertEqual(partial.state, ConversationState.SPEAKING)
            rt2, _engine2, _store2 = self.make_runtime(db, DeterministicConversationProvider(("Alpha ", "Beta")))
            reopened = rt2.reopen_conversation(ctx, "conv-c1")
            self.assertIn("recoverable generating", reopened.message)
            latest = rt2.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(latest["control_state"], ResponseControlState.ACTIVE.value)
            self.assertEqual(latest["generated_text"], "Alpha ")
            resumed = rt2.resume_response(ctx, "conv-c1")
            self.assertEqual(resumed.response_text, "Alpha Beta")

    def test_stale_stream_generation_cannot_finalize_response(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider()
            rt, _engine, _store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            stream_thread, holder = self.start_blocked_stream(rt, provider, ctx)
            active = rt._active_streams[(ctx.owner_id, "conv-c1")]
            stream_epoch = active.control_epoch
            rt.interrupt_response(ctx, "conv-c1")
            self.assertGreater(rt.store.latest_response(ctx.owner_id, "conv-c1")["control_epoch"], stream_epoch)
            self.release_and_join(provider, stream_thread, holder)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.INTERRUPTED.value)
            self.assertEqual(final["control_epoch"], stream_epoch + 1)

    def test_late_provider_event_after_restart_is_not_authoritative(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "memory.db"
            provider = BlockingLateProvider()
            rt1, _engine1, _store1 = self.make_runtime(db, provider)
            ctx = self.ctx()
            stream_thread, holder = self.start_blocked_stream(rt1, provider, ctx)
            rt2, _engine2, _store2 = self.make_runtime(db, DeterministicConversationProvider(("unused",)))
            canceled = rt2.cancel_response(ctx, "conv-c1")
            self.assertEqual(canceled.state, ConversationState.CANCELED)
            self.release_and_join(provider, stream_thread, holder)
            final = rt2.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.CANCELED.value)
            self.assertEqual(final["generated_text"], "Alpha ")

    def test_duplicate_terminal_event_is_rejected_or_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            provider = BlockingLateProvider([
                {"event_type": ResponseStreamEventType.RESPONSE_COMPLETED},
                {"event_type": ResponseStreamEventType.RESPONSE_COMPLETED},
            ])
            rt, _engine, store = self.make_runtime(Path(td) / "memory.db", provider)
            ctx = self.ctx()
            thread, holder = self.start_blocked_stream(rt, provider, ctx)
            result = self.release_and_join(provider, thread, holder)
            self.assertEqual(result.state, ConversationState.COMPLETED)
            final = rt.store.latest_response(ctx.owner_id, "conv-c1")
            self.assertEqual(final["generation_state"], GenerationState.COMPLETED.value)
            self.assertEqual(len([s for s in store.list_sources_for_conversation(ctx.owner_id, "conv-c1") if s.role == "assistant"]), 1)


if __name__ == "__main__":
    unittest.main()
