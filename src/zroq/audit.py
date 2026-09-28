"""Append-only, hash-chained audit log."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid
from typing import Any, Mapping

from .contracts import AuditEvent, canonical_json, sha256_digest, utc_now


class AuditIntegrityError(RuntimeError):
    pass


class AuditLog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._events: list[AuditEvent] = []
        self._lock = threading.RLock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._load_and_verify()

    def append(
        self,
        event_type: str,
        actor: str,
        task_id: str | None,
        action_id: str | None,
        payload: Mapping[str, Any],
    ) -> AuditEvent:
        with self._lock:
            sequence = len(self._events) + 1
            previous_hash = self._events[-1].event_hash if self._events else "GENESIS"
            timestamp = utc_now()
            material = {
                "sequence": sequence,
                "event_type": event_type,
                "timestamp": timestamp.isoformat(),
                "actor": actor,
                "task_id": task_id,
                "action_id": action_id,
                "payload": dict(payload),
                "previous_hash": previous_hash,
            }
            event_hash = sha256_digest(material)
            event = AuditEvent(
                sequence=sequence,
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                timestamp=timestamp,
                actor=actor,
                task_id=task_id,
                action_id=action_id,
                payload=dict(payload),
                previous_hash=previous_hash,
                event_hash=event_hash,
            )
            self._events.append(event)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(canonical_json(event) + "\n")
            return event

    def events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def verify_integrity(self) -> bool:
        with self._lock:
            previous = "GENESIS"
            for expected_sequence, event in enumerate(self._events, start=1):
                if event.sequence != expected_sequence or event.previous_hash != previous:
                    raise AuditIntegrityError("audit chain ordering or previous hash mismatch")
                material = {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp.isoformat(),
                    "actor": event.actor,
                    "task_id": event.task_id,
                    "action_id": event.action_id,
                    "payload": dict(event.payload),
                    "previous_hash": event.previous_hash,
                }
                if sha256_digest(material) != event.event_hash:
                    raise AuditIntegrityError("audit event hash mismatch")
                previous = event.event_hash
            return True

    def _load_and_verify(self) -> None:
        self._events.clear()
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                raw["timestamp"] = datetime.fromisoformat(raw["timestamp"])
                self._events.append(AuditEvent(**raw))
        self.verify_integrity()
