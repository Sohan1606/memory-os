"""
Time machine (§21) - reconstruct what was actually known at a past moment.

This reads ONLY stored history:
  * `world_changes` for world-state transitions (written from V8.3 onward)
  * `mission_events` for mission transitions
  * `cognitive_events` for everything else

The hard rule: if history does not cover the requested moment, the answer is
`HISTORY NOT AVAILABLE`. We never back-project current state onto the past and
present it as a reconstruction. A world fact created last Tuesday genuinely did
not exist last Monday, and a fact whose change history predates V8.3 is reported
as having unknown earlier state rather than an invented one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

UNAVAILABLE = "HISTORY NOT AVAILABLE"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class TimeMachine:
    """Point-in-time reconstruction from recorded history only."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------- coverage
    def coverage(self, user_id: str) -> dict[str, Any]:
        """
        The window history can actually answer for. Anything before
        `earliest` is not reconstructable and must be reported as such.
        """
        rows = [
            self.db.query_one(
                "SELECT MIN(created_at) AS a, MAX(created_at) AS b FROM"
                " cognitive_events WHERE user_id=?", (user_id,)),
            self.db.query_one(
                "SELECT MIN(created_at) AS a, MAX(created_at) AS b FROM"
                " world_changes WHERE user_id=?", (user_id,)),
            self.db.query_one(
                "SELECT MIN(created_at) AS a, MAX(created_at) AS b FROM"
                " mission_events WHERE user_id=?", (user_id,)),
        ]
        starts = [_parse(r["a"]) for r in rows if r and r["a"]]
        ends = [_parse(r["b"]) for r in rows if r and r["b"]]
        starts = [s for s in starts if s]
        ends = [e for e in ends if e]
        if not starts:
            return {"available": False, "earliest": None, "latest": None,
                    "detail": f"{UNAVAILABLE} — no history has been recorded yet."}
        return {"available": True, "earliest": _iso(min(starts)),
                "latest": _iso(max(ends)) if ends else None,
                "detail": (f"History is available from {_iso(min(starts))} "
                           f"onward.")}

    # ------------------------------------------------- world reconstruction
    def world_at(self, user_id: str, when: str,
                 correlation_id: str | None = None) -> dict[str, Any]:
        """
        Reconstruct world state as of `when`, using recorded changes only.
        """
        moment = _parse(when)
        if moment is None:
            return {"available": False, "reason": f"{UNAVAILABLE} — "
                    f"'{when}' is not a valid ISO-8601 timestamp."}
        cov = self.coverage(user_id)
        if not cov["available"]:
            self.bus.emit(user_id, "history.unavailable",
                          "No recorded history", correlation_id=correlation_id,
                          payload={"requested": when})
            return {"available": False, "requested": when,
                    "reason": cov["detail"]}
        earliest = _parse(cov["earliest"])
        if earliest and moment < earliest:
            self.bus.emit(user_id, "history.unavailable",
                          f"Requested {when}, history starts {cov['earliest']}",
                          correlation_id=correlation_id,
                          payload={"requested": when,
                                   "earliest": cov["earliest"]})
            return {"available": False, "requested": when,
                    "earliest": cov["earliest"],
                    "reason": (f"{UNAVAILABLE} — the request predates the "
                               f"earliest recorded history "
                               f"({cov['earliest']}).")}

        entities = self.db.query(
            "SELECT * FROM world_entities WHERE user_id=?", (user_id,))
        reconstructed: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []

        for row in entities:
            entity = dict(row)
            created = _parse(entity.get("created_at"))
            if created and created > moment:
                continue  # did not exist yet - correctly absent

            changes = self.db.query(
                "SELECT * FROM world_changes WHERE user_id=? AND entity_id=?"
                " AND datetime(created_at) <= datetime(?) ORDER BY rowid",
                (user_id, entity["id"], _iso(moment)))
            if not changes:
                # It existed but we have no recorded transitions at or before
                # the moment. We will NOT assume its current state applied then.
                unknown.append({
                    "id": entity["id"], "kind": entity["kind"],
                    "label": entity["label"],
                    "state_at_time": "UNKNOWN",
                    "reason": ("Existed at this time, but no change history was "
                               "recorded at or before this moment. Its state "
                               "then cannot be established."),
                })
                continue
            last = changes[-1]
            reconstructed.append({
                "id": entity["id"], "kind": entity["kind"],
                "label": entity["label"],
                "state_at_time": last["new_state"] or entity["state"],
                "confidence_at_time": last["confidence"],
                "as_of": last["created_at"],
                "established_by": last["change"],
                "changes_before": len(changes),
            })

        self.bus.emit(user_id, "history.reconstructed",
                      f"World state as of {_iso(moment)}",
                      correlation_id=correlation_id,
                      payload={"requested": when,
                               "entities": len(reconstructed),
                               "unknown": len(unknown)})
        return {
            "available": True,
            "requested": when,
            "as_of": _iso(moment),
            "entities": reconstructed,
            "unknown": unknown,
            "coverage": cov,
            "detail": (f"{len(reconstructed)} fact(s) reconstructed from "
                       f"recorded history; {len(unknown)} with no history at "
                       f"that point."),
        }

    # ----------------------------------------------- mission reconstruction
    def missions_at(self, user_id: str, when: str) -> dict[str, Any]:
        moment = _parse(when)
        if moment is None:
            return {"available": False,
                    "reason": f"{UNAVAILABLE} — '{when}' is not a valid timestamp."}
        rows = self.db.query("SELECT * FROM missions WHERE user_id=?", (user_id,))
        out: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        for row in rows:
            created = _parse(row["created_at"])
            if created and created > moment:
                continue
            events = self.db.query(
                "SELECT * FROM mission_events WHERE user_id=? AND mission_id=?"
                " AND datetime(created_at) <= datetime(?) ORDER BY rowid",
                (user_id, row["id"], _iso(moment)))
            if not events:
                unknown.append({"id": row["id"], "title": row["title"],
                                "state_at_time": "UNKNOWN",
                                "reason": "No mission history at that point."})
                continue
            state = None
            for event in events:
                if event["new_state"]:
                    state = event["new_state"]
            out.append({"id": row["id"], "title": row["title"],
                        "state_at_time": state or "UNKNOWN",
                        "as_of": events[-1]["created_at"],
                        "events_before": len(events)})
        if not out and not unknown:
            return {"available": True, "requested": when, "as_of": _iso(moment),
                    "missions": [], "unknown": [],
                    "detail": "No missions existed at that time."}
        return {"available": True, "requested": when, "as_of": _iso(moment),
                "missions": out, "unknown": unknown,
                "detail": f"{len(out)} mission(s) reconstructed."}

    # ------------------------------------------------------------- diffing
    def diff(self, user_id: str, start: str, end: str) -> dict[str, Any]:
        """What changed in the world between two moments, from real records."""
        a = self.world_at(user_id, start)
        b = self.world_at(user_id, end)
        if not a.get("available") or not b.get("available"):
            return {"available": False,
                    "reason": (a.get("reason") if not a.get("available")
                               else b.get("reason"))}
        before = {e["id"]: e for e in a["entities"]}
        after = {e["id"]: e for e in b["entities"]}
        added = [after[k] for k in after.keys() - before.keys()]
        removed = [before[k] for k in before.keys() - after.keys()]
        changed = [
            {"id": k, "label": after[k]["label"],
             "from": before[k]["state_at_time"], "to": after[k]["state_at_time"]}
            for k in before.keys() & after.keys()
            if before[k]["state_at_time"] != after[k]["state_at_time"]
        ]
        return {"available": True, "start": start, "end": end,
                "added": added, "removed": removed, "changed": changed,
                "detail": (f"{len(added)} appeared, {len(changed)} changed "
                           f"state, {len(removed)} no longer present.")}

    # ------------------------------------------------------- event timeline
    def timeline(self, user_id: str, *, start: str | None = None,
                 end: str | None = None, limit: int = 200) -> dict[str, Any]:
        sql = "SELECT * FROM cognitive_events WHERE user_id=?"
        params: list[Any] = [user_id]
        if start:
            sql += " AND datetime(created_at) >= datetime(?)"
            params.append(start)
        if end:
            sql += " AND datetime(created_at) <= datetime(?)"
            params.append(end)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = [dict(r) for r in self.db.query(sql, params)]
        if not rows:
            return {"available": False, "events": [],
                    "reason": f"{UNAVAILABLE} — no events in that window."}
        return {"available": True, "events": rows, "count": len(rows)}
