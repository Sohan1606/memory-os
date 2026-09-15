"""MEMORY//OS FastAPI application."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .cognition.autonomy import LEVELS
from .cognition.events import EVENT_TYPES
from .config import settings
from .runtime import Runtime, get_runtime
from .cognition.continuity import REASONS as CONTINUITY_REASONS
from .schemas.api import (AutonomyRequest, ChatRequest, ChatResponse,
                          ConsolidateRequest, ControlRequest, DecisionRequest,
                          FocusRequest,
                          ImportRequest, InfluenceOutcomeRequest,
                          MemoryCreateRequest, MemoryUpdateRequest,
                          NeedEvaluationRequest, OutcomeRequest,
                          PredictionObservationRequest, PredictionResolveRequest,
                          SandboxRequest, SearchRequest)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(
    title="MEMORY//OS API",
    description="Local-first AI agent with long-term memory. LangGraph + LangChain "
                "+ ChromaDB + local embeddings + SQLite.",
    version="1.0.0",
)

origins = ["*"] if settings.cors_origins.strip() == "*" else [
    o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"],
)


def rt() -> Runtime:
    return get_runtime()


def uid(runtime: Runtime, provided: str | None) -> str:
    return (provided or runtime.settings.demo_user_id).strip()[:100]


@app.exception_handler(ValueError)
async def value_error_handler(_request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------- diagnostics
@app.get("/api/health")
def health(runtime: Runtime = Depends(rt)) -> dict[str, Any]:
    return runtime.health()


# ---------------------------------------------------------------------- chat
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, runtime: Runtime = Depends(rt)) -> ChatResponse:
    user_id = uid(runtime, req.user_id)
    runtime.db.execute(
        "INSERT OR IGNORE INTO conversations (id, user_id, title, created_at, updated_at)"
        " VALUES (?, ?, ?, datetime('now'), datetime('now'))",
        (req.thread_id, user_id, req.message[:60]))
    # v8 cognitive loop runs alongside the agent. It builds understanding
    # (intent, world, prediction, attention) but never blocks the reply: if it
    # fails the conversation continues and the failure is recorded as an event.
    # v8.2: one correlation id spans the cognitive loop AND the agent's
    # execution trace, so /api/cognition/turn/{id} and /api/execution/{id}
    # describe the same turn.
    correlation_id = f"turn_{uuid.uuid4().hex[:12]}"
    trace: dict[str, Any] | None = None
    try:
        trace = runtime.cognition.process_turn(
            user_id, req.message, conversation_id=req.thread_id,
            correlation_id=correlation_id)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Cognitive loop failed for this turn: %s", exc)

    result = runtime.agent.run(user_id, req.thread_id, req.message,
                               correlation_id=correlation_id)
    for role, content in (("user", req.message), ("assistant", result["answer"])):
        runtime.db.execute(
            "INSERT INTO messages (thread_id, user_id, role, content, created_at)"
            " VALUES (?,?,?,?, datetime('now'))", (req.thread_id, user_id, role, content))
    runtime.db.execute("UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                       (req.thread_id,))
    if trace is not None:
        runtime.cognition.bus.emit(
            user_id, "conversation.response", result["answer"][:200],
            subject_kind="conversation", subject_id=req.thread_id,
            correlation_id=trace["correlation_id"],
            payload={"provider": result["provider"]})

    return ChatResponse(
        answer=result["answer"], provider=result["provider"],
        recalled=result["recalled"], activity=result["activity"],
        thread_id=req.thread_id,
        correlation_id=trace["correlation_id"] if trace else None,
        cognition=_summarize_trace(trace) if trace else None)


def _summarize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Compact, human-readable view of the turn for the primary UI."""
    attention = trace.get("attention")
    arbitration = trace.get("arbitration")
    transition = trace.get("intent_transition") or {}
    context = trace.get("context") or {}

    # v8.2 arbitration records carry the winner as a flat candidate. The v8.1
    # nested {"memory": {...}} shape is still accepted so older callers and
    # stored traces keep working.
    summary_arbitration = None
    if arbitration and arbitration.get("winner"):
        winner = arbitration["winner"]
        content = (winner.get("content")
                   or (winner.get("memory") or {}).get("content"))
        summary_arbitration = {
            "winner": content,
            "winner_id": (winner.get("memory_id")
                          or (winner.get("memory") or {}).get("id")),
            "explanation": (arbitration.get("reason")
                            or arbitration.get("explanation")),
            "conflict": bool(arbitration.get("conflict")),
            "uncertainty": arbitration.get("uncertainty"),
        }

    return {
        "need": trace["need"]["need"],
        "need_confidence": trace["need"]["confidence"],
        "intent": (trace["intent"] or {}).get("label"),
        "world_entities": [{"kind": e["kind"], "label": e["label"],
                            "state": e["state"]} for e in trace["world_entities"]],
        "recalled": trace["retrieval"]["count"],
        "degraded": trace["retrieval"]["degraded"],
        "arbitration": summary_arbitration,
        "predictions": len(trace.get("predictions") or []),
        "attention": ({"decision": attention["decision"],
                       "why_now": attention["why_now"],
                       "surface": attention["surface"]} if attention else None),
        "autonomy": trace["autonomy_level"],
        # ------------------------------------------------------------- v8.2
        "mode": (trace.get("routing") or {}).get("mode"),
        "degraded_mode": bool((trace.get("routing") or {}).get("degraded")),
        "intent_changed": bool(transition.get("changed")),
        "intent_changed_because": transition.get("changed_because"),
        "intent_uncertainty": transition.get("uncertainty"),
        "context_items": context.get("item_count", 0),
        "context_truncated": bool(context.get("truncated")),
        "continuity": [{"summary": c["summary"], "reason": c["reason_label"]}
                       for c in (trace.get("continuity") or [])[:3]],
        "policy_changes": [{"key": p["key"], "value": p["value"]}
                           for p in (trace.get("policy_changes") or [])
                           if p.get("changed")],
        "influenced_by": [i["memory_id"] for i in (trace.get("influences") or [])],
        "excluded_memories": trace["retrieval"].get("excluded_count", 0),
    }


