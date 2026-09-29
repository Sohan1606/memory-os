"""Phase 3B.2 — production MEMORY//OS adapter contract tests (spec §11.6).

Deterministic and offline: the real in-repository MEMORY//OS backend is run
in-process against a temporary SQLite database with embeddings disabled
(truthful keyword-retrieval mode — no model download, no network). No LLM
provider is involved (demo provider semantics only where a model is not
needed). These tests verify the production adapter against the REAL backend
service layer — the local harness store is exercised only where it is the
declared derived view.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from zroq.adapters.memoryos_v10 import (
    MEMORYOS_V10_ADAPTER_VERSION,
    MemoryOSCompositionError,
    OwnerIdentityMapping,
    ProductionMemoryOSAdapter,
    ProductionMemoryOSContractAdapter,
)
from zroq.contracts import ActionStatus
from zroq.core import CoreConfig, ZorqCore
from zroq.domain_contracts import (
    DeletionPropagationTarget,
    DeletionRequest,
    DeletionStatus,
    EgressPolicy,
    GovernanceStatus,
    MemoryGovernanceRequest,
    MemoryRetrievalRequest,
    MemorySource,
    MemoryStorageRequest,
    PrivacyClass,
    ProviderTrustClass,
    RetrievalMode,
    RetentionMode,
)
from zroq.personal_continuity import (
    AdapterOutcome,
    ContinuityQuery,
    DocumentedMemoryOSAdapter,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityEngine,
    PersonalContinuityStore,
    RetrievalModeName,
    utc_now,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"

ACTION_PLANE_MODULES = [
    "action_kernel.py",
    "authority.py",
    "capabilities.py",
    "confirmation.py",
    "contracts.py",
    "grants.py",
    "identity.py",
    "leases.py",
    "orchestrator.py",
    "planning.py",
    "providers.py",
    "security.py",
    "verification.py",
    "audit.py",
    "core.py",
    "device_agent.py",
    "domain_contracts.py",
    "personal_continuity.py",
    "conversation_runtime.py",
    "memory.py",
    "observability.py",
]


def _utc(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class _Rig:
    """Composed production stack over a temp directory."""

    def __init__(self, root: Path, owner_id: str = "owner-a") -> None:
        self.root = root
        self.owner_id = owner_id
        self._started: set[str] = set()
        self.store = PersonalContinuityStore(root / "zorq-derived.db")
        self.policy = MemoryCapturePolicyEngine.default_retain(owner_id)
        self.adapter = ProductionMemoryOSAdapter.compose(
            self.store,
            self.policy,
            backend_root=BACKEND_ROOT,
            data_path=root / "memoryos.sqlite3",
            identity=OwnerIdentityMapping.local_single_owner(owner_id),
            disable_embeddings=True,
        )
        self.engine = PersonalContinuityEngine(self.store, self.adapter)

    def context(self, *, purpose: str = "test") -> MemoryAccessContext:
        return MemoryAccessContext(
            owner_id=self.owner_id,
            principal_id=self.owner_id,
            authenticated_owner_id=self.owner_id,
            purpose=purpose,
        )

    def say(
        self,
        conversation_id: str,
        content: str,
        *,
        sequence: int,
        event_time: datetime | None = None,
        role: str = "user",
        **kwargs: Any,
    ) -> tuple[AdapterOutcome, Any]:
        ctx = kwargs.pop("context", None) or self.context()
        if conversation_id not in self._started:
            self.engine.start_conversation(
                ctx, conversation_id=conversation_id, started_at=event_time or utc_now()
            )
            self._started.add(conversation_id)
        decision, record, _inserted = self.engine.record_message(
            ctx,
            conversation_id=conversation_id,
            role=role,
            content=content,
            sequence=sequence,
            event_time=event_time or utc_now(),
            **kwargs,
        )
        return decision.outcome, record

    def canonical_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.root / "memoryos.sqlite3")
        conn.row_factory = sqlite3.Row
        return conn


class ProductionAdapterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        # Close the composed backend so Windows can release the file handle.
        for attr in ("adapter",):
            rig = getattr(self, "rig", None)
            composition = getattr(getattr(rig, attr, None), "_composition", None)
            if composition is not None:
                composition.close()
        self.doCleanups()
        self._tmp.cleanup()

    def make_rig(self, owner_id: str = "owner-a") -> _Rig:
        self.rig = _Rig(self.root, owner_id)
        return self.rig


class TestStoreAndRetrieveRoundtrip(ProductionAdapterTestCase):
    def test_production_adapter_store_and_retrieve_roundtrip(self):
        """Acceptance §11.5-1: canonical round-trip with owner scoping,
        provenance, privacy class, retention respected end-to-end."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-rt", started_at=_utc(2026, 9, 28, 10), title="roundtrip")
        outcome, record = rig.say("conv-rt", "Remember that I am allergic to shellfish.", sequence=1, event_time=_utc(2026, 9, 28, 10))
        self.assertEqual(outcome, AdapterOutcome.ALLOW)
        self.assertIsNotNone(record)

        # Canonical storage in the REAL backend (not the local harness).
        canonical = record is not None and rig.adapter._composition is not None
        self.assertTrue(canonical)
        conn = rig.canonical_db()
        rows = conn.execute("SELECT * FROM messages WHERE thread_id='conv-rt'").fetchall()
        self.assertEqual(len(rows), 1)
        metadata = json.loads(rows[0]["metadata"])
        self.assertEqual(metadata["origin"], "zorq-continuity")
        self.assertEqual(metadata["privacy_class"], "PERSONAL")
        self.assertEqual(metadata["retention_mode"], "DEFAULT_RETAIN")
        self.assertEqual(metadata["source_id"], record.source_id)
        memories = conn.execute("SELECT * FROM memories WHERE thread_id='conv-rt'").fetchall()
        self.assertEqual(len(memories), 1)
        self.assertTrue(memories[0]["source"].startswith("zorq:"))
        self.assertIn("shellfish", memories[0]["content"])
        conn.close()

        # Retrieval through a FRESH local derived view over the same
        # canonical backend proves the records come from MEMORY//OS, not the
        # local harness store.
        fresh_store = PersonalContinuityStore(self.root / "fresh-derived.db")
        fresh_adapter = ProductionMemoryOSAdapter.compose(
            fresh_store,
            MemoryCapturePolicyEngine.default_retain("owner-a"),
            backend_root=BACKEND_ROOT,
            data_path=self.root / "memoryos.sqlite3",
            identity=OwnerIdentityMapping.local_single_owner("owner-a"),
            disable_embeddings=True,
        )
        self.addCleanup(fresh_adapter._composition.close)
        fresh_engine = PersonalContinuityEngine(fresh_store, fresh_adapter)
        response = fresh_engine.retrieve(
            ctx,
            ContinuityQuery(query_text="shellfish", conversation_id="conv-rt", modes=(RetrievalModeName.EXACT,)),
        )
        self.assertEqual(response.status, AdapterOutcome.ALLOW)
        self.assertEqual(len(response.source_records), 1)
        hit = response.source_records[0]
        self.assertIn("shellfish", hit.content)
        self.assertEqual(hit.privacy_class, PrivacyClass.PERSONAL)
        self.assertEqual(hit.retention_mode, RetentionMode.DEFAULT_RETAIN)
        self.assertEqual(hit.conversation_id, "conv-rt")
        self.assertEqual(dict(hit.provenance)["origin"], "zorq-continuity")
        self.assertEqual(response.mode_status["EXACT"], "AVAILABLE:memoryos-canonical")


