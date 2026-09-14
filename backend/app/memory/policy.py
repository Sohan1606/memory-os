"""Deterministic memory policy: what is worth remembering, and as what.

Used directly in LOCAL DEMO mode, and as a validation/normalisation layer for
memories proposed by an LLM in REAL AI mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DURABLE_PATTERNS: list[tuple[str, str]] = [
    (r"\bmy name is\b|\bi am called\b|\bcall me\b", "IDENTITY"),
    (r"\bi prefer\b|\bi like\b|\bi love\b|\bi enjoy\b|\bi'd rather\b|\bi hate\b|\bi dislike\b", "PREFERENCE"),
    # a stated switch is a preference change, not loose context
    (r"\b(switch(ed|ing)? to|moved to|now using|changed to)\b", "PREFERENCE"),
    (r"\bconcise\b|\bbrief\b|\bdetailed\b|\bstep by step\b|\bexamples? (first|before)\b|\bexplanations?\b", "COMMUNICATION_STYLE"),
    (r"\bi'?m building\b|\bi am building\b|\bworking on\b|\bmy project\b|\bi'?m developing\b", "PROJECT"),
    (r"\bmy goal\b|\bi want to\b|\bi plan to\b|\bi'?m aiming\b|\blearning\b|\bexploring\b", "GOAL"),
    (r"\bevery day\b|\busually\b|\bi always\b|\bi never\b|\bmorning\b routine", "HABIT"),
    (r"\bmy (wife|husband|partner|team|manager|colleague|friend|company)\b", "RELATIONSHIP"),
    (r"\bi work (at|for|as)\b|\bi live in\b|\bi use\b|\bmy stack\b", "FACT"),
]

TRANSIENT_PATTERNS = [
    r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|cool|nice)\b",
    r"\bwhat('| i)?s the weather\b",
    r"\bwhat time is it\b",
    r"^\s*(explain|describe|define|show me|tell me about|how do i|what is|what are)\b",
    # any interrogative turn is a request, not durable knowledge about the user
    r"^\s*(what|which|who|when|where|why|how|do|does|did|can|could|should|would|is|are)\b.*\?\s*$",
    r"\b(what do you (remember|know)|do you recall)\b",
]

SWITCH_PATTERNS = r"\b(actually|instead|switch(ed|ing)? to|no longer|not anymore|changed to|now i)\b"


@dataclass
class MemoryCandidate:
    content: str
    category: str
    importance: float
    confidence: float
    is_durable: bool
    reason: str


def _normalise(text: str) -> str:
    t = " ".join(text.strip().split())
    t = re.sub(r"^(please\s+)?remember that\s+", "", t, flags=re.I)
    t = re.sub(r"^(please\s+)?remember\s+", "", t, flags=re.I)
    t = re.sub(r"^i'?m\b", "I am", t, flags=re.I)
    if t and not t.endswith((".", "!", "?")):
        t += "."
    return t[:1].upper() + t[1:] if t else t


def classify(text: str) -> str:
    low = text.lower()
    # communication-style cues only count alongside a preference statement
    matches = [cat for pat, cat in DURABLE_PATTERNS if re.search(pat, low)]
    if "COMMUNICATION_STYLE" in matches and any(
        m in matches for m in ("PREFERENCE",)
    ):
        return "COMMUNICATION_STYLE"
    for pat, cat in DURABLE_PATTERNS:
        if re.search(pat, low):
            return cat
    return "CONTEXT"


def evaluate(text: str) -> MemoryCandidate:
    """Decide whether a statement carries durable, user-specific knowledge."""
    raw = text.strip()
    low = raw.lower()
    # "Remember that X" is an instruction; "what do you remember" is a question.
    explicit = bool(re.match(r"^\s*(please\s+)?remember\b", low)) and not low.rstrip().endswith("?")

    if not explicit:
        for pat in TRANSIENT_PATTERNS:
            if re.search(pat, low):
                return MemoryCandidate(raw, "CONTEXT", 0.2, 0.3, False,
                                       "Transient request, not durable knowledge.")
    if len(raw.split()) < 3 and not explicit:
        return MemoryCandidate(raw, "CONTEXT", 0.1, 0.2, False, "Too short to be durable.")

    durable = explicit or any(re.search(p, low) for p, _ in DURABLE_PATTERNS)
    if not durable:
        return MemoryCandidate(raw, "CONTEXT", 0.3, 0.4, False,
                               "No durable self-referential signal detected.")

    content = _normalise(raw)
    category = classify(raw)
    importance = 0.85 if category in {"IDENTITY", "PREFERENCE", "COMMUNICATION_STYLE"} else 0.7
    if explicit:
        importance = min(1.0, importance + 0.1)
    confidence = 0.9 if explicit else 0.75
    return MemoryCandidate(content, category, importance, confidence, True,
                           f"Durable {category.replace('_', ' ').lower()} signal detected.")


def looks_like_change(text: str) -> bool:
    """Heuristic for statements that supersede an earlier preference."""
    return bool(re.search(SWITCH_PATTERNS, text.lower()))


def classify_extracted(text: str) -> MemoryCandidate | None:
    """
    Wrap an already-extracted statement (e.g. one proposed by LangMem) as a
    storable candidate.

    Extraction has already happened upstream, so the durability heuristics that
    `evaluate` applies to raw user utterances are deliberately skipped. Only
    trivially short strings are rejected. Returns None when the text is unusable.
    """
    cleaned = _normalise(text)
    if len(cleaned) < 8:
        return None
    return MemoryCandidate(
        content=cleaned,
        category=classify(cleaned),
        importance=0.75,
        confidence=0.8,
        is_durable=True,
        reason="Extracted by LangMem",
    )