@app.get("/api/conversations")
def conversations(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    rows = runtime.db.query(
        "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id=?"
        " ORDER BY datetime(updated_at) DESC", (u,))
    return [dict(r) for r in rows]


@app.get("/api/conversations/{thread_id}/messages")
def thread_messages(thread_id: str, user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    rows = runtime.db.query(
        "SELECT role, content, created_at FROM messages WHERE thread_id=? AND user_id=?"
        " ORDER BY id ASC", (thread_id, u))
    return {"thread_id": thread_id, "messages": [dict(r) for r in rows],
            "checkpoint_messages": runtime.agent.history(thread_id)}


@app.delete("/api/conversations/{thread_id}")
def delete_conversation(thread_id: str, user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    runtime.db.execute("DELETE FROM messages WHERE thread_id=? AND user_id=?", (thread_id, u))
    runtime.db.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (thread_id, u))
    return {"deleted": thread_id}


# ------------------------------------------------------------------ memories
@app.get("/api/memories")
def list_memories(user_id: str | None = None, category: str | None = None,
                  q: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    mems = runtime.memory.list(u, category=category, query=q)
    return {"memories": [m.to_dict() for m in mems], "stats": runtime.memory.stats(u)}


@app.post("/api/memories", status_code=201)
def create_memory(req: MemoryCreateRequest, runtime: Runtime = Depends(rt)):
    u = uid(runtime, req.user_id)
    return runtime.memory.create(u, req.content, category=req.category,
                                 importance=req.importance, source=req.source)


@app.get("/api/memories/graph")
def memory_graph(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return runtime.memory.graph(uid(runtime, user_id))


@app.get("/api/memories/timeline")
def memory_timeline(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    return {"events": runtime.memory.events(u, limit=300),
            "memories": [m.to_dict() for m in runtime.memory.list(u)]}


@app.post("/api/memories/search")
def search_memories(req: SearchRequest, runtime: Runtime = Depends(rt)):
    u = uid(runtime, req.user_id)
    query = req.query.strip()
    if not query:
        return {"query": "", "state": "EMPTY", "results": [], "path": [],
                "mode": runtime.vectors.mode}
    results = runtime.memory.search(u, query, top_k=req.top_k, category=req.category)
    if not results:
        return {"query": query, "state": "NO_STRONG_MATCH", "results": [], "path": [],
                "mode": runtime.vectors.mode}
    state = "STRONG" if any(r.strength == "strong" for r in results) else "WEAK"
    return {"query": query, "state": state,
            "results": [r.to_dict() for r in results],
            "path": runtime.memory.retrieval_path(u, query, results),
            "mode": runtime.vectors.mode}


@app.post("/api/memories/consolidate")
def consolidate(req: ConsolidateRequest, runtime: Runtime = Depends(rt)):
    u = uid(runtime, req.user_id)
    try:
        return runtime.memory.consolidate(u, req.memory_ids, req.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/memories/{memory_id}")
def get_memory(memory_id: str, runtime: Runtime = Depends(rt)):
    mem = runtime.memory.get(memory_id)
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": mem.to_dict(), "versions": runtime.memory.versions(memory_id),
            "related": [runtime.memory.get(r).to_dict()
                        for r in mem.related_memory_ids if runtime.memory.get(r)]}


@app.patch("/api/memories/{memory_id}")
def update_memory(memory_id: str, req: MemoryUpdateRequest, runtime: Runtime = Depends(rt)):
    try:
        mem = runtime.memory.update(memory_id, content=req.content, category=req.category,
                                    importance=req.importance, reason=req.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": mem.to_dict(), "versions": runtime.memory.versions(memory_id)}


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str, runtime: Runtime = Depends(rt)):
    if not runtime.memory.delete(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": memory_id}


# --------------------------------------------------------------------- admin
@app.get("/api/events")
def events(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return {"events": runtime.memory.events(uid(runtime, user_id))}


@app.get("/api/export")
def export(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return runtime.memory.export(uid(runtime, user_id))


@app.post("/api/import")
def import_memories(req: ImportRequest, runtime: Runtime = Depends(rt)):
    u = uid(runtime, req.user_id)
    payload = req.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("memories"), list):
        raise HTTPException(status_code=400, detail="Invalid export payload: 'memories' missing.")
    if req.replace:
        runtime.memory.delete_all(u)
    imported = 0
    for raw in payload["memories"]:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        if not content or len(content) > 2000:
            continue
        category = str(raw.get("category", "CONTEXT")).upper()[:40]
        runtime.memory.create(u, content, category=category,
                              importance=float(raw.get("importance", 0.7)),
                              source="import", allow_duplicate=False)
        imported += 1
    return {"imported": imported, "total": len(runtime.memory.list(u))}


@app.post("/api/reset")
def reset(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    if uid(runtime, user_id) != runtime.settings.demo_user_id:
        raise HTTPException(status_code=400, detail="Reset applies to the demo user only.")
    count = runtime.reset_demo()
    return {"reset": True, "seeded": count}


@app.delete("/api/memories")
def delete_all(user_id: str | None = None, confirm: bool = False,
               runtime: Runtime = Depends(rt)):
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to delete all memories.")
    return {"deleted": runtime.memory.delete_all(uid(runtime, user_id))}


# --------------------------------------------------------------------- voice
@app.get("/api/voice/status")
def voice_status(runtime: Runtime = Depends(rt)):
    return {"mode": runtime.transcriber.mode, "detail": runtime.transcriber.detail,
            "browser_fallback": True}


@app.post("/api/voice/transcribe")
async def transcribe(file: UploadFile = File(...), runtime: Runtime = Depends(rt)):
    if not runtime.transcriber.available:
        raise HTTPException(status_code=503,
                            detail="Local Whisper is not configured. Use browser speech input.")
    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (limit 25MB).")
    try:
        return {"transcript": runtime.transcriber.transcribe(data)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")


# ----------------------------------------------------------------- cognition
# Every route below reads from the canonical cognitive event log and the
# subsystems that write to it. Nothing here fabricates state: where evidence is
# missing the payload says INSUFFICIENT EVIDENCE / NOT CONFIGURED.

@app.get("/api/cognition/status")
def cognition_status(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Single snapshot powering the Observatory."""
    return runtime.cognition.status(uid(runtime, user_id))


@app.get("/api/cognition/events")
def cognitive_events(user_id: str | None = None, limit: int = 100,
                     since: int | None = None, kind: str | None = None,
                     runtime: Runtime = Depends(rt)):
    """Activity feed. `since` enables incremental polling without duplicates."""
    u = uid(runtime, user_id)
    bus = runtime.cognition.bus
    if since is not None:
        events = bus.since(u, since, limit=min(limit, 500))
    else:
        types = [t for t in EVENT_TYPES if t.startswith(f"{kind}.")] if kind else None
        events = bus.recent(u, limit=min(limit, 500), types=types)
    return {"events": [e.as_dict() for e in events],
            "counts": bus.counts(u),
            "latest_id": max((e.id for e in events), default=since or 0)}


@app.get("/api/cognition/turn/{correlation_id}")
def cognition_turn(correlation_id: str, runtime: Runtime = Depends(rt)):
    """Replay every event emitted during one conversational turn."""
    events = runtime.cognition.bus.for_correlation(correlation_id)
    if not events:
        raise HTTPException(status_code=404, detail="Unknown correlation id.")
    return {"correlation_id": correlation_id,
            "events": [e.as_dict() for e in events], "count": len(events)}


@app.get("/api/cognition/why")
def cognition_why(subject_kind: str, subject_id: str, user_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    """'Why do you think that?' for any object in the system."""
    return runtime.cognition.why(uid(runtime, user_id), subject_kind, subject_id)


@app.get("/api/cognition/changes")
def cognition_changes(user_id: str | None = None, since: int | None = None,
                      runtime: Runtime = Depends(rt)):
    return runtime.cognition.what_changed(uid(runtime, user_id), since)


@app.get("/api/cognition/resume")
def cognition_resume(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Returning-user briefing built from real recorded state."""
    return runtime.cognition.continuity.resume(uid(runtime, user_id))


@app.get("/api/cognition/self")
def cognition_self(runtime: Runtime = Depends(rt)):
    """Honest capability report, including what is NOT CONFIGURED."""
    return runtime.cognition.self_model.health_summary()


# ---------------------------------------------------------------- world model
@app.get("/api/world")
def world(user_id: str | None = None, kind: str | None = None,
          state: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    w = runtime.cognition.world
    return {"entities": w.list(u, kind=kind, state=state), "summary": w.summary(u),
            "due_soon": w.due_soon(u)}


@app.get("/api/world/graph")
def world_graph(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return runtime.cognition.world.graph(uid(runtime, user_id))


# ------------------------------------------------------------------- intents
@app.get("/api/intents")
def intents(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    return {"current": runtime.cognition.intent.current(u),
            "history": runtime.cognition.intent.list(u)}


# --------------------------------------------------------------- predictions
@app.get("/api/predictions")
def predictions(user_id: str | None = None, status: str | None = None,
                runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    return {"predictions": runtime.cognition.predictions.list(u, status=status),
            "accuracy": runtime.cognition.predictions.accuracy(u)}


@app.post("/api/predictions/{prediction_id}/resolve")
def resolve_prediction(prediction_id: str, body: PredictionResolveRequest,
                       runtime: Runtime = Depends(rt)):
    u = uid(runtime, body.user_id)
    try:
        result = runtime.cognition.predictions.evaluate(
            u, prediction_id, body.correct, body.note)
        if result is None:
            raise HTTPException(status_code=404, detail="Unknown prediction.")
        return result
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown prediction.")


# ----------------------------------------------------------------- causality
@app.get("/api/causality/{node_kind}/{node_id}")
def causality(node_kind: str, node_id: str, user_id: str | None = None,
              runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    graph = runtime.cognition.causal
    return {"node": {"kind": node_kind, "id": node_id},
            "downstream": graph.downstream(node_kind, node_id),
            "upstream": graph.upstream(node_kind, node_id),
            "chain": graph.chain(node_kind, node_id),
            "impact": (graph.impact(u, node_id) if node_kind == "memory"
                       else {"detail": "Impact is only measured for memories."})}


@app.get("/api/decisions")
def decisions(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return {"decisions": runtime.cognition.decisions.list(uid(runtime, user_id))}


@app.post("/api/decisions")
def record_decision(body: DecisionRequest, runtime: Runtime = Depends(rt)):
    return runtime.cognition.decisions.record(
        uid(runtime, body.user_id), body.question, body.chosen,
        alternatives=body.alternatives, expectation=body.expectation,
        influenced_by=body.influenced_by)


# ------------------------------------------------------- memory reputation
@app.get("/api/memories/{memory_id}/reputation")
def memory_reputation(memory_id: str, user_id: str | None = None,
                      runtime: Runtime = Depends(rt)):
    """Reputation is evidence-based and separate from confidence."""
    return runtime.cognition.reputation.get(uid(runtime, user_id), memory_id)


@app.post("/api/memories/outcome")
def memory_outcome(body: OutcomeRequest, runtime: Runtime = Depends(rt)):
    """Attribute a real outcome to a memory - this is how reputation is earned."""
    return runtime.cognition.reputation.record_outcome(
        uid(runtime, body.user_id), body.memory_id, body.positive)


# ------------------------------------------------------------------ autonomy
@app.get("/api/autonomy")
def autonomy(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    return {"level": runtime.cognition.autonomy.level(u),
            "levels": list(LEVELS),
            "trust": runtime.cognition.trust.all(u),
            "interventions": runtime.cognition.attention.recent(u, limit=25),
            "precision": runtime.cognition.attention.precision(u)}


@app.post("/api/autonomy")
def set_autonomy(body: AutonomyRequest, runtime: Runtime = Depends(rt)):
    u = uid(runtime, body.user_id)
    level = runtime.cognition.autonomy.set_level(u, body.level, body.reason)
    return {"level": level, "trust": runtime.cognition.trust.all(u)}


# ------------------------------------------------------------------ learning
@app.get("/api/learning")
def learning(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    le = runtime.cognition.learning
    return {"policies": le.policies(u), "self_evaluation": le.self_evaluation(u)}


@app.post("/api/learning/consolidate")
def consolidate_learning(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Background pattern mining over the real event log."""
    return runtime.cognition.learning.consolidate(uid(runtime, user_id))


# ------------------------------------------------------------------- sandbox
@app.post("/api/sandbox")
def sandbox(body: SandboxRequest, runtime: Runtime = Depends(rt)):
    """Counterfactual projection. Never mutates real state."""
    return runtime.cognition.sandbox.simulate(uid(runtime, body.user_id), body.question)


@app.get("/api/sandbox")
def sandbox_history(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return {"runs": runtime.cognition.sandbox.history(uid(runtime, user_id))}


# --------------------------------------------------------------- v8.1 routes
@app.get("/api/provider")
def provider_status(runtime: Runtime = Depends(rt)):
    """Active provider, model and mode - the UI reads this to label REAL vs DEMO."""
    status = runtime.provider.status()
    return {"provider": status.as_dict(),
            "extraction": runtime.cognition.extractor.status(),
            "perception": runtime.cognition.perception.capabilities(),
            # v8.2: what the model can actually do, and how each task will run.
            "capabilities": runtime.cognition.router.report().as_dict(),
            "routing": runtime.cognition.router.routing_table()}


# ======================================================================
#                       V8.2 COGNITIVE CORE ROUTES
# ======================================================================

@app.get("/api/capabilities")
def capabilities(runtime: Runtime = Depends(rt)):
    """
    Honest capability report for the active provider and model.

    Capabilities are SUPPORTED / NOT_SUPPORTED / UNKNOWN. UNKNOWN is never
    treated as available.
    """
    return {"capabilities": runtime.cognition.router.report().as_dict(),
            "routing": runtime.cognition.router.routing_table()}


@app.get("/api/capabilities/route")
def capability_route(task: str | None = None, runtime: Runtime = Depends(rt)):
    """
    How tasks would execute right now, and why.

    Without `task` this returns the full routing table, so the Observatory can
    show every task class and its honest mode in one call.
    """
    router = runtime.cognition.router
    if task is None:
        return {"routes": router.routing_table()}
    try:
        return router.route(task).as_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ execution
@app.get("/api/execution/{correlation_id}")
def execution_trace(correlation_id: str, runtime: Runtime = Depends(rt)):
    """
    The real execution trace for one turn: MODEL_CALL, TOOL_DECISION,
    TOOL_RESULT, MODEL_REVISION, FINAL_RESPONSE.
    """
    steps = runtime.cognition.traces.for_correlation(correlation_id)
    if not steps:
        raise HTTPException(status_code=404,
                            detail="No execution trace for that correlation id.")
    return {"correlation_id": correlation_id, "steps": steps,
            "count": len(steps)}


@app.get("/api/execution")
def recent_executions(user_id: str | None = None, limit: int = 20,
                      runtime: Runtime = Depends(rt)):
    return {"traces": runtime.cognition.traces.recent(
        uid(runtime, user_id), limit=min(limit, 100))}


# ---------------------------------------------------------------- arbitration
@app.get("/api/arbitration")
def arbitrations(user_id: str | None = None, limit: int = 20,
                 runtime: Runtime = Depends(rt)):
    return {"records": runtime.cognition.arbiter_v2.recent(
        uid(runtime, user_id), limit=min(limit, 100))}


@app.get("/api/arbitration/{arbitration_id}")
def arbitration_detail(arbitration_id: str, runtime: Runtime = Depends(rt)):
    record = runtime.cognition.arbiter_v2.get(arbitration_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown arbitration record.")
    return record


# ------------------------------------------------------------------ influence
@app.get("/api/influence")
def influences(user_id: str | None = None, pending: bool = False,
               limit: int = 50, runtime: Runtime = Depends(rt)):
    """Recorded memory influences. `pending=true` returns unresolved ones."""
    u = uid(runtime, user_id)
    ledger = runtime.cognition.influence
    items = (ledger.pending(u, limit=min(limit, 200)) if pending
             else ledger.recent(u, limit=min(limit, 200)))
    return {"influences": items, "count": len(items)}


@app.post("/api/influence/{influence_id}/outcome")
def influence_outcome(influence_id: str, body: InfluenceOutcomeRequest,
                      runtime: Runtime = Depends(rt)):
    """
    Attach an OBSERVED outcome to a memory influence.

    Reputation only moves for SUPPORTED / CONTRADICTED backed by evidence.
    """
    try:
        return runtime.cognition.influence.record_outcome(
            uid(runtime, body.user_id), influence_id, verdict=body.verdict,
            detail=body.detail, evidence=body.evidence)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown influence id.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/memories/{memory_id}/impact")
def memory_impact(memory_id: str, user_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    """The causal story of one memory, as far as evidence allows."""
    return runtime.cognition.influence.impact(uid(runtime, user_id), memory_id)


# --------------------------------------------------------------------- policy
@app.get("/api/cognitive-policy")
def cognitive_policy(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Behavioural policy with confidence, evidence and reasons."""
    u = uid(runtime, user_id)
    engine = runtime.cognition.policy
    return {"policies": engine.list(u), "effective": engine.effective(u),
            "learned": engine.learned(u)}


@app.get("/api/cognitive-policy/{key}")
def cognitive_policy_explain(key: str, user_id: str | None = None,
                             runtime: Runtime = Depends(rt)):
    try:
        return runtime.cognition.policy.explain(uid(runtime, user_id), key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/api/cognitive-policy/{key}")
def cognitive_policy_revert(key: str, user_id: str | None = None,
                            runtime: Runtime = Depends(rt)):
    """'Stop doing that' — revert a learned policy to its default."""
    try:
        return runtime.cognition.policy.revert(uid(runtime, user_id), key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------------------------------------------------------------- trust
@app.get("/api/trust")
def capability_trust(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Per-capability reliability. No single global trust score."""
    u = uid(runtime, user_id)
    return {"capabilities": runtime.cognition.capability_trust.all(u)}


# --------------------------------------------------------------------- intent
@app.get("/api/intents/transitions")
def intent_transitions(user_id: str | None = None, intent_id: str | None = None,
                       limit: int = 50, runtime: Runtime = Depends(rt)):
    return {"transitions": runtime.cognition.intent_evolution.transitions(
        uid(runtime, user_id), intent_id, limit=min(limit, 200))}


@app.get("/api/intents/{intent_id}/why")
def intent_why(intent_id: str, user_id: str | None = None,
               runtime: Runtime = Depends(rt)):
    """'Why did this intent change?' from the transition ledger."""
    return runtime.cognition.intent_evolution.explain(
        uid(runtime, user_id), intent_id)


# ---------------------------------------------------------------------- needs
@app.get("/api/needs")
def needs(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    rows = runtime.db.query(
        "SELECT id, need, confidence, signals, source, utterance, was_correct,"
        " created_at FROM need_hypotheses WHERE user_id=? ORDER BY id DESC"
        " LIMIT 50", (u,))
    hypotheses = []
    for row in rows:
        entry = dict(row)
        try:
            entry["signals"] = json.loads(entry.get("signals") or "[]")
        except (json.JSONDecodeError, TypeError):
            entry["signals"] = []
        # was_correct stays None when no feedback exists - that is honest,
        # not a default of "correct".
        entry["evaluated"] = entry["was_correct"] is not None
        hypotheses.append(entry)
    return {"recent": runtime.cognition.needs.recent(u, limit=10),
            "hypotheses": hypotheses,
            "accuracy": runtime.cognition.needs.accuracy(u)}


@app.post("/api/needs/{hypothesis_id}/evaluate")
def evaluate_need(hypothesis_id: str, body: NeedEvaluationRequest,
                  runtime: Runtime = Depends(rt)):
    result = runtime.cognition.needs.evaluate(
        uid(runtime, body.user_id), hypothesis_id, body.correct)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown need hypothesis.")
    return result


# ----------------------------------------------------------------- continuity
@app.get("/api/continuity")
def continuity(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Open items worth carrying forward, each with a stated reason."""
    u = uid(runtime, user_id)
    engine = runtime.cognition.continuity
    engine.refresh(u)
    return {"items": engine.open_items(u), "reasons": CONTINUITY_REASONS}


@app.post("/api/continuity/{item_id}/close")
def close_continuity(item_id: str, user_id: str | None = None,
                     runtime: Runtime = Depends(rt)):
    result = runtime.cognition.continuity.close(uid(runtime, user_id), item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Unknown continuity item.")
    return result


# ---------------------------------------------------------------------- focus
@app.post("/api/focus")
def set_focus(body: FocusRequest, runtime: Runtime = Depends(rt)):
    """Record which object the user has open, for 'that memory' resolution."""
    try:
        return runtime.cognition.focus.set_focus(
            uid(runtime, body.user_id), body.subject_kind, body.subject_id,
            session_id=body.session_id, label=body.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/focus")
def get_focus(user_id: str | None = None, session_id: str = "default",
              runtime: Runtime = Depends(rt)):
    return {"focus": runtime.cognition.focus.current(
        uid(runtime, user_id), session_id=session_id)}


@app.delete("/api/focus")
def clear_focus(user_id: str | None = None, session_id: str = "default",
                runtime: Runtime = Depends(rt)):
    return {"cleared": runtime.cognition.focus.clear(
        uid(runtime, user_id), session_id=session_id)}


# --------------------------------------------------------------- explanations
@app.get("/api/cognition/why-now")
def cognition_why_now(subject_kind: str, subject_id: str,
                      user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """'Why now?' — what made this relevant at this moment."""
    return runtime.cognition.why_now(uid(runtime, user_id), subject_kind,
                                     subject_id)


@app.get("/api/memories/{memory_id}/why-used")
def memory_why_used(memory_id: str, user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    """'Why did you use this memory?' — arbitration + influence evidence."""
    return runtime.cognition.explain_memory_use(uid(runtime, user_id), memory_id)


# ----------------------------------------------------------------- prediction
@app.post("/api/predictions/{prediction_id}/observe")
def observe_prediction(prediction_id: str, body: PredictionObservationRequest,
                       runtime: Runtime = Depends(rt)):
    """
    Evaluate a prediction against an OBSERVED reality.

    Refuses to score the prediction when the observation does not actually
    bear on it — see docs/V8.2.md.
    """
    try:
        result = runtime.cognition.predictions.observe(
            uid(runtime, body.user_id), prediction_id, body.observation,
            supports=body.supports, evidence=body.evidence)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown prediction.")
    if result is None:
        raise HTTPException(status_code=404,
                            detail="Unknown or already-resolved prediction.")
    return result


@app.get("/api/memory-health")
def memory_health(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Evidence-based diagnosis of the memory store. Read-only."""
    return runtime.cognition.health.report(uid(runtime, user_id))


@app.post("/api/memory-health/apply")
def memory_health_apply(payload: dict = Body(...), user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    """
    Apply one health remedy. Non-destructive: the strongest action is a soft
    RETIRE that preserves the row and its version history.
    """
    from .cognition.health import Finding, REMEDIES

    user = uid(runtime, user_id)
    if (payload.get("remedy") or "").upper() not in REMEDIES:
        raise HTTPException(400, f"remedy must be one of {list(REMEDIES)}")
    try:
        finding = Finding(
            memory_id=str(payload["memory_id"]), issue=str(payload.get("issue", "")),
            remedy=str(payload["remedy"]).upper(),
            confidence=float(payload.get("confidence", 0.5)),
            evidence=str(payload.get("evidence", "")),
            related_id=payload.get("related_id"))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid finding: {exc}") from exc

    result = runtime.cognition.health.apply(user, finding)
    if not result.get("applied"):
        raise HTTPException(409, result.get("reason", "Could not apply remedy."))
    return result


@app.post("/api/perceive")
async def perceive(file: UploadFile = File(...), user_id: str | None = None,
                   runtime: Runtime = Depends(rt)):
    """
    Ingest a file as one cognitive perception.

    Images and unsupported formats are accepted but honestly reported as
    METADATA ONLY / NOT AVAILABLE rather than silently pretended-to-be-read.
    """
    user = uid(runtime, user_id)
    data = await file.read()
    perception = runtime.cognition.perception.ingest_file(
        user, file.filename or "upload", data)
    return perception.as_dict()


# --------------------------------------------------------- §19 user control
@app.post("/api/control")
def cognitive_control(body: ControlRequest, runtime: Runtime = Depends(rt)):
    """
    Execute a natural-language cognitive command ("forget that", "why do you
    believe that?", "that's wrong", "stop asking me about X").

    Returns 422 when the message contains no recognisable command — ordinary
    conversation must go through /api/chat, not here.
    """
    user = uid(runtime, body.user_id)
    result = runtime.cognition.control.handle(
        user, body.message, session_id=body.session_id)
    if result is None:
        raise HTTPException(
            422, "No explicit cognitive command was recognised in that message.")
    return result


@app.get("/api/control/commands")
def cognitive_commands():
    """The commands the system can actually act on, with real examples."""
    return {
        "commands": [
            {"command": "FORGET", "examples": ["forget that",
                                               "delete what I said about Redis"],
             "effect": "Deletes the referenced memory after resolving it."},
            {"command": "CORRECT", "examples": ["that's wrong",
                                                "actually it's Postgres"],
             "effect": "Records a contradiction and replaces the content."},
            {"command": "REMEMBER", "examples": ["remember that I deploy on "
                                                 "Tuesdays"],
             "effect": "Stores an explicit, high-authority memory."},
            {"command": "EXPLAIN_BELIEF", "examples": ["why do you believe that?",
                                                       "where did you get that?"],
             "effect": "Reports source, confidence, reputation and impact."},
            {"command": "EXPLAIN_BEHAVIOUR",
             "examples": ["why do you always ask so many questions?"],
             "effect": "Explains a learned policy from its stored evidence."},
            {"command": "STOP_TOPIC", "examples": ["stop asking me about the "
                                                   "migration"],
             "effect": "Records an explicit instruction that overrides "
                       "inferred behaviour."},
            {"command": "PRIORITISE", "examples": ["this is important"],
             "effect": "Marks the focused memory as important."},
        ],
        "note": ("Commands only fire on unambiguous phrasing. If the target "
                 "cannot be resolved the system asks rather than guessing."),
    }
