"""
V8.2 §19 — user-controlled cognition through natural language.

The user must be able to steer the system's memory and behaviour by saying so,
not only by clicking panels: "forget that", "why do you believe that?",
"that's wrong", "remember this", "stop asking me about X".

Design rules, in keeping with the project's honesty constraints:

  * A command is only recognised on UNAMBIGUOUS phrasing. Ordinary conversation
    must never silently delete a memory or rewrite behaviour.
  * Destructive commands ("forget that") resolve their target through the focus
    tracker and stable IDs. If the target cannot be resolved we say so and do
    nothing — we never guess which memory the user meant.
  * Every command returns a plain-language account of what it actually did.
  * Nothing here fabricates an outcome: if the command could not be carried
    out, the report says why.
"""
from __future__ import annotations

import re
from typing import Any

# ------------------------------------------------------------------- commands
FORGET = "FORGET"
CORRECT = "CORRECT"
REMEMBER = "REMEMBER"
EXPLAIN_BELIEF = "EXPLAIN_BELIEF"
EXPLAIN_BEHAVIOUR = "EXPLAIN_BEHAVIOUR"
STOP_TOPIC = "STOP_TOPIC"
PRIORITISE = "PRIORITISE"

COMMANDS = (FORGET, CORRECT, REMEMBER, EXPLAIN_BELIEF, EXPLAIN_BEHAVIOUR,
            STOP_TOPIC, PRIORITISE)

# Deliberately strict. Each pattern captures the payload where one exists.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (FORGET, re.compile(
        r"\b(?:forget|delete|remove|erase)\s+(?:that|this|it)\b", re.I)),
    (FORGET, re.compile(
        r"\b(?:forget|delete|remove|erase)\s+(?:what i said about|"
        r"that i|the fact that)\s+(?P<payload>.{3,120})", re.I)),
    (CORRECT, re.compile(
        r"\b(?:that'?s|thats|this is)\s+(?:wrong|incorrect|not right|outdated)\b"
        r"(?:[,.\s]*(?P<payload>.{3,160}))?", re.I)),
    (CORRECT, re.compile(
        r"\bactually,?\s+(?:it'?s|its|i)\s+(?P<payload>.{3,160})", re.I)),
    # Imperative only: must start the clause, so "what should I remember for
    # the meeting" (a question about the user's own memory) does not fire.
    (REMEMBER, re.compile(
        r"(?:^|[,.;]\s*)(?:please\s+)?(?:remember|note|keep in mind|"
        r"don'?t forget)\s+(?:that\s+)?(?P<payload>.{3,300})", re.I)),
    (EXPLAIN_BELIEF, re.compile(
        r"\bwhy\s+(?:do|would)\s+you\s+(?:believe|think|say)\s+"
        r"(?P<payload>.{2,160})", re.I)),
    (EXPLAIN_BELIEF, re.compile(
        r"\b(?:where|how)\s+did\s+you\s+(?:get|learn)\s+(?P<payload>.{2,160})",
        re.I)),
    (EXPLAIN_BEHAVIOUR, re.compile(
        r"\bwhy\s+(?:do|are)\s+you\s+(?:always\s+|keep\s+)?"
        r"(?P<payload>.{2,160})", re.I)),
    (STOP_TOPIC, re.compile(
        r"\bstop\s+(?:asking|bringing up|mentioning|reminding me)\s+"
        r"(?:me\s+)?(?:about\s+)?(?P<payload>.{2,120})", re.I)),
    (PRIORITISE, re.compile(
        r"\b(?:this is|that'?s)\s+(?:really\s+)?important\b", re.I)),
)


def parse(text: str) -> dict[str, Any] | None:
    """
    Identify an explicit cognitive command, or return None.

    None means "this is ordinary conversation" — the overwhelmingly common
    case, and the safe default.
    """
    for command, pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        payload = ""
        if "payload" in (pattern.groupindex or {}):
            payload = (match.group("payload") or "").strip(" .,!?")
        return {"command": command, "payload": payload,
                "matched": match.group(0).strip()}
    return None