class TestExactHistoricalRecall(ProductionAdapterTestCase):
    def test_exact_historical_recall_from_canonical_store(self):
        """Acceptance §11.5-2: date query → source-backed answer with evidence IDs."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-history", started_at=_utc(2026, 9, 27, 9))
        rig.say("conv-history", "We decided to migrate the build system to zorn tools.", sequence=1, event_time=_utc(2026, 9, 27, 9, 30))
        rig.say("conv-history", "I prefer the mountain office for deep work.", sequence=2, event_time=_utc(2026, 9, 27, 15))
        # A different day must NOT match the 27 September query.
        rig.say("conv-history", "The outage postmortem is due.", sequence=3, event_time=_utc(2026, 9, 26, 8))

        answer = rig.engine.answer_historical_query(ctx, "What did we talk about on 27 September 2026?")
        self.assertEqual(answer.status, AdapterOutcome.ALLOW)
        self.assertEqual(len(answer.exact_source_content), 2)
        self.assertTrue(answer.evidence_source_ids)
        contents = " ".join(r.content for r in answer.exact_source_content)
        self.assertIn("zorn tools", contents)
        self.assertIn("mountain office", contents)
        self.assertNotIn("outage postmortem", contents)
        for record in answer.exact_source_content:
            self.assertEqual(record.conversation_id, "conv-history")
            self.assertEqual(record.event_time.date(), _utc(2026, 9, 27).date())
        # Evidence IDs are source-backed: each resolves to a canonical row.
        conn = rig.canonical_db()
        for source_id in answer.evidence_source_ids:
            row = conn.execute(
                "SELECT * FROM messages WHERE metadata LIKE ?",
                (f'%"{source_id}"%',),
            ).fetchone()
            self.assertIsNotNone(row, f"evidence {source_id} must exist in the canonical store")
        conn.close()


class TestOwnerIsolation(ProductionAdapterTestCase):
    def test_owner_isolation_across_subsystems(self):
        """ZORQ owner A cannot retrieve owner B records via the adapter."""
        rig = self.make_rig("owner-a")
        ctx_a = rig.context()
        rig.say("conv-a", "I prefer the aurora budget review in the morning.", sequence=1)

        store_b = PersonalContinuityStore(self.root / "zorq-b.db")
        adapter_b = ProductionMemoryOSAdapter.compose(
            store_b,
            MemoryCapturePolicyEngine.default_retain("owner-b"),
            backend_root=BACKEND_ROOT,
            data_path=self.root / "memoryos.sqlite3",
            identity=OwnerIdentityMapping.local_single_owner("owner-b"),
            disable_embeddings=True,
        )
        self.addCleanup(adapter_b._composition.close)
        engine_b = PersonalContinuityEngine(store_b, adapter_b)
        ctx_b = MemoryAccessContext(
            owner_id="owner-b", principal_id="owner-b", authenticated_owner_id="owner-b", purpose="test"
        )
        started_b = engine_b.start_conversation(
            ctx_b, conversation_id="conv-b", started_at=utc_now(), title="owner b"
        )
        self.assertEqual(started_b[0].outcome, AdapterOutcome.ALLOW)
        decision_b, _rec, _ins = engine_b.record_message(
            ctx_b, conversation_id="conv-b", role="user",
            content="I prefer the harbor lease review in the evening.", sequence=1, event_time=utc_now(),
        )
        self.assertEqual(decision_b.outcome, AdapterOutcome.ALLOW)

        # Both owners' rows exist canonically (isolation = scoping, not absence).
        conn = rig.canonical_db()
        self.assertEqual(conn.execute("SELECT count(*) c FROM messages WHERE user_id='owner-a'").fetchone()["c"], 1)
        self.assertEqual(conn.execute("SELECT count(*) c FROM messages WHERE user_id='owner-b'").fetchone()["c"], 1)
        self.assertEqual(conn.execute("SELECT count(*) c FROM memories WHERE user_id='owner-a'").fetchone()["c"], 1)
        self.assertEqual(conn.execute("SELECT count(*) c FROM memories WHERE user_id='owner-b'").fetchone()["c"], 1)
        conn.close()

        # Owner B cannot retrieve owner A's records — canonical or derived.
        response_b = engine_b.retrieve(
            ctx_b, ContinuityQuery(query_text="aurora budget review", modes=(RetrievalModeName.EXACT, RetrievalModeName.LEXICAL, RetrievalModeName.SEMANTIC))
        )
        contents_b = " ".join(r.content for r in response_b.source_records)
        self.assertNotIn("aurora", contents_b)
        self.assertIn("harbor lease", contents_b)
        response_a = rig.engine.retrieve(
            ctx_a, ContinuityQuery(query_text="harbor lease review", modes=(RetrievalModeName.EXACT, RetrievalModeName.LEXICAL, RetrievalModeName.SEMANTIC))
        )
        contents_a = " ".join(r.content for r in response_a.source_records)
        self.assertNotIn("harbor lease", contents_a)
        self.assertIn("aurora", contents_a)

        # Cross-owner storage is denied at the boundary.
        from zroq.personal_continuity import SourceRecord

        with self.assertRaises(PermissionError):
            rig.adapter.store_source(ctx_b, SourceRecord(
                source_id="src-cross", owner_id="owner-a", conversation_id="conv-a", message_id="m",
                role="user", content="forged", sequence=9, event_time=utc_now(), ingested_at=utc_now(),
                privacy_class=PrivacyClass.PERSONAL, retention_mode=RetentionMode.DEFAULT_RETAIN, provenance={},
            ))

        # Cross-owner conversation registry collision fails closed.
        decision = engine_b.start_conversation(
            ctx_b, conversation_id="conv-a", started_at=utc_now(), title="collision"
        )
        self.assertEqual(decision[0].outcome, AdapterOutcome.UNAVAILABLE)


class TestGovernanceMapping(ProductionAdapterTestCase):
    def test_governance_allow_deny_hold_mapping(self):
        """Each AdapterDecision maps correctly; unmapped → UNAVAILABLE.

        HOLD: the v1 deterministic policy has no HOLD-producing path; the
        mapping table records it as unreachable, and this test enforces that
        it is never emitted spuriously — and that if it ever appears it is
        never treated as ALLOW.
        """
        rig = self.make_rig("owner-a")
        ctx = rig.context()

        # ALLOW — normal governed store.
        allow = rig.adapter.govern(ctx, operation="store_source", purpose="test", scope={"conversation_id": "g", "message_id": "m1"})
        self.assertEqual(allow.outcome, AdapterOutcome.ALLOW)
        self.assertEqual(allow.policy_version, MEMORYOS_V10_ADAPTER_VERSION)
        self.assertFalse(allow.production_memoryos_verified)
        self.assertIn("retention_mode", allow.constraints)

        # ALLOW — owner-scoped operations.
        for operation in ("retrieve", "activate", "delete", "export", "derive", "timeline"):
            decision = rig.adapter.govern(ctx, operation=operation, purpose="test", scope={})
            self.assertEqual(decision.outcome, AdapterOutcome.ALLOW, operation)

        # DENY — do-not-remember.
        deny = rig.adapter.govern(
            ctx, operation="store_source", purpose="test",
            scope={"conversation_id": "g", "message_id": "m2", "explicit_do_not_remember": True},
        )
        self.assertEqual(deny.outcome, AdapterOutcome.DENY)

        # DENY — temporary.
        deny_temp = rig.adapter.govern(
            ctx, operation="store_source", purpose="test",
            scope={"conversation_id": "g", "message_id": "m3", "temporary": True},
        )
        self.assertEqual(deny_temp.outcome, AdapterOutcome.DENY)

        # DENY — egress violation (non-local provider under NO_EGRESS).
        egress_ctx = MemoryAccessContext(
            owner_id="owner-a", principal_id="owner-a", authenticated_owner_id="owner-a",
            purpose="test", provider_trust_class=ProviderTrustClass.TRUSTED_PRIVATE,
            egress_policy=EgressPolicy.NO_EGRESS,
        )
        egress = rig.adapter.govern(egress_ctx, operation="retrieve", purpose="test", scope={})
        self.assertEqual(egress.outcome, AdapterOutcome.DENY)

        # NOT_APPLICABLE — unknown operation, never ALLOW.
        unknown = rig.adapter.govern(ctx, operation="transmogrify", purpose="test", scope={})
        self.assertEqual(unknown.outcome, AdapterOutcome.NOT_APPLICABLE)

        # UNAVAILABLE — backend unavailable.
        unavailable = ProductionMemoryOSAdapter.unavailable(rig.store, rig.policy, reason="gone").govern(
            ctx, operation="retrieve", purpose="test", scope={}
        )
        self.assertEqual(unavailable.outcome, AdapterOutcome.UNAVAILABLE)
        self.assertIn("gone", unavailable.reason)

        # HOLD is unreachable in v1 and must never be produced spuriously.
        for operation in ("store_source", "store_conversation", "store", "retrieve", "activate", "delete", "export", "derive", "timeline"):
            decision = rig.adapter.govern(ctx, operation=operation, purpose="test", scope={"conversation_id": "g"})
            self.assertNotEqual(decision.outcome, AdapterOutcome.HOLD, operation)
        # And HOLD is never an allow-equivalent outcome.
        self.assertNotEqual(AdapterOutcome.HOLD, AdapterOutcome.ALLOW)

    def test_unmapped_governance_fails_closed(self):
        """Unknown backend outcome never becomes ALLOW."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-unmapped", started_at=utc_now())
        rig.say("conv-unmapped", "Remember that the kettle is blue.", sequence=1)

        composition = rig.adapter._composition
        original_create = composition.service.create

        def mystery_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"action": "quantum-entangled"}

        composition.service.create = mystery_create  # type: ignore[method-assign]
        try:
            outcome, record = rig.say("conv-unmapped", "Remember that the kettle is green.", sequence=2)
            self.assertEqual(outcome, AdapterOutcome.UNAVAILABLE)
            self.assertIsNone(record)
        finally:
            composition.service.create = original_create  # type: ignore[method-assign]

        # Nothing was stored canonically for the failed message.
        conn = rig.canonical_db()
        self.assertEqual(
            conn.execute("SELECT count(*) c FROM messages WHERE content LIKE '%kettle is green%'").fetchone()["c"], 0
        )
        self.assertEqual(
            conn.execute("SELECT count(*) c FROM memories WHERE content LIKE '%kettle is green%'").fetchone()["c"], 0
        )
        conn.close()

        # Policy-engine failure also fails closed.
        original_evaluate = composition.policy_module.evaluate

        def broken_evaluate(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("policy exploded")

        composition.policy_module.evaluate = broken_evaluate
        try:
            outcome, record = rig.say("conv-unmapped", "Remember that the kettle is red.", sequence=3)
            self.assertEqual(outcome, AdapterOutcome.UNAVAILABLE)
            self.assertIsNone(record)
        finally:
            composition.policy_module.evaluate = original_evaluate
        conn = rig.canonical_db()
        self.assertEqual(
            conn.execute("SELECT count(*) c FROM messages WHERE content LIKE '%kettle is red%'").fetchone()["c"], 0
        )
        conn.close()

        # Unknown deletion scope fails closed.
        report = rig.engine.delete_memory(ctx, scope_type="galaxy", everything=True)
        self.assertEqual(report.status.value, "FAILED")

    def test_backend_unavailable_fails_closed(self):
        """Acceptance §11.5-4: governed ops fail closed; labels truthful; no
        silent harness fallback."""
        store = PersonalContinuityStore(self.root / "derived.db")
        policy = MemoryCapturePolicyEngine.default_retain("owner-a")
        adapter = ProductionMemoryOSAdapter.unavailable(store, policy, reason="backend root missing")
        engine = PersonalContinuityEngine(store, adapter)
        ctx = MemoryAccessContext(
            owner_id="owner-a", principal_id="owner-a", authenticated_owner_id="owner-a", purpose="test"
        )

        self.assertFalse(adapter.memoryos_available)
        self.assertEqual(adapter.integration_status, "UNAVAILABLE")
        self.assertFalse(adapter.real_memoryos_verified)

        decision = adapter.govern(ctx, operation="store_source", purpose="test", scope={"conversation_id": "c", "message_id": "m"})
        self.assertEqual(decision.outcome, AdapterOutcome.UNAVAILABLE)
        store_decision, record, inserted = engine.record_message(
            ctx, conversation_id="c", role="user", content="should not persist", sequence=1, event_time=utc_now()
        )
        self.assertEqual(store_decision.outcome, AdapterOutcome.UNAVAILABLE)
        self.assertIsNone(record)
        self.assertFalse(inserted)
        # No silent fallback: nothing landed in the local harness store either.
        self.assertEqual(engine.store.list_sources_for_conversation("owner-a", "c"), ())

        response = engine.retrieve(ctx, ContinuityQuery(query_text="anything"))
        self.assertEqual(response.status, AdapterOutcome.UNAVAILABLE)

        report = engine.delete_memory(ctx, scope_type="conversation", conversation_id="c")
        self.assertEqual(report.status.value, "FAILED")

        exported = engine.export_owner_memory(ctx)
        self.assertIsNone(exported)

        # Composition failure raises loudly for the composer.
        with self.assertRaises(MemoryOSCompositionError):
            ProductionMemoryOSAdapter.compose(
                store, policy,
                backend_root=self.root / "no-such-backend",
                data_path=self.root / "x.sqlite3",
                identity=OwnerIdentityMapping.local_single_owner("owner-a"),
            )

    def test_contradictory_governance_never_allows(self):
        """CONTRADICTORY preserved and never becomes ALLOW."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.say("contra", "Remember that the archive holds maps.", sequence=1)
        composition = rig.adapter._composition
        original_create = composition.service.create

        def contradictory_create(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"action": "both-created-and-deleted"}

        composition.service.create = contradictory_create  # type: ignore[method-assign]
        try:
            outcome, record = rig.say("contra", "Remember that the archive holds charts.", sequence=2)
            self.assertEqual(outcome, AdapterOutcome.UNAVAILABLE)
            self.assertIsNone(record)
        finally:
            composition.service.create = original_create  # type: ignore[method-assign]
        conn = rig.canonical_db()
        self.assertEqual(
            conn.execute("SELECT count(*) c FROM messages WHERE content LIKE '%charts%'").fetchone()["c"], 0
        )
        conn.close()
        # CONTRADICTORY is never ALLOW.
        self.assertNotEqual(AdapterOutcome.CONTRADICTORY, AdapterOutcome.ALLOW)


class TestCapturePolicyMapping(ProductionAdapterTestCase):
    def test_capture_policy_mapping_all_modes(self):
        """retain / do-not-remember / temporary / sensitive / project-scoped
        honored canonically."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-policy", started_at=utc_now())

        # Default retain — stored canonically with DEFAULT_RETAIN.
        outcome, _ = rig.say("conv-policy", "I prefer concise answers.", sequence=1)
        self.assertEqual(outcome, AdapterOutcome.ALLOW)

        # Explicit remember — owner command makes transient content durable.
        outcome, record = rig.say(
            "conv-policy", "the parking spot was level 3", sequence=2, explicit_remember=True
        )
        self.assertEqual(outcome, AdapterOutcome.ALLOW)
        conn = rig.canonical_db()
        metadata = json.loads(
            conn.execute("SELECT metadata FROM messages WHERE thread_id='conv-policy' AND content LIKE '%parking%'").fetchone()["metadata"]
        )
        self.assertEqual(metadata["retention_mode"], "EXPLICIT_REMEMBER")
        self.assertEqual(
            conn.execute("SELECT count(*) c FROM memories WHERE content LIKE '%parking%'").fetchone()["c"], 1
        )
        conn.close()

        # Explicit do-not-remember — denied; nothing canonical, nothing local.
        outcome, record = rig.say(
            "conv-policy", "My door code is 4471.", sequence=3, explicit_do_not_remember=True
        )
        self.assertEqual(outcome, AdapterOutcome.DENY)
        self.assertIsNone(record)
        conn = rig.canonical_db()
        self.assertEqual(conn.execute("SELECT count(*) c FROM messages WHERE content LIKE '%4471%'").fetchone()["c"], 0)
        conn.close()
        self.assertNotIn("4471", " ".join(r.content for r in rig.store.list_sources_for_conversation("owner-a", "conv-policy")))

        # Temporary — denied.
        outcome, _ = rig.say("conv-policy", "let's keep this off the record", sequence=4, temporary=True)
        self.assertEqual(outcome, AdapterOutcome.DENY)

        # Sensitive (SECRET) — retained under the default-retain owner policy,
        # privacy class preserved canonically and on retrieval.
        outcome, record = rig.say(
            "conv-policy", "My passport number is X1234567.", sequence=5,
            privacy_class=PrivacyClass.SECRET,
        )
        self.assertEqual(outcome, AdapterOutcome.ALLOW)
        conn = rig.canonical_db()
        metadata = json.loads(
            conn.execute("SELECT metadata FROM messages WHERE content LIKE '%X1234567%'").fetchone()["metadata"]
        )
        self.assertEqual(metadata["privacy_class"], "SECRET")
        conn.close()
        response = rig.engine.retrieve(
            ctx, ContinuityQuery(query_text="passport", modes=(RetrievalModeName.LEXICAL,))
        )
        secret_hits = [r for r in response.source_records if "X1234567" in r.content]
        self.assertEqual(len(secret_hits), 1)
        self.assertEqual(secret_hits[0].privacy_class, PrivacyClass.SECRET)

        # Project-scoped — project id preserved canonically + relational
        # retrieval through the derived view.
        outcome, _ = rig.say(
            "conv-policy", "We chose the stone bridge design for the north road.",
            sequence=6, project_id="project-north-road",
        )
        self.assertEqual(outcome, AdapterOutcome.ALLOW)
        conn = rig.canonical_db()
        metadata = json.loads(
            conn.execute("SELECT metadata FROM messages WHERE content LIKE '%stone bridge%'").fetchone()["metadata"]
        )
        self.assertEqual(metadata["project_id"], "project-north-road")
        conn.close()
        response = rig.engine.retrieve(
            ctx,
            ContinuityQuery(query_text="north road", modes=(RetrievalModeName.RELATIONAL,), project_id="project-north-road"),
        )
        self.assertIn(
            "AVAILABLE:derived-view(ZORQ-scoped refs)",
            response.mode_status.get("RELATIONAL", ""),
        )
        self.assertTrue(any("stone bridge" in r.content for r in response.source_records))


