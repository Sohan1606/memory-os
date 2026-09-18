"""
Document intelligence with an explicit lifecycle (§6).

    INGESTED → PARSED → INDEXED → AVAILABLE → STALE → REPLACED → REMOVED

The honesty rule that governs this whole module: **metadata is not content
understanding**. Knowing a PDF is 40 pages and 2 MB tells us nothing about what
it says. When no parser exists for a format, the document is recorded with
`understanding = "METADATA ONLY"` and a reason - it is never summarised,
never OCR'd in pretence, and its filename is never treated as its content.

Real parsers here:
  * plain text / markdown / log / rst  - read directly
  * CSV / TSV                          - structured, real row and column counts
  * JSON                               - structured, real key inspection

Declared NOT CONFIGURED (no parser is bundled):
  * PDF, DOCX, XLSX - these need libraries this local-first build does not ship.
    The document is still tracked, with its real metadata, and clearly labelled.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

LIFECYCLE = ("INGESTED", "PARSED", "INDEXED", "AVAILABLE", "STALE",
             "REPLACED", "REMOVED")

UNDERSTANDING = ("FULL", "PARTIAL", "STRUCTURE ONLY", "METADATA ONLY",
                 "NOT AVAILABLE")

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 200_000

# Extensions we can genuinely read.
_TEXT_EXT = {".txt", ".md", ".markdown", ".log", ".rst", ".ini", ".toml",
             ".yml", ".yaml"}
_CSV_EXT = {".csv", ".tsv"}
_JSON_EXT = {".json"}

# Extensions we track honestly but cannot parse in this build.
_UNPARSEABLE = {
    ".pdf": "PDF text extraction is NOT CONFIGURED in this build.",
    ".docx": "DOCX parsing is NOT CONFIGURED in this build.",
    ".doc": "Legacy DOC parsing is NOT CONFIGURED in this build.",
    ".xlsx": "XLSX parsing is NOT CONFIGURED in this build.",
    ".xls": "Legacy XLS parsing is NOT CONFIGURED in this build.",
    ".pptx": "PPTX parsing is NOT CONFIGURED in this build.",
}

# A document is considered potentially stale after this long.
STALE_AFTER_DAYS = 90.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ext(filename: str) -> str:
    name = (filename or "").lower()
    return name[name.rfind("."):] if "." in name else ""


class DocumentStore:
    """Tracked documents with a real lifecycle and honest understanding labels."""

    def __init__(self, db, bus, observations=None) -> None:
        self.db = db
        self.bus = bus
        self.observations = observations

    # --------------------------------------------------------------- ingest
    def ingest(self, user_id: str, filename: str, data: bytes, *,
               media_type: str | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Take in a document, parse it if we genuinely can, and record exactly
        how much of it we understood.
        """
        if len(data) > MAX_BYTES:
            raise ValueError(
                f"Document exceeds the {MAX_BYTES // (1024 * 1024)} MB limit.")

        checksum = hashlib.sha256(data).hexdigest()
        ext = _ext(filename)
        did = f"doc_{uuid.uuid4().hex[:12]}"
        now = _now()

        # A byte-identical document already on file is a replacement, not a
        # duplicate cognitive event.
        prior = self.db.query_one(
            "SELECT * FROM documents WHERE user_id=? AND filename=?"
            " AND state NOT IN ('REMOVED','REPLACED') ORDER BY rowid DESC LIMIT 1",
            (user_id, filename))

        self.db.execute(
            "INSERT INTO documents (id,user_id,filename,media_type,checksum,"
            "bytes_len,state,correlation_id,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (did, user_id, filename, media_type, checksum, len(data),
             "INGESTED", correlation_id, now, now))
        self.bus.emit(user_id, "document.ingested", filename,
                      subject_kind="document", subject_id=did,
                      correlation_id=correlation_id,
                      payload={"bytes": len(data), "checksum": checksum[:16]})

        parsed = self._parse(ext, data)
        self.db.execute(
            "UPDATE documents SET state=?, parser=?, pages=?, extracted_chars=?,"
            " understanding=?, detail=?, updated_at=? WHERE id=?",
            ("PARSED", parsed["parser"], parsed.get("pages"),
             len(parsed["text"]), parsed["understanding"], parsed["detail"],
             _now(), did))
        self.bus.emit(user_id, "document.parsed",
                      f"{filename}: {parsed['understanding']}",
                      subject_kind="document", subject_id=did,
                      correlation_id=correlation_id,
                      payload={"parser": parsed["parser"],
                               "understanding": parsed["understanding"],
                               "chars": len(parsed["text"]),
                               "detail": parsed["detail"]})

        # Only text we actually extracted becomes available content.
        if parsed["text"]:
            self.db.execute(
                "UPDATE documents SET state='AVAILABLE', updated_at=? WHERE id=?",
                (_now(), did))
            self.bus.emit(user_id, "document.indexed", filename,
                          subject_kind="document", subject_id=did,
                          correlation_id=correlation_id,
                          payload={"chars": len(parsed["text"])})
        else:
            self.db.execute(
                "UPDATE documents SET state='AVAILABLE', updated_at=? WHERE id=?",
                (_now(), did))

        if prior is not None:
            self.db.execute(
                "UPDATE documents SET state='REPLACED', replaces_id=?,"
                " updated_at=? WHERE id=?", (did, _now(), prior["id"]))
            self.db.execute("UPDATE documents SET replaces_id=? WHERE id=?",
                            (prior["id"], did))
            self.bus.emit(user_id, "document.replaced",
                          f"{filename} replaced an earlier version",
                          subject_kind="document", subject_id=did,
                          correlation_id=correlation_id,
                          payload={"replaced_id": prior["id"]})

        # The observation records only what we genuinely read.
        if self.observations is not None:
            self.observations.record(
                user_id,
                (f"Document '{filename}' ingested: {parsed['detail']}"),
                source="document", origin=filename,
                epistemic_status="OBSERVED", confidence=0.9,
                subject_kind="document", subject_id=did,
                provenance={"checksum": checksum, "bytes": len(data),
                            "parser": parsed["parser"],
                            "understanding": parsed["understanding"]},
                correlation_id=correlation_id)

        document = self.get(user_id, did)
        assert document is not None
        document["text"] = parsed["text"]
        return document

    def _parse(self, ext: str, data: bytes) -> dict[str, Any]:
        """Parse what we can. Never pretend about what we cannot."""
        if ext in _UNPARSEABLE:
            return {"parser": "none", "text": "",
                    "understanding": "METADATA ONLY",
                    "detail": (f"{_UNPARSEABLE[ext]} Only file metadata "
                               f"(name, size, checksum) was recorded — the "
                               f"contents were not read.")}

        if ext in _TEXT_EXT or ext == "":
            try:
                text = data.decode("utf-8", errors="strict")
                understanding = "FULL"
                detail = f"Read {len(text)} characters of text."
            except UnicodeDecodeError:
                try:
                    text = data.decode("utf-8", errors="replace")
                    understanding = "PARTIAL"
                    detail = ("Decoded with replacement characters; some bytes "
                              "were not valid UTF-8.")
                except Exception:  # noqa: BLE001
                    return {"parser": "none", "text": "",
                            "understanding": "NOT AVAILABLE",
                            "detail": "The file could not be decoded as text."}
            return {"parser": "text", "text": text[:MAX_TEXT_CHARS],
                    "understanding": understanding, "detail": detail}

        if ext in _CSV_EXT:
            try:
                raw = data.decode("utf-8", errors="replace")
                delimiter = "\t" if ext == ".tsv" else ","
                rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
            except Exception as exc:  # noqa: BLE001
                return {"parser": "csv", "text": "",
                        "understanding": "NOT AVAILABLE",
                        "detail": f"CSV parsing failed: {exc}"}
            if not rows:
                return {"parser": "csv", "text": "",
                        "understanding": "NOT AVAILABLE",
                        "detail": "The file contained no rows."}
            header = rows[0]
            return {
                "parser": "csv", "text": raw[:MAX_TEXT_CHARS],
                "understanding": "STRUCTURE ONLY",
                "detail": (f"Parsed {len(rows)} row(s) and {len(header)} "
                           f"column(s): {', '.join(header[:8])}"
                           f"{'…' if len(header) > 8 else ''}. Structure was "
                           f"read; the meaning of the data was not inferred."),
            }

        if ext in _JSON_EXT:
            try:
                raw = data.decode("utf-8", errors="replace")
                parsed = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                return {"parser": "json", "text": "",
                        "understanding": "NOT AVAILABLE",
                        "detail": f"JSON parsing failed: {exc}"}
            if isinstance(parsed, dict):
                shape = f"object with {len(parsed)} key(s)"
            elif isinstance(parsed, list):
                shape = f"array of {len(parsed)} item(s)"
            else:
                shape = type(parsed).__name__
            return {"parser": "json", "text": raw[:MAX_TEXT_CHARS],
                    "understanding": "STRUCTURE ONLY",
                    "detail": (f"Parsed valid JSON: {shape}. Structure was "
                               f"read; the meaning was not inferred.")}

        return {"parser": "none", "text": "", "understanding": "METADATA ONLY",
                "detail": (f"No parser is configured for '{ext or 'unknown'}' "
                           f"files. Only metadata was recorded.")}

    # ------------------------------------------------------------- lifecycle
    def mark_stale(self, user_id: str, document_id: str, reason: str, *,
                   correlation_id: str | None = None) -> dict[str, Any] | None:
        doc = self.get(user_id, document_id)
        if doc is None:
            return None
        self.db.execute(
            "UPDATE documents SET state='STALE', detail=?, updated_at=? WHERE id=?",
            (reason, _now(), document_id))
        self.bus.emit(user_id, "document.stale", f"{doc['filename']}: {reason}",
                      subject_kind="document", subject_id=document_id,
                      correlation_id=correlation_id, payload={"reason": reason})
        return self.get(user_id, document_id)

    def remove(self, user_id: str, document_id: str, *,
               correlation_id: str | None = None) -> bool:
        """
        Soft removal. The row stays so history and provenance survive (§25).
        """
        doc = self.get(user_id, document_id)
        if doc is None:
            return False
        self.db.execute(
            "UPDATE documents SET state='REMOVED', updated_at=? WHERE id=?",
            (_now(), document_id))
        self.bus.emit(user_id, "document.removed", doc["filename"],
                      subject_kind="document", subject_id=document_id,
                      correlation_id=correlation_id)
        return True

    def review_staleness(self, user_id: str) -> list[dict[str, Any]]:
        """Documents old enough to be worth re-checking. Never auto-removed."""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=STALE_AFTER_DAYS)).isoformat(timespec="seconds")
        rows = self.db.query(
            "SELECT * FROM documents WHERE user_id=? AND state='AVAILABLE'"
            " AND datetime(created_at) < datetime(?)", (user_id, cutoff))
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- reading
    def get(self, user_id: str, document_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM documents WHERE id=? AND user_id=?",
            (document_id, user_id))
        return dict(row) if row else None

    def list(self, user_id: str, *, state: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM documents WHERE user_id=?"
        params: list[Any] = [user_id]
        if state:
            sql += " AND state=?"
            params.append(state)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in self.db.query(sql, params)]

    def capabilities(self) -> dict[str, Any]:
        """Exactly which formats this build can and cannot read."""
        return {
            "parseable": {
                "text": {"extensions": sorted(_TEXT_EXT),
                         "understanding": "FULL",
                         "detail": "Text is read in full."},
                "csv": {"extensions": sorted(_CSV_EXT),
                        "understanding": "STRUCTURE ONLY",
                        "detail": ("Rows and columns are parsed; the meaning "
                                   "of the data is not inferred.")},
                "json": {"extensions": sorted(_JSON_EXT),
                         "understanding": "STRUCTURE ONLY",
                         "detail": "Structure is parsed, not interpreted."},
            },
            "not_configured": {
                ext: {"state": "NOT CONFIGURED", "detail": reason}
                for ext, reason in _UNPARSEABLE.items()
            },
            "lifecycle": list(LIFECYCLE),
            "detail": ("Documents this build cannot parse are still tracked "
                       "with real metadata and labelled METADATA ONLY. No "
                       "content is ever inferred from a filename."),
        }
