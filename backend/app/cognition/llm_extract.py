"""
Model-assisted structured extraction (v8.1).

When a real LLM is active, intent / need / world entities / memory candidates are
extracted by the model against a strict schema and then validated here. When no
model is active this module reports `available = False` and the caller keeps
using the deterministic rule engine.

Design rules:
  * The model never writes directly to state. It proposes; we validate.
  * Anything that fails validation is dropped, not coerced into a guess.
  * Extraction failure is never fatal - the deterministic path still runs.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Vocabularies the model must choose from. Anything else is rejected.
NEEDS = ("information", "action", "planning", "decision_support", "reassurance",
         "listening", "reflection", "exploration", "second_opinion",
         "delegation", "silence")
ENTITY_KINDS = ("goal", "project", "commitment", "person", "risk", "resource",
                "constraint")
MEMORY_TYPES = ("personal", "preference", "identity", "project", "habit",
                "relationship", "communication_style", "skill", "constraint")

_SCHEMA = """Return ONLY a JSON object with this exact shape:
{
  "intent": {"goal": string|null, "confidence": number, "evidence": string},
  "need": {"type": one of %(needs)s, "confidence": number, "evidence": string},
  "entities": [{"kind": one of %(kinds)s, "name": string,
                "confidence": number, "evidence": string}],
  "memories": [{"content": string, "type": one of %(mtypes)s,
                "importance": number, "confidence": number, "evidence": string}]
}
Rules:
- Only include something the user ACTUALLY stated. Never invent.
- "evidence" must quote the user's own words.
- Questions are not goals and are not memories.
- Confidence and importance are between 0 and 1.
- Use empty arrays when nothing qualifies.
- Output JSON only. No prose, no markdown fences.""" % {
    "needs": list(NEEDS), "kinds": list(ENTITY_KINDS), "mtypes": list(MEMORY_TYPES)}


@dataclass
class Extraction:
    """Validated extraction result. `available` is False when no model ran."""

    available: bool = False
    source: str = "none"          # llm | none
    intent: dict[str, Any] | None = None
    need: dict[str, Any] | None = None
    entities: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    raw: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"available": self.available, "source": self.source,
                "intent": self.intent, "need": self.need,
                "entities": self.entities, "memories": self.memories,
                "error": self.error}


def _clamp(value: Any, lo: float = 0.0, hi: float = 1.0,
           default: float = 0.5) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _text(value: Any, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def parse_payload(raw: str) -> dict[str, Any]:
    """
    Pull a JSON object out of a model response.

    Small local models often wrap JSON in prose or code fences, so we locate the
    outermost balanced object rather than trusting the whole string.
    """
    text = raw.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object in model output.")
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("Unbalanced JSON in model output.")


def validate(payload: dict[str, Any], utterance: str) -> Extraction:
    """Validate a raw model payload into trustworthy structures."""
    out = Extraction(available=True, source="llm")
    lowered = utterance.lower()

    intent = payload.get("intent") or {}
    goal = _text(intent.get("goal"), 200)
    if goal and goal.lower() not in {"null", "none", "n/a"}:
        out.intent = {"goal": goal,
                      "confidence": _clamp(intent.get("confidence"), default=0.5),
                      "evidence": _text(intent.get("evidence"), 200)}

    need = payload.get("need") or {}
    need_type = _text(need.get("type"), 40).lower().replace(" ", "_")
    if need_type in NEEDS:
        out.need = {"need": need_type,
                    "confidence": _clamp(need.get("confidence"), default=0.5),
                    "evidence": _text(need.get("evidence"), 200),
                    "hypothesis": True}

    for raw_entity in (payload.get("entities") or [])[:8]:
        if not isinstance(raw_entity, dict):
            continue
        kind = _text(raw_entity.get("kind"), 30).lower()
        name = _text(raw_entity.get("name"), 120)
        if kind not in ENTITY_KINDS or len(name) < 3:
            continue
        out.entities.append({
            "kind": kind, "label": name,
            "confidence": _clamp(raw_entity.get("confidence"), default=0.5),
            "evidence": _text(raw_entity.get("evidence"), 200)})

    for raw_mem in (payload.get("memories") or [])[:8]:
        if not isinstance(raw_mem, dict):
            continue
        content = _text(raw_mem.get("content"), 300)
        if len(content) < 6:
            continue
        # Guard against hallucinated memories: require some lexical overlap with
        # what the user actually said.
        words = {w for w in re.findall(r"[a-z]{4,}", content.lower())}
        if words and not any(w in lowered for w in words):
            log.debug("Dropping unsupported memory candidate: %s", content)
            continue
        mem_type = _text(raw_mem.get("type"), 40).lower()
        out.memories.append({
            "content": content,
            "type": mem_type if mem_type in MEMORY_TYPES else "personal",
            "importance": _clamp(raw_mem.get("importance"), default=0.6),
            "confidence": _clamp(raw_mem.get("confidence"), default=0.6),
            "evidence": _text(raw_mem.get("evidence"), 200)})

    return out


class LLMExtractor:
    """Structured extraction through the active chat model."""

    def __init__(self, provider, lock=None) -> None:
        self.provider = provider
        # Shared with the agent so extraction never competes with a reply for
        # the single loaded model.
        self.lock = lock

    @property
    def available(self) -> bool:
        status = self.provider.status()
        return status.available and status.name != "demo"

    def status(self) -> dict[str, Any]:
        if self.available:
            st = self.provider.status()
            return {"state": "ACTIVE", "model": st.model,
                    "detail": f"Structured extraction via {st.model}."}
        return {"state": "NOT CONFIGURED", "model": None,
                "detail": "No LLM configured; deterministic rule engine is used."}

    def extract(self, utterance: str, *, context: str = "") -> Extraction:
        """
        Ask the model for structured understanding of one utterance.

        Returns an Extraction with `available=False` if no model is configured or
        the call fails, so the caller can fall back without special-casing.
        """
        if not self.available:
            return Extraction(available=False, source="none",
                              error="No LLM configured.")

        # Never queue behind a reply that is already generating: the user's
        # answer matters more than background understanding. Falling back to the
        # deterministic engine here costs nothing and keeps latency predictable.
        if self.lock is not None and not self.lock.acquire(blocking=False):
            return Extraction(available=False, source="none",
                              error="Model busy; deterministic engine used.")
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            model = self.provider.chat_model()
            prompt = (f"{_SCHEMA}\n\nConversation context:\n{context or '(none)'}\n\n"
                      f"User said: \"{utterance}\"")
            response = model.invoke([
                SystemMessage(content="You extract structured data. Output JSON only."),
                HumanMessage(content=prompt)])
            raw = response.content if isinstance(response.content, str) else str(response.content)
            result = validate(parse_payload(raw), utterance)
            result.raw = raw[:1200]
            return result
        except Exception as exc:
            log.info("LLM extraction failed (%s); using deterministic engine.", exc)
            return Extraction(available=False, source="none",
                              error=f"{type(exc).__name__}: {exc}"[:200])
        finally:
            if self.lock is not None:
                self.lock.release()