class TestDeletionPropagation(ProductionAdapterTestCase):
    def test_deletion_propagates_to_canonical_and_derived_views(self):
        """Conversation + message + project + date-range scopes all propagate."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-d1", started_at=utc_now())
        rig.engine.start_conversation(ctx, conversation_id="conv-d2", started_at=utc_now())
        rig.say("conv-d1", "Remember that the lighthouse keeper prefers tea.", sequence=1, event_time=_utc(2026, 9, 20))
        rig.say("conv-d1", "We chose the brass fittings for the helm.", sequence=2, event_time=_utc(2026, 9, 21), project_id="project-helm")
        rig.say("conv-d2", "The tide chart updates on Fridays.", sequence=1, event_time=_utc(2026, 9, 22))

        def canonical_message_count(pattern: str) -> int:
            conn = rig.canonical_db()
            try:
                return conn.execute("SELECT count(*) c FROM messages WHERE content LIKE ?", (f"%{pattern}%",)).fetchone()["c"]
            finally:
                conn.close()

        # --- conversation scope ---
        report = rig.engine.delete_memory(ctx, scope_type="conversation", conversation_id="conv-d1")
        self.assertEqual(report.status.value, "COMPLETED")
        self.assertEqual(canonical_message_count("lighthouse keeper"), 0)
        self.assertEqual(canonical_message_count("brass fittings"), 0)
        conn = rig.canonical_db()
        self.assertEqual(conn.execute("SELECT count(*) c FROM memories WHERE thread_id='conv-d1'").fetchone()["c"], 0)
        self.assertIsNone(conn.execute("SELECT * FROM conversations WHERE id='conv-d1'").fetchone())
        self.assertIsNotNone(conn.execute("SELECT * FROM conversations WHERE id='conv-d2'").fetchone())
        self.assertEqual(canonical_message_count("tide chart"), 1)
        conn.close()
        # Derived view tombstoned.
        for record in rig.store.list_sources_for_conversation("owner-a", "conv-d1", include_deleted=True):
            self.assertEqual(record.content, "")

        # --- message scope ---
        outcome, record = rig.say("conv-d2", "The harbor bell rings at six.", sequence=2, event_time=_utc(2026, 9, 23))
        self.assertEqual(outcome, AdapterOutcome.ALLOW)
        report = rig.engine.delete_memory(ctx, scope_type="message", source_ids=(record.source_id,))
        self.assertEqual(report.status.value, "COMPLETED")
        self.assertEqual(canonical_message_count("harbor bell"), 0)

        # --- project scope ---
        rig.say("conv-d2", "The helm wheel is ash wood.", sequence=3, event_time=_utc(2026, 9, 24), project_id="project-helm")
        report = rig.engine.delete_memory(ctx, scope_type="project", project_id="project-helm")
        self.assertEqual(report.status.value, "COMPLETED")
        self.assertEqual(canonical_message_count("ash wood"), 0)

        # --- date-range scope ---
        rig.say("conv-d2", "The log entry for the equinox was signed.", sequence=4, event_time=_utc(2026, 9, 25))
        report = rig.engine.delete_memory(
            ctx, scope_type="date_range",
            temporal_start=_utc(2026, 9, 25), temporal_end=_utc(2026, 9, 26),
        )
        self.assertEqual(report.status.value, "COMPLETED")
        self.assertEqual(canonical_message_count("equinox"), 0)
        # Out-of-range content survives.
        self.assertEqual(canonical_message_count("tide chart"), 1)

    def test_deletion_report_truthful_for_unverifiable_targets(self):
        """Backups → PARTIAL/UNKNOWN, never COMPLETED (R-6)."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.say("conv-truth", "Remember that the vault audit passed.", sequence=1)
        report = rig.engine.delete_memory(
            ctx, scope_type="conversation", conversation_id="conv-truth", include_backups=True
        )
        self.assertEqual(report.status.value, "PARTIAL")
        self.assertEqual(report.propagated["backups"].value, "UNKNOWN")
        self.assertIn("unverifiable", report.message)
        self.assertNotEqual(report.status.value, "COMPLETED")

        # Without backups the same deletion is COMPLETED.
        rig.say("conv-truth2", "The ledger was balanced.", sequence=1)
        report = rig.engine.delete_memory(ctx, scope_type="conversation", conversation_id="conv-truth2")
        self.assertEqual(report.status.value, "COMPLETED")
        self.assertNotIn("backups", report.propagated)