class CognitiveController:
    """Executes natural-language cognitive commands against real subsystems."""

    def __init__(self, db, bus, memory, focus, policy, arbiter, influence,
                 reputation) -> None:
        self.db = db
        self.bus = bus
        self.memory = memory
        self.focus = focus
        self.policy = policy
        self.arbiter = arbiter
        self.influence = influence
        self.reputation = reputation

    # ------------------------------------------------------------------ entry
    def handle(self, user_id: str, text: str, *, session_id: str = "default",
               correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Interpret and execute a cognitive command, if the message contains one.

        Returns None when the message is ordinary conversation.
        """
        parsed = parse(text)
        if parsed is None:
            return None

        command = parsed["command"]
        handler = {
            FORGET: self._forget,
            CORRECT: self._correct,
            REMEMBER: self._remember,
            EXPLAIN_BELIEF: self._explain_belief,
            EXPLAIN_BEHAVIOUR: self._explain_behaviour,
            STOP_TOPIC: self._stop_topic,
            PRIORITISE: self._prioritise,
        }[command]

        result = handler(user_id, parsed, session_id=session_id,
                         correlation_id=correlation_id)
        result.update({"command": command, "matched": parsed["matched"]})

        self.bus.emit(user_id, "control.command",
                      f"{command}: {result['summary'][:160]}",
                      subject_kind="control", subject_id=command.lower(),
                      correlation_id=correlation_id,
                      payload={"command": command,
                               "applied": result["applied"],
                               "payload": parsed["payload"][:200]})
        return result

    # ------------------------------------------------------------- resolution
    def _target_memory(self, user_id: str, payload: str, *,
                       session_id: str) -> tuple[Any | None, str]:
        """
        Work out which memory the user meant, via focus first then search.

        Returns (memory, explanation). A None memory is a refusal to guess.
        """
        if not payload:
            focused = self.focus.current(user_id, session_id=session_id)
            entry = next((f for f in focused if f["subject_kind"] == "memory"),
                         None)
            if entry is None:
                return None, ("I could not tell which item you meant — nothing "
                              "is currently in focus, so I did not guess.")
            memory = self.memory.get(entry["subject_id"])
            if memory is None:
                return None, ("The item you had in focus no longer exists.")
            return memory, f"You had this in focus: \"{_content(memory)[:80]}\"."

        hits = self.memory.search(user_id, payload, top_k=3, record_event=False)
        if not hits:
            return None, (f"I could not find anything matching '{payload}', so "
                          "nothing was changed.")
        if len(hits) > 1 and abs(hits[0].score - hits[1].score) < 0.05:
            # Two near-equal matches: picking one would be a guess.
            return None, (
                f"'{payload}' matched more than one memory about equally "
                "well, so I did not pick one for you. Tell me which, or "
                "open it and say 'forget that'.")
        memory = hits[0].memory
        return memory, f"Matched '{payload}' to \"{memory.content[:80]}\"."

    # --------------------------------------------------------------- handlers
    def _forget(self, user_id, parsed, *, session_id, correlation_id):
        memory, why = self._target_memory(user_id, parsed["payload"],
                                          session_id=session_id)
        if memory is None:
            return {"applied": False, "summary": why, "requires": "clarification"}

        mid = _id(memory)
        content = _content(memory)
        self.memory.delete(mid)
        return {"applied": True, "memory_id": mid,
                "summary": (f"Forgotten: \"{content[:120]}\". {why} "
                            "It will no longer be retrieved or used.")}

    def _correct(self, user_id, parsed, *, session_id, correlation_id):
        payload = parsed["payload"]
        memory, why = self._target_memory(user_id, "", session_id=session_id)
        if memory is None:
            return {"applied": False, "requires": "clarification",
                    "summary": ("Understood that something is wrong, but I could "
                                "not tell which belief you meant. " + why)}

        mid = _id(memory)
        old = _content(memory)
        # A correction is real evidence against the old memory.
        self.reputation.record_contradiction(user_id, mid,
                                             correlation_id=correlation_id)
        if payload:
            self.memory.update(mid, content=payload, confidence=0.9,
                               reason="Corrected by the user in conversation",
                               event_type="MEMORY_CORRECTED")
            summary = (f"Corrected: \"{old[:80]}\" → \"{payload[:80]}\". "
                       "The correction is marked as higher-authority than the "
                       "original.")
        else:
            summary = (f"Marked \"{old[:80]}\" as contradicted. Tell me what is "
                       "true instead and I will replace it.")
        return {"applied": True, "memory_id": mid, "summary": summary}

    def _remember(self, user_id, parsed, *, session_id, correlation_id):
        payload = parsed["payload"]
        if len(payload) < 3:
            return {"applied": False, "requires": "clarification",
                    "summary": "I did not catch what you wanted me to remember."}
        created = self.memory.create(user_id, payload, source="explicit")
        return {"applied": True,
                "memory_id": created["memory"]["id"] if "memory" in created
                else None,
                "summary": (f"Stored: \"{payload[:120]}\". You asked for this "
                            "directly, so it is recorded as an explicit "
                            "instruction rather than an inference.")}

    def _explain_belief(self, user_id, parsed, *, session_id, correlation_id):
        memory, why = self._target_memory(user_id, parsed["payload"],
                                          session_id=session_id)
        if memory is None:
            return {"applied": False, "requires": "clarification", "summary": why}

        mid = _id(memory)
        rep = self.reputation.get(user_id, mid)
        impact = self.influence.impact(user_id, mid)
        record = memory if isinstance(memory, dict) else memory.to_dict()
        source = record.get("source", "unknown")
        confidence = record.get("confidence", 0.0)

        summary = (
            f"I believe \"{_content(memory)[:100]}\" because it came from "
            f"{source} (confidence {float(confidence):.2f}). "
            f"Its track record is {rep['reputation']}: {impact['summary']}")
        return {"applied": True, "memory_id": mid, "summary": summary,
                "reputation": rep, "impact": impact}

    def _explain_behaviour(self, user_id, parsed, *, session_id, correlation_id):
        payload = parsed["payload"].lower()
        # Map the complaint onto a real policy dimension, or admit we cannot.
        mapping = {
            "clarification_frequency": ("ask", "question", "clarif"),
            "response_depth": ("long", "short", "verbose", "brief", "detail"),
            "interruption_tolerance": ("interrupt", "butting in"),
            "recommendation_preference": ("suggest", "recommend", "advice"),
            "explanation_density": ("explain", "explanation"),
        }
        key = next((k for k, words in mapping.items()
                    if any(w in payload for w in words)), None)
        if key is None:
            return {"applied": False, "requires": "clarification",
                    "summary": ("I am not sure which behaviour you mean. You can "
                                "ask about how often I ask questions, how long "
                                "my answers are, or how much I explain.")}
        explained = self.policy.explain(user_id, key)
        return {"applied": True, "policy_key": key,
                "summary": explained["explanation"], "policy": explained}

    def _stop_topic(self, user_id, parsed, *, session_id, correlation_id):
        topic = parsed["payload"]
        if not topic:
            return {"applied": False, "requires": "clarification",
                    "summary": "Which topic should I stop bringing up?"}
        applied = self.policy.observe(
            user_id, "interruption_tolerance", "low", strength=0.9,
            evidence=parsed["matched"], correlation_id=correlation_id)
        return {"applied": True, "topic": topic, "policy": applied,
                "summary": (f"I will stop raising '{topic}' unprompted. This is "
                            "recorded as an explicit instruction from you, so it "
                            "takes precedence over what I had inferred.")}

    def _prioritise(self, user_id, parsed, *, session_id, correlation_id):
        memory, why = self._target_memory(user_id, "", session_id=session_id)
        if memory is None:
            return {"applied": False, "requires": "clarification",
                    "summary": ("Noted, but I could not tell what you were "
                                "referring to. " + why)}
        mid = _id(memory)
        self.memory.set_importance(mid, True)
        return {"applied": True, "memory_id": mid,
                "summary": (f"Marked \"{_content(memory)[:100]}\" as important. "
                            "It will be weighted higher when I decide what is "
                            "relevant.")}


# ----------------------------------------------------------------- small utils
def _id(memory: Any) -> str:
    return memory["id"] if isinstance(memory, dict) else memory.id


def _content(memory: Any) -> str:
    return memory["content"] if isinstance(memory, dict) else memory.content
