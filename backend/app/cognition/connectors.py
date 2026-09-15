"""
Connector framework and research abstraction (§26-§27).

This module defines *interfaces*, not integrations. MEMORY//OS is local-first
and this build ships with nothing connected. That is a legitimate, honestly
reported state - `NOT CONNECTED` is a valid answer, and it is the only answer
this module will give until a real provider is wired in.

What exists here:
  * a registry describing what a connector WOULD provide, with its scopes
  * a uniform `NOT CONNECTED` result shape so callers cannot mistake absence
    for emptiness ("no calendar events" vs "no calendar")
  * a research session model that tracks questions, claims and contradictions
    but refuses to produce findings without a provider

What deliberately does NOT exist:
  * any hardcoded "sample" calendar entry, email, or search result
  * any code path that returns data while claiming a provider is connected
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

NOT_CONNECTED = "NOT CONNECTED"
NOT_CONFIGURED = "RESEARCH PROVIDER NOT CONFIGURED"

CONNECTOR_STATES = ("NOT CONNECTED", "CONFIGURED", "DEGRADED", "ERROR")

RESEARCH_STATES = ("DRAFT", "BLOCKED", "COMPLETED")

# The connector interfaces this architecture is prepared for. Declaring an
# interface is not the same as having one: every entry below is NOT CONNECTED.
DECLARED: tuple[dict[str, Any], ...] = (
    {"name": "calendar",
     "capabilities": ["read_events", "read_availability"],
     "scopes": ["calendar.read"],
     "would_provide": "Commitments and deadlines with real times."},
    {"name": "email",
     "capabilities": ["read_threads"],
     "scopes": ["mail.read"],
     "would_provide": "Open loops and awaited replies."},
    {"name": "files",
     "capabilities": ["read_documents"],
     "scopes": ["files.read"],
     "would_provide": "Documents beyond those uploaded by hand."},
    {"name": "tasks",
     "capabilities": ["read_tasks", "write_tasks"],
     "scopes": ["tasks.read", "tasks.write"],
     "would_provide": "External task state to reconcile against missions."},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConnectorRegistry:
    """Declares connector interfaces and reports their true state."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def declare_defaults(self, user_id: str) -> list[dict[str, Any]]:
        """Register the known interfaces, all as NOT CONNECTED."""
        for spec in DECLARED:
            existing = self.db.query_one(
                "SELECT id FROM connectors WHERE user_id=? AND name=?",
                (user_id, spec["name"]))
            if existing:
                continue
            cid = f"conn_{uuid.uuid4().hex[:10]}"
            self.db.execute(
                "INSERT INTO connectors (id,user_id,name,state,capabilities,"
                "scopes,detail,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (cid, user_id, spec["name"], NOT_CONNECTED,
                 json.dumps(spec["capabilities"]), json.dumps(spec["scopes"]),
                 spec["would_provide"], _now()))
            self.bus.emit(user_id, "connector.declared", spec["name"],
                          subject_kind="connector", subject_id=cid,
                          payload={"state": NOT_CONNECTED})
        return self.list(user_id)

    def list(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM connectors WHERE user_id=? ORDER BY name", (user_id,))
        out = []
        for row in rows:
            item = dict(row)
            for key in ("capabilities", "scopes"):
                try:
                    item[key] = json.loads(item.get(key) or "[]")
                except (TypeError, ValueError):
                    item[key] = []
            out.append(item)
        return out

    def fetch(self, user_id: str, name: str,
              correlation_id: str | None = None) -> dict[str, Any]:
        """
        Attempt to read from a connector.

        Always returns a NOT CONNECTED result in this build. Critically, the
        shape distinguishes "there is no connector" from "the connector
        returned nothing" - conflating those would be a lie about the world.
        """
        row = self.db.query_one(
            "SELECT * FROM connectors WHERE user_id=? AND name=?", (user_id, name))
        if row is None:
            return {"connected": False, "state": NOT_CONNECTED, "name": name,
                    "items": None,
                    "detail": (f"No connector named '{name}' is declared. "
                               f"This is not an empty result — there is no "
                               f"source to read from.")}
        self.bus.emit(user_id, "connector.unavailable", name,
                      subject_kind="connector", subject_id=row["id"],
                      correlation_id=correlation_id,
                      payload={"state": row["state"]})
        self.db.execute("UPDATE connectors SET last_checked_at=? WHERE id=?",
                        (_now(), row["id"]))
        return {
            "connected": False, "state": row["state"], "name": name,
            "items": None,
            "detail": (f"The '{name}' connector is {row['state']}. No data was "
                       f"retrieved because no provider is configured — this is "
                       f"not the same as the source being empty."),
        }

    def status(self, user_id: str) -> dict[str, Any]:
        connectors = self.list(user_id)
        connected = [c for c in connectors if c["state"] == "CONFIGURED"]
        return {
            "connectors": connectors,
            "total": len(connectors),
            "connected": len(connected),
            "detail": ("No connectors are configured in this build. The "
                       "interfaces are declared so the architecture is ready, "
                       "but nothing external is being read." if not connected
                       else f"{len(connected)} connector(s) configured."),
        }


class ResearchMode:
    """
    Research sessions (§27).

    The state model is real - questions, claims, contradictions and open
    questions are all tracked. What is absent is a provider to gather evidence,
    so a session cannot progress past BLOCKED. It will not invent findings.
    """

    def __init__(self, db, bus, provider=None) -> None:
        self.db = db
        self.bus = bus
        self.provider = provider   # always None in this build

    @property
    def available(self) -> bool:
        return self.provider is not None

    def start(self, user_id: str, question: str,
              correlation_id: str | None = None) -> dict[str, Any]:
        question = " ".join((question or "").split())[:500]
        if len(question) < 3:
            raise ValueError("A research question is required.")
        rid = f"rs_{uuid.uuid4().hex[:10]}"
        now = _now()
        state = "BLOCKED" if not self.available else "DRAFT"
        self.db.execute(
            "INSERT INTO research_sessions (id,user_id,question,state,"
            "provider_state,claims,contradictions,open_questions,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, user_id, question, state,
             NOT_CONFIGURED if not self.available else "CONFIGURED",
             json.dumps([]), json.dumps([]), json.dumps([question]), now, now))

        self.bus.emit(user_id, "research.requested", question,
                      subject_kind="research", subject_id=rid,
                      correlation_id=correlation_id,
                      payload={"state": state})
        if not self.available:
            self.bus.emit(user_id, "research.unavailable", NOT_CONFIGURED,
                          subject_kind="research", subject_id=rid,
                          correlation_id=correlation_id)
        return self.get(user_id, rid)  # type: ignore[return-value]

    def get(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM research_sessions WHERE id=? AND user_id=?",
            (session_id, user_id))
        if row is None:
            return None
        item = dict(row)
        for key in ("claims", "contradictions", "open_questions"):
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except (TypeError, ValueError):
                item[key] = []
        item["detail"] = (
            f"{NOT_CONFIGURED}. The question and its open threads are tracked, "
            f"but no evidence has been gathered because there is no research "
            f"provider. No findings have been generated."
            if item["provider_state"] == NOT_CONFIGURED else
            f"Research session is {item['state']}.")
        return item

    def list(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM research_sessions WHERE user_id=? ORDER BY rowid DESC"
            " LIMIT ?", (user_id, int(limit)))
        return [self.get(user_id, r["id"]) for r in rows]  # type: ignore[misc]

    def status(self, user_id: str) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider_state": ("CONFIGURED" if self.available
                               else NOT_CONFIGURED),
            "sessions": len(self.list(user_id)),
            "detail": ("Research mode has no provider configured. Questions can "
                       "be tracked, but no evidence gathering, no web access "
                       "and no findings are possible." if not self.available
                       else "Research provider is configured."),
        }