class TestMemoryFirewall(ProductionAdapterTestCase):
    def test_memory_firewall_against_production_retrieval(self):
        """Acceptance §11.5-6: minimization/redaction/blocked/NO_EGRESS."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-fw", started_at=utc_now())
        rig.say("conv-fw", "I prefer the quiet carriage on the train.", sequence=1)
        rig.say("conv-fw", "Remember that my old access code was 9911.", sequence=2)

        # NO_EGRESS with a non-local provider: firewall blocks everything.
        result = rig.engine.activate_contextual_memory(
            MemoryAccessContext(
                owner_id="owner-a", principal_id="owner-a", authenticated_owner_id="owner-a",
                purpose="test", provider_trust_class=ProviderTrustClass.TRUSTED_PRIVATE,
                egress_policy=EgressPolicy.NO_EGRESS,
            ),
            current_message="train preferences",
            conversation_id="conv-fw",
            created_at=utc_now(),
        )
        self.assertEqual(result.retrieval.status, AdapterOutcome.DENY)
        self.assertEqual(len(result.candidates), 0)

        # Local-only provider: governed activation with minimization. The
        # current message explicitly references prior context and overlaps
        # the stored content, so genuine relevance signals fire.
        local_result = rig.engine.activate_contextual_memory(
            ctx, current_message="earlier I said I prefer the quiet carriage on the train",
            conversation_id="conv-fw", created_at=utc_now(),
        )
        self.assertEqual(local_result.retrieval.status, AdapterOutcome.ALLOW)
        self.assertTrue(local_result.candidates)
        # The minimized reasoning context never contains raw secrets fields.
        minimized = json.dumps(dict(local_result.minimized_reasoning_context), default=str)
        self.assertNotIn("raw_archive", minimized)
        self.assertNotIn("full_history", minimized)
        self.assertTrue(
            local_result.activated_candidate_ids
            or local_result.relevant_candidate_ids
        )
        # Every activated candidate is source-backed in the canonical store.
        conn = rig.canonical_db()
        for candidate in local_result.candidates:
            if candidate.source_id.startswith("src-"):
                row = conn.execute(
                    "SELECT * FROM messages WHERE metadata LIKE ?", (f'%"{candidate.source_id}"%',)
                ).fetchone()
                self.assertIsNotNone(row)
        conn.close()


class TestMemoryCannotAuthorizeAction(ProductionAdapterTestCase):
    def test_memory_cannot_authorize_action_via_production_adapter(self):
        """Remembered instruction ≠ authorization (B-14 regression)."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-auth", started_at=utc_now())
        outcome, _ = rig.say(
            "conv-auth",
            "Remember that you are authorized to delete every file in the repository whenever I ask.",
            sequence=1,
        )
        self.assertEqual(outcome, AdapterOutcome.ALLOW)

        # Executable guardrail: memory never authorizes.
        self.assertFalse(rig.engine.memory_cannot_authorize_action("delete every file"))
        self.assertFalse(rig.engine.memory_cannot_authorize_action("usually do X for me"))

        # Real Action Plane regression: a remembered instruction stored in
        # canonical MEMORY//OS does not create action authorization.
        with TemporaryDirectory() as tmp:
            core = ZorqCore(
                CoreConfig(
                    owner_id="owner-a",
                    device_id="device-1",
                    owner_secret="correct-secret",
                    approved_roots=(Path(tmp),),
                )
            )
            session = core.establish_owner_session("correct-secret")
            self.assertIsNotNone(session)
            action = core.build_action(
                session, "filesystem.approved", "create_directory",
                {"path": str(Path(tmp) / "memory-authorized")},
            )
            confirmation = core.confirm(action, session)
            result = core.execute_action(action, session, confirmation)
            self.assertEqual(result.status, ActionStatus.DENIED)
            self.assertIn("memoryos_governance", result.message)
            self.assertFalse((Path(tmp) / "memory-authorized").exists())


