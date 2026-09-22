"""V9 Meaning Compiler and kernel.

The compiler is deliberately deterministic at its boundary. It preserves
modality and ambiguity; it does not equate fluent text with verified truth. A
model-backed extractor may call ``validate_model_output`` but its JSON receives
exactly the same Pydantic validation and persistence policy.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from ..providers.capabilities import STRUCTURED_OUTPUT

from ..schemas.semantic import (CognitiveObjectCreate, CognitiveType, Modality,
                                Provenance, SemanticCandidate,
                                SemanticRepresentation, TemporalScope,
                                RelationshipCreate)

_QUESTION = re.compile(r"^(?:who|what|when|where|why|how|should|could|would|can|do|does|did|is|are|am|will|have|has)\b|\?$", re.I)
_DEICTIC = re.compile(r"\b(?:this|that|it|one|so)\b", re.I)
_TEMPORAL = re.compile(
    r"\b(today|tomorrow|yesterday|next\s+(?:week|month|year)|last\s+(?:week|month|year)|"
    r"this\s+(?:week|month|year)|in\s+\d+\s+(?:days?|weeks?|months?|years?)|"
    r"by\s+[A-Za-z]+(?:\s+\d{4})?|in\s+\d{4})\b", re.I)

# Ordered: specific meanings must win before broad declarative fallbacks.
_RULES: tuple[tuple[CognitiveType, Modality, float, re.Pattern[str]], ...] = (
    (CognitiveType.CORRECTION, Modality.ASSERTED, .95,
     re.compile(r"^(?:actually|correction\s*[:,-]?|no,?\s+(?:instead|i)|i meant\b|change that\b|keep .+,\s*not .+)", re.I)),
    (CognitiveType.CONTRADICTION, Modality.NEGATED, .90,
     re.compile(r"\b(?:contradicts?|is not true|is false|that(?:'s| is) wrong)\b", re.I)),
    (CognitiveType.HYPOTHETICAL, Modality.HYPOTHETICAL, .90,
     re.compile(r"^(?:what if|suppose|imagine|hypothetically)\b", re.I)),
    (CognitiveType.PREFERENCE, Modality.ASSERTED, .94,
     re.compile(r"\b(?:i prefer|my preference is|i like .+ (?:better|more)|i would rather|i'd rather)\b", re.I)),
    (CognitiveType.VALUE, Modality.ASSERTED, .92,
     re.compile(r"\b(?:i value|matters? (?:most )?to me|important to me)\b", re.I)),
    (CognitiveType.BOUNDARY, Modality.NEGATED, .93,
     re.compile(r"\b(?:i (?:will not|won't|cannot|can't)|my boundary is|do not .+ for me|never .+ without)\b", re.I)),
    (CognitiveType.COMMITMENT, Modality.OBLIGATED, .92,
     re.compile(r"\b(?:i commit to|i promise|i will definitely|i must|i have committed)\b", re.I)),
    (CognitiveType.DECISION, Modality.ASSERTED, .94,
     re.compile(r"\b(?:i decided|i have decided|my decision is|we decided)\b", re.I)),
    (CognitiveType.PREDICTION, Modality.TENTATIVE, .78,
     re.compile(r"\b(?:i predict|i expect .+ will|probably will|is likely to)\b", re.I)),
    (CognitiveType.ASSUMPTION, Modality.TENTATIVE, .72,
     re.compile(r"\b(?:i assume|assuming that|my assumption is)\b", re.I)),
    (CognitiveType.HYPOTHESIS, Modality.POSSIBLE, .60,
     re.compile(r"\b(?:maybe|perhaps|possibly|could be|might be|i might|my hypothesis is)\b", re.I)),
    (CognitiveType.BELIEF, Modality.TENTATIVE, .64,
     re.compile(r"\b(?:i do not think|i don't think|i'm not sure|i am not sure)\b", re.I)),
    (CognitiveType.BELIEF, Modality.TENTATIVE, .68,
     re.compile(r"\b(?:i think|i believe|it seems to me|i suspect)\b", re.I)),
    (CognitiveType.INTENT, Modality.TENTATIVE, .72,
     re.compile(r"\b(?:i'm|i am)\s+(?:seriously\s+)?considering\b", re.I)),
    (CognitiveType.GOAL, Modality.DESIRED, .88,
     re.compile(r"\b(?:my goal is|i want to|i aim to|i hope to)\b", re.I)),
    (CognitiveType.INTENT, Modality.INTENDED, .90,
     re.compile(r"\b(?:i intend to|i'm going to|i am going to)\b", re.I)),
    (CognitiveType.PLAN, Modality.INTENDED, .90,
     re.compile(r"\b(?:my plan is|i plan to|the plan is)\b", re.I)),
    (CognitiveType.NEED, Modality.ASSERTED, .88,
     re.compile(r"\b(?:i need to|i need help|what i need is)\b", re.I)),
    (CognitiveType.OUTCOME, Modality.OBSERVED, .90,
     re.compile(r"\b(?:the outcome was|it resulted in|what happened was|ended up)\b", re.I)),
    (CognitiveType.EXPERIENCE, Modality.OBSERVED, .86,
     re.compile(r"\b(?:i learned from|my experience was|when i .+ i (?:learned|found))\b", re.I)),
    (CognitiveType.SKILL, Modality.ASSERTED, .84,
     re.compile(r"\b(?:i learned how to|i can now|a skill i have)\b", re.I)),
    (CognitiveType.PRINCIPLE, Modality.ASSERTED, .82,
     re.compile(r"\b(?:a principle i follow|as a rule|i've learned that|i learned that)\b", re.I)),
    (CognitiveType.CONCLUSION, Modality.ASSERTED, .80,
     re.compile(r"\b(?:i conclude|the conclusion is|therefore)\b", re.I)),
    (CognitiveType.OBSERVATION, Modality.OBSERVED, .84,
     re.compile(r"\b(?:i noticed|i observed|i saw that)\b", re.I)),
)

# Types whose explicit user statements can materially enter personal state.
_MATERIAL = frozenset({
    CognitiveType.FACT, CognitiveType.OBSERVATION, CognitiveType.BELIEF,
    CognitiveType.HYPOTHESIS, CognitiveType.PREFERENCE, CognitiveType.VALUE,
    CognitiveType.GOAL, CognitiveType.INTENT, CognitiveType.NEED,
    CognitiveType.PLAN, CognitiveType.COMMITMENT, CognitiveType.DECISION,
    CognitiveType.BOUNDARY, CognitiveType.ASSUMPTION, CognitiveType.PREDICTION,
    CognitiveType.CONCLUSION, CognitiveType.CORRECTION,
    CognitiveType.CONTRADICTION, CognitiveType.OUTCOME,
    CognitiveType.EXPERIENCE, CognitiveType.SKILL, CognitiveType.PRINCIPLE,
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MeaningCompiler:
    """Compile one utterance into validated semantic candidates."""

    def compile(self, text: str, *, source: str = "conversation") -> SemanticRepresentation:
        text = text.strip()
        if not text:
            raise ValueError("Meaning cannot be compiled from blank text.")
        temporal = self._temporal(text)
        if _QUESTION.search(text):
            candidate = SemanticCandidate(
                type=CognitiveType.QUESTION, content=text,
                modality=Modality.QUESTIONED, confidence=.99,
                provenance=Provenance.USER_STATED, source=source,
                temporal_scope=temporal, material=False)
            ambiguous = bool(re.search(r"\b(?:that|it|this|before|last time)\b", text, re.I))
            return SemanticRepresentation(
                input_text=text, source=source, candidates=[candidate],
                ambiguous=ambiguous,
                ambiguity_reason=("The question contains a reference that must be resolved from authorized conversation focus."
                                  if ambiguous else None))

        if re.search(r"\b(?:i have not|i haven't|i have not yet) decided\b", text, re.I):
            return SemanticRepresentation(input_text=text, source=source, candidates=[
                SemanticCandidate(
                    type=CognitiveType.CLAIM, content=text,
                    modality=Modality.TENTATIVE, confidence=.88,
                    provenance=Provenance.USER_STATED, source=source,
                    temporal_scope=temporal, material=False)])

        for kind, modality, confidence, pattern in _RULES:
            if pattern.search(text):
                ambiguous = bool(_DEICTIC.search(text)) and kind in {
                    CognitiveType.CORRECTION, CognitiveType.BELIEF,
                    CognitiveType.CONTRADICTION}
                candidate = SemanticCandidate(
                    type=kind, content=text, modality=modality,
                    confidence=confidence, provenance=Provenance.USER_STATED,
                    source=source, temporal_scope=temporal,
                    material=kind in _MATERIAL)
                return SemanticRepresentation(
                    input_text=text, source=source, candidates=[candidate],
                    ambiguous=ambiguous,
                    ambiguity_reason=((
                        "The statement refers to an object that must be resolved from authorized conversation focus."
                    ) if ambiguous else None))

        # Imperatives, conversational fragments and transient feelings are
        # turn meaning, not automatic long-term state. First-person durable
        # declarations remain user-stated FACTs (never externally verified).
        first_person = bool(re.search(r"\b(?:i am|i'm|i have|my |we are|we have)\b", text, re.I))
        transient = bool(re.search(
            r"\b(?:i am|i'm)\s+(?:confused|unsure|fine|okay|tired|hungry|angry|sad|happy|busy)\b|"
            r"\b(?:right now|at the moment)\b", text, re.I))
        material = first_person and not transient
        kind = CognitiveType.FACT if material else CognitiveType.CLAIM
        candidate = SemanticCandidate(
            type=kind, content=text, modality=Modality.ASSERTED,
            confidence=.82 if material else .70,
            provenance=Provenance.USER_STATED, source=source,
            temporal_scope=temporal, material=material)
        return SemanticRepresentation(input_text=text, source=source,
                                      candidates=[candidate])

    @staticmethod
    def _temporal(text: str) -> TemporalScope:
        match = _TEMPORAL.search(text)
        if not match:
            return TemporalScope()
        expression = match.group(0)
        lowered = expression.lower()
        kind = "FUTURE" if ("next" in lowered or "tomorrow" in lowered or lowered.startswith("in ") or lowered.startswith("by ")) else "PAST" if ("last" in lowered or "yesterday" in lowered) else "CURRENT"
        return TemporalScope(expression=expression, kind=kind)

    @staticmethod
    def validate_model_output(payload: str | dict[str, Any]) -> SemanticRepresentation:
        """Validate model-produced semantics. Invalid output fails closed."""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
            return SemanticRepresentation.model_validate(data)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ValueError(f"Invalid semantic extraction: {exc}") from exc


_MODEL_SCHEMA = """Return ONLY one JSON object matching this contract:
{
  "schema_version": "9.0",
  "input_text": <the exact user text>,
  "source": "local_model",
  "candidates": [{
    "type": <one of: %s>,
    "content": <the exact user text, never an invented paraphrase>,
    "modality": <one of: %s>,
    "confidence": <number from 0 to 0.85>,
    "provenance": "MODEL_HYPOTHESIS",
    "source": "local_model",
    "temporal_scope": {"expression": string|null, "start": null,
                       "end": null, "kind": "PAST|CURRENT|FUTURE|UNSPECIFIED"},
    "status": "PROPOSED", "evidence": [], "relationships": [],
    "material": false
  }],
  "ambiguous": boolean,
  "ambiguity_reason": string|null,
  "compiler": "model-assisted"
}
Preserve uncertainty and negation. A question is QUESTION, not a goal. A model
interpretation is always MODEL_HYPOTHESIS, never FACT merely because it sounds
plausible. Use at most three candidates. Output JSON only.""" % (
    [v.value for v in CognitiveType], [v.value for v in Modality])


class ModelMeaningCompiler:
    """Optional local-model proposal path behind the existing provider/router."""

    def __init__(self, provider, capability_router, lock=None) -> None:
        self.provider = provider
        self.capability_router = capability_router
        self.lock = lock

    def status(self) -> dict[str, Any]:
        if self.provider is None or self.capability_router is None:
            return {"state": "NOT_CONFIGURED", "detail": "No model provider is wired."}
        status = self.provider.status()
        # V9.0.1 does not initiate paid external semantic calls. The existing
        # agent provider remains untouched; semantic assistance is local Ollama.
        if status.name != "ollama":
            return {"state": "NOT_CONFIGURED",
                    "detail": "Local Ollama semantic extraction is not configured."}
        if not status.available:
            return {"state": "NOT_CONNECTED", "detail": status.detail}
        report = self.capability_router.report()
        if not report.supports(STRUCTURED_OUTPUT):
            return {"state": "DEGRADED",
                    "detail": report.reason(STRUCTURED_OUTPUT)}
        return {"state": "ACTIVE", "detail": f"Local semantic extraction via {status.model}.",
                "model": status.model}

    def propose(self, text: str, deterministic: SemanticRepresentation,
                *, source: str) -> tuple[SemanticRepresentation | None, str | None]:
        status = self.status()
        if status["state"] != "ACTIVE":
            return None, f"{status['state']}: {status['detail']}"
        acquired = self.lock is None or self.lock.acquire(blocking=False)
        if not acquired:
            return None, "DEGRADED: model busy; deterministic compiler used."
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            response = self.provider.chat_model().invoke([
                SystemMessage(content="You classify semantic meaning. Return strict JSON only."),
                HumanMessage(content=f"{_MODEL_SCHEMA}\n\nUser text:\n{text}")])
            raw = response.content if isinstance(response.content, str) else str(response.content)
            proposal = MeaningCompiler.validate_model_output(raw)
            self._validate_policy(proposal, text)
            # Persistence significance is never delegated to the model. It may
            # improve turn interpretation only where deterministic policy had
            # already established materiality.
            deterministic_material = any(c.material for c in deterministic.candidates)
            for candidate in proposal.candidates:
                candidate.material = bool(
                    deterministic_material and candidate.type in _MATERIAL
                    and not proposal.ambiguous)
                candidate.source = f"local_model:{status.get('model') or 'ollama'}"[:120]
            proposal.source = source
            proposal.compiler = "model-assisted"
            return proposal, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"[:300]
        finally:
            if self.lock is not None and acquired:
                self.lock.release()

    @staticmethod
    def _validate_policy(proposal: SemanticRepresentation, text: str) -> None:
        if proposal.input_text.strip() != text.strip():
            raise ValueError("Model semantic input_text did not match the authorized utterance.")
        if len(proposal.candidates) > 3:
            raise ValueError("Model proposed too many semantic candidates.")
        for candidate in proposal.candidates:
            if candidate.provenance != Provenance.MODEL_HYPOTHESIS:
                raise ValueError("Model candidates must retain MODEL_HYPOTHESIS provenance.")
            if candidate.type == CognitiveType.FACT:
                raise ValueError("A model hypothesis cannot be promoted directly to FACT.")
            if candidate.confidence > .85:
                raise ValueError("Model semantic confidence exceeds the policy ceiling.")
            if candidate.content.strip() != text.strip():
                raise ValueError("Model candidate content is not grounded in the user text.")


class MeaningKernel:
    """Compiler → validation → cognitive object → state → canonical EventBus."""

    def __init__(self, db, bus, personal_state, focus=None, *, provider=None,
                 capability_router=None, model_lock=None) -> None:
        self.db = db
        self.bus = bus
        self.personal_state = personal_state
        self.focus = focus
        self.compiler = MeaningCompiler()
        self.model_compiler = ModelMeaningCompiler(
            provider, capability_router, model_lock)

    def process(self, user_id: str, text: str, *, source: str = "conversation",
                thread_id: str | None = None, correlation_id: str | None = None,
                persist: bool = True) -> dict[str, Any]:
        deterministic = self.compiler.compile(text, source=source)
        proposed, model_error = self.model_compiler.propose(
            text, deterministic, source=source)
        semantic = proposed or deterministic
        if proposed is None and model_error:
            semantic.compiler = "deterministic-fallback"
        resolved_correction_target: str | None = None
        # Resolution is authorization-safe because FocusTracker.current is
        # scoped by the verified namespace and conversation session. A vague
        # "change that" still remains ambiguous unless both one target and a
        # concrete replacement are present.
        if (semantic.ambiguous and semantic.candidates
                and semantic.candidates[0].type == CognitiveType.CORRECTION
                and self.focus is not None):
            focused = [entry for entry in self.focus.current(
                user_id, session_id=thread_id or "default")
                if entry["subject_kind"] == "cognitive_object"]
            replacement = re.search(
                r"(?:change\s+(?:that|this|it)\s+to|i meant)\s+(.+?)[.!?]*$",
                text, re.I)
            if len(focused) == 1 and replacement and replacement.group(1).strip():
                resolved_correction_target = focused[0]["subject_id"]
                semantic.candidates[0].content = replacement.group(1).strip()
                semantic.ambiguous = False
                semantic.ambiguity_reason = None
        compilation_id = f"meaning_{uuid.uuid4().hex[:20]}"
        now = _now()
        self.db.execute(
            "INSERT INTO meaning_compilations "
            "(id,user_id,thread_id,input_text,source,compiler,status,semantic_json,error,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (compilation_id, user_id, thread_id, text, source, semantic.compiler,
             "VALID", semantic.model_dump_json(), model_error, correlation_id, now))
        self.bus.emit(
            user_id, "meaning.compiled", "Compiled conversational meaning.",
            subject_kind="meaning_compilation", subject_id=compilation_id,
            correlation_id=correlation_id,
            payload={"candidate_types": [c.type.value for c in semantic.candidates],
                     "ambiguous": semantic.ambiguous, "compiler": semantic.compiler,
                     "model_state": self.model_compiler.status()["state"],
                     "fallback": bool(proposed is None)})

        created: list[dict[str, Any]] = []
        # Ambiguity is preserved, never guessed through. Questions and generic
        # claims remain turn semantics unless the caller explicitly persists.
        if persist and not semantic.ambiguous:
            for candidate in semantic.candidates:
                if not candidate.material:
                    continue
                if resolved_correction_target:
                    replacement = CognitiveObjectCreate(
                        type=candidate.type, content=candidate.content,
                        modality=candidate.modality, confidence=candidate.confidence,
                        provenance=candidate.provenance, source=candidate.source,
                        temporal_scope=candidate.temporal_scope,
                        status="ACTIVE", evidence=candidate.evidence,
                        metadata={"corrects": resolved_correction_target,
                                  "compilation_id": compilation_id})
                    changed = self.personal_state.supersede(
                        user_id, resolved_correction_target, replacement,
                        reason="User correction resolved from conversation focus.",
                        correlation_id=correlation_id, thread_id=thread_id)
                    if changed:
                        created.append(changed["replacement"])
                        try:
                            self.focus.set_focus(
                                user_id, "cognitive_object", changed["replacement"]["id"],
                                session_id=thread_id or "default",
                                label=(f"CORRECTION: "
                                       f"{changed['replacement']['content'][:80]}"),
                                correlation_id=correlation_id)
                        except Exception:
                            pass
                    continue
                duplicate = self.db.query_one(
                    "SELECT * FROM cognitive_objects WHERE user_id=? AND type=?"
                    " AND lower(trim(content))=lower(trim(?))"
                    " AND status NOT IN ('RETIRED','SUPERSEDED','DELETED') LIMIT 1",
                    (user_id, candidate.type.value, candidate.content))
                if duplicate:
                    created.append(self.personal_state._object(duplicate))
                    continue
                obj = self.personal_state.create(
                    user_id, candidate, thread_id=thread_id,
                    correlation_id=correlation_id, compilation_id=compilation_id)
                created.append(obj)
                if self.focus is not None:
                    try:
                        self.focus.set_focus(
                            user_id, "cognitive_object", obj["id"],
                            session_id=thread_id or "default",
                            label=f"{obj['type']}: {obj['content'][:80]}",
                            correlation_id=correlation_id)
                    except Exception:
                        pass
        return {"compilation_id": compilation_id,
                "semantic": semantic.model_dump(mode="json"),
                "semantic_mode": "MODEL" if proposed is not None else "DETERMINISTIC",
                "model_fallback_reason": model_error,
                "created_objects": created, "created_at": now}

    def compilation(self, user_id: str, compilation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM meaning_compilations WHERE id=? AND user_id=?",
            (compilation_id, user_id))
        if not row:
            return None
        item = dict(row)
        item["semantic"] = json.loads(item.pop("semantic_json"))
        return item

    def recent(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM meaning_compilations WHERE user_id=? ORDER BY datetime(created_at) DESC LIMIT ?",
            (user_id, max(1, min(limit, 100))))
        output = []
        for row in rows:
            item = dict(row)
            item["semantic"] = json.loads(item.pop("semantic_json"))
            output.append(item)
        return output
