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
import uuid
from typing import Annotated, Any, Sequence, TypedDict

from langchain_core.messages import (AIMessage, BaseMessage, HumanMessage,
                                     SystemMessage, ToolMessage)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

log = logging.getLogger(__name__)

from ..memory import policy
from ..memory.service import MemoryService
from .execution import (DEGRADED, FINAL_RESPONSE, LIMIT_REACHED, MODEL_CALL,
                        MODEL_REVISION, TOOL_DECISION, Cancellation,
                        ExecutionTrace, run_tool_safely)

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
    # v8.2: identifies the ExecutionTrace for this run. The trace itself is held
    # on the agent (it is not JSON-serialisable) and looked up by this key, so
    # LangGraph checkpoints stay small and portable.
    run_id: str


class MemoryAgent:
    """Builds and runs the LangGraph StateGraph."""

    def __init__(self, service: MemoryService, provider, checkpointer=None,
                 langmem=None, llm_lock=None, *, recorder=None,
                 context_builder=None, router=None, policy_engine=None,
                 max_tool_depth: int = 4, turn_timeout_s: float | None = None) -> None:
        self.service = service
        self.provider = provider
        self.checkpointer = checkpointer
        # A local model server keeps one model resident. Serialising turns keeps
        # concurrent requests from forcing a second load and failing.
        self.llm_lock = llm_lock
        # Optional LangMem extractor. Inert unless langmem is installed AND a
        # tool-calling model is configured; see memory/langmem_adapter.py.
        self.langmem = langmem
        # --- v8.2 cognitive wiring. All optional so the agent stays usable
        # standalone (and so existing tests constructing it directly still work).
        self.recorder = recorder            # TraceRecorder
        self.context_builder = context_builder  # ContextBuilder
        self.router = router                # CapabilityRouter
        self.policy_engine = policy_engine  # CognitivePolicyEngine
        self.max_tool_depth = max_tool_depth
        self.turn_timeout_s = turn_timeout_s
        self._traces: dict[str, ExecutionTrace] = {}
        self._bundles: dict[str, Any] = {}
        self._graph = None

    # ----------------------------------------------------------- trace access
    def _trace(self, run_id: str) -> ExecutionTrace | None:
        return self._traces.get(run_id)

    def _emit(self, trace: ExecutionTrace | None, stage: str, detail: str = "",
              **payload: Any) -> dict[str, Any] | None:
        """Record a trace step through the recorder when one is wired."""
        if trace is None:
            return None
        if self.recorder is not None:
            return self.recorder.record(trace, stage, detail, **payload)
        return trace.add(stage, detail, **payload)

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
            """
            Assemble the context for this turn.

            v8.2: when a ContextBuilder is wired, this is the ONE canonical
            context-assembly stage — memories, goals, commitments, world state,
            intent, preferences and capability state, scoped and ranked. Without
            it (standalone agent) the v8.1 memory preload still runs, so the
            agent keeps working unchanged.
            """
            user_id = state["user_id"]
            trace = agent_self._trace(state.get("run_id", ""))
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

            if agent_self.context_builder is not None and last:
                try:
                    bundle = agent_self.context_builder.build(
                        user_id, last,
                        retrieved=[{**r["memory"], "score": r["score"],
                                    "reasons": r.get("reasons", [])}
                                   for r in recalled],
                        thread_id=state["thread_id"],
                        correlation_id=trace.correlation_id if trace else None)
                    agent_self._bundles[state.get("run_id", "")] = bundle
                    agent_self._emit(
                        trace, "CONTEXT_BUILD",
                        f"Assembled {len(bundle.items)} context item(s)",
                        items=len(bundle.items), truncated=bundle.truncated,
                        degraded=bundle.degraded)
                    activity.append({"type": "CONTEXT_BUILD",
                                     "items": len(bundle.items),
                                     "truncated": bundle.truncated})
                except Exception as exc:  # context must never break the turn
                    log.info("Context assembly degraded: %s", exc)
                    activity.append({"type": "CONTEXT_BUILD", "degraded": True,
                                     "detail": str(exc)[:200]})
            return {"recalled": recalled, "activity": activity}

        def agent_node(state: AgentState) -> dict[str, Any]:
            user_id, thread_id = state["user_id"], state["thread_id"]
            run_id = state.get("run_id", "")
            trace = agent_self._trace(run_id)
            sink: list[dict[str, Any]] = []
            tools = agent_self._tools_for(user_id, thread_id, sink)
            model = provider.chat_model()

            # --- v8.2 bounded loop guards, checked before every model call ---
            if trace is not None:
                stop, why = trace.should_stop()
                if stop:
                    agent_self._emit(trace, LIMIT_REACHED,
                                     f"Stopped: {why}", reason=why)
                    note = (f"I stopped working on this turn because it {why}. "
                            "Here is what I had at that point.")
                    return {"messages": [AIMessage(content=note)],
                            "activity": [{"type": LIMIT_REACHED, "reason": why}],
                            "provider": provider.name}

            # Prefer the assembled cognitive context; fall back to the v8.1
            # memory-only block when no builder is wired.
            bundle = agent_self._bundles.get(run_id)
            if bundle is not None:
                context_block = bundle.to_prompt()
            else:
                context_lines = [
                    f"- [{r['memory']['category']}] {r['memory']['content']} "
                    f"(id={r['memory']['id']}, relevance={r['score']})"
                    for r in state.get("recalled", [])
                ]
                context_block = ("Relevant long-term memories:\n"
                                 + "\n".join(context_lines) if context_lines
                                 else "No long-term memories matched yet.")

            # Behavioural policy shapes HOW to answer, never WHAT is true.
            style_block = agent_self._style_block(user_id)

            if model is not None:
                is_revision = bool(trace and trace.tool_rounds() > 0)
                bound = model.bind_tools(tools)
                msgs = [SystemMessage(content=SYSTEM_PROMPT),
                        SystemMessage(content=context_block),
                        *([SystemMessage(content=style_block)] if style_block else []),
                        *state["messages"]]
                agent_self._emit(
                    trace, MODEL_REVISION if is_revision else MODEL_CALL,
                    (f"Reconsidering after {trace.tool_rounds()} tool round(s)"
                     if is_revision else
                     f"Asking {provider.name} model {provider.status().model}"),
                    provider=provider.name)
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
                    agent_self._emit(trace, DEGRADED, note,
                                     error=str(exc)[:200])
                    result = agent_self._demo_turn(state, tools, sink)
                    result["activity"] = list(result.get("activity", [])) + [
                        {"type": "PROVIDER_DEGRADED", "detail": note}]
                    result["provider"] = f"{provider.name} (degraded)"
                    last = result["messages"][-1]
                    result["messages"] = [AIMessage(content=f"{last.content}\n\n[{note}]")]
                    return result

                acts: list[dict[str, Any]] = [
                    {"type": MODEL_REVISION if is_revision else MODEL_CALL,
                     "provider": provider.name}]
                calls = getattr(response, "tool_calls", []) or []
                for call in calls:
                    acts.append({"type": TOOL_DECISION, "tool": call["name"]})
                    agent_self._emit(trace, TOOL_DECISION,
                                     f"Model chose tool '{call['name']}'",
                                     tool=call["name"])
                if not calls:
                    agent_self._emit(trace, FINAL_RESPONSE,
                                     "Model produced a final answer",
                                     provider=provider.name)
                return {"messages": [response], "activity": acts,
                        "provider": provider.name}

            # ---- deterministic DEMO planner (clearly labelled, same tools) ----
            result = agent_self._demo_turn(state, tools, sink)
            agent_self._emit(trace, FINAL_RESPONSE,
                             "Deterministic planner produced the answer",
                             provider="demo")
            return result

        def tools_node(state: AgentState) -> dict[str, Any]:
            """
            Execute the tool calls the MODEL chose.

            v8.2 replaces the prebuilt ToolNode so every call gets duplicate
            detection, failure recovery and a real TOOL_RESULT trace entry. The
            tools themselves are unchanged.
            """
            user_id, thread_id = state["user_id"], state["thread_id"]
            run_id = state.get("run_id", "")
            trace = agent_self._trace(run_id)
            sink: list[dict[str, Any]] = []
            by_name = {t.name: t for t in
                       agent_self._tools_for(user_id, thread_id, sink)}

            last = state["messages"][-1]
            messages: list[BaseMessage] = []
            activity: list[dict[str, Any]] = []
            for call in (getattr(last, "tool_calls", None) or []):
                name = call.get("name", "")
                args = call.get("args", {}) or {}
                tool = by_name.get(name)
                if tool is None:
                    content = (f"TOOL_ERROR: no tool named '{name}' exists. "
                               f"Available tools: {', '.join(sorted(by_name))}.")
                    activity.append({"type": "TOOL_FAILED", "tool": name,
                                     "error": "unknown tool"})
                else:
                    outcome = run_tool_safely(trace, agent_self.recorder, tool,
                                              args) if trace is not None else \
                        agent_self._invoke_untraced(tool, args)
                    content = outcome["content"]
                    activity.append({
                        "type": "TOOL_RESULT" if outcome["ok"] else "TOOL_FAILED",
                        "tool": name,
                        **({"duplicate": True} if outcome.get("duplicate") else {}),
                        **({"error": outcome["error"]} if outcome.get("error") else {})})
                messages.append(ToolMessage(content=content,
                                            tool_call_id=call.get("id", name)))
            return {"messages": messages, "activity": activity + sink}

        def should_continue(state: AgentState) -> str:
            last = state["messages"][-1]
            trace = agent_self._trace(state.get("run_id", ""))
            if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                # Bounded loop: refuse another tool round past the limit.
                if trace is not None:
                    stop, why = trace.should_stop()
                    if stop:
                        agent_self._emit(trace, LIMIT_REACHED,
                                         f"Not running more tools: {why}",
                                         reason=why)
                        return "memory_manager"
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
        graph.add_node("tools", tools_node)
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

    # ------------------------------------------------------------ style block
    def _style_block(self, user_id: str) -> str:
        """
        Turn the learned behavioural policy into a short instruction.

        Only dimensions with genuine evidence are mentioned — defaults are left
        unsaid so the model is not steered by preferences we never observed.
        """
        if self.policy_engine is None:
            return ""
        try:
            learned = self.policy_engine.learned(user_id)
        except Exception:
            return ""
        phrasing = {
            ("response_depth", "brief"): "Keep the answer short and direct.",
            ("response_depth", "detailed"): "Give a thorough, detailed answer.",
            ("clarification_frequency", "low"):
                "Do not ask clarifying questions unless it is truly ambiguous.",
            ("clarification_frequency", "high"):
                "Confirm your understanding before giving a long answer.",
            ("planning_preference", "structured"):
                "Lay out the steps before the answer.",
            ("planning_preference", "none"): "Skip planning; answer directly.",
            ("recommendation_preference", "single"):
                "Recommend one option rather than listing alternatives.",
            ("recommendation_preference", "options"):
                "Offer the alternatives rather than a single recommendation.",
            ("explanation_density", "minimal"):
                "Do not explain your reasoning unless asked.",
            ("explanation_density", "verbose"):
                "Briefly cite what evidence you used.",
        }
        lines = [phrasing[(p["key"], p["value"])] for p in learned
                 if (p["key"], p["value"]) in phrasing
                 and p["confidence"] >= 0.45]
        if not lines:
            return ""
        return ("Observed preferences for this user (apply to STYLE only, never "
                "invent facts):\n" + "\n".join(f"- {line}" for line in lines))

    @staticmethod
    def _invoke_untraced(tool, args: dict[str, Any]) -> dict[str, Any]:
        """Tool invocation for a standalone agent with no trace wired."""
        try:
            return {"ok": True, "content": str(tool.invoke(args)),
                    "duplicate": False, "error": None}
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:300]
            return {"ok": False,
                    "content": f"TOOL_ERROR: {getattr(tool, 'name', 'tool')} "
                               f"failed ({detail}).",
                    "duplicate": False, "error": detail}

    # ----------------------------------------------------------------- invoke
    def run(self, user_id: str, thread_id: str, message: str, *,
            correlation_id: str | None = None,
            cancellation: Cancellation | None = None,
            timeout_s: float | None = None) -> dict[str, Any]:
        graph = self.build()
        config = {"configurable": {"thread_id": thread_id}}

        # v8.2: one ExecutionTrace per turn, carrying the loop limits.
        run_id = uuid.uuid4().hex
        trace: ExecutionTrace | None = None
        if self.recorder is not None:
            trace = self.recorder.new_trace(
                user_id, correlation_id=correlation_id, thread_id=thread_id,
                deadline_s=timeout_s if timeout_s is not None else self.turn_timeout_s,
                max_tool_depth=self.max_tool_depth, cancellation=cancellation)
            self._traces[run_id] = trace

        state_in = {
            "messages": [HumanMessage(content=message)],
            "user_id": user_id, "thread_id": thread_id,
            "recalled": [], "activity": [], "provider": self.provider.name,
            "run_id": run_id,
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
            self._emit(trace, DEGRADED, note, error=str(exc)[:200])
            self._cleanup_run(run_id)
            return {"answer": f"[{note}]", "recalled": [],
                    "activity": [{"type": "PROVIDER_DEGRADED", "detail": note}],
                    "provider": f"{self.provider.name} (degraded)", "degraded": True,
                    "correlation_id": trace.correlation_id if trace else None}
        answer = ""
        for m in reversed(final["messages"]):
            if isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
                if str(m.content).strip():
                    answer = str(m.content)
                    break

        if not answer.strip():
            # The loop ended while the model was still mid-tool-call - it hit the
            # depth limit, the deadline, or was cancelled. Say so plainly rather
            # than returning an empty reply or inventing an answer.
            if trace is not None:
                stop, why = trace.should_stop()
                reason = why or "the turn ended before a final answer was produced"
            else:
                reason = "the turn ended before a final answer was produced"
            answer = (f"I stopped before finishing this one: {reason}. "
                      "I did not produce a final answer, so nothing here is "
                      "guessed - ask again and I will pick it back up.")
            self._emit(trace, LIMIT_REACHED, f"No final answer: {reason}",
                       reason=reason)

        bundle = self._bundles.get(run_id)
        result = {
            "answer": answer,
            "recalled": final.get("recalled", []),
            "activity": final.get("activity", []),
            "provider": self.provider.name,
            "message_count": len(final["messages"]),
            "correlation_id": trace.correlation_id if trace else None,
            "execution": trace.summary() if trace else None,
            "context": bundle.as_dict() if bundle is not None else None,
        }
        self._cleanup_run(run_id)
        return result

    def _cleanup_run(self, run_id: str) -> None:
        """Drop per-run scratch state so long-lived processes do not grow."""
        self._traces.pop(run_id, None)
        self._bundles.pop(run_id, None)

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