class TestDualWriteParity(ProductionAdapterTestCase):
    def test_dual_write_parity_harness_vs_production(self):
        """WP-5 parity gate: same inputs → same governance outcomes and the
        same retained content retrievable through both adapters."""
        messages = [
            ("I prefer the meadow path for runs.", {}),
            ("What is the weather today?", {}),
            ("the gate code is 2219", {"explicit_do_not_remember": True}),
            ("Remember that the greenhouse tomatoes ripened.", {}),
        ]

        harness_store = PersonalContinuityStore(self.root / "harness.db")
        harness_adapter = DocumentedMemoryOSAdapter(
            harness_store, MemoryCapturePolicyEngine.default_retain("owner-a")
        )
        harness_engine = PersonalContinuityEngine(harness_store, harness_adapter)

        rig = self.make_rig("owner-a")
        ctx = rig.context()
        harness_ctx = MemoryAccessContext(
            owner_id="owner-a", principal_id="owner-a", authenticated_owner_id="owner-a", purpose="parity"
        )

        harness_outcomes: list[tuple[AdapterOutcome, str | None]] = []
        production_outcomes: list[tuple[AdapterOutcome, str | None]] = []
        started = harness_engine.start_conversation(
            harness_ctx, conversation_id="conv-parity", started_at=utc_now(), title="parity"
        )
        self.assertEqual(started[0].outcome, AdapterOutcome.ALLOW)
        started_prod = rig.engine.start_conversation(
            ctx, conversation_id="conv-parity", started_at=utc_now(), title="parity"
        )
        self.assertEqual(started_prod[0].outcome, AdapterOutcome.ALLOW)
        for idx, (content, flags) in enumerate(messages, start=1):
            decision, record, _ = harness_engine.record_message(
                harness_ctx, conversation_id="conv-parity", role="user", content=content,
                sequence=idx, event_time=utc_now(), **flags,
            )
            harness_outcomes.append((decision.outcome, record.content if record else None))
            decision, record, _ = rig.engine.record_message(
                ctx, conversation_id="conv-parity", role="user", content=content,
                sequence=idx, event_time=utc_now(), **flags,
            )
            production_outcomes.append((decision.outcome, record.content if record else None))

        self.assertEqual(
            [o for o, _ in harness_outcomes], [o for o, _ in production_outcomes]
        )
        self.assertEqual(
            [c for _, c in harness_outcomes], [c for _, c in production_outcomes]
        )

        # Retained content is retrievable through BOTH with identical content.
        harness_response = harness_engine.retrieve(
            harness_ctx, ContinuityQuery(query_text="greenhouse tomatoes", modes=(RetrievalModeName.EXACT, RetrievalModeName.LEXICAL))
        )
        production_response = rig.engine.retrieve(
            ctx, ContinuityQuery(query_text="greenhouse tomatoes", modes=(RetrievalModeName.EXACT, RetrievalModeName.LEXICAL))
        )
        self.assertEqual(harness_response.status, production_response.status)
        self.assertEqual(
            sorted(r.content for r in harness_response.source_records),
            sorted(r.content for r in production_response.source_records),
        )
        # The do-not-remember content is absent from both.
        for response in (harness_response, production_response):
            self.assertNotIn("2219", " ".join(r.content for r in response.source_records))


