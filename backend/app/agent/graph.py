"""LangGraph agent with a genuine tool-calling loop.

Graph shape:

    START -> load_context -> agent -> (tools -> agent)* -> memory_manager -> END

`agent` is a real LangGraph node. When a tool-calling chat model is configured
(OpenAI/Ollama) the MODEL decides which memory tool to call; results are fed
back through a ToolNode and the model runs again. When no LLM is configured the
node falls back to a deterministic planner that calls the SAME LangChain tools -
and the response is labelled DEMO so nothing is overstated.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage,
                                     SystemMessage, ToolMessage)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

log = logging.getLogger(__name__)

from ..memory import policy
from ..memory.service import MemoryService

SYSTEM_PROMPT = """You are MEMORY//OS, an assistant with persistent long-term memory.

You have memory tools. Use them deliberately:
- Call search_memory before answering questions about the user, their projects,
  preferences, goals or history.
- Call save_memory when the user states a durable fact about themselves.
- Call update_memory when new information contradicts a stored memory.
- Do not store transient questions or small talk.

Honour retrieved communication preferences (for example: be concise).
Never invent memories you did not retrieve."""


def append_activity(left: list[dict[str, Any]] | None,
                    right: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Reducer so every node appends to the activity trail instead of
    replacing it (each node returns only its own events)."""
    return (left or []) + (right or [])


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_id: str
    thread_id: str
    recalled: list[dict[str, Any]]
    activity: Annotated[list[dict[str, Any]], append_activity]
    provider: str


