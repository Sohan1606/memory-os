"""
Memory health engine (v8.1).

Diagnoses the memory store and proposes *non-destructive* remedies. Valuable
history is never hard-deleted: the strongest action is RETIRE, which preserves
the row and its version history.

Findings -> remedies
    duplicate             -> MERGE
    stale                 -> REVALIDATE
    contradicted          -> REVALIDATE
    unsupported           -> DOWNGRADE
    low_value             -> DOWNGRADE
    harmful               -> QUARANTINE
    repeatedly_unhelpful  -> RETIRE

Every finding cites concrete evidence. Nothing is invented: if there is not
enough evidence to judge a memory, it simply produces no finding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

REMEDIES = ("MERGE", "REVALIDATE", "DOWNGRADE", "QUARANTINE", "ARCHIVE", "RETIRE")

STALE_DAYS = 180
DUPLICATE_SIMILARITY = 0.82
LOW_VALUE_IMPORTANCE = 0.25
# Below this a memory has already been downgraded; re-flagging it would make the
# health report never reach a settled state.
ALREADY_DOWNGRADED = 0.12
UNHELPFUL_RETRIEVALS = 5

# Memory kinds that should not be aged out just because they are old.
_TIMELESS = {"identity", "principle", "relationship"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(timestamp: str) -> float | None:
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (_now() - dt).total_seconds() / 86400.0
    except (ValueError, AttributeError):
        return None


# Function words carry no identifying meaning. Without removing them, two
# genuine duplicates phrased slightly differently ("every Friday" vs "each
# Friday") score far below threshold purely because of filler.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "using", "use", "uses", "every", "each", "all",
    "any", "this", "that", "these", "those", "his", "her", "its", "their",
    "our", "into", "onto", "from", "out", "via", "per", "always", "usually",
    "often", "prefer", "prefers", "preferred", "likes", "like", "liked",
    "wants", "want", "has", "have", "had", "was", "were", "are", "been",
    "being", "does", "did", "done", "when", "while", "then", "than", "some",
})


def _singular(word: str) -> str:
    """Crude de-pluralisation so 'editor' and 'editors' compare as equal."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and word[-3] in "sxzh":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    """Content words only, singularised - filler is stripped before comparison."""
    return {_singular(w) for w in re.findall(r"[a-z0-9]{3,}", text.lower())
            if w not in _STOPWORDS}


def _similarity(a: str, b: str) -> float:
    """Jaccard overlap of content words - deterministic, no embedding call."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class Finding:
    memory_id: str
    issue: str
    remedy: str
    confidence: float
    evidence: str
    related_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryHealthEngine:
    """Evidence-based diagnosis and safe repair of the memory store."""

    def __init__(self, memory, reputation, bus) -> None:
        self.memory = memory
        self.reputation = reputation
        self.bus = bus

    # ------------------------------------------------------------- diagnose
    def diagnose(self, user_id: str) -> list[Finding]:
        memories = self.memory.list(user_id, status="active")
        findings: list[Finding] = []
        seen_pairs: set[tuple[str, str]] = set()

        for i, mem in enumerate(memories):
            rep = self.reputation.get(user_id, mem.id)

            # duplicates - compare against later entries only, once per pair
            for other in memories[i + 1:]:
                if other.category != mem.category:
                    continue
                pair = tuple(sorted((mem.id, other.id)))
                if pair in seen_pairs:
                    continue
                score = _similarity(mem.content, other.content)
                if score >= DUPLICATE_SIMILARITY:
                    seen_pairs.add(pair)
                    findings.append(Finding(
                        memory_id=other.id, issue="duplicate", remedy="MERGE",
                        confidence=round(score, 2), related_id=mem.id,
                        evidence=(f"{int(score * 100)}% token overlap with "
                                  f"memory {mem.id}.")))

            # contradictions carry the most weight - surface them first
            contradictions = int(rep.get("contradictions", 0) or 0)
            if contradictions >= 2:
                findings.append(Finding(
                    memory_id=mem.id, issue="contradicted", remedy="REVALIDATE",
                    confidence=0.8,
                    evidence=f"{contradictions} recorded contradictions."))
                continue

            # stale - old, never reinforced, and not a timeless category
            age = _age_days(mem.updated_at)
            if (age is not None and age > STALE_DAYS
                    and mem.category not in _TIMELESS
                    and mem.reinforcement_count == 0):
                findings.append(Finding(
                    memory_id=mem.id, issue="stale", remedy="REVALIDATE",
                    confidence=0.6,
                    evidence=(f"Not updated in {int(age)} days and never "
                              "reinforced.")))

            # repeatedly retrieved but never once useful
            retrievals = int(rep.get("retrievals", 0) or 0)
            positives = int(rep.get("positive_outcomes", 0) or 0)
            influences = int(rep.get("influences", 0) or 0)
            if retrievals >= UNHELPFUL_RETRIEVALS and influences == 0 and positives == 0:
                findings.append(Finding(
                    memory_id=mem.id, issue="repeatedly_unhelpful", remedy="RETIRE",
                    confidence=0.65,
                    evidence=(f"Retrieved {retrievals} times, never influenced "
                              "a response or a positive outcome.")))
                continue

            # low value - weak importance AND no evidence of usefulness
            if (ALREADY_DOWNGRADED < mem.importance <= LOW_VALUE_IMPORTANCE
                    and retrievals == 0):
                findings.append(Finding(
                    memory_id=mem.id, issue="low_value", remedy="DOWNGRADE",
                    confidence=0.5,
                    evidence=(f"Importance {mem.importance:.2f} and never "
                              "retrieved.")))

            # unsupported - asserted with low confidence and no corroboration
            if mem.confidence < 0.4 and mem.reinforcement_count == 0:
                findings.append(Finding(
                    memory_id=mem.id, issue="unsupported", remedy="DOWNGRADE",
                    confidence=0.55,
                    evidence=(f"Confidence {mem.confidence:.2f} with no "
                              "reinforcing evidence.")))

        return findings

    # --------------------------------------------------------------- report
    def report(self, user_id: str) -> dict[str, Any]:
        findings = self.diagnose(user_id)
        total = len(self.memory.list(user_id, status="active"))
        by_issue: dict[str, int] = {}
        for f in findings:
            by_issue[f.issue] = by_issue.get(f.issue, 0) + 1

        if total == 0:
            grade = "INSUFFICIENT EVIDENCE"
        else:
            ratio = len({f.memory_id for f in findings}) / total
            grade = ("HEALTHY" if ratio < 0.1
                     else "NEEDS ATTENTION" if ratio < 0.35 else "AT RISK")

        return {"total_active": total, "findings": [f.as_dict() for f in findings],
                "by_issue": by_issue, "grade": grade,
                "checked_at": _now().isoformat(timespec="seconds")}

    # ----------------------------------------------------------------- heal
    def apply(self, user_id: str, finding: Finding, *,
              correlation_id: str | None = None) -> dict[str, Any]:
        """
        Apply one remedy. Never destroys history:
          MERGE      - retires the duplicate, keeps the original
          DOWNGRADE  - lowers importance, memory stays active
          REVALIDATE - flags for confirmation, changes no content
          QUARANTINE - excludes from retrieval, fully reversible
          RETIRE     - soft status change, version history preserved
        """
        mem = self.memory.get(finding.memory_id)
        if mem is None:
            return {"applied": False, "reason": "Memory no longer exists."}

        remedy = finding.remedy
        if remedy == "DOWNGRADE":
            new_importance = round(max(0.05, mem.importance * 0.6), 3)
            self.memory.update(finding.memory_id, importance=new_importance,
                               reason=f"health:{finding.issue}")
            self.bus.emit(user_id, "memory.weakened",
                          f"Lowered importance of a {finding.issue} memory",
                          subject_kind="memory", subject_id=finding.memory_id,
                          correlation_id=correlation_id,
                          payload={"importance": new_importance,
                                   "evidence": finding.evidence})

        elif remedy in ("RETIRE", "MERGE"):
            # Soft retirement in BOTH stores: the row and all of its versions
            # stay on disk and can be restored.
            self.memory.update(finding.memory_id, status="retired",
                               reason=f"health:{finding.issue}")
            self.reputation.retire(user_id, finding.memory_id,
                                   correlation_id=correlation_id)
            event = "memory.merged" if remedy == "MERGE" else "memory.retired"
            self.bus.emit(user_id, event,
                          (f"Merged into {finding.related_id}"
                           if remedy == "MERGE" else "Retired an unhelpful memory"),
                          subject_kind="memory", subject_id=finding.memory_id,
                          correlation_id=correlation_id,
                          payload={"evidence": finding.evidence,
                                   "kept": finding.related_id})

        elif remedy == "QUARANTINE":
            self.memory.update(finding.memory_id, status="quarantined",
                               reason=f"health:{finding.issue}")
            self.bus.emit(user_id, "memory.quarantined",
                          "Set a questionable memory aside",
                          subject_kind="memory", subject_id=finding.memory_id,
                          correlation_id=correlation_id,
                          payload={"evidence": finding.evidence})

        elif remedy == "REVALIDATE":
            self.bus.emit(user_id, "memory.stale",
                          f"Flagged a {finding.issue} memory for confirmation",
                          subject_kind="memory", subject_id=finding.memory_id,
                          correlation_id=correlation_id,
                          payload={"evidence": finding.evidence})
        else:
            return {"applied": False, "reason": f"Unsupported remedy {remedy}."}

        return {"applied": True, "memory_id": finding.memory_id,
                "remedy": remedy, "evidence": finding.evidence}