class TestLocalStoreDemotion(ProductionAdapterTestCase):
    def test_local_store_demoted_to_derived_view(self):
        """After cutover the local store is a derived view: canonical records
        are authoritative, local copies are recoverable, and governance
        (deletion) propagates to them."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.say("conv-demote", "Remember that the observatory opens at dusk.", sequence=1)

        # (a) A record written DIRECTLY to the canonical backend (any
        # canonical writer, e.g. the MEMORY//OS chat flow) is retrievable
        # through the production adapter — canonical authority.
        composition = rig.adapter._composition
        composition.db.execute(
            "INSERT INTO messages (thread_id, user_id, role, content, metadata, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                "conv-native", composition.identity.memoryos_user_id, "user",
                "The native backend chat wrote this turn.",
                None,
                utc_now().isoformat(),
            ),
        )
        response = rig.engine.retrieve(
            ctx, ContinuityQuery(query_text="native backend chat", modes=(RetrievalModeName.LEXICAL,))
        )
        native_hits = [r for r in response.source_records if "native backend chat" in r.content]
        self.assertEqual(len(native_hits), 1)
        self.assertEqual(dict(native_hits[0].provenance)["origin"], "memoryos-chat")

        # (b) Tampering with the derived view (local copy removed) does not
        # remove canonical truth — retrieval still serves the canonical row.
        with rig.store._connect() as conn:
            conn.execute("DELETE FROM messages WHERE owner_id='owner-a'")
        response = rig.engine.retrieve(
            ctx, ContinuityQuery(query_text="observatory opens", modes=(RetrievalModeName.LEXICAL, RetrievalModeName.EXACT))
        )
        self.assertTrue(any("observatory" in r.content for r in response.source_records))

        # (c) Governance inheritance: adapter deletion propagates to the
        # canonical store AND the derived view.
        rig.say("conv-demote2", "Remember that the chapel bell cracked.", sequence=1)
        report = rig.engine.delete_memory(ctx, scope_type="conversation", conversation_id="conv-demote2")
        self.assertEqual(report.status.value, "COMPLETED")
        conn = rig.canonical_db()
        self.assertEqual(conn.execute("SELECT count(*) c FROM messages WHERE content LIKE '%chapel bell%'").fetchone()["c"], 0)
        conn.close()
        for record in rig.store.list_sources_for_conversation("owner-a", "conv-demote2", include_deleted=True):
            self.assertEqual(record.content, "")


class TestRestartRecovery(ProductionAdapterTestCase):
    def test_restart_recovery_against_canonical_store(self):
        """Runtime reopen + continuity across restart."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.engine.start_conversation(ctx, conversation_id="conv-restart", started_at=utc_now())
        rig.say("conv-restart", "Remember that the ferry leaves at dawn.", sequence=1, event_time=_utc(2026, 9, 27, 6))

        # Simulate process restart: close the composition, re-compose over
        # the SAME canonical paths with a FRESH derived view.
        rig.adapter._composition.close()
        store2 = PersonalContinuityStore(self.root / "zorq-derived-2.db")
        adapter2 = ProductionMemoryOSAdapter.compose(
            store2,
            MemoryCapturePolicyEngine.default_retain("owner-a"),
            backend_root=BACKEND_ROOT,
            data_path=self.root / "memoryos.sqlite3",
            identity=OwnerIdentityMapping.local_single_owner("owner-a"),
            disable_embeddings=True,
        )
        engine2 = PersonalContinuityEngine(store2, adapter2)

        response = engine2.retrieve(
            ctx, ContinuityQuery(query_text="ferry", modes=(RetrievalModeName.EXACT, RetrievalModeName.LEXICAL))
        )
        hits = [r for r in response.source_records if "ferry" in r.content]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].conversation_id, "conv-restart")

        # Historical recall still works after restart.
        answer = engine2.answer_historical_query(ctx, "What did we talk about on 27 September 2026?")
        self.assertEqual(answer.status, AdapterOutcome.ALLOW)
        self.assertTrue(any("ferry" in r.content for r in answer.exact_source_content))

        # New writes after restart land canonically alongside old ones.
        started2 = engine2.start_conversation(
            ctx, conversation_id="conv-restart", started_at=utc_now(), title="restart"
        )
        self.assertEqual(started2[0].outcome, AdapterOutcome.ALLOW)
        decision, record, _ins = engine2.record_message(
            ctx, conversation_id="conv-restart", role="user",
            content="The ferry ticket office opens later in winter.",
            sequence=2, event_time=utc_now(),
        )
        self.assertEqual(decision.outcome, AdapterOutcome.ALLOW)
        conn = sqlite3.connect(self.root / "memoryos.sqlite3")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT content FROM messages WHERE thread_id='conv-restart' ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        conn.close()
        adapter2._composition.close()


