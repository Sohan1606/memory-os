"""Production MEMORY//OS v10.2 adapter — Phase 3B.2 (spec §11).

This module wires the existing, unchanged ZORQ memory boundary to the real,
in-repository MEMORY//OS backend (``backend/app``) at the service layer, in
process (DQ-2 placement recommendation). It implements:

1. The Phase 3B engine adapter surface (the ``DocumentedMemoryOSAdapter``
   boundary used by ``PersonalContinuityEngine``): ``govern``,
   ``store_source``, ``retrieve``, ``delete``, ``export`` plus the
   ``policy_engine`` attribute and truthful integration-status labels.
2. The Phase 3A ``MemoryOSAdapterContract`` protocol (``govern`` /
   ``retrieve`` / ``store`` / ``delete`` over domain-contract records) via
   ``ProductionMemoryOSContractAdapter``. The two protocols declare methods
   with identical names but incompatible signatures, so they are implemented
   as an explicit composed pair rather than one class; this is an
   implementation reality of the existing contracts, not a new API.

Boundary rules enforced here (spec §11.2 / §11.5):

* MEMORY//OS is the canonical memory + governance authority. The canonical
  raw-source archive is the backend ``messages`` table; canonical governed
  memories are backend ``memories`` rows created through ``MemoryService``
  (which applies the backend's own duplicate/conflict governance). The ZORQ
  local ``PersonalContinuityStore`` is demoted to a derived view: it may be
  read for modes the canonical store cannot serve (ZORQ project/entity/goal/
  decision relational refs, causal history, FTS lexical, timeline
  enrichment) — always labelled ``derived-view`` in ``mode_status`` — and it
  inherits governance and deletion obligations.
* Governance fail-closed (DQ-4): unmapped or erroring backend states never
  become ALLOW. Backend unavailability ⇒ every governed operation returns
  UNAVAILABLE, exactly like ``UnavailableMemoryOSAdapter``; there is no
  silent fallback to the local harness for production paths.
* Deletion propagation is truthful (DQ-5 / R-6): canonical + derived targets
  are reported individually; anything unverifiable (backups) is PARTIAL /
  UNKNOWN, never COMPLETED.
* Owner identity mapping is explicit (DQ-3): a single ``OwnerIdentityMapping``
  pairs the ZORQ owner with one MEMORY//OS ``user_id``. No implicit user
  creation; no cross-owner access (backend queries are user_id-scoped).
* No Action Plane module imports this package and no Action Plane semantics
  change (spec §11.3). Memory never authorizes action
  (``MEMORY RETRIEVAL ≠ MEMORY GOVERNANCE ≠ AUTHORIZATION``).

Local-first constraints (Rev 2 §11.2 item 7): composition targets a local
backend root and local SQLite/Chroma paths only; no network is assumed. All
records reuse UTC-canonical timestamps, the event/ingest-time distinction,
and versioned contracts so Z-LD.1/Z-DIST.1 can extend without redesign.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from zroq.domain_contracts import (
    ContractValidationError,
    DeletionPropagationTarget,
    DeletionRequest,
    DeletionResult,
    DeletionStatus,
    GovernanceStatus,
    LifecycleState,
    MemoryGovernanceDecision,
    MemoryGovernanceRequest,
    MemoryOSAdapterContract,
    MemoryRetrievalHit,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemorySource,
    MemoryStorageRequest,
    PrivacyClass,
    Provenance,
    ProviderTrustClass,
    EgressPolicy,
    RetrievalMode,
    RetentionMode,
    SourceType,
    TemporalExtent,
    VerificationState,
)
from zroq.personal_continuity import (
    AdapterDecision,
    AdapterOutcome,
    ContinuityQuery,
    DeletionReport,
    DeletionRuntimeStatus,
    MemoryAccessContext,
    MemoryCapturePolicyEngine,
    PersonalContinuityStore,
    RetrievalModeName,
    RetrievalResponse,
    SourceRecord,
    _ensure_utc,
    _iso,
    _json,
    _stable_id,
    _token_set,
    parse_date_expression,
    utc_now,
)

MEMORYOS_V10_ADAPTER_VERSION = "memoryos-v10.2.0/zorq-adapter-1.0.0"

# MemoryService.create() is the only canonical memory-creation entry point we
# use; these are the actions it may truthfully return. Anything else is a
# contradictory backend state and fails closed.
_KNOWN_MEMORY_ACTIONS = frozenset({"created", "reinforced", "updated"})

# Scope types accepted by the ZORQ deletion boundary (must match the local
# derived-view store's _source_ids_for_deletion contract).
_SUPPORTED_SCOPE_TYPES = frozenset(
    {"message", "conversation", "project", "date_range", "derived_memory"}
)

_ZORQ_MEMORY_SOURCE_PREFIX = "zorq:"


class MemoryOSCompositionError(RuntimeError):
    """Raised when the real MEMORY//OS backend cannot be composed.

    Callers that must keep running (fail-closed mode) catch this and compose
    ``ProductionMemoryOSAdapter.unavailable(...)`` — every governed operation
    then returns UNAVAILABLE with this error's text as the reason.
    """


# ---------------------------------------------------------------------------
# Owner identity mapping (DQ-3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnerIdentityMapping:
    """Explicit ZORQ owner ↔ MEMORY//OS user pairing.

    Single-owner by design in 3B.2. The MEMORY//OS backend's default
    configuration (auth disabled) treats ``user_id`` as an owner-scoping key,
    so pairing does not create backend users implicitly; it only declares
    which scoping key this ZORQ owner maps to. ``device_scope`` is recorded
    so Z-DIST.1 can scope the mapping per device without contract change.
    """

    owner_id: str
    memoryos_user_id: str
    device_scope: str = "local"
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _ensure_utc(self.created_at, "created_at")
        if not self.owner_id or not self.owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        if not self.memoryos_user_id or not self.memoryos_user_id.strip():
            raise ValueError("memoryos_user_id must be a non-empty string")
        if any(c.isspace() for c in self.owner_id) or any(
            c.isspace() for c in self.memoryos_user_id
        ):
            raise ValueError("identity ids must not contain whitespace")
        if not self.device_scope:
            raise ValueError("device_scope must be a non-empty string")

    @classmethod
    def local_single_owner(
        cls, owner_id: str, *, memoryos_user_id: str | None = None, device_scope: str = "local"
    ) -> "OwnerIdentityMapping":
        """Map one ZORQ owner to one backend user id (identity-preserving)."""
        return cls(
            owner_id=owner_id,
            memoryos_user_id=memoryos_user_id or owner_id,
            device_scope=device_scope,
        )


# ---------------------------------------------------------------------------
# Backend composition (lazy import per DQ-2; this module is the only place
# src/zroq may touch backend/)
# ---------------------------------------------------------------------------


def _parse_backend_timestamp(value: str) -> datetime:
    """Parse backend timestamps ('YYYY-MM-DD HH:MM:SS' or ISO) to aware UTC."""
    text = (value or "").strip()
    if not text:
        return utc_now()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class MemoryOSV10Composition:
    """A composed, verified handle on the real MEMORY//OS backend services.

    Holds the real ``Database``, ``VectorStore`` and ``MemoryService`` plus
    the backend policy module. Constructed via :meth:`compose`, which
    performs the lazy backend import and a read-only composition check.
    """

    backend_root: Path
    data_path: Path
    vector_path: Path
    identity: OwnerIdentityMapping
    db: Any
    vectors: Any
    service: Any
    policy_module: Any
    composed_at: datetime = field(default_factory=utc_now)
    composition_log: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def compose(
        cls,
        *,
        backend_root: Path | str,
        data_path: Path | str,
        vector_path: Path | str | None = None,
        identity: OwnerIdentityMapping,
        disable_embeddings: bool = False,
    ) -> "MemoryOSV10Composition":
        """Import and construct the real backend services (local only).

        Raises :class:`MemoryOSCompositionError` on any failure — never a
        half-composed handle.
        """
        root = Path(backend_root).resolve()
        if not root.is_dir():
            raise MemoryOSCompositionError(f"MEMORY//OS backend root not found: {root}")
        app_root = root / "app"
        if not (app_root / "main.py").is_file():
            raise MemoryOSCompositionError(
                f"directory is not the MEMORY//OS backend (app/main.py missing): {root}"
            )
        log: list[Mapping[str, Any]] = []

        root_str = str(root)
        if root_str not in sys.path:
            sys.path.insert(0, root_str)
            log.append({"step": "sys.path", "detail": root_str})

        try:
            db_mod = importlib.import_module("app.persistence.db")
            vector_mod = importlib.import_module("app.memory.vector_store")
            service_mod = importlib.import_module("app.memory.service")
            policy_mod = importlib.import_module("app.memory.policy")
        except Exception as exc:  # pragma: no cover - environment dependent
            raise MemoryOSCompositionError(
                f"MEMORY//OS backend import failed: {type(exc).__name__}: {exc}"
            ) from exc
        log.append({"step": "import", "detail": "app.persistence.db, app.memory.{vector_store,service,policy}"})

        data = Path(data_path)
        vectors_path = Path(vector_path) if vector_path else data.parent / "chroma"
        try:
            data.parent.mkdir(parents=True, exist_ok=True)
            vectors_path.mkdir(parents=True, exist_ok=True)
            db = db_mod.Database(data)
            vectors = vector_mod.VectorStore(
                vectors_path, collection_name="zorch_memory", disable_embeddings=disable_embeddings
            )
            service = service_mod.MemoryService(db, vectors)
        except Exception as exc:
            raise MemoryOSCompositionError(
                f"MEMORY//OS backend construction failed: {type(exc).__name__}: {exc}"
            ) from exc
        log.append({"step": "construct", "detail": {"database": str(data), "vectors": str(vectors_path), "vector_mode": vectors.mode}})

        # Read-only composition verification: expected canonical tables exist
        # and the service/policy surface is the real one.
        try:
            tables = {
                row["name"]
                for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")
            }
            expected = {"memories", "memory_versions", "memory_events", "messages", "conversations"}
            missing = expected - tables
            if missing:
                raise MemoryOSCompositionError(
                    f"MEMORY//OS schema incomplete; missing tables: {sorted(missing)}"
                )
            for attr in ("evaluate", "classify"):
                if not callable(getattr(policy_mod, attr, None)):
                    raise MemoryOSCompositionError(
                        f"MEMORY//OS policy module lacks required function: {attr}"
                    )
            for attr in ("create", "search", "delete", "export", "list"):
                if not callable(getattr(service, attr, None)):
                    raise MemoryOSCompositionError(
                        f"MEMORY//OS MemoryService lacks required method: {attr}"
                    )
        except MemoryOSCompositionError:
            raise
        except Exception as exc:
            raise MemoryOSCompositionError(
                f"MEMORY//OS composition verification failed: {type(exc).__name__}: {exc}"
            ) from exc
        log.append({"step": "verify", "detail": "schema + service + policy surface OK"})

        return cls(
            backend_root=root,
            data_path=data,
            vector_path=vectors_path,
            identity=identity,
            db=db,
            vectors=vectors,
            service=service,
            policy_module=policy_mod,
            composed_at=utc_now(),
            composition_log=tuple(log),
        )

    @property
    def backend_status(self) -> Mapping[str, Any]:
        """Truthful status block for facades/audit (never raises)."""
        try:
            vector_mode = getattr(self.vectors, "mode", "unknown")
            embedding_model = getattr(self.vectors, "embedding_model", None)
        except Exception:
            vector_mode, embedding_model = "unknown", None
        return {
            "backend": "memoryos-v10.2.0",
            "database_path": str(self.data_path),
            "vector_store": "chromadb" if vector_mode == "semantic" else "sqlite-keyword-fallback",
            "vector_mode": vector_mode,
            "embedding_model": embedding_model,
            "policy_engine": "memoryos-deterministic-policy-engine",
            "memoryos_user_id": self.identity.memoryos_user_id,
            "device_scope": self.identity.device_scope,
            "composed_at": _iso(self.composed_at),
        }

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Production adapter (Phase 3B engine boundary)
# ---------------------------------------------------------------------------


class ProductionMemoryOSAdapter:
    """Adapter implementing the Phase 3B engine boundary over the REAL
    MEMORY//OS v10.2 backend.

    Canonical authority: raw sources live in the backend ``messages`` table;
    governed memories are created through the backend ``MemoryService`` under
    its own duplicate/conflict policy; retrieval serves canonical records
    first and labels derived-view assistance explicitly. The local
    ``PersonalContinuityStore`` is written in parallel as a derived view and
    is never the production authority (DQ-7).
    """

    contract_version = MEMORYOS_V10_ADAPTER_VERSION

    def __init__(
        self,
        store: PersonalContinuityStore,
        policy_engine: MemoryCapturePolicyEngine,
        composition: MemoryOSV10Composition | None,
        *,
        composition_error: str = "",
    ) -> None:
        self.store = store
        self.policy_engine = policy_engine
        self._composition = composition
        self._composition_error = composition_error
        self._real_memoryos_verified = False
        self._verification_record: Mapping[str, Any] | None = None
        if composition is not None:
            self.store.observe(
                composition.identity.owner_id,
                "memory.integration.status",
                {
                    "adapter_contract_version": self.contract_version,
                    "integration_status": self.integration_status,
                    "real_memoryos_verified": False,
                    "backend": dict(composition.backend_status),
                },
            )

    # -- construction helpers ---------------------------------------------

    @classmethod
    def compose(
        cls,
        store: PersonalContinuityStore,
        policy_engine: MemoryCapturePolicyEngine,
        *,
        backend_root: Path | str,
        data_path: Path | str,
        vector_path: Path | str | None = None,
        identity: OwnerIdentityMapping,
        disable_embeddings: bool = False,
    ) -> "ProductionMemoryOSAdapter":
        """Compose against the real backend; raises on failure (fail loud)."""
        composition = MemoryOSV10Composition.compose(
            backend_root=backend_root,
            data_path=data_path,
            vector_path=vector_path,
            identity=identity,
            disable_embeddings=disable_embeddings,
        )
        return cls(store, policy_engine, composition)

    @classmethod
    def unavailable(
        cls,
        store: PersonalContinuityStore,
        policy_engine: MemoryCapturePolicyEngine,
        *,
        reason: str,
    ) -> "ProductionMemoryOSAdapter":
        """Fail-closed adapter: every governed operation returns UNAVAILABLE."""
        return cls(store, policy_engine, None, composition_error=reason)

    # -- truthful status ----------------------------------------------------

    @property
    def memoryos_available(self) -> bool:
        return self._composition is not None

    @property
    def real_memoryos_verified(self) -> bool:
        return self._real_memoryos_verified

    @property
    def integration_status(self) -> str:
        if not self.memoryos_available:
            return "UNAVAILABLE"
        return "AVAILABLE_VERIFIED" if self._real_memoryos_verified else "AVAILABLE_UNVERIFIED"

    @property
    def backend_status(self) -> Mapping[str, Any]:
        if not self.memoryos_available:
            return {
                "backend": "memoryos-v10.2.0",
                "available": False,
                "composition_error": self._composition_error,
            }
        return dict(self._composition.backend_status)

    def status_record(self) -> Mapping[str, Any]:
        """Complete truthful integration record for audit/facades."""
        return {
            "adapter": "ProductionMemoryOSAdapter",
            "adapter_contract_version": self.contract_version,
            "integration_status": self.integration_status,
            "memoryos_available": self.memoryos_available,
            "real_memoryos_verified": self._real_memoryos_verified,
            "verification_record": dict(self._verification_record) if self._verification_record else None,
            "composition_error": self._composition_error or None,
            "backend": dict(self.backend_status),
        }

    def mark_verified(self, *, gates: Mapping[str, bool], report_path: str) -> None:
        """Transition ``real_memoryos_verified`` to True — gates required.

        Refuses (raises) unless every G-1…G-5 gate is recorded as passed and
        a verification report path is supplied. This is the only mutation of
        the verification flag; it exists so no code path can claim
        verification without evidence (no fake status).
        """
        required = {"G-1", "G-2", "G-3", "G-4", "G-5"}
        missing = required - set(gates)
        failed = {gate for gate in required if gates.get(gate) is not True}
        if missing or failed or not report_path:
            raise PermissionError(
                "verification cannot be claimed: "
                f"missing={sorted(missing)} not_passed={sorted(failed)} report={bool(report_path)}"
            )
        self._real_memoryos_verified = True
        self._verification_record = {
            "gates": {gate: True for gate in sorted(required)},
            "report": report_path,
            "recorded_at": _iso(utc_now()),
        }
        if self._composition is not None:
            self.store.observe(
                self._composition.identity.owner_id,
                "memory.integration.verified",
                {
                    "adapter_contract_version": self.contract_version,
                    "gates": sorted(required),
                    "report": report_path,
                },
            )

    # -- governance (DQ-4 mapping table) ------------------------------------

    def govern(
        self,
        context: MemoryAccessContext,
        *,
        operation: str,
        purpose: str,
        privacy_class: PrivacyClass = PrivacyClass.PERSONAL,
        scope: Mapping[str, Any] | None = None,
    ) -> AdapterDecision:
        scope = dict(scope or {})
        decision_id = _stable_id(
            context.owner_id, operation, purpose, _json(scope), utc_now().isoformat(), prefix="memgov"
        )

        def _decision(outcome: AdapterOutcome, reason: str) -> AdapterDecision:
            return AdapterDecision(
                outcome=outcome,
                decision_id=decision_id,
                owner_id=context.owner_id,
                operation=operation,
                purpose=purpose,
                reason=reason,
                policy_version=self.contract_version,
                constraints=scope,
                production_memoryos_verified=self._real_memoryos_verified,
            )

        # 1. Backend availability — fail closed first, always.
        if not self.memoryos_available:
            decision = _decision(
                AdapterOutcome.UNAVAILABLE,
                "real MEMORY//OS backend unavailable; governed operation fails closed"
                + (f" ({self._composition_error})" if self._composition_error else ""),
            )
            self.store.observe(
                context.owner_id,
                "memory.governance",
                {"decision_id": decision_id, "operation": operation, "outcome": decision.outcome.value},
            )
            return decision

        # 2. Egress policy — no non-local provider under NO_EGRESS.
        if (
            context.provider_trust_class != ProviderTrustClass.LOCAL_ONLY
            and context.egress_policy == EgressPolicy.NO_EGRESS
        ):
            decision = _decision(
                AdapterOutcome.DENY, "provider egress denied by local-only/no-egress policy"
            )
        # 3. Owner isolation.
        elif context.owner_id not in context.allowed_owner_ids:
            decision = _decision(AdapterOutcome.DENY, "owner isolation denied")
        # 4. Storage operations: owner capture policy decides retention;
        #    canonical eligibility is then governed by the real MEMORY//OS
        #    policy at store time (see store_source).
        elif operation in {"store_conversation", "store_source", "store"}:
            try:
                retain, retention_mode, reason = self.policy_engine.decide_retention(
                    conversation_id=str(scope.get("conversation_id", "conversation")),
                    message_id=scope.get("message_id"),
                    privacy_class=privacy_class,
                    explicit_remember=bool(scope.get("explicit_remember", False)),
                    explicit_do_not_remember=bool(scope.get("explicit_do_not_remember", False)),
                    temporary=bool(scope.get("temporary", False)),
                    project_id=scope.get("project_id"),
                )
            except Exception as exc:
                decision = _decision(
                    AdapterOutcome.UNAVAILABLE,
                    f"capture policy evaluation failed; failing closed: {type(exc).__name__}: {exc}",
                )
            else:
                if retain:
                    scope = {**scope, "retention_mode": retention_mode.value}
                    decision = _decision(
                        AdapterOutcome.ALLOW,
                        "owner capture policy retained the record; canonical storage is"
                        " governed by the real MEMORY//OS v10.2 policy engine at store time"
                        f" ({reason})",
                    )
                else:
                    decision = _decision(AdapterOutcome.DENY, reason)
        # 5. Owner-scoped read/derive/delete/export operations.
        elif operation in {"retrieve", "activate", "delete", "export", "derive", "timeline"}:
            decision = _decision(
                AdapterOutcome.ALLOW,
                "owner-scoped governed operation allowed against the canonical MEMORY//OS store",
            )
        # 6. Unknown operations are not governed by this adapter — never ALLOW.
        else:
            decision = _decision(
                AdapterOutcome.NOT_APPLICABLE, "operation is not governed by this adapter"
            )

        # store_conversation additionally registers the conversation in the
        # canonical backend registry (idempotent INSERT OR IGNORE, mirroring
        # the backend's own chat flow). This is a write inside govern(),
        # matching the existing adapter-boundary precedent (the harness
        # already observes governance events inside govern).
        if decision.outcome == AdapterOutcome.ALLOW and operation == "store_conversation":
            try:
                self._register_canonical_conversation(
                    conversation_id=str(scope.get("conversation_id", "")),
                    project_id=scope.get("project_id"),
                )
            except Exception:
                # Registration failure must not silently pass: the canonical
                # registry is part of canonical storage.
                decision = AdapterDecision(
                    outcome=AdapterOutcome.UNAVAILABLE,
                    decision_id=decision.decision_id,
                    owner_id=decision.owner_id,
                    operation=decision.operation,
                    purpose=decision.purpose,
                    reason="canonical conversation registration failed; failing closed",
                    policy_version=decision.policy_version,
                    constraints=decision.constraints,
                    production_memoryos_verified=self._real_memoryos_verified,
                )

        self.store.observe(
            context.owner_id,
            "memory.governance",
            {"decision_id": decision_id, "operation": operation, "outcome": decision.outcome.value},
        )
        return decision

    def _register_canonical_conversation(self, *, conversation_id: str, project_id: str | None) -> None:
        if not conversation_id or self._composition is None:
            return
        user_id = self._composition.identity.memoryos_user_id
        now = _iso(utc_now())
        existing = self._composition.db.query_one(
            "SELECT user_id FROM conversations WHERE id=?", (conversation_id,)
        )
        if existing is not None:
            if existing["user_id"] != user_id:
                # Cross-owner collision on the canonical registry: deny hard.
                raise PermissionError(
                    f"conversation id {conversation_id!r} is owned by another MEMORY//OS user"
                )
            return
        # Backend gap (reported, not patched): ZORQ conversation titles live
        # in the ZORQ derived view; the canonical registry records the
        # conversation id as the title.
        self._composition.db.execute(
            "INSERT OR IGNORE INTO conversations (id, user_id, title, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (conversation_id, user_id, conversation_id, now, now),
        )

    # -- canonical storage ---------------------------------------------------

    def _canonical_message_metadata(self, record: SourceRecord) -> dict[str, Any]:
        """Full round-trip metadata persisted with the canonical message row."""
        return {
            "source_id": record.source_id,
            "message_id": record.message_id,
            "sequence": record.sequence,
            "event_time": _iso(record.event_time),
            "ingested_at": _iso(record.ingested_at),
            "privacy_class": record.privacy_class.value,
            "retention_mode": record.retention_mode.value,
            "provenance": dict(record.provenance),
            "lifecycle_state": record.lifecycle_state.value,
            "project_id": record.project_id,
            "branch_id": record.branch_id,
            "entity_ids": list(record.entity_ids),
            "goal_ids": list(record.goal_ids),
            "decision_ids": list(record.decision_ids),
            "local_display_time": record.local_display_time,
            "timezone_name": record.timezone_name,
            "utc_offset_minutes": record.utc_offset_minutes,
            "origin": "zorq-continuity",
            "zorq_schema_version": "3b",
        }

    def store_source(
        self,
        context: MemoryAccessContext,
        record: SourceRecord,
        *,
        explicit_remember: bool = False,
        explicit_do_not_remember: bool = False,
        temporary: bool = False,
    ) -> tuple[AdapterDecision, SourceRecord | None, bool]:
        if record.owner_id != context.owner_id:
            raise PermissionError("cross-owner source storage denied")
        import hashlib

        decision = self.govern(
            context,
            operation="store_source",
            purpose=context.purpose,
            privacy_class=record.privacy_class,
            scope={
                "conversation_id": record.conversation_id,
                "message_id": record.message_id,
                "source_id": record.source_id,
                "project_id": record.project_id,
                "explicit_remember": explicit_remember,
                "explicit_do_not_remember": explicit_do_not_remember,
                "temporary": temporary,
                "content_sha256": hashlib.sha256(record.content.encode("utf-8")).hexdigest(),
            },
        )
        if decision.outcome != AdapterOutcome.ALLOW or self._composition is None:
            return decision, None, False

        composition = self._composition
        user_id = composition.identity.memoryos_user_id

        # Real MEMORY//OS capture policy governs canonical memory creation.
        try:
            candidate = composition.policy_module.evaluate(record.content)
            durable = bool(candidate.is_durable) or explicit_remember
            category = candidate.category
            importance = float(candidate.importance)
            confidence = float(candidate.confidence)
            policy_reason = candidate.reason
        except Exception as exc:
            return (
                AdapterDecision(
                    outcome=AdapterOutcome.UNAVAILABLE,
                    decision_id=decision.decision_id,
                    owner_id=decision.owner_id,
                    operation=decision.operation,
                    purpose=decision.purpose,
                    reason=f"MEMORY//OS policy evaluation failed; failing closed: {type(exc).__name__}: {exc}",
                    policy_version=decision.policy_version,
                    constraints=decision.constraints,
                    production_memoryos_verified=self._real_memoryos_verified,
                ),
                None,
                False,
            )

        canonical: dict[str, Any] = {
            "message_rowid": None,
            "memory_id": None,
            "memory_action": None,
            "policy_category": category,
            "durable": durable,
            "policy_reason": policy_reason,
        }

        try:
            # 1. Canonical governed memory (when the real policy retains it).
            if durable:
                created = composition.service.create(
                    user_id,
                    record.content,
                    category=category,
                    importance=importance,
                    confidence=confidence,
                    source=f"{_ZORQ_MEMORY_SOURCE_PREFIX}{record.source_id}",
                    thread_id=record.conversation_id,
                    allow_duplicate=False,
                )
                action = created.get("action")
                if action not in _KNOWN_MEMORY_ACTIONS:
                    # Contradictory backend governance — never ALLOW.
                    raise RuntimeError(
                        f"contradictory MEMORY//OS create action: {action!r}"
                    )
                canonical["memory_action"] = action
                canonical["memory_id"] = (created.get("memory") or {}).get("id")

            # 2. Canonical raw source row (all retained content, durable or
            #    not — mirrors the backend's own archive-every-turn flow).
            metadata = self._canonical_message_metadata(record)
            cursor = composition.db.execute(
                "INSERT INTO messages (thread_id, user_id, role, content, metadata, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    record.conversation_id,
                    user_id,
                    record.role,
                    record.content,
                    _json(metadata),
                    _iso(record.event_time),
                ),
            )
            canonical["message_rowid"] = getattr(cursor, "lastrowid", None)
            self._register_canonical_conversation(
                conversation_id=record.conversation_id, project_id=record.project_id
            )
            composition.db.execute(
                "UPDATE conversations SET updated_at=? WHERE id=? AND user_id=?",
                (_iso(utc_now()), record.conversation_id, user_id),
            )
        except Exception as exc:
            # Best-effort compensation of the canonical memory write, then
            # fail closed. The raw-source row was never inserted (it is the
            # last write), so canonical storage stays consistent.
            memory_id = canonical.get("memory_id")
            if memory_id and canonical.get("memory_action") == "created":
                try:
                    composition.service.delete(memory_id)
                except Exception:
                    pass
            return (
                AdapterDecision(
                    outcome=AdapterOutcome.UNAVAILABLE,
                    decision_id=decision.decision_id,
                    owner_id=decision.owner_id,
                    operation=decision.operation,
                    purpose=decision.purpose,
                    reason=f"canonical MEMORY//OS storage failed; failing closed: {type(exc).__name__}: {exc}",
                    policy_version=decision.policy_version,
                    constraints=decision.constraints,
                    production_memoryos_verified=self._real_memoryos_verified,
                ),
                None,
                False,
            )

        # 3. Local derived view (DQ-7): parallel write, governance inherited.
        stored, inserted = self.store.store_source_record(record)

        decision = AdapterDecision(
            outcome=decision.outcome,
            decision_id=decision.decision_id,
            owner_id=decision.owner_id,
            operation=decision.operation,
            purpose=decision.purpose,
            reason=decision.reason,
            policy_version=decision.policy_version,
            constraints={**decision.constraints, "canonical": canonical},
            production_memoryos_verified=self._real_memoryos_verified,
        )
        self.store.observe(
            context.owner_id,
            "memory.source.store.canonical",
            {
                "source_id": record.source_id,
                "conversation_id": record.conversation_id,
                "canonical": canonical,
            },
        )
        return decision, stored, inserted

    # -- canonical retrieval ---------------------------------------------------

    def _source_from_canonical_row(self, row: Any) -> SourceRecord:
        """Reconstruct a ZORQ SourceRecord from a canonical backend row."""
        metadata: dict[str, Any] = {}
        raw_metadata = row["metadata"] if "metadata" in row.keys() else None
        if raw_metadata:
            try:
                parsed = json.loads(raw_metadata)
                if isinstance(parsed, dict):
                    metadata = parsed
            except (ValueError, TypeError):
                metadata = {}
        event_time = _parse_backend_timestamp(
            metadata.get("event_time") or row["created_at"]
        )
        ingested_at = _parse_backend_timestamp(
            metadata.get("ingested_at") or row["created_at"]
        )
        provenance = metadata.get("provenance")
        if isinstance(provenance, dict):
            # The canonical origin label is metadata-level; carry it inside the
            # reconstructed provenance so retrieved records stay traceable.
            provenance = {**provenance, "origin": metadata.get("origin", "zorq-continuity")}
        else:
            provenance = {
                "origin": "memoryos-chat",
                "thread_id": row["thread_id"],
                "row_id": row["id"] if "id" in row.keys() else None,
            }
        return SourceRecord(
            source_id=str(
                metadata.get("source_id")
                or f"memoryos-msg-{row['id'] if 'id' in row.keys() else row['thread_id']}"
            ),
            owner_id=self._composition.identity.owner_id if self._composition else "",
            conversation_id=row["thread_id"],
            message_id=str(metadata.get("message_id") or f"memoryos-row-{row['id'] if 'id' in row.keys() else 'x'}"),
            role=row["role"],
            content=row["content"],
            sequence=int(metadata.get("sequence") or 0),
            event_time=event_time,
            ingested_at=ingested_at,
            privacy_class=PrivacyClass(metadata.get("privacy_class", PrivacyClass.PERSONAL.value)),
            retention_mode=RetentionMode(
                metadata.get("retention_mode", RetentionMode.DEFAULT_RETAIN.value)
            ),
            provenance=provenance,
            lifecycle_state=LifecycleState(
                metadata.get("lifecycle_state", LifecycleState.CURRENT.value)
            ),
            project_id=metadata.get("project_id"),
            branch_id=metadata.get("branch_id"),
            entity_ids=tuple(metadata.get("entity_ids", ())),
            goal_ids=tuple(metadata.get("goal_ids", ())),
            decision_ids=tuple(metadata.get("decision_ids", ())),
            local_display_time=metadata.get("local_display_time", ""),
            timezone_name=metadata.get("timezone_name", "UTC"),
            utc_offset_minutes=int(metadata.get("utc_offset_minutes", 0)),
        )

    def _canonical_rows(
        self,
        *,
        conversation_id: str | None = None,
        temporal_start: datetime | None = None,
        temporal_end: datetime | None = None,
        source_ids: Sequence[str] = (),
        message_ids: Sequence[str] = (),
        limit: int = 200,
    ) -> list[Any]:
        """Owner-scoped canonical message query (user_id enforced)."""
        assert self._composition is not None
        user_id = self._composition.identity.memoryos_user_id
        clauses = ["user_id=?"]
        params: list[Any] = [user_id]
        if conversation_id:
            clauses.append("thread_id=?")
            params.append(conversation_id)
        if temporal_start:
            clauses.append("created_at >= ?")
            params.append(_iso(temporal_start))
        if temporal_end:
            clauses.append("created_at < ?")
            params.append(_iso(temporal_end))
        if source_ids or message_ids:
            # source_id/message_id live in the metadata JSON; filter in
            # Python over a bounded owner-scoped scan.
            wanted_sources = set(source_ids)
            wanted_messages = set(message_ids)
            sql = (
                "SELECT * FROM messages WHERE " + " AND ".join(clauses) +
                " ORDER BY created_at DESC, id DESC LIMIT ?"
            )
            params.append(max(limit * 10, 400))
            rows = self._composition.db.query(sql, params)
            filtered = []
            for row in rows:
                try:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                except (ValueError, TypeError):
                    metadata = {}
                if metadata.get("source_id") in wanted_sources:
                    filtered.append(row)
                elif metadata.get("message_id") in wanted_messages:
                    filtered.append(row)
            filtered.sort(
                key=lambda r: (r["created_at"], r["id"]), reverse=True
            )
            return filtered[:limit]
        sql = (
            "SELECT * FROM messages WHERE " + " AND ".join(clauses) +
            " ORDER BY created_at DESC, id DESC LIMIT ?"
        )
        params.append(limit)
        return list(self._composition.db.query(sql, params))

    def _canonical_source_by_id(self, source_id: str) -> SourceRecord | None:
        rows = self._canonical_rows(source_ids=(source_id,), limit=1)
        return self._source_from_canonical_row(rows[0]) if rows else None

    def _pseudo_source_from_memory(self, memory: Any) -> SourceRecord:
        """Surface a canonical governed memory as a source-backed record.

        Backend-native memories (not created via the ZORQ boundary) have no
        canonical message row; they are represented with full provenance of
        their canonical origin. This never fabricates content: the text is
        the canonical memory content itself.
        """
        assert self._composition is not None
        created_at = _parse_backend_timestamp(memory.created_at)
        source_id = (
            memory.source[len(_ZORQ_MEMORY_SOURCE_PREFIX):]
            if (memory.source or "").startswith(_ZORQ_MEMORY_SOURCE_PREFIX)
            else f"memoryos-mem-{memory.id}"
        )
        return SourceRecord(
            source_id=source_id,
            owner_id=self._composition.identity.owner_id,
            conversation_id=memory.thread_id or "memoryos",
            message_id=f"memory-{memory.id}",
            role="memory",
            content=memory.content,
            sequence=0,
            event_time=created_at,
            ingested_at=created_at,
            privacy_class=PrivacyClass.PERSONAL,
            retention_mode=RetentionMode.DEFAULT_RETAIN,
            provenance={
                "origin": "memoryos-memory",
                "memory_id": memory.id,
                "memory_category": memory.category,
                "memory_source": memory.source,
                "memory_version": memory.version,
            },
            lifecycle_state=LifecycleState.CURRENT,
            project_id=None,
        )

    def retrieve(
        self,
        context: MemoryAccessContext,
        query: ContinuityQuery,
        engine: Any,
    ) -> RetrievalResponse:
        decision = self.govern(
            context,
            operation="retrieve",
            purpose=context.purpose,
            scope={"query": query.query_text, "modes": [m.value for m in query.modes]},
        )
        if decision.outcome != AdapterOutcome.ALLOW:
            return RetrievalResponse(
                decision.outcome,
                decision,
                query,
                (),
                mode_status={"all": decision.outcome.value},
                message=decision.reason,
            )

        try:
            return self._retrieve_canonical(context, query, decision, engine)
        except Exception as exc:
            return RetrievalResponse(
                AdapterOutcome.UNAVAILABLE,
                decision,
                query,
                (),
                mode_status={"all": "UNAVAILABLE"},
                message=(
                    "canonical MEMORY//OS retrieval failed; failing closed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

    def _retrieve_canonical(
        self,
        context: MemoryAccessContext,
        query: ContinuityQuery,
        decision: AdapterDecision,
        engine: Any,
    ) -> RetrievalResponse:
        assert self._composition is not None
        user_id = self._composition.identity.memoryos_user_id
        mode_status: dict[str, str] = {}
        calendar_timezone = query.calendar_timezone or context.owner_calendar_timezone or "UTC"
        mode_status["calendar_timezone"] = calendar_timezone
        mode_status["calendar_timezone_source"] = (
            "query_override" if query.calendar_timezone
            else ("owner_context" if context.owner_calendar_timezone else "utc_fallback")
        )

        active_query = query
        if not query.temporal_start and not query.temporal_end:
            parsed = parse_date_expression(query.query_text, calendar_timezone)
            if parsed:
                active_query = ContinuityQuery(
                    query_text=query.query_text,
                    modes=query.modes,
                    temporal_start=parsed[0],
                    temporal_end=parsed[1],
                    calendar_timezone=calendar_timezone,
                    source_ids=query.source_ids,
                    message_ids=query.message_ids,
                    conversation_id=query.conversation_id,
                    project_id=query.project_id,
                    entity_ids=query.entity_ids,
                    goal_ids=query.goal_ids,
                    decision_ids=query.decision_ids,
                    include_deleted=query.include_deleted,
                    limit=query.limit,
                )

        scores: dict[str, float] = {}
        records: dict[str, SourceRecord] = {}

        # --- EXACT / TEMPORAL: canonical raw sources. ---
        if (
            RetrievalModeName.EXACT in active_query.modes
            or RetrievalModeName.TEMPORAL in active_query.modes
            or active_query.source_ids
            or active_query.message_ids
            or active_query.conversation_id
        ):
            for row in self._canonical_rows(
                conversation_id=active_query.conversation_id,
                temporal_start=active_query.temporal_start,
                temporal_end=active_query.temporal_end,
                source_ids=active_query.source_ids,
                message_ids=active_query.message_ids,
                limit=max(active_query.limit, 50),
            ):
                record = self._source_from_canonical_row(row)
                records[record.source_id] = record
                base = (
                    0.85
                    if (active_query.source_ids or active_query.message_ids or active_query.temporal_start)
                    else 0.4
                )
                scores[record.source_id] = max(scores.get(record.source_id, 0.0), base)
            for mode in (RetrievalModeName.EXACT, RetrievalModeName.TEMPORAL):
                if mode in active_query.modes:
                    mode_status[mode.value] = "AVAILABLE:memoryos-canonical"

        # --- LEXICAL: canonical memory search + canonical message scan. ---
        if RetrievalModeName.LEXICAL in active_query.modes and active_query.query_text:
            query_tokens = _token_set(active_query.query_text)
            for hit in self._composition.service.search(
                user_id, active_query.query_text, top_k=max(active_query.limit, 20)
            ):
                record = self._record_for_memory_hit(hit.memory)
                if record is None:
                    continue
                if active_query.project_id and record.project_id != active_query.project_id:
                    continue
                records[record.source_id] = record
                overlap = len(query_tokens & _token_set(record.content))
                scores[record.source_id] = max(
                    scores.get(record.source_id, 0.0), min(0.75, 0.25 + 0.1 * overlap)
                )
            for row in self._canonical_rows(
                conversation_id=active_query.conversation_id,
                limit=max(active_query.limit * 5, 100),
            ):
                record = self._source_from_canonical_row(row)
                overlap = len(query_tokens & _token_set(record.content))
                if overlap <= 0:
                    continue
                if active_query.project_id and record.project_id != active_query.project_id:
                    continue
                records[record.source_id] = record
                scores[record.source_id] = max(
                    scores.get(record.source_id, 0.0), min(0.75, 0.25 + 0.1 * overlap)
                )
            mode_status[RetrievalModeName.LEXICAL.value] = "AVAILABLE:memoryos-canonical"

        # --- SEMANTIC: real backend vector search (semantic or truthful
        #     keyword fallback, per backend vector mode). ---
        if RetrievalModeName.SEMANTIC in active_query.modes and active_query.query_text:
            for hit in self._composition.service.search(
                user_id, active_query.query_text, top_k=max(active_query.limit, 20)
            ):
                record = self._record_for_memory_hit(hit.memory)
                if record is None:
                    continue
                records[record.source_id] = record
                scores[record.source_id] = max(
                    scores.get(record.source_id, 0.0), min(1.0, float(hit.score))
                )
            vector_mode = getattr(self._composition.vectors, "mode", "keyword")
            mode_status[RetrievalModeName.SEMANTIC.value] = f"AVAILABLE:memoryos-{vector_mode}"

        # --- RELATIONAL / CAUSAL: ZORQ-scoped references live in the
        #     derived view only (backend gap — reported, not patched).
        if RetrievalModeName.RELATIONAL in active_query.modes and (
            active_query.project_id
            or active_query.entity_ids
            or active_query.goal_ids
            or active_query.decision_ids
        ):
            broader = ContinuityQuery(
                query_text=active_query.query_text,
                modes=(RetrievalModeName.RELATIONAL,),
                temporal_start=active_query.temporal_start,
                temporal_end=active_query.temporal_end,
                calendar_timezone=calendar_timezone,
                project_id=active_query.project_id,
                include_deleted=active_query.include_deleted,
                limit=max(active_query.limit, 100),
            )
            for record in self.store.candidate_sources(
                context.owner_id, broader, limit=max(active_query.limit, 100)
            ):
                signal = 0.0
                if active_query.project_id and record.project_id == active_query.project_id:
                    signal += 0.35
                if set(active_query.entity_ids) & set(record.entity_ids):
                    signal += 0.25
                if set(active_query.goal_ids) & set(record.goal_ids):
                    signal += 0.25
                if set(active_query.decision_ids) & set(record.decision_ids):
                    signal += 0.25
                if signal > 0:
                    records[record.source_id] = record
                    scores[record.source_id] = max(scores.get(record.source_id, 0.0), signal)
            mode_status[RetrievalModeName.RELATIONAL.value] = "AVAILABLE:derived-view(ZORQ-scoped refs)"

        if RetrievalModeName.CAUSAL in active_query.modes:
            derived = self.store.search_derived(
                context.owner_id, active_query, limit=max(active_query.limit, 40)
            )
            for mem in derived:
                source = self.store.get_source(context.owner_id, mem.source_id)
                if source is not None:
                    records[source.source_id] = source
                    scores[source.source_id] = max(scores.get(source.source_id, 0.0), 0.7)
            mode_status[RetrievalModeName.CAUSAL.value] = "AVAILABLE:derived-view"

        ordered = sorted(
            records.values(),
            key=lambda r: (scores.get(r.source_id, 0.0), r.event_time, -r.sequence),
            reverse=True,
        )[: active_query.limit]
        source_ids = [r.source_id for r in ordered]
        derived_records = self.store.derived_for_sources(context.owner_id, source_ids)
        timeline_events = self.store.timeline_for_sources(context.owner_id, source_ids)
        self.store.observe(
            context.owner_id,
            "memory.retrieve.canonical",
            {
                "query_hash": __import__("hashlib").sha256(active_query.query_text.encode()).hexdigest()[:16],
                "count": len(ordered),
            },
        )
        return RetrievalResponse(
            status=AdapterOutcome.ALLOW,
            decision=decision,
            query=active_query,
            source_records=tuple(ordered),
            derived_memories=derived_records,
            timeline_events=timeline_events,
            source_scores={sid: scores.get(sid, 0.0) for sid in source_ids},
            mode_status=mode_status,
            message=(
                "governed retrieval completed against the canonical MEMORY//OS store"
                " (+ ZORQ derived views where labelled)"
            ),
        )

    def _record_for_memory_hit(self, memory: Any) -> SourceRecord | None:
        """Map a canonical memory hit to its source-backed record.

        ZORQ-created memories map back to their canonical message row; native
        backend memories are surfaced as provenance-labelled pseudo-sources.
        """
        source_ref = memory.source or ""
        if source_ref.startswith(_ZORQ_MEMORY_SOURCE_PREFIX):
            source_id = source_ref[len(_ZORQ_MEMORY_SOURCE_PREFIX):]
            record = self._canonical_source_by_id(source_id)
            if record is not None:
                return record
        return self._pseudo_source_from_memory(memory)

    # -- deletion propagation (DQ-5) -------------------------------------------

    def delete(
        self,
        context: MemoryAccessContext,
        *,
        deletion_id: str,
        scope_type: str,
        scope: Mapping[str, Any],
        include_backups: bool = False,
    ) -> DeletionReport:
        decision = self.govern(
            context,
            operation="delete",
            purpose=context.purpose,
            scope={"scope_type": scope_type, **dict(scope)},
        )
        if decision.outcome != AdapterOutcome.ALLOW:
            return DeletionReport(
                deletion_id,
                context.owner_id,
                DeletionRuntimeStatus.FAILED,
                decision,
                {},
                (),
                (),
                message=decision.reason,
            )
        if scope_type not in _SUPPORTED_SCOPE_TYPES:
            return DeletionReport(
                deletion_id,
                context.owner_id,
                DeletionRuntimeStatus.FAILED,
                decision,
                {},
                (),
                (),
                message=f"unsupported deletion scope_type: {scope_type} (fail closed)",
            )

        # 1. Canonical deletion (best effort with truthful reporting).
        propagated: dict[str, DeletionRuntimeStatus] = {}
        canonical_source_ids: list[str] = []
        canonical_memory_ids: list[str] = []
        canonical_failure = ""
        if self._composition is not None and scope_type != "derived_memory":
            try:
                canonical_source_ids, canonical_memory_ids = self._delete_canonical_scope(
                    scope_type, dict(scope)
                )
                propagated["canonical_source"] = DeletionRuntimeStatus.COMPLETED
                propagated["canonical_memory"] = DeletionRuntimeStatus.COMPLETED
                propagated["canonical_embeddings"] = DeletionRuntimeStatus.COMPLETED
                propagated["canonical_indexes"] = DeletionRuntimeStatus.COMPLETED
                propagated["canonical_graphs"] = DeletionRuntimeStatus.COMPLETED
            except Exception as exc:
                canonical_failure = f"{type(exc).__name__}: {exc}"
                propagated["canonical_source"] = DeletionRuntimeStatus.UNKNOWN
                propagated["canonical_memory"] = DeletionRuntimeStatus.UNKNOWN
                propagated["canonical_embeddings"] = DeletionRuntimeStatus.UNKNOWN
                propagated["canonical_indexes"] = DeletionRuntimeStatus.UNKNOWN
                propagated["canonical_graphs"] = DeletionRuntimeStatus.UNKNOWN
        elif scope_type == "derived_memory":
            # Derived-only scope: canonical sources are intentionally
            # preserved (governed derived records only).
            propagated["canonical_source"] = DeletionRuntimeStatus.RETAINED_BY_POLICY
            propagated["canonical_memory"] = DeletionRuntimeStatus.RETAINED_BY_POLICY

        # 2. Derived-view deletion via the local store (unchanged semantics:
        #    tombstones, deletion audit, FTS/index scrub).
        local_report = self.store.delete_scope(
            owner_id=context.owner_id,
            deletion_id=deletion_id,
            principal_id=context.principal_id,
            scope_type=scope_type,
            scope=scope,
            decision=decision,
            include_backups=include_backups,
        )
        for target, status in local_report.propagated.items():
            propagated[f"derived_view.{target}"] = status

        # 3. Truthful overall status.
        unknown_targets = [t for t, s in propagated.items() if s == DeletionRuntimeStatus.UNKNOWN]
        if canonical_failure:
            overall = DeletionRuntimeStatus.PARTIAL if not unknown_targets else DeletionRuntimeStatus.UNKNOWN
        elif include_backups:
            propagated["backups"] = DeletionRuntimeStatus.UNKNOWN
            overall = DeletionRuntimeStatus.PARTIAL
        else:
            overall = DeletionRuntimeStatus.COMPLETED

        deleted_source_ids = list(dict.fromkeys(canonical_source_ids + list(local_report.deleted_source_ids)))
        deleted_memory_ids = list(dict.fromkeys(canonical_memory_ids + list(local_report.deleted_memory_ids)))
        message = (
            f"deletion propagated to canonical MEMORY//OS store ({len(canonical_source_ids)} sources,"
            f" {len(canonical_memory_ids)} memories) and ZORQ derived views"
        )
        if canonical_failure:
            message += f"; canonical deletion failed: {canonical_failure}"
        if include_backups:
            message += "; backup targets unverifiable (no backup system is configured) — reported UNKNOWN, never COMPLETED"

        return DeletionReport(
            deletion_id=deletion_id,
            owner_id=context.owner_id,
            status=overall,
            decision=decision,
            propagated=propagated,
            deleted_source_ids=tuple(deleted_source_ids),
            deleted_memory_ids=tuple(deleted_memory_ids),
            retained_by_policy=local_report.retained_by_policy,
            message=message,
        )

    def _delete_canonical_scope(
        self, scope_type: str, scope: dict[str, Any]
    ) -> tuple[list[str], list[str]]:
        """Delete canonical rows for the scope; returns (source_ids, memory_ids).

        Memory deletion goes through MemoryService.delete so versions,
        relationships, vector embeddings and the canonical event log are all
        updated by the backend's own governance.
        """
        assert self._composition is not None
        composition = self._composition
        user_id = composition.identity.memoryos_user_id

        if scope_type == "message":
            source_ids = tuple(scope.get("source_ids", ()))
            message_ids = tuple(scope.get("message_ids", ()))
            rows = self._canonical_rows(
                source_ids=source_ids, message_ids=message_ids, limit=max(len(source_ids) + len(message_ids), 1) * 5
            )
        elif scope_type == "conversation":
            conversation_id = str(scope.get("conversation_id", ""))
            rows = self._canonical_rows(conversation_id=conversation_id, limit=100000)
        elif scope_type == "project":
            all_rows = self._canonical_rows(limit=100000)
            rows = []
            for row in all_rows:
                try:
                    metadata = json.loads(row["metadata"]) if row["metadata"] else {}
                except (ValueError, TypeError):
                    metadata = {}
                if metadata.get("project_id") == scope.get("project_id"):
                    rows.append(row)
        elif scope_type == "date_range":
            start = scope.get("temporal_start")
            end = scope.get("temporal_end")
            rows = self._canonical_rows(
                temporal_start=_parse_backend_timestamp(start) if start else None,
                temporal_end=_parse_backend_timestamp(end) if end else None,
                limit=100000,
            )
        else:  # pragma: no cover - guarded by caller
            raise ValueError(f"unsupported canonical scope: {scope_type}")

        # Owner check on the canonical registry before any delete.
        for conversation_id in {row["thread_id"] for row in rows}:
            owner_row = composition.db.query_one(
                "SELECT user_id FROM conversations WHERE id=?", (conversation_id,)
            )
            if owner_row is not None and owner_row["user_id"] != user_id:
                raise PermissionError(
                    f"conversation {conversation_id!r} belongs to another MEMORY//OS user"
                )

        deleted_source_ids: list[str] = []
        deleted_memory_ids: list[str] = []
        memory_rows = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            except (ValueError, TypeError):
                metadata = {}
            source_id = metadata.get("source_id")
            if source_id:
                deleted_source_ids.append(str(source_id))
            if (metadata.get("origin") == "zorq-continuity") and source_id:
                memory_rows.extend(
                    composition.db.query(
                        "SELECT id FROM memories WHERE user_id=? AND source=?",
                        (user_id, f"{_ZORQ_MEMORY_SOURCE_PREFIX}{source_id}"),
                    )
                )
        # Memories linked to deleted conversations (reinforcement may have
        # re-pointed source attribution, so also sweep by thread).
        for conversation_id in {row["thread_id"] for row in rows}:
            memory_rows.extend(
                composition.db.query(
                    "SELECT id FROM memories WHERE user_id=? AND thread_id=?",
                    (user_id, conversation_id),
                )
            )
        seen_memory_ids: set[str] = set()
        for memory_row in memory_rows:
            memory_id = memory_row["id"]
            if memory_id in seen_memory_ids:
                continue
            seen_memory_ids.add(memory_id)
            if composition.service.delete(memory_id):
                deleted_memory_ids.append(memory_id)

        if rows:
            composition.db.execute(
                "DELETE FROM messages WHERE user_id=? AND id IN ({})".format(
                    ",".join("?" for _ in rows)
                ),
                [user_id, *[row["id"] for row in rows]],
            )
            for conversation_id in {row["thread_id"] for row in rows}:
                remaining = composition.db.query_one(
                    "SELECT count(*) c FROM messages WHERE user_id=? AND thread_id=?",
                    (user_id, conversation_id),
                )
                if remaining is None or remaining["c"] == 0:
                    composition.db.execute(
                        "DELETE FROM conversations WHERE id=? AND user_id=?",
                        (conversation_id, user_id),
                    )
        return deleted_source_ids, deleted_memory_ids

    # -- export ---------------------------------------------------------------

    def export(self, context: MemoryAccessContext) -> tuple[AdapterDecision, Mapping[str, Any] | None]:
        decision = self.govern(
            context, operation="export", purpose=context.purpose, scope={"owner_id": context.owner_id}
        )
        if decision.outcome != AdapterOutcome.ALLOW:
            return decision, None
        if self._composition is None:
            return decision, None
        user_id = self._composition.identity.memoryos_user_id
        try:
            canonical_export = dict(self._composition.service.export(user_id))
            canonical_export["conversations"] = [
                dict(row)
                for row in self._composition.db.query(
                    "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id=?"
                    " ORDER BY created_at",
                    (user_id,),
                )
            ]
            canonical_export["messages"] = [
                dict(row)
                for row in self._composition.db.query(
                    "SELECT thread_id, role, content, metadata, created_at FROM messages"
                    " WHERE user_id=? ORDER BY created_at, id LIMIT 5000",
                    (user_id,),
                )
            ]
            canonical_export["retrieval_stats"] = self._composition.service.stats(user_id)
        except Exception as exc:
            return (
                AdapterDecision(
                    outcome=AdapterOutcome.UNAVAILABLE,
                    decision_id=decision.decision_id,
                    owner_id=decision.owner_id,
                    operation=decision.operation,
                    purpose=decision.purpose,
                    reason=f"canonical MEMORY//OS export failed; failing closed: {type(exc).__name__}: {exc}",
                    policy_version=decision.policy_version,
                    constraints=decision.constraints,
                    production_memoryos_verified=self._real_memoryos_verified,
                ),
                None,
            )
        export_data = {
            "origin": {
                "canonical_store": "memoryos-v10.2.0 (authoritative)",
                "derived_view": "zorq PersonalContinuityStore (derived, governance inherited)",
                "adapter_contract_version": self.contract_version,
                "integration_status": self.integration_status,
            },
            "canonical": canonical_export,
            "derived_view": dict(self.store.export_owner(context.owner_id)),
        }
        return decision, export_data


# ---------------------------------------------------------------------------
# Phase 3A contract protocol adapter (govern / retrieve / store / delete over
# domain-contract records). Composed pair — the protocol method names collide
# with the engine-boundary names, so this is a separate class delegating to
# ProductionMemoryOSAdapter.
# ---------------------------------------------------------------------------


def _context_from_owner(owner_id: str, principal_id: str, purpose: str) -> MemoryAccessContext:
    return MemoryAccessContext(
        owner_id=owner_id,
        principal_id=principal_id,
        authenticated_owner_id=owner_id,
        purpose=purpose or "phase-3a-contract-operation",
    )


class ProductionMemoryOSContractAdapter(MemoryOSAdapterContract):
    """``MemoryOSAdapterContract`` implementation over the production adapter.

    Requires the composed pair (production adapter + engine) because the 3A
    retrieve contract is served through the engine's governed retrieval path.
    """

    contract_version = MEMORYOS_V10_ADAPTER_VERSION

    def __init__(self, adapter: ProductionMemoryOSAdapter, engine: Any) -> None:
        self.adapter = adapter
        self.engine = engine

    # -- govern ------------------------------------------------------------

    def govern(self, request: MemoryGovernanceRequest) -> MemoryGovernanceDecision:
        context = _context_from_owner(request.owner_id, request.principal_id, request.purpose)
        decision = self.adapter.govern(
            context,
            operation=request.operation,
            purpose=request.purpose,
            privacy_class=request.privacy_class,
            scope={"request_id": request.request_id, "source_ids": list(request.source_ids)},
        )
        return MemoryGovernanceDecision(
            decision_id=decision.decision_id,
            request_id=request.request_id,
            owner_id=request.owner_id,
            created_at=utc_now(),
            status=GovernanceStatus(decision.outcome.value),
            reason=decision.reason,
            policy_version=decision.policy_version,
        )

    # -- retrieve ----------------------------------------------------------

    def retrieve(self, request: MemoryRetrievalRequest) -> MemoryRetrievalResult:
        context = _context_from_owner(request.owner_id, request.principal_id, request.purpose)
        mode_map = {
            RetrievalMode.EXACT: RetrievalModeName.EXACT,
            RetrievalMode.SEMANTIC: RetrievalModeName.SEMANTIC,
            RetrievalMode.TEMPORAL: RetrievalModeName.TEMPORAL,
            RetrievalMode.RELATIONAL: RetrievalModeName.RELATIONAL,
            RetrievalMode.CAUSAL_HISTORICAL: RetrievalModeName.CAUSAL,
        }
        modes = tuple(mode_map[m] for m in request.modes if m in mode_map)
        if not modes:
            modes = (RetrievalModeName.EXACT,)
        query = ContinuityQuery(
            query_text=request.query_text,
            modes=modes,
            temporal_start=request.temporal_start,
            temporal_end=request.temporal_end,
            calendar_timezone=None,
            source_ids=tuple(request.scope.get("source_ids", ())),
            message_ids=tuple(request.scope.get("message_ids", ())),
            conversation_id=request.scope.get("conversation_id"),
            project_id=request.project_id,
            entity_ids=tuple(request.entity_ids),
            limit=int(request.scope.get("limit", 20)),
        )
        try:
            response = self.adapter.retrieve(context, query, self.engine)
        except Exception as exc:
            return MemoryRetrievalResult(
                result_id=_stable_id(request.retrieval_id, "unavailable", prefix="mrr"),
                retrieval_id=request.retrieval_id,
                owner_id=request.owner_id,
                created_at=utc_now(),
                governance_status=GovernanceStatus.UNAVAILABLE,
                hits=(),
                message=f"production retrieval failed; failing closed: {type(exc).__name__}: {exc}",
            )
        hits: list[MemoryRetrievalHit] = []
        for record in response.source_records:
            relevance = min(1.0, max(0.0, float(response.source_scores.get(record.source_id, 0.0))))
            provenance = Provenance(
                provenance_id=_stable_id(record.owner_id, record.source_id, prefix="prov"),
                owner_id=record.owner_id,
                source_type=SourceType.CONVERSATION_MESSAGE,
                source_id=record.source_id,
                created_at=record.ingested_at,
                observed_at=record.event_time,
                conversation_id=record.conversation_id,
                message_id=record.message_id,
                confidence=1.0,
                verification_state=VerificationState.UNVERIFIED,
            )
            hits.append(
                MemoryRetrievalHit(
                    hit_id=_stable_id(request.retrieval_id, record.source_id, prefix="hit"),
                    owner_id=record.owner_id,
                    record_id=record.source_id,
                    source_id=record.source_id,
                    created_at=record.ingested_at,
                    relevance=relevance,
                    temporal_metadata=TemporalExtent(
                        event_time=record.event_time,
                        created_at=record.ingested_at,
                        ingested_at=record.ingested_at,
                        local_display_time=record.local_display_time,
                        timezone_name=record.timezone_name,
                        utc_offset_minutes=record.utc_offset_minutes,
                    ),
                    governance_status=GovernanceStatus(response.status.value),
                    provenance=provenance,
                    conflict_state=record.lifecycle_state,
                )
            )
        return MemoryRetrievalResult(
            result_id=_stable_id(request.retrieval_id, "result", prefix="mrr"),
            retrieval_id=request.retrieval_id,
            owner_id=request.owner_id,
            created_at=utc_now(),
            governance_status=GovernanceStatus(response.status.value),
            hits=tuple(hits),
            message=response.message,
        )

    # -- store -------------------------------------------------------------

    def store(self, request: MemoryStorageRequest) -> MemoryGovernanceDecision:
        source = request.source
        context = _context_from_owner(source.owner_id, source.owner_id, "phase-3a-contract-store")
        # The engine's store contract requires a registered conversation;
        # register it through the governed engine path (idempotent upsert).
        conversation_id = source.conversation_id or "contract"
        if hasattr(self.engine, "start_conversation"):
            self.engine.start_conversation(
                context,
                conversation_id=conversation_id,
                started_at=source.observed_at,
            )
        record = SourceRecord(
            source_id=source.source_id,
            owner_id=source.owner_id,
            conversation_id=conversation_id,
            message_id=source.message_id or source.source_id,
            # The 3A MemorySource contract carries content by reference and
            # has no role field; contract stores default to the user role.
            role="user",
            content=str(source.content_ref),
            sequence=1,
            event_time=source.observed_at,
            ingested_at=source.ingested_at,
            privacy_class=source.privacy_class,
            retention_mode=source.retention_mode,
            provenance={
                "origin": "phase-3a-contract",
                "storage_request_id": request.storage_request_id,
                "governance_decision_id": request.governance_decision_id,
            },
            lifecycle_state=LifecycleState.CURRENT,
        )
        decision, _stored, _inserted = self.adapter.store_source(context, record)
        return MemoryGovernanceDecision(
            decision_id=decision.decision_id,
            request_id=request.storage_request_id,
            owner_id=request.owner_id,
            created_at=utc_now(),
            status=GovernanceStatus(decision.outcome.value),
            reason=decision.reason,
            policy_version=decision.policy_version,
        )

    # -- delete ------------------------------------------------------------

    def delete(self, request: DeletionRequest) -> DeletionResult:
        context = _context_from_owner(request.owner_id, request.principal_id, "phase-3a-contract-delete")
        scope: dict[str, Any] = {}
        if request.conversation_id:
            scope["conversation_id"] = request.conversation_id
        if request.project_id:
            scope["project_id"] = request.project_id
        if request.item_ids:
            scope["source_ids"] = list(request.item_ids)
        if request.temporal_start:
            scope["temporal_start"] = _iso(request.temporal_start)
        if request.temporal_end:
            scope["temporal_end"] = _iso(request.temporal_end)
        scope_type = request.scope_type
        deletion_id = request.deletion_id
        include_backups = DeletionPropagationTarget.BACKUPS in request.requested_propagation
        report = self.adapter.delete(
            context,
            deletion_id=deletion_id,
            scope_type=scope_type,
            scope=scope,
            include_backups=include_backups,
        )
        propagated = {
            target: DeletionStatus(status.value)
            for target, status in report.propagated.items()
        }
        unknown_targets = tuple(
            target for target, status in propagated.items() if status == DeletionStatus.UNKNOWN
        )
        return DeletionResult(
            result_id=_stable_id(deletion_id, "result", prefix="del"),
            deletion_id=deletion_id,
            owner_id=request.owner_id,
            created_at=utc_now(),
            status=DeletionStatus(report.status.value),
            propagated=propagated,
            retained_by_policy=report.retained_by_policy,
            unknown_targets=unknown_targets,
            message=report.message,
        )


def compose_production_memoryos(
    store: PersonalContinuityStore,
    policy_engine: MemoryCapturePolicyEngine,
    *,
    backend_root: Path | str,
    data_path: Path | str,
    vector_path: Path | str | None = None,
    identity: OwnerIdentityMapping,
    disable_embeddings: bool = False,
) -> ProductionMemoryOSAdapter:
    """Convenience composition entry point (see ProductionMemoryOSAdapter.compose)."""
    return ProductionMemoryOSAdapter.compose(
        store,
        policy_engine,
        backend_root=backend_root,
        data_path=data_path,
        vector_path=vector_path,
        identity=identity,
        disable_embeddings=disable_embeddings,
    )
