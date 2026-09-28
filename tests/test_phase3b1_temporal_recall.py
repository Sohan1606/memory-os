from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from datetime import datetime, timezone
import tempfile
import unittest

from zroq.domain_contracts import EgressPolicy, PrivacyClass, ProviderTrustClass
from zroq.personal_continuity import (
    AdapterOutcome,
    ContinuityQuery,
    DocumentedMemoryOSAdapter,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityEngine,
    PersonalContinuityStore,
    RetrievalModeName,
)


def dt(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


class Phase3B1TemporalRecallCorrectnessTests(unittest.TestCase):
    def make_engine(self, db_path, owner="owner-1"):
        store = PersonalContinuityStore(db_path)
        adapter = DocumentedMemoryOSAdapter(
            store,
            MemoryCapturePolicyEngine.default_retain(owner),
            memoryos_available=True,
            real_memoryos_verified=False,
        )
        return PersonalContinuityEngine(store, adapter), store, adapter

    def ctx(self, owner="owner-1", timezone_name="UTC"):
        return MemoryAccessContext(
            owner_id=owner,
            principal_id=f"principal-{owner}",
            authenticated_owner_id=owner,
            purpose="phase3b1 temporal recall test",
            provider_trust_class=ProviderTrustClass.LOCAL_ONLY,
            egress_policy=EgressPolicy.NO_EGRESS,
            owner_calendar_timezone=timezone_name,
        )

    def start(self, engine, context, conversation_id="conv-time"):
        engine.start_conversation(
            context,
            conversation_id=conversation_id,
            started_at=dt(2026, 9, 26, 18),
            title="timezone boundary",
            topic="calendar recall",
            privacy_class=PrivacyClass.PERSONAL,
        )

    def msg(self, engine, context, *, conversation_id="conv-time", message_id, content, event_time, local_display_time="", timezone_name="UTC", offset=0, sequence=1):
        return engine.record_message(
            context,
            conversation_id=conversation_id,
            role="user",
            content=content,
            sequence=sequence,
            event_time=event_time,
            message_id=message_id,
            local_display_time=local_display_time,
            timezone_name=timezone_name,
            utc_offset_minutes=offset,
        )[1]

    def test_date_only_query_uses_owner_calendar_timezone_for_kolkata_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="Asia/Kolkata")
            self.start(engine, context)
            source = self.msg(
                engine,
                context,
                message_id="kolkata-0030",
                content="Kolkata boundary message on local 27 September 2026.",
                event_time=dt(2026, 9, 26, 19, 0),
                local_display_time="2026-09-27 00:30",
                timezone_name="Asia/Kolkata",
                offset=330,
            )
            answer = engine.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            self.assertEqual(answer.status, AdapterOutcome.ALLOW)
            self.assertIn(source.source_id, answer.evidence_source_ids)
            self.assertIn("Kolkata boundary", " ".join(r.content for r in answer.exact_source_content))
            self.assertEqual(answer.mode_status["calendar_timezone"], "Asia/Kolkata")

    def test_beginning_and_end_of_local_day_are_included_but_neighbors_are_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="Asia/Kolkata")
            self.start(engine, context)
            self.msg(engine, context, message_id="before", content="outside before local day", event_time=dt(2026, 9, 26, 18, 29, 59), timezone_name="Asia/Kolkata", offset=330, sequence=1)
            start = self.msg(engine, context, message_id="start", content="included at local day start", event_time=dt(2026, 9, 26, 18, 30, 0), timezone_name="Asia/Kolkata", offset=330, sequence=2)
            end = self.msg(engine, context, message_id="end", content="included at local day end", event_time=dt(2026, 9, 27, 18, 29, 59), timezone_name="Asia/Kolkata", offset=330, sequence=3)
            self.msg(engine, context, message_id="after", content="outside after local day", event_time=dt(2026, 9, 27, 18, 30, 0), timezone_name="Asia/Kolkata", offset=330, sequence=4)
            answer = engine.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            ids = {r.source_id for r in answer.exact_source_content}
            self.assertEqual(ids, {start.source_id, end.source_id})

    def test_utc_positive_offset_calendar_day(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="Asia/Tokyo")
            self.start(engine, context)
            source = self.msg(engine, context, message_id="tokyo", content="Tokyo local 27 September message.", event_time=dt(2026, 9, 26, 15, 30), local_display_time="2026-09-27 00:30", timezone_name="Asia/Tokyo", offset=540)
            answer = engine.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            self.assertIn(source.source_id, answer.evidence_source_ids)

    def test_utc_negative_offset_calendar_day(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="America/Los_Angeles")
            self.start(engine, context)
            source = self.msg(engine, context, message_id="la", content="Los Angeles local 27 September message.", event_time=dt(2026, 9, 27, 7, 30), local_display_time="2026-09-27 00:30", timezone_name="America/Los_Angeles", offset=-420)
            answer = engine.answer_historical_query(context, "What did we talk about on 27 September 2026?")
            self.assertIn(source.source_id, answer.evidence_source_ids)

    def test_dst_observing_timezone_uses_zoneinfo_day_boundaries(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="America/New_York")
            engine.start_conversation(context, conversation_id="conv-dst", started_at=dt(2026, 11, 1, 4), title="DST", topic="DST day")
            early = self.msg(engine, context, conversation_id="conv-dst", message_id="ny-early", content="New York DST day early message.", event_time=dt(2026, 11, 1, 4, 30), local_display_time="2026-11-01 00:30", timezone_name="America/New_York", offset=-240, sequence=1)
            late = self.msg(engine, context, conversation_id="conv-dst", message_id="ny-late", content="New York DST day late message.", event_time=dt(2026, 11, 2, 4, 30), local_display_time="2026-11-01 23:30", timezone_name="America/New_York", offset=-300, sequence=2)
            answer = engine.answer_historical_query(context, "What did we talk about on 1 November 2026?")
            self.assertEqual({r.source_id for r in answer.exact_source_content}, {early.source_id, late.source_id})

    def test_exact_timestamp_query_remains_absolute_utc(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="Asia/Kolkata")
            self.start(engine, context)
            source = self.msg(engine, context, message_id="exact", content="Exact timestamp evidence.", event_time=dt(2026, 9, 26, 19, 0), local_display_time="2026-09-27 00:30", timezone_name="Asia/Kolkata", offset=330)
            response = engine.retrieve(context, ContinuityQuery("2026-09-26T19:00:00Z", modes=(RetrievalModeName.EXACT, RetrievalModeName.TEMPORAL), limit=10))
            self.assertIn(source.source_id, {r.source_id for r in response.source_records})

    def test_date_range_query_uses_calendar_timezone(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="Asia/Kolkata")
            self.start(engine, context)
            day27 = self.msg(engine, context, message_id="d27", content="local day 27 evidence", event_time=dt(2026, 9, 26, 19, 0), timezone_name="Asia/Kolkata", offset=330, sequence=1)
            day28 = self.msg(engine, context, message_id="d28", content="local day 28 evidence", event_time=dt(2026, 9, 27, 19, 0), timezone_name="Asia/Kolkata", offset=330, sequence=2)
            self.msg(engine, context, message_id="d29", content="local day 29 excluded", event_time=dt(2026, 9, 28, 19, 0), timezone_name="Asia/Kolkata", offset=330, sequence=3)
            response = engine.retrieve(context, ContinuityQuery("from 27 September 2026 to 28 September 2026", modes=(RetrievalModeName.EXACT, RetrievalModeName.TEMPORAL), limit=10))
            self.assertEqual({r.source_id for r in response.source_records}, {day27.source_id, day28.source_id})

    def test_query_timezone_override_beats_owner_default(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name="UTC")
            self.start(engine, context)
            source = self.msg(engine, context, message_id="override", content="override timezone evidence", event_time=dt(2026, 9, 26, 19, 0), local_display_time="2026-09-27 00:30", timezone_name="Asia/Kolkata", offset=330)
            response = engine.retrieve(context, ContinuityQuery("27 September 2026", modes=(RetrievalModeName.EXACT, RetrievalModeName.TEMPORAL), calendar_timezone="Asia/Kolkata", limit=10))
            self.assertIn(source.source_id, {r.source_id for r in response.source_records})
            self.assertEqual(response.mode_status["calendar_timezone"], "Asia/Kolkata")
            self.assertEqual(response.mode_status["calendar_timezone_source"], "query_override")

    def test_unknown_owner_timezone_falls_back_to_utc_truthfully(self):
        with tempfile.TemporaryDirectory() as td:
            engine, _store, _adapter = self.make_engine(Path(td) / "memory.db")
            context = self.ctx(timezone_name=None)
            self.start(engine, context)
            self.msg(engine, context, message_id="fallback", content="fallback UTC should not treat this as local 27th", event_time=dt(2026, 9, 26, 19, 0), local_display_time="2026-09-27 00:30", timezone_name="Asia/Kolkata", offset=330)
            response = engine.retrieve(context, ContinuityQuery("27 September 2026", modes=(RetrievalModeName.EXACT, RetrievalModeName.TEMPORAL), limit=10))
            self.assertEqual(response.source_records, ())
            self.assertEqual(response.mode_status["calendar_timezone"], "UTC")
            self.assertEqual(response.mode_status["calendar_timezone_source"], "utc_fallback")


if __name__ == "__main__":
    unittest.main()