class TestEmergencyStopScope(ProductionAdapterTestCase):
    def test_emergency_stop_scope_documented_and_enforced(self):
        """DQ-8: ZORQ stop halts the kernel; the adapter's in-flight behavior
        is bounded and truthful — memory governance continues (it is not the
        action plane) but never authorizes action."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        rig.say("conv-stop", "Remember that the workshop closes early.", sequence=1)

        with TemporaryDirectory() as tmp:
            core = ZorqCore(
                CoreConfig(
                    owner_id="owner-a",
                    device_id="device-1",
                    owner_secret="correct-secret",
                    approved_roots=(Path(tmp),),
                )
            )
            session = core.establish_owner_session("correct-secret")
            self.assertIsNotNone(session)
            core.emergency_stop("owner test stop")
            action = core.build_action(
                session, "filesystem.approved", "create_directory", {"path": str(Path(tmp) / "post-stop")}
            )
            result = core.execute_action(action, session)
            self.assertEqual(result.status, ActionStatus.STOPPED)
            self.assertIn("emergency stop", result.message)

        # During/after the stop the adapter remains truthful and bounded:
        # governed memory operations continue under canonical governance…
        outcome, record = rig.say("conv-stop", "The workshop reopens on Monday.", sequence=2)
        self.assertEqual(outcome, AdapterOutcome.ALLOW)
        # …the integration status is reported truthfully…
        status = rig.adapter.status_record()
        self.assertEqual(status["integration_status"], "AVAILABLE_UNVERIFIED")
        self.assertFalse(status["real_memoryos_verified"])
        self.assertEqual(status["adapter_contract_version"], MEMORYOS_V10_ADAPTER_VERSION)
        # …and memory still cannot authorize anything.
        self.assertFalse(rig.engine.memory_cannot_authorize_action("delete everything"))


class TestNoActionPlaneChanges(ProductionAdapterTestCase):
    def test_no_action_plane_changes_regression(self):
        """Static scans: (a) no src/zroq module outside adapters/ touches the
        backend; (b) action-plane + engine modules unchanged vs the canonical
        migration base; (c) action modules never import the adapter; (d)
        compileall clean."""

        # (a) Only the adapter module may reference backend imports.
        offenders = []
        for path in sorted((REPO_ROOT / "src" / "zroq").rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            if "adapters" in rel.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if (
                    "app.persistence" in line
                    or "app.memory" in line
                    or "app.main" in line
                    or stripped.startswith("import app")
                    or stripped.startswith("from app")
                ):
                    offenders.append(f"{rel}:{lineno}")
        self.assertEqual(offenders, [], f"backend imports leaked into core modules: {offenders}")

        # (b) Action-plane and engine modules are byte-identical to the
        # canonical migration commit (the 3B.2 baseline: HEAD before this
        # phase; the migration itself is not under test here).
        try:
            base = subprocess.run(
                ["git", "rev-parse", "--verify", "262159d^{commit}"],
                cwd=REPO_ROOT, capture_output=True, text=True, check=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            self.skipTest("git base commit unavailable in this checkout")
        diff = subprocess.run(
            ["git", "diff", "--name-only", base, "--", *[f"src/zroq/{m}" for m in ACTION_PLANE_MODULES]],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(diff, "", f"action-plane/engine modules were modified: {diff}")

        # (c) Action-plane modules never import the production adapter.
        adapter_importers = []
        for module in ACTION_PLANE_MODULES:
            path = REPO_ROOT / "src" / "zroq" / module
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            if "zroq.adapters" in text or "adapters.memoryos" in text or "ProductionMemoryOS" in text:
                adapter_importers.append(module)
        self.assertEqual(adapter_importers, [])

        # (d) compileall clean across the whole source tree.
        compile_result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q", str(REPO_ROOT / "src"), str(REPO_ROOT / "tests")],
            capture_output=True, text=True,
        )
        self.assertEqual(compile_result.returncode, 0, compile_result.stderr[-2000:])


class TestAuditRecords(ProductionAdapterTestCase):
    def test_audit_records_adapter_contract_version_and_status(self):
        """Truthful integration labels in status records, decisions, and the
        derived-view observation log."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()

        # Status record is truthful before verification.
        status = rig.adapter.status_record()
        self.assertEqual(status["adapter"], "ProductionMemoryOSAdapter")
        self.assertEqual(status["adapter_contract_version"], MEMORYOS_V10_ADAPTER_VERSION)
        self.assertEqual(status["integration_status"], "AVAILABLE_UNVERIFIED")
        self.assertFalse(status["real_memoryos_verified"])
        self.assertTrue(status["memoryos_available"])
        self.assertEqual(status["backend"]["backend"], "memoryos-v10.2.0")

        # Verification cannot be claimed without all gates.
        with self.assertRaises(PermissionError):
            rig.adapter.mark_verified(gates={"G-1": True}, report_path="x")
        with self.assertRaises(PermissionError):
            rig.adapter.mark_verified(
                gates={"G-1": True, "G-2": True, "G-3": True, "G-4": True, "G-5": False},
                report_path="x",
            )
        self.assertFalse(rig.adapter.real_memoryos_verified)

        # After a full gate record the transition is truthful.
        rig.adapter.mark_verified(
            gates={"G-1": True, "G-2": True, "G-3": True, "G-4": True, "G-5": True},
            report_path="docs/zorq/ZORQ-PHASE3B2-VERIFICATION.md",
        )
        self.assertTrue(rig.adapter.real_memoryos_verified)
        self.assertEqual(rig.adapter.integration_status, "AVAILABLE_VERIFIED")
        status = rig.adapter.status_record()
        self.assertEqual(status["verification_record"]["report"], "docs/zorq/ZORQ-PHASE3B2-VERIFICATION.md")

        # Governance decisions carry the production contract version and the
        # verified flag.
        decision = rig.adapter.govern(ctx, operation="retrieve", purpose="test", scope={})
        self.assertEqual(decision.policy_version, MEMORYOS_V10_ADAPTER_VERSION)
        self.assertTrue(decision.production_memoryos_verified)

        # The derived-view observation log records the integration lifecycle.
        with rig.store._connect() as conn:
            events = [
                dict(row)
                for row in conn.execute(
                    "SELECT event_type, metadata_json FROM observability_events WHERE owner_id=? ORDER BY event_id",
                    ("owner-a",),
                ).fetchall()
            ]
        event_types = [e["event_type"] for e in events]
        self.assertIn("memory.integration.status", event_types)
        self.assertIn("memory.integration.verified", event_types)
        self.assertIn("memory.governance", event_types)
        status_event = next(e for e in events if e["event_type"] == "memory.integration.status")
        metadata = json.loads(status_event["metadata_json"]) if isinstance(status_event["metadata_json"], str) else dict(status_event["metadata_json"])
        self.assertEqual(metadata["adapter_contract_version"], MEMORYOS_V10_ADAPTER_VERSION)