class MemoryAgent:
    """Builds and runs the LangGraph StateGraph."""

    def __init__(self, service: MemoryService, provider, checkpointer=None,
                 langmem=None, llm_lock=None) -> None:
        self.service = service
        self.provider = provider
        self.checkpointer = checkpointer
        # A local model server keeps one model resident. Serialising turns keeps
        # concurrent requests from forcing a second load and failing.
        self.llm_lock = llm_lock
        # Optional LangMem extractor. Inert unless langmem is installed AND a
        # tool-calling model is configured; see memory/langmem_adapter.py.
        self.langmem = langmem
        self._graph = None

    # ------------------------------------------------------------------ build
    def _tools_for(self, user_id: str, thread_id: str, sink: list[dict[str, Any]]):
        from .tools import build_memory_tools

        def on_event(kind: str, payload: dict[str, Any]) -> None:
            sink.append({"type": kind, **payload})

        return build_memory_tools(self.service, user_id, thread_id, on_event)

    def build(self):
        if self._graph is not None:
            return self._graph

        service = self.service
        provider = self.provider
        agent_self = self

        def load_context(state: AgentState) -> dict[str, Any]:
            """Pre-retrieve long-term memory so it is always available as context."""
            user_id = state["user_id"]
            last = ""
            for m in reversed(state["messages"]):
                if isinstance(m, HumanMessage):
                    last = str(m.content)
                    break
            recalled: list[dict[str, Any]] = []
            activity = [{"type": "LOAD_CONTEXT", "thread_id": state["thread_id"]}]
            if last:
                results = service.search(user_id, last, top_k=4)
                recalled = [r.to_dict() for r in results]
                activity.append({"type": "MEMORY_PRELOAD", "count": len(recalled)})
            return {"recalled": recalled, "activity": activity}

        def agent_node(state: AgentState) -> dict[str, Any]:
            user_id, thread_id = state["user_id"], state["thread_id"]
            sink: list[dict[str, Any]] = []
            tools = agent_self._tools_for(user_id, thread_id, sink)
            model = provider.chat_model()

            context_lines = [
                f"- [{r['memory']['category']}] {r['memory']['content']} "
                f"(id={r['memory']['id']}, relevance={r['score']})"
                for r in state.get("recalled", [])
            ]
            context_block = ("Relevant long-term memories:\n" + "\n".join(context_lines)
                             if context_lines else "No long-term memories matched yet.")

            if model is not None:
                bound = model.bind_tools(tools)
                msgs = [SystemMessage(content=SYSTEM_PROMPT),
                        SystemMessage(content=context_block),
                        *state["messages"]]
                try:
                    response = bound.invoke(msgs)
                except Exception as exc:
                    # A local model can genuinely fail mid-turn - most often it
                    # cannot load because the machine is out of RAM. Fall back to
                    # the deterministic planner and SAY SO, rather than failing
                    # the request with an opaque 500.
                    log.warning("Model call failed (%s); deterministic reply.", exc)
                    note = ("The local model could not run this turn "
                            f"({MemoryAgent._short_reason(str(exc))}). This reply "
                            "came from the deterministic fallback, not the "
                            "language model.")
                    result = agent_self._demo_turn(state, tools, sink)
                    result["activity"] = list(result.get("activity", [])) + [
                        {"type": "PROVIDER_DEGRADED", "detail": note}]
                    result["provider"] = f"{provider.name} (degraded)"
                    last = result["messages"][-1]
                    result["messages"] = [AIMessage(content=f"{last.content}\n\n[{note}]")]
                    return result
                acts = [{"type": "MODEL_CALL", "provider": provider.name}]
                for call in getattr(response, "tool_calls", []) or []:
                    acts.append({"type": "TOOL_DECISION", "tool": call["name"]})
                return {"messages": [response], "activity": acts,
                        "provider": provider.name}

            # ---- deterministic DEMO planner (clearly labelled, same tools) ----
            result = agent_self._demo_turn(state, tools, sink)
            return result

        def should_continue(state: AgentState) -> str:
            last = state["messages"][-1]
            if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                return "tools"
            return "memory_manager"

        def memory_manager(state: AgentState) -> dict[str, Any]:
            """Background extraction pass: capture durable knowledge the model
            did not explicitly save."""
            user_id = state["user_id"]
            last_human = ""
            for m in reversed(state["messages"]):
                if isinstance(m, HumanMessage):
                    last_human = str(m.content)
                    break
            activity: list[dict[str, Any]] = []
            if last_human:
                # If LangMem is genuinely ACTIVE, let it propose the memory and
                # classify the result with our policy engine. Otherwise the
                # deterministic policy engine is the sole extractor.
                candidate = None
                if self.langmem is not None and self.langmem.status().active:
                    for text in self.langmem.extract(
                            [{"role": "user", "content": last_human}]):
                        proposed = policy.classify_extracted(text)
                        if proposed is not None:
                            candidate = proposed
                            activity.append({"type": "LANGMEM_EXTRACT",
                                             "detail": "langmem proposed a memory"})
                            break
                if candidate is None:
                    candidate = policy.evaluate(last_human)
                already_saved = any(
                    isinstance(m, ToolMessage) and "memory_id" in str(m.content)
                    for m in state["messages"][-6:])
                if candidate.is_durable and not already_saved:
                    result = service.create(
                        user_id, candidate.content, category=candidate.category,
                        importance=candidate.importance, confidence=candidate.confidence,
                        source="memory-manager", thread_id=state["thread_id"])
                    activity.append({"type": "MEMORY_MANAGER",
                                     "action": result["action"],
                                     "memory_id": result["memory"]["id"],
                                     "category": result["memory"]["category"]})
            return {"activity": activity}

        graph = StateGraph(AgentState)
        graph.add_node("load_context", load_context)
        graph.add_node("agent", agent_node)
        graph.add_node("tools", ToolNode(self._tools_for("__schema__", "__schema__", [])))
        graph.add_node("memory_manager", memory_manager)

        graph.add_edge(START, "load_context")
        graph.add_edge("load_context", "agent")
        graph.add_conditional_edges("agent", should_continue,
                                    {"tools": "tools", "memory_manager": "memory_manager"})
        graph.add_edge("tools", "agent")
        graph.add_edge("memory_manager", END)

        self._graph = graph.compile(checkpointer=self.checkpointer)
        return self._graph

    # ------------------------------------------------------------- demo turn
    def _demo_turn(self, state: AgentState, tools, sink) -> dict[str, Any]:
        """Deterministic planner. Calls the real LangChain tools, but makes no
        claim of model reasoning - the API reports provider='demo'."""
        service = self.service
        user_id = state["user_id"]
        by_name = {t.name: t for t in tools}
        text = ""
        for m in reversed(state["messages"]):
            if isinstance(m, HumanMessage):
                text = str(m.content)
                break
        low = text.lower().strip()
        activity: list[dict[str, Any]] = [{"type": "DEMO_PLANNER"}]
        recalled = state.get("recalled", [])

        asks_recall = bool(re.search(
            r"\b(what|which|who|how|do you) .*(remember|know|recall|prefer|my|about me)\b|"
            r"\bwhat do you remember\b|\bmy (projects?|preferences?|goals?|style)\b", low))
        states_fact = policy.evaluate(text).is_durable

        reply: str
        if asks_recall:
            raw = by_name["search_memory"].invoke({"query": text, "top_k": 5})
            activity.append({"type": "TOOL_DECISION", "tool": "search_memory"})
            if raw.startswith("NO_STRONG_MATCH"):
                reply = ("No stored memory is strongly relevant to that yet. "
                         "Tell me something durable and I will remember it.")
            else:
                items = json.loads(raw)
                lines = [f"- {i['content']} ({i['category'].replace('_',' ').title()}, "
                         f"relevance {int(i['relevance']*100)}%)" for i in items]
                reply = "Here is what I remember:\n" + "\n".join(lines)
        elif states_fact:
            cand = policy.evaluate(text)
            raw = by_name["save_memory"].invoke({
                "content": cand.content, "category": cand.category,
                "importance": cand.importance})
            activity.append({"type": "TOOL_DECISION", "tool": "save_memory"})
            saved = json.loads(raw)
            verb = {"created": "Stored", "reinforced": "Reinforced",
                    "updated": "Updated"}.get(saved["action"], "Stored")
            reply = (f"{verb} that as a {saved['category'].replace('_',' ').lower()} memory "
                     f"(version {saved['version']}). I will apply it in future conversations.")
        else:
            style = next((r for r in recalled
                          if r["memory"]["category"] == "COMMUNICATION_STYLE"), None)
            if recalled:
                top = recalled[0]["memory"]["content"]
                reply = (f"Using what I remember — {top.rstrip('.')} — here is a direct answer. "
                         f"This is LOCAL DEMO mode, so the wording is composed deterministically "
                         f"rather than generated by a language model.")
            else:
                reply = ("LOCAL DEMO mode: no language model is configured, so responses are "
                         "composed deterministically. Memory, embeddings and retrieval are "
                         "fully real — try telling me a preference, then ask what I remember.")
            if style:
                reply += f" (Applied preference: {style['memory']['content']})"

        return {"messages": [AIMessage(content=reply)], "activity": activity,
                "provider": "demo"}

    # ----------------------------------------------------------------- invoke
    def run(self, user_id: str, thread_id: str, message: str) -> dict[str, Any]:
        graph = self.build()
        config = {"configurable": {"thread_id": thread_id}}
        state_in = {
            "messages": [HumanMessage(content=message)],
            "user_id": user_id, "thread_id": thread_id,
            "recalled": [], "activity": [], "provider": self.provider.name,
        }
        try:
            if self.llm_lock is not None:
                with self.llm_lock:
                    final = graph.invoke(state_in, config=config)
            else:
                final = graph.invoke(state_in, config=config)
        except Exception as exc:
            # Last-resort net: the node above already degrades model failures, so
            # reaching here means something outside the model broke. Report it
            # honestly instead of returning an opaque 500.
            log.warning("Agent turn failed outside the model call: %s", exc)
            note = (f"This turn could not be completed ({self._short_reason(str(exc))}). "
                    "No reply was generated by the language model.")
            return {"answer": f"[{note}]", "recalled": [],
                    "activity": [{"type": "PROVIDER_DEGRADED", "detail": note}],
                    "provider": f"{self.provider.name} (degraded)", "degraded": True}
        answer = ""
        for m in reversed(final["messages"]):
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                answer = str(m.content)
                break
        return {
            "answer": answer,
            "recalled": final.get("recalled", []),
            "activity": final.get("activity", []),
            "provider": self.provider.name,
            "message_count": len(final["messages"]),
        }

    @staticmethod
    def _short_reason(text: str) -> str:
        """One clean clause for the user - no stack traces, no chain of thought."""
        lowered = text.lower()
        if "more system memory" in lowered or "not enough memory" in lowered:
            return "not enough memory to load the model"
        if "connection" in lowered or "refused" in lowered:
            return "the model server is unreachable"
        if "timeout" in lowered or "timed out" in lowered:
            return "the model timed out"
        return "model error"

    def history(self, thread_id: str) -> list[dict[str, str]]:
        graph = self.build()
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snap = graph.get_state(config)
        except Exception:
            return []
        out = []
        for m in (snap.values or {}).get("messages", []):
            if isinstance(m, HumanMessage):
                out.append({"role": "user", "content": str(m.content)})
            elif isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                out.append({"role": "assistant", "content": str(m.content)})
        return out
