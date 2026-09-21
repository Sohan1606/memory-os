"""
Connected Research engine (V8.4.3) — real external evidence gathering with a
strict, auditable chain:

    SOURCE -> FETCH -> EVIDENCE -> CLAIM -> CORROBORATION/CONFLICT
           -> WORLD MODEL UPDATE (bounded, explicit) -> EXPLANATION

This is a NEW, additive capability. It does not replace or reconfigure the
V8.3 `ResearchMode` / `ConnectorRegistry` in `connectors.py`, which remains
exactly as it was: an honest `BLOCKED — NOT CONFIGURED` state machine for a
declarative connector provider that this build still does not have. This
engine answers a different question — "fetch these real URLs and tell me
honestly what you found" — and never silently claims to be the same feature.

Non-negotiables enforced here (see docs/V8.4.3.md for the full rationale):
  * every fetch goes through `net_security.fetch`, so SSRF/redirect/size
    protection applies uniformly and cannot be bypassed by this layer;
  * a failed fetch is persisted, never discarded — the fetch ledger is the
    literal audit trail phase 2 requires;
  * no evidence is created without real fetched content behind it;
  * no claim exists without evidence_ids/source_ids provenance;
  * corroboration counts INDEPENDENT DOMAINS, not repeated pages on one site;
  * conflicting claims are BOTH preserved, never silently resolved;
  * a claim can influence the World Model only through an explicit,
    confidence-capped, separately-recorded proposal + apply step — it can
    never become a claim, skill, principle, or memory by itself.

Claim extraction and conflict detection are DETERMINISTIC (sentence
splitting + lexical key comparison), not semantic understanding. This is
documented honestly rather than dressed up as comprehension (§ Phase 5/7).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit

from . import content_safety, net_security

SESSION_STATES = ("DRAFT", "RUNNING", "PARTIAL", "COMPLETED", "BLOCKED", "FAILED")

MAX_SOURCES_PER_SESSION = 10
MAX_EVIDENCE_PER_FETCH = 3
MAX_CLAIMS_PER_SESSION = 40
MAX_CLAIM_CHARS = 320
MIN_CLAIM_CHARS = 20

CORROBORATE_SIMILARITY = 0.85     # near-identical statement -> same claim
CONFLICT_KEY_SIMILARITY = 0.82    # same subject, differing value -> conflict

# Bounds applied to externally-sourced confidence. A single, uncorroborated
# web claim is capped low; independently corroborated claims may rise, but
# never to certainty. This is the concrete form of the "evidence != belief"
# / "confidence must be capped" rule from the V8.4.3 spec.
SINGLE_SOURCE_CONFIDENCE_CAP = 0.55
CORROBORATED_CONFIDENCE_CAP = 0.80
WORLD_UPDATE_CONFIDENCE_CAP = 0.60

_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
          r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
          r"nov(?:ember)?|dec(?:ember)?")
_VALUE_TOKEN_RE = re.compile(
    rf"\b(?:{_MONTHS})\b|\b\d[\d,./:%-]*\d\b|\b\d+\b", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_BOILERPLATE_RE = re.compile(
    r"^(cookie|subscribe|sign in|log ?in|advertisement|skip to|all rights "
    r"reserved|privacy policy|terms of (service|use))\b", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _loads(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        return default


def _domain_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    return host.lower()


def _conflict_key(statement: str) -> str:
    key = _VALUE_TOKEN_RE.sub("§", statement.lower())
    key = re.sub(r"[^\w§\s]", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def _value_tokens(statement: str) -> set[str]:
    return {t.lower() for t in _VALUE_TOKEN_RE.findall(statement)}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def split_sentences(text: str) -> list[str]:
    """Deterministic sentence-ish splitting — not linguistic parsing."""
    out: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(para):
            sentence = sentence.strip()
            if MIN_CLAIM_CHARS <= len(sentence) <= MAX_CLAIM_CHARS and not _BOILERPLATE_RE.match(sentence):
                out.append(sentence)
    return out


class ResearchEngine:
    """Real, evidence-backed research sessions with full provenance."""

    def __init__(self, db, bus, world_v2=None) -> None:
        self.db = db
        self.bus = bus
        self.world_v2 = world_v2

    # ------------------------------------------------------------- session
    def start(self, user_id: str, question: str,
             correlation_id: str | None = None) -> dict[str, Any]:
        question = " ".join((question or "").split())[:500]
        if len(question) < 3:
            raise ValueError("A research question is required.")
        sid = _new_id("rsess")
        now = _iso()
        self.db.execute(
            "INSERT INTO research_sessions_v2 (id,user_id,question,state,"
            "provider_state,correlation_id,source_count,evidence_count,"
            "claim_count,conflict_count,open_questions,detail,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, user_id, question, "DRAFT", "CONFIGURED", correlation_id,
             0, 0, 0, 0, json.dumps([question]),
             "Session created. No sources fetched yet.", now, now))
        self.bus.emit(user_id, "research.requested", question,
                      subject_kind="research", subject_id=sid,
                      correlation_id=correlation_id,
                      payload={"question": question})
        self.bus.emit(user_id, "research.started",
                      f"Started research session for: {question[:120]}",
                      subject_kind="research", subject_id=sid,
                      correlation_id=correlation_id, payload={})
        return self.get(user_id, sid)  # type: ignore[return-value]

    def _row(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM research_sessions_v2 WHERE id=? AND user_id=?",
            (session_id, user_id))
        return dict(row) if row else None

    def get(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        item = self._row(user_id, session_id)
        if item is None:
            return None
        item["open_questions"] = _loads(item.get("open_questions"), [])
        return item

    def list(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM research_sessions_v2 WHERE user_id=? "
            "ORDER BY rowid DESC LIMIT ?", (user_id, int(limit)))
        return [self.get(user_id, r["id"]) for r in rows]  # type: ignore[misc]

    def status(self, user_id: str) -> dict[str, Any]:
        sessions = self.list(user_id)
        return {
            "available": True,
            "detail": ("Connected Research fetches real http(s) URLs through "
                      "an SSRF-defended pipeline. There is no search-engine "
                      "integration: URLs must be supplied explicitly (by the "
                      "user or a tool call), never invented."),
            "session_count": len(sessions),
            "limits": {
                "max_sources_per_session": MAX_SOURCES_PER_SESSION,
                "max_evidence_per_fetch": MAX_EVIDENCE_PER_FETCH,
                "max_claims_per_session": MAX_CLAIMS_PER_SESSION,
            },
        }

    def _touch(self, session_id: str, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(
            f"UPDATE research_sessions_v2 SET {sets}, updated_at=? WHERE id=?",
            (*fields.values(), _iso(), session_id))

    # --------------------------------------------------------------- fetch
    def fetch(self, user_id: str, session_id: str, url: str,
             correlation_id: str | None = None) -> dict[str, Any]:
        """
        Fetch one real URL into a session: source registration, fetch
        ledger entry, evidence extraction and claim derivation — or an
        honest, persisted failure at any stage.
        """
        session = self._row(user_id, session_id)
        if session is None:
            return {"status": "NOT_FOUND",
                    "detail": f"No research session '{session_id}' for this user."}

        existing_sources = self.db.query(
            "SELECT * FROM research_sources WHERE session_id=?", (session_id,))
        by_url = {s["canonical_url"]: dict(s) for s in existing_sources}
        source = by_url.get(url)

        if source is None and len(existing_sources) >= MAX_SOURCES_PER_SESSION:
            fetch_id = _new_id("rfetch")
            now = _iso()
            self.db.execute(
                "INSERT INTO research_fetches (id,session_id,user_id,source_id,"
                "requested_url,final_url,status,http_status,content_type,"
                "latency_ms,redirect_count,redirect_chain,content_hash,"
                "bytes_read,error_code,error_detail,correlation_id,fetched_at,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fetch_id, session_id, user_id, None, url, None, "BLOCKED",
                 None, None, 0, 0, json.dumps([]), None, 0,
                 "SOURCE_LIMIT_EXCEEDED",
                 f"Session already has {len(existing_sources)} sources; the "
                 f"{MAX_SOURCES_PER_SESSION}-source bound protects against "
                 "unbounded fetching.", correlation_id, now, now))
            self.bus.emit(user_id, "research.fetch_blocked", url,
                          subject_kind="research", subject_id=session_id,
                          correlation_id=correlation_id,
                          payload={"error_code": "SOURCE_LIMIT_EXCEEDED"})
            return {"status": "BLOCKED", "error_code": "SOURCE_LIMIT_EXCEEDED",
                    "detail": "Source limit for this session reached."}

        if source is None:
            source_id = _new_id("rsrc")
            domain = _domain_of(url)
            now = _iso()
            self.db.execute(
                "INSERT INTO research_sources (id,session_id,user_id,"
                "canonical_url,domain,title,publisher,source_type,"
                "source_quality,availability,metadata,discovered_at,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_id, session_id, user_id, url, domain, None, None,
                 "web", None, "UNKNOWN", json.dumps({}), now, now))
            source = self.db.query_one(
                "SELECT * FROM research_sources WHERE id=?", (source_id,))
            source = dict(source)
            self.bus.emit(user_id, "research.source_registered", url,
                          subject_kind="research", subject_id=session_id,
                          correlation_id=correlation_id,
                          payload={"source_id": source_id, "domain": domain})

        if session["state"] == "DRAFT":
            self._touch(session_id, state="RUNNING")

        self.bus.emit(user_id, "research.fetch_started", url,
                      subject_kind="research", subject_id=session_id,
                      correlation_id=correlation_id,
                      payload={"source_id": source["id"]})

        result = net_security.fetch(url)
        fetch_id = _new_id("rfetch")
        now = _iso()
        self.db.execute(
            "INSERT INTO research_fetches (id,session_id,user_id,source_id,"
            "requested_url,final_url,status,http_status,content_type,"
            "latency_ms,redirect_count,redirect_chain,content_hash,"
            "bytes_read,error_code,error_detail,correlation_id,fetched_at,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fetch_id, session_id, user_id, source["id"], url,
             result.final_url, result.status, result.http_status,
             result.content_type, result.latency_ms, result.redirect_count,
             json.dumps(result.redirect_chain), result.content_hash,
             result.bytes_read, result.error_code, result.error_detail,
             correlation_id, now, now))

        availability = ("REACHABLE" if result.ok else
                        ("BLOCKED" if result.status == "BLOCKED" else "UNREACHABLE"))
        self.db.execute(
            "UPDATE research_sources SET availability=? WHERE id=?",
            (availability, source["id"]))

        event_type = {
            "COMPLETED": "research.fetch_completed",
            "BLOCKED": "research.fetch_blocked",
        }.get(result.status, "research.fetch_failed")
        self.bus.emit(user_id, event_type, url,
                      subject_kind="research", subject_id=session_id,
                      correlation_id=correlation_id,
                      payload={"fetch_id": fetch_id, "status": result.status,
                               "http_status": result.http_status,
                               "error_code": result.error_code})

        source_count = len(self.db.query(
            "SELECT id FROM research_sources WHERE session_id=?", (session_id,)))

        if not result.ok or not result.body_text:
            self._touch(session_id, source_count=source_count)
            return {
                "status": result.status, "fetch_id": fetch_id,
                "source_id": source["id"], "error_code": result.error_code,
                "detail": result.error_detail or "Fetch did not succeed.",
            }

        extracted = content_safety.extract_text(result.body_text, result.content_type)
        if extracted.title and not source.get("title"):
            self.db.execute("UPDATE research_sources SET title=? WHERE id=?",
                            (extracted.title[:200], source["id"]))

        evidence_records, claim_records, conflicts_created = self._record_evidence_and_claims(
            user_id, session_id, source, fetch_id, result, extracted, correlation_id)

        counts = self._recount(session_id)
        state = "RUNNING"
        self._touch(session_id, **counts, state=state)

        return {
            "status": "COMPLETED", "fetch_id": fetch_id, "source_id": source["id"],
            "http_status": result.http_status, "bytes_read": result.bytes_read,
            "evidence_created": len(evidence_records),
            "claims_created_or_updated": len(claim_records),
            "conflicts_detected": conflicts_created,
            "injection_flags": extracted.injection_flags,
            "detail": ("Injection-style language was found in the page content "
                      "and is recorded as page content only; it was NOT "
                      "executed and grants the source no authority."
                      if extracted.injection_flags else
                      "Fetched and processed as untrusted evidence."),
        }

    def _recount(self, session_id: str) -> dict[str, int]:
        source_count = len(self.db.query(
            "SELECT id FROM research_sources WHERE session_id=?", (session_id,)))
        evidence_count = len(self.db.query(
            "SELECT id FROM research_evidence WHERE session_id=?", (session_id,)))
        claim_count = len(self.db.query(
            "SELECT id FROM research_claims WHERE session_id=?", (session_id,)))
        conflict_count = len(self.db.query(
            "SELECT id FROM research_conflicts WHERE session_id=?", (session_id,)))
        return {"source_count": source_count, "evidence_count": evidence_count,
                "claim_count": claim_count, "conflict_count": conflict_count}

    # ------------------------------------------------------ evidence/claims
    def _record_evidence_and_claims(
        self, user_id: str, session_id: str, source: dict[str, Any],
        fetch_id: str, result: net_security.FetchResult,
        extracted: content_safety.ExtractedContent,
        correlation_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        sentences = split_sentences(extracted.text)[:MAX_EVIDENCE_PER_FETCH]
        source_quality = self._source_quality(source, result)
        evidence_records: list[dict[str, Any]] = []
        claim_records: list[dict[str, Any]] = []
        conflicts_created = 0

        for sentence in sentences:
            ev_id = _new_id("rev")
            now = _iso()
            excerpt = content_safety.quote_excerpt(sentence)
            evidence_strength = self._evidence_strength(result, sentence)
            self.db.execute(
                "INSERT INTO research_evidence (id,session_id,user_id,"
                "source_id,fetch_id,excerpt,locator,evidence_type,"
                "evidence_strength,source_quality,freshness,injection_flags,"
                "retrieved_at,content_hash,created_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev_id, session_id, user_id, source["id"], fetch_id, excerpt,
                 source["canonical_url"], "excerpt", evidence_strength,
                 source_quality, "FRESH",
                 json.dumps(content_safety.scan_for_injection(sentence)),
                 now, result.content_hash, now))
            evidence_records.append({"id": ev_id, "excerpt": excerpt})
            self.bus.emit(user_id, "research.evidence_recorded", excerpt[:120],
                          subject_kind="research", subject_id=session_id,
                          correlation_id=correlation_id,
                          payload={"evidence_id": ev_id, "source_id": source["id"]})

            claim_count = len(self.db.query(
                "SELECT id FROM research_claims WHERE session_id=?", (session_id,)))
            if claim_count >= MAX_CLAIMS_PER_SESSION:
                continue
            record, created_conflict = self._upsert_claim(
                user_id, session_id, sentence, ev_id, source, evidence_strength,
                source_quality, correlation_id)
            claim_records.append(record)
            if created_conflict:
                conflicts_created += 1

        return evidence_records, claim_records, conflicts_created

    @staticmethod
    def _source_quality(source: dict[str, Any],
                        result: net_security.FetchResult) -> float:
        """
        Deterministic, honestly-limited heuristic: authority is measured only
        from reachable metadata (scheme, redirect count, HTTP success), never
        from an opinion about the publisher. Documented limitation, not a
        claim of real authority scoring.
        """
        quality = 0.5
        if source["canonical_url"].startswith("https://"):
            quality += 0.1
        if result.redirect_count == 0:
            quality += 0.05
        elif result.redirect_count > 2:
            quality -= 0.1
        if result.http_status == 200:
            quality += 0.05
        return round(max(0.1, min(0.9, quality)), 3)

    @staticmethod
    def _evidence_strength(result: net_security.FetchResult, sentence: str) -> float:
        strength = 0.4
        if result.content_type and "html" in (result.content_type or ""):
            strength += 0.05
        if 40 <= len(sentence) <= 220:
            strength += 0.1
        return round(max(0.1, min(0.7, strength)), 3)

    def _upsert_claim(
        self, user_id: str, session_id: str, statement: str, evidence_id: str,
        source: dict[str, Any], evidence_strength: float, source_quality: float,
        correlation_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        existing = self.db.query(
            "SELECT * FROM research_claims WHERE session_id=?", (session_id,))
        new_values_for_match = _value_tokens(statement)
        best_match: dict[str, Any] | None = None
        best_score = 0.0
        for row in existing:
            score = _similar(row["statement"], statement)
            if score > best_score:
                best_match, best_score = dict(row), score

        # Near-identical wording AND the same factual values (dates/numbers)
        # is the same claim restated — corroboration. Near-identical wording
        # with DIFFERENT values is a conflict, never silently merged.
        if (best_match is not None and best_score >= CORROBORATE_SIMILARITY
                and _value_tokens(best_match["statement"]) == new_values_for_match):
            return self._corroborate(
                user_id, session_id, best_match, evidence_id, source,
                evidence_strength, source_quality, correlation_id), False

        # Conflict check: similar SUBJECT (numbers/dates masked out) but a
        # genuinely different value. Lexical, deterministic — documented as
        # such rather than claimed to be semantic contradiction detection.
        new_key = _conflict_key(statement)
        new_values = _value_tokens(statement)
        conflict_created = False
        conflicting_with: dict[str, Any] | None = None
        if new_values:
            for row in existing:
                row = dict(row)
                if _similar(new_key, _conflict_key(row["statement"])) >= CONFLICT_KEY_SIMILARITY:
                    other_values = _value_tokens(row["statement"])
                    if other_values and other_values != new_values:
                        conflicting_with = row
                        break

        claim_id = _new_id("rclaim")
        now = _iso()
        independent_domains = 1
        confidence = self._claim_confidence(evidence_strength, source_quality, independent_domains)
        conflict_group = conflicting_with["conflict_group"] if conflicting_with and conflicting_with.get("conflict_group") else (
            _new_id("conflict") if conflicting_with else None)
        status = "contested" if conflicting_with else "unsupported"

        self.db.execute(
            "INSERT INTO research_claims (id,session_id,user_id,statement,"
            "evidence_ids,source_ids,evidence_strength,source_quality,"
            "corroboration_count,independent_domain_count,freshness,"
            "claim_confidence,conflict_group,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, session_id, user_id, statement, json.dumps([evidence_id]),
             json.dumps([source["id"]]), evidence_strength, source_quality, 1,
             independent_domains, "FRESH", confidence, conflict_group, status,
             now, now))
        self.bus.emit(user_id, "research.claim_created", statement[:140],
                      subject_kind="research", subject_id=session_id,
                      correlation_id=correlation_id,
                      payload={"claim_id": claim_id, "confidence": confidence})

        if conflicting_with is not None:
            if not conflicting_with.get("conflict_group"):
                self.db.execute(
                    "UPDATE research_claims SET conflict_group=?, status=? "
                    "WHERE id=?", (conflict_group, "contested", conflicting_with["id"]))
            self.db.execute(
                "INSERT INTO research_conflicts (id,session_id,user_id,"
                "conflict_group,claim_ids,subject_key,reason,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_new_id("rconf"), session_id, user_id, conflict_group,
                 json.dumps([conflicting_with["id"], claim_id]), new_key,
                 (f"'{conflicting_with['statement'][:80]}' and "
                  f"'{statement[:80]}' share the same subject but report "
                  "different values; both are preserved rather than resolved."),
                 now))
            self.bus.emit(
                user_id, "research.claim_conflict_detected",
                f"Conflicting claims about: {new_key[:100]}",
                subject_kind="research", subject_id=session_id,
                correlation_id=correlation_id,
                payload={"conflict_group": conflict_group,
                         "claim_ids": [conflicting_with["id"], claim_id]})
            conflict_created = True

        return {"id": claim_id, "statement": statement, "confidence": confidence,
                "status": status}, conflict_created

    def _corroborate(
        self, user_id: str, session_id: str, existing: dict[str, Any],
        evidence_id: str, source: dict[str, Any], evidence_strength: float,
        source_quality: float, correlation_id: str | None,
    ) -> dict[str, Any]:
        evidence_ids = _loads(existing.get("evidence_ids"), [])
        source_ids = _loads(existing.get("source_ids"), [])
        evidence_ids.append(evidence_id)
        new_domain = source["domain"] not in self._domains_for_sources(
            existing["session_id"], source_ids)
        if source["id"] not in source_ids:
            source_ids.append(source["id"])
        independent_domains = existing["independent_domain_count"] + (1 if new_domain else 0)
        corroboration_count = existing["corroboration_count"] + 1
        avg_strength = (existing["evidence_strength"] + evidence_strength) / 2
        avg_quality = ((existing["source_quality"] or 0.5) + source_quality) / 2
        confidence = self._claim_confidence(avg_strength, avg_quality, independent_domains)

        self.db.execute(
            "UPDATE research_claims SET evidence_ids=?, source_ids=?, "
            "evidence_strength=?, source_quality=?, corroboration_count=?, "
            "independent_domain_count=?, claim_confidence=?, status=?, "
            "updated_at=? WHERE id=?",
            (json.dumps(evidence_ids), json.dumps(source_ids), avg_strength,
             avg_quality, corroboration_count, independent_domains, confidence,
             "corroborated" if independent_domains > 1 else existing["status"],
             _iso(), existing["id"]))
        self.bus.emit(
            user_id, "research.claim_corroborated", existing["statement"][:140],
            subject_kind="research", subject_id=session_id,
            correlation_id=correlation_id,
            payload={"claim_id": existing["id"],
                     "independent_domain_count": independent_domains,
                     "confidence": confidence})
        return {"id": existing["id"], "statement": existing["statement"],
                "confidence": confidence, "status": "corroborated"}

    def _domains_for_sources(self, session_id: str, source_ids: list[str]) -> set[str]:
        if not source_ids:
            return set()
        placeholders = ",".join("?" * len(source_ids))
        rows = self.db.query(
            f"SELECT domain FROM research_sources WHERE session_id=? "
            f"AND id IN ({placeholders})", (session_id, *source_ids))
        return {r["domain"] for r in rows}

    @staticmethod
    def _claim_confidence(evidence_strength: float, source_quality: float,
                          independent_domain_count: int) -> float:
        raw = 0.20 + 0.15 * evidence_strength + 0.15 * source_quality
        raw += 0.08 * min(max(independent_domain_count - 1, 0), 3)
        cap = (CORROBORATED_CONFIDENCE_CAP if independent_domain_count > 1
               else SINGLE_SOURCE_CONFIDENCE_CAP)
        return round(max(0.05, min(raw, cap)), 3)

    # -------------------------------------------------------------- finish
    def finish(self, user_id: str, session_id: str,
              correlation_id: str | None = None) -> dict[str, Any] | None:
        session = self._row(user_id, session_id)
        if session is None:
            return None
        fetches = self.db.query(
            "SELECT status FROM research_fetches WHERE session_id=?", (session_id,))
        counts = self._recount(session_id)
        attempted = len(fetches)
        succeeded = sum(1 for f in fetches if f["status"] == "COMPLETED")

        if attempted == 0:
            state, detail = "BLOCKED", "No sources were fetched in this session."
        elif counts["evidence_count"] == 0:
            state, detail = "FAILED", (f"{attempted} fetch(es) attempted; none "
                                       "produced usable evidence.")
        elif succeeded < attempted:
            state, detail = "PARTIAL", (f"{succeeded}/{attempted} fetch(es) "
                                        "succeeded; some sources failed or "
                                        "were blocked (see the fetch ledger).")
        else:
            state, detail = "COMPLETED", (f"All {attempted} fetch(es) succeeded; "
                                          f"{counts['claim_count']} claim(s) "
                                          f"derived from {counts['evidence_count']} "
                                          "evidence record(s).")

        self._touch(session_id, state=state, detail=detail, **counts)
        event = "research.completed" if state in ("COMPLETED", "PARTIAL") else "research.failed"
        self.bus.emit(user_id, event, detail, subject_kind="research",
                      subject_id=session_id, correlation_id=correlation_id,
                      payload={"state": state, **counts})
        return self.get(user_id, session_id)

    # -------------------------------------------------------------- reads
    def _scoped(self, user_id: str, session_id: str, table: str) -> list[dict[str, Any]]:
        if self._row(user_id, session_id) is None:
            return []
        rows = self.db.query(
            f"SELECT * FROM {table} WHERE session_id=? AND user_id=? ORDER BY rowid",
            (session_id, user_id))
        return [dict(r) for r in rows]

    def sources(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return self._scoped(user_id, session_id, "research_sources")

    def fetches(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        out = []
        for row in self._scoped(user_id, session_id, "research_fetches"):
            row["redirect_chain"] = _loads(row.get("redirect_chain"), [])
            out.append(row)
        return out

    def evidence(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        out = []
        for row in self._scoped(user_id, session_id, "research_evidence"):
            row["injection_flags"] = _loads(row.get("injection_flags"), [])
            out.append(row)
        return out

    def claims(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        out = []
        for row in self._scoped(user_id, session_id, "research_claims"):
            row["evidence_ids"] = _loads(row.get("evidence_ids"), [])
            row["source_ids"] = _loads(row.get("source_ids"), [])
            out.append(row)
        return out

    def get_claim(self, user_id: str, claim_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM research_claims WHERE id=? AND user_id=?",
            (claim_id, user_id))
        if row is None:
            return None
        item = dict(row)
        item["evidence_ids"] = _loads(item.get("evidence_ids"), [])
        item["source_ids"] = _loads(item.get("source_ids"), [])
        return item

    def conflicts(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        out = []
        for row in self._scoped(user_id, session_id, "research_conflicts"):
            row["claim_ids"] = _loads(row.get("claim_ids"), [])
            out.append(row)
        return out

    # ---------------------------------------------------- world model bridge
    def propose_world_update(
        self, user_id: str, session_id: str, claim_id: str, kind: str,
        label: str, correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Propose (but do not yet apply) a bounded World Model update derived
        from a research claim. Nothing about the World Model changes here.
        """
        claim = self.get_claim(user_id, claim_id)
        if claim is None or claim["session_id"] != session_id:
            raise ValueError(f"No claim '{claim_id}' in session '{session_id}'.")
        proposed_confidence = round(
            min(float(claim["claim_confidence"]), WORLD_UPDATE_CONFIDENCE_CAP), 3)
        update_id = _new_id("rwu")
        now = _iso()
        self.db.execute(
            "INSERT INTO research_world_updates (id,session_id,user_id,"
            "claim_id,world_entity_id,kind,label,proposed_confidence,"
            "applied_confidence,state,reason,correlation_id,created_at,"
            "resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (update_id, session_id, user_id, claim_id, None, kind, label[:120],
             proposed_confidence, None, "PROPOSED",
             f"Derived from research claim: {claim['statement'][:160]}",
             correlation_id, now, None))
        self.bus.emit(
            user_id, "research.world_update_proposed", label[:120],
            subject_kind="research", subject_id=session_id,
            correlation_id=correlation_id,
            payload={"update_id": update_id, "claim_id": claim_id,
                     "proposed_confidence": proposed_confidence})
        return self.get_world_update(user_id, update_id)  # type: ignore[return-value]

    def get_world_update(self, user_id: str, update_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM research_world_updates WHERE id=? AND user_id=?",
            (update_id, user_id))
        return dict(row) if row else None

    def world_updates(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        return self._scoped(user_id, session_id, "research_world_updates")

    def apply_world_update(
        self, user_id: str, update_id: str, *, confirm: bool,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Apply a PROPOSED world update. Requires an explicit `confirm=True` —
        this is the one and only path by which research evidence can reach
        the World Model, and it is always a distinct, separately-recorded
        step from claim creation (never automatic).
        """
        if not confirm:
            raise ValueError(
                "World model updates from research require explicit "
                "confirmation (confirm=True); nothing was changed.")
        update = self.get_world_update(user_id, update_id)
        if update is None:
            raise ValueError(f"No proposed world update '{update_id}'.")
        if update["state"] != "PROPOSED":
            raise ValueError(
                f"World update '{update_id}' is '{update['state']}', not "
                "PROPOSED; it cannot be applied again.")
        if self.world_v2 is None:
            raise ValueError("World model integration is not wired in this build.")

        claim = self.get_claim(user_id, update["claim_id"])
        sources = self.db.query(
            "SELECT canonical_url FROM research_sources WHERE session_id=? "
            "AND id IN (%s)" % ",".join("?" * len(claim["source_ids"])),
            (update["session_id"], *claim["source_ids"])) if claim and claim["source_ids"] else []
        evidence_lines = [claim["statement"]] if claim else []
        evidence_lines += [s["canonical_url"] for s in sources]

        verdict = self.world_v2.reconcile(
            user_id, kind=update["kind"], label=update["label"],
            confidence=update["proposed_confidence"], source="research",
            evidence=evidence_lines, correlation_id=correlation_id)

        applied_confidence = (verdict.get("entity") or {}).get("confidence")
        self.db.execute(
            "UPDATE research_world_updates SET state=?, applied_confidence=?, "
            "resolved_at=? WHERE id=?",
            ("APPLIED", applied_confidence, _iso(), update_id))
        self.bus.emit(
            user_id, "research.world_update_applied", update["label"][:120],
            subject_kind="research", subject_id=update["session_id"],
            correlation_id=correlation_id,
            payload={"update_id": update_id, "verdict": verdict["verdict"],
                     "applied_confidence": applied_confidence})
        return {"update": self.get_world_update(user_id, update_id),
                "reconciliation": verdict}