class TestContractAdapterProtocol(ProductionAdapterTestCase):
    def test_contract_adapter_protocol_roundtrip(self):
        """Supplementary: the 3A MemoryOSAdapterContract protocol is served by
        the composed production pair."""
        rig = self.make_rig("owner-a")
        contract = ProductionMemoryOSContractAdapter(rig.adapter, rig.engine)
        now = utc_now()

        # govern
        govern_decision = contract.govern(
            MemoryGovernanceRequest(
                request_id="req-1", owner_id="owner-a", principal_id="owner-a", created_at=now,
                operation="store_source", purpose="contract test",
                privacy_class=PrivacyClass.PERSONAL, retention_policy_id="policy-default-retain",
            )
        )
        self.assertEqual(govern_decision.status, GovernanceStatus.ALLOW)
        self.assertEqual(govern_decision.policy_version, MEMORYOS_V10_ADAPTER_VERSION)

        # store
        store_decision = contract.store(
            MemoryStorageRequest(
                storage_request_id="store-1", owner_id="owner-a", created_at=now,
                source=MemorySource(
                    source_id="src-contract-1", owner_id="owner-a", source_owner_id="owner-a",
                    source_type=zroq_source_type(), created_at=now, observed_at=now,
                    ingested_at=now, privacy_class=PrivacyClass.PERSONAL,
                    retention_mode=RetentionMode.EXPLICIT_REMEMBER,
                    retention_policy_id="policy-default-retain",
                    content_ref="Remember that the contract test ran clean.",
                    conversation_id="conv-contract", message_id="m-1",
                ),
                derived_memory=None,
                governance_decision_id=govern_decision.decision_id,
            )
        )
        self.assertEqual(store_decision.status, GovernanceStatus.ALLOW)

        # retrieve
        retrieval = contract.retrieve(
            MemoryRetrievalRequest(
                retrieval_id="ret-1", owner_id="owner-a", principal_id="owner-a", created_at=now,
                modes=(RetrievalMode.EXACT, RetrievalMode.SEMANTIC, RetrievalMode.CAUSAL_HISTORICAL),
                query_text="contract test", scope={"conversation_id": "conv-contract"},
                purpose="contract test",
            )
        )
        self.assertEqual(retrieval.governance_status, GovernanceStatus.ALLOW)
        self.assertTrue(retrieval.hits)
        self.assertTrue(all(hit.owner_id == "owner-a" for hit in retrieval.hits))
        self.assertTrue(all(0.0 <= hit.relevance <= 1.0 for hit in retrieval.hits))
        self.assertTrue(all(hit.provenance.source_id for hit in retrieval.hits))

        # delete
        deletion = contract.delete(
            DeletionRequest(
                deletion_id="del-1", owner_id="owner-a", principal_id="owner-a", created_at=now,
                scope_type="conversation", authentication_context="owner-session",
                conversation_id="conv-contract",
                requested_propagation=(
                    DeletionPropagationTarget.SOURCE,
                    DeletionPropagationTarget.DERIVED_MEMORY,
                    DeletionPropagationTarget.EMBEDDINGS,
                    DeletionPropagationTarget.INDEXES,
                    DeletionPropagationTarget.GRAPHS,
                    DeletionPropagationTarget.CACHES,
                ),
            )
        )
        self.assertEqual(deletion.status, DeletionStatus.COMPLETED)
        conn = rig.canonical_db()
        self.assertEqual(
            conn.execute("SELECT count(*) c FROM messages WHERE content LIKE '%contract test ran clean%'").fetchone()["c"], 0
        )
        conn.close()


class TestCanonicalRegistryAndExport(ProductionAdapterTestCase):
    def test_canonical_conversation_registry_and_export_labels(self):
        """Supplementary: canonical conversation registration + labeled export."""
        rig = self.make_rig("owner-a")
        ctx = rig.context()
        decision, _record = rig.engine.start_conversation(
            ctx, conversation_id="conv-registry", started_at=utc_now(), title="Registry"
        )
        self.assertEqual(decision.outcome, AdapterOutcome.ALLOW)
        conn = rig.canonical_db()
        row = conn.execute("SELECT * FROM conversations WHERE id='conv-registry'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["user_id"], "owner-a")
        conn.close()

        rig.say("conv-registry", "Remember that the registry test stored this turn.", sequence=1)
        exported = rig.engine.export_owner_memory(ctx)
        self.assertIsNotNone(exported)
        origin = exported["origin"]
        self.assertIn("memoryos-v10.2.0", origin["canonical_store"])
        self.assertIn("derived", origin["derived_view"])
        self.assertEqual(origin["adapter_contract_version"], MEMORYOS_V10_ADAPTER_VERSION)
        self.assertTrue(exported["canonical"]["memories"])
        self.assertTrue(exported["canonical"]["messages"])
        self.assertIn("derived_view", exported)


def zroq_source_type():
    from zroq.domain_contracts import SourceType

    return SourceType.CONVERSATION_MESSAGE


if __name__ == "__main__":
    unittest.main()
