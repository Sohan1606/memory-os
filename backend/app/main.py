"""MEMORY//OS FastAPI application."""
from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .cognition.autonomy import LEVELS
from .cognition.events import EVENT_TYPES
from .config import settings
from .runtime import Runtime, get_runtime
from .cognition.continuity import REASONS as CONTINUITY_REASONS
from .schemas.api import (AutonomyRequest, ChatRequest, ChatResponse,
                          SurfaceTurnRequest, ConsolidateRequest, ControlRequest, DecisionRequest,
                          FocusRequest,
                          ImportRequest, InfluenceOutcomeRequest,
                          MemoryCreateRequest, MemoryUpdateRequest,
                          NeedEvaluationRequest, OutcomeRequest,
                          PredictionObservationRequest, PredictionResolveRequest,
                          SandboxRequest, SearchRequest)
from .schemas.api import (AttentionReactionRequest, AttentionRequest,
                          BackgroundControlRequest, BackgroundRunRequest,
                          MissionCreateRequest, MissionStateRequest,
                          MissionStepRequest, ObservationRequest,
                          OutcomeObservationRequest, ResearchRequest,
                          SimulationCommitRequest, SimulationRequest,
                          WorldReconcileRequest)
from .schemas.api import (DecisionOutcomeRequest, ExperienceCreateRequest,
                          ExperienceLifecycleRequest, KnowledgeCorrectionRequest,
                          KnowledgeOutcomeRequest, KnowledgeRetrievalRequest,
                          KnowledgeUseRequest, PrincipleCandidateRequest,
                          SkillCandidateRequest, ExplanationQueryRequest)
from .schemas.api import (ResearchFetchRequest, ResearchSessionCreateRequest,
                          ResearchWorldApplyRequest, ResearchWorldProposeRequest)
from .schemas.portability import (ExportCreateRequest, RestoreApplyRequest,
                                  RestoreDryRunRequest)
from .schemas.security import (LoginRequest, RegisterRequest, RoleChangeRequest,
                               UserCreateRequest, NamespaceMigrationRequest)
from .schemas.semantic import (CognitiveObjectCreate, CognitiveObjectUpdate,
                               MeaningCompileRequest, RelationshipCreate)
from .schemas.v10 import (MaintenanceAuditRequest, ModelErrorCreate,
                          V10ActionRequest, ProposalActionRequest,
                          ContradictionClass)
from .security.authz import P_COGNITION_READ, P_COGNITION_WRITE
from .security.context import (current_principal, current_request_id,
                               get_principal, get_request_id)
from .security.errors import (E_CSRF, E_FORBIDDEN, E_RATE_LIMITED,
                              E_UNAUTHENTICATED, error_body)
from .security.identity import AuthError, AuthorizationError
from .security.observability import redact
from .security.principal import Principal

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(
    title="MEMORY//OS API",
    description="Local-first AI agent with long-term memory. LangGraph + LangChain "
                "+ ChromaDB + local embeddings + SQLite.",
    version="8.5",
)

origins = ["*"] if settings.cors_origins.strip() == "*" else [
    o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=origins,
    # Credentials (session cookie) may only cross origins that are explicitly
    # allow-listed; the wildcard demo keeps the established V8.4.4 behavior.
    allow_credentials=settings.cors_origins.strip() != "*",
    allow_methods=["*"], allow_headers=["*"],
)


def rt() -> Runtime:
    return get_runtime()


# ------------------------------------------------------------ V8.5 identity
# Routes reachable WITHOUT a session when AUTH_MODE=required. Everything else
# under /api requires an authenticated principal.
PUBLIC_ROUTES = {
    "/api/health", "/api/health/live", "/api/health/ready",
    "/api/auth/login", "/api/auth/register", "/api/auth/session",
}

# Mutating methods that require the CSRF header when the request was
# authenticated via the session COOKIE (bearer-token calls are CSRF-immune).
_CSRF_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Rate-limit categories per path prefix (most specific first).
_RATE_CATEGORIES = (
    ("/api/auth/", "auth"),
    ("/api/research", "research"),
    ("/api/portability/", "portability"),
    ("/api/chat", "expensive"),
    ("/api/sandbox", "expensive"),
    ("/api/perceive", "expensive"),
)


def _rate_category(path: str) -> str:
    for prefix, category in _RATE_CATEGORIES:
        if path.startswith(prefix):
            return category
    return "api"


def _extract_token(request: Request, cookie_name: str) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.cookies.get(cookie_name) or None


def _client_key(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _active_runtime() -> Runtime:
    """The runtime the ROUTES will use. Tests override the `rt` dependency;
    the middleware must honour the same override or it would construct (and
    enforce against) a different Runtime than the handlers."""
    override = app.dependency_overrides.get(rt)
    return override() if override else get_runtime()


@app.middleware("http")
async def production_trust_middleware(request: Request, call_next):
    """One enforcement point: correlation id, authentication, CSRF,
    rate limiting, body-size bounds, latency metrics and safe errors."""
    runtime = _active_runtime()
    cfg = runtime.settings
    request_id = request.headers.get("x-request-id", "").strip()[:64] \
        or f"req_{uuid.uuid4().hex[:16]}"
    rid_token = current_request_id.set(request_id)
    principal_token = None
    started = time.monotonic()
    path = request.url.path
    status_code = 500
    try:
        if not path.startswith("/api"):
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response

        # ---- body size bound (Content-Length; declared size is enforced
        # again by the portability layer's own byte-accurate caps).
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cfg.max_request_bytes:
            runtime.metrics.inc("security.oversized_requests")
            status_code = 413
            return JSONResponse(status_code=413, content=error_body(
                413, None, request_id))

        # ---- authentication
        principal: Principal | None = None
        if cfg.auth_mode == "required":
            token = _extract_token(request, cfg.session_cookie_name)
            if token:
                try:
                    principal = runtime.identity.verify_session(token)
                except AuthError:
                    principal = None
            if principal is None and path not in PUBLIC_ROUTES:
                runtime.metrics.inc("security.unauthenticated_requests")
                status_code = 401
                return JSONResponse(status_code=401, content=error_body(
                    401, None, request_id, code=E_UNAUTHENTICATED))
            # ---- CSRF: cookie-authenticated mutations need the header.
            # PUBLIC_ROUTES (login/register) are exempt: the login page cannot
            # hold a CSRF token before a session exists, and those routes are
            # rate-limited per client instead.
            if (principal is not None and path not in PUBLIC_ROUTES
                    and request.method in _CSRF_METHODS
                    and not request.headers.get("authorization", "").lower().startswith("bearer ")
                    and request.cookies.get(cfg.session_cookie_name)):
                sent = request.headers.get("x-csrf-token", "")
                if not sent or not hmac.compare_digest(
                        sent, principal.csrf_token or ""):
                    runtime.metrics.inc("security.csrf_rejected")
                    try:
                        runtime.cognition.bus.emit(
                            principal.namespace, "security.suspicious_request",
                            "Rejected a mutation without a valid CSRF token.",
                            subject_kind="request", subject_id=request_id,
                            payload={"path": path, "method": request.method})
                    except Exception:  # pragma: no cover
                        pass
                    status_code = 403
                    return JSONResponse(status_code=403, content=error_body(
                        403, "The request failed CSRF validation.", request_id,
                        code=E_CSRF))
        else:
            # V8.4.4-compatible local mode: one anonymous local principal.
            principal = Principal.local(cfg.demo_user_id)

        principal_token = current_principal.set(principal)

        # ---- rate limiting (per principal; per client for anonymous auth).
        category = _rate_category(path)
        limiter_key = principal.user_id if principal else _client_key(request)
        if category == "auth":
            limiter_key = _client_key(request)
        allowed, limit, remaining = runtime.rate_limiter.check(limiter_key, category)
        if not allowed:
            runtime.metrics.inc("security.rate_limited_total")
            try:
                runtime.cognition.bus.emit(
                    principal.namespace if principal else cfg.demo_user_id,
                    "security.rate_limited",
                    "Rejected a request that exceeded the rate limit.",
                    subject_kind="request", subject_id=request_id,
                    payload={"path": path, "category": category, "limit": limit})
            except Exception:  # pragma: no cover
                pass
            status_code = 429
            response = JSONResponse(status_code=429, content=error_body(
                429, None, request_id, code=E_RATE_LIMITED))
            response.headers["Retry-After"] = "60"
            response.headers["X-RateLimit-Limit"] = str(limit)
            return response

        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        # Production-safe 500: full detail (redacted) to the log, stable
        # envelope with the correlation id to the client. Never a stack trace.
        log.exception("Unhandled error for %s %s [%s]",
                      request.method, path, request_id)
        runtime.metrics.inc("http.unhandled_exceptions")
        status_code = 500
        return JSONResponse(status_code=500, content=error_body(500, None, request_id))
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        if path.startswith("/api"):
            route = request.scope.get("route")
            route_path = getattr(route, "path", path)
            try:
                runtime.metrics.observe_request(route_path, request.method,
                                                status_code, elapsed_ms)
            except Exception:  # pragma: no cover - metrics never break requests
                pass
        if principal_token is not None:
            current_principal.reset(principal_token)
        current_request_id.reset(rid_token)


def principal_or_401() -> Principal:
    p = get_principal()
    if p is None:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    return p


def require_permission(permission: str, runtime: Runtime) -> Principal:
    p = principal_or_401()
    if not p.can(permission):
        runtime.metrics.inc("security.authorization_denied_total")
        try:
            runtime.cognition.bus.emit(
                p.namespace, "authorization.denied",
                "Denied a request lacking the required permission.",
                subject_kind="request", subject_id=get_request_id() or "unknown",
                payload={"permission": permission, "role": p.role})
        except Exception:  # pragma: no cover
            pass
        raise HTTPException(status_code=403,
                            detail="You do not have permission to do that.")
    return p


def uid(runtime: Runtime, provided: str | None) -> str:
    """Resolve the namespace for this request.

    V8.5 rule: when authentication is REQUIRED the namespace comes ONLY from
    the verified session — any caller-supplied user_id is ignored, so no ID
    substitution (IDOR) is possible at any /api surface or tool. In disabled
    mode the established V8.4.4 behavior (optional explicit user_id, demo
    default) is preserved exactly.
    """
    principal = get_principal()
    if runtime.settings.auth_mode == "required":
        if principal is None:
            raise HTTPException(status_code=401, detail="Authentication is required.")
        return principal.namespace
    return (provided or runtime.settings.demo_user_id).strip()[:100]


def ensure_owner(runtime: Runtime, owner_namespace: str | None) -> None:
    """Object-level ownership check for routes addressed by bare object id.

    In `required` mode a mismatch is indistinguishable from absence (404), so
    guessed or substituted IDs leak neither data nor existence. In `disabled`
    mode the established single-user V8.4.4 behavior is preserved unchanged.
    """
    if runtime.settings.auth_mode != "required":
        return
    principal = principal_or_401()
    if owner_namespace != principal.namespace:
        runtime.metrics.inc("security.idor_blocked")
        try:
            runtime.cognition.bus.emit(
                principal.namespace, "authorization.denied",
                "Blocked access to an object owned by another user.",
                subject_kind="request", subject_id=get_request_id() or "unknown",
                payload={"reason": "ownership_mismatch"})
        except Exception:  # pragma: no cover
            pass
        raise HTTPException(status_code=404, detail="Object not found.")


@app.exception_handler(HTTPException)
async def http_exception_handler(_request, exc: HTTPException):
    """Wrap every HTTPException in the stable V8.5 envelope. `detail` is
    preserved verbatim so all established V8.4.4 clients keep working."""
    detail = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    return JSONResponse(status_code=exc.status_code,
                        content=error_body(exc.status_code, redact(detail),
                                           get_request_id()),
                        headers=getattr(exc, "headers", None))


@app.exception_handler(ValueError)
async def value_error_handler(_request, exc: ValueError):
    return JSONResponse(status_code=400, content=error_body(
        400, redact(str(exc)), get_request_id()))


@app.exception_handler(KeyError)
async def key_error_handler(_request, _exc: KeyError):
    return JSONResponse(status_code=404, content=error_body(
        404, "Object not found.", get_request_id()))


@app.exception_handler(AuthError)
async def auth_error_handler(_request, exc: AuthError):
    return JSONResponse(status_code=401, content=error_body(
        401, str(exc), get_request_id(), code=E_UNAUTHENTICATED))


@app.exception_handler(AuthorizationError)
async def authz_error_handler(_request, exc: AuthorizationError):
    return JSONResponse(status_code=403, content=error_body(
        403, str(exc), get_request_id(), code=E_FORBIDDEN))


# ---------------------------------------------------------------- diagnostics
@app.get("/api/health")
def health(runtime: Runtime = Depends(rt)) -> dict[str, Any]:
    return runtime.health()


@app.get("/api/health/live")
def health_live() -> dict[str, Any]:
    """Liveness ONLY: the process is up and serving. Says nothing about
    dependencies — that is what /api/health/ready is for."""
    return {"status": "alive"}


@app.get("/api/health/ready")
def health_ready(runtime: Runtime = Depends(rt)):
    """Readiness + per-dependency truth (ACTIVE / DEGRADED / NOT_CONFIGURED /
    BLOCKED / FAILED). Returns 503 when a REQUIRED dependency has failed."""
    report = runtime.readiness()
    status = 200 if report["status"] == "ready" else 503
    return JSONResponse(status_code=status, content=report)


@app.get("/api/metrics")
def metrics_endpoint(runtime: Runtime = Depends(rt)):
    """Operational metrics. Counts and latencies only — labels are route
    templates and coarse categories, never cognitive content."""
    require_permission("health.read", runtime)
    return runtime.metrics.snapshot()


# -------------------------------------------------------------- V8.5 auth
def _session_cookie(response: Response, token: str, cfg) -> None:
    response.set_cookie(
        cfg.session_cookie_name, token,
        max_age=int(cfg.session_ttl_hours * 3600),
        httponly=True, samesite="lax", secure=cfg.cookie_secure,
        path="/")


@app.post("/api/auth/register", status_code=201)
def auth_register(req: RegisterRequest, runtime: Runtime = Depends(rt)):
    if runtime.settings.auth_mode != "required":
        raise HTTPException(status_code=404, detail="Authentication is not enabled.")
    try:
        user = runtime.identity.register(
            email=req.email, password=req.password, display_name=req.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"user": user}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, runtime: Runtime = Depends(rt)):
    if runtime.settings.auth_mode != "required":
        raise HTTPException(status_code=404, detail="Authentication is not enabled.")
    token, principal = runtime.identity.login(
        email=req.email, password=req.password,
        client=request.headers.get("user-agent", "")[:200])
    body = {"user": principal.as_dict(), "csrf_token": principal.csrf_token,
            "token": token, "token_type": "bearer",
            "expires_in_hours": runtime.settings.session_ttl_hours}
    response = JSONResponse(content=body)
    _session_cookie(response, token, runtime.settings)
    return response


@app.post("/api/auth/logout")
def auth_logout(runtime: Runtime = Depends(rt)):
    if runtime.settings.auth_mode != "required":
        raise HTTPException(status_code=404, detail="Authentication is not enabled.")
    principal = principal_or_401()
    runtime.identity.logout(principal)
    response = JSONResponse(content={"logged_out": True})
    response.delete_cookie(runtime.settings.session_cookie_name, path="/")
    return response


@app.get("/api/auth/session")
def auth_session(runtime: Runtime = Depends(rt)):
    """The authenticated principal for this request (Observatory surface)."""
    principal = get_principal()
    if runtime.settings.auth_mode != "required":
        return {"auth_mode": "disabled",
                "user": Principal.local(runtime.settings.demo_user_id).as_dict(),
                "note": "Local single-user mode. Authentication is not enabled."}
    if principal is None:
        return {"auth_mode": "required", "user": None}
    return {"auth_mode": "required", "user": principal.as_dict(),
            "session_id": principal.session_id}


@app.get("/api/auth/sessions")
def auth_sessions(runtime: Runtime = Depends(rt)):
    """The caller's OWN sessions (id, lifecycle, client). Never tokens."""
    principal = principal_or_401()
    if runtime.settings.auth_mode != "required":
        return {"sessions": []}
    return {"sessions": runtime.identity.list_sessions(principal.user_id)}


@app.post("/api/auth/sessions/{session_id}/revoke")
def auth_revoke_session(session_id: str, runtime: Runtime = Depends(rt)):
    principal = principal_or_401()
    try:
        runtime.identity.revoke_session(principal, session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"revoked": session_id}


# ------------------------------------------------------------- V8.5 admin
@app.get("/api/admin/users")
def admin_list_users(runtime: Runtime = Depends(rt)):
    principal = require_permission("users.manage", runtime)
    return {"users": runtime.identity.list_users(principal.tenant_id)}


@app.post("/api/admin/users", status_code=201)
def admin_create_user(req: UserCreateRequest, runtime: Runtime = Depends(rt)):
    principal = require_permission("users.manage", runtime)
    if req.role == "owner" or (req.role == "admin" and not principal.can("roles.manage")):
        raise HTTPException(status_code=403,
                            detail="You do not have permission to grant that role.")
    try:
        user = runtime.identity.create_user(
            email=req.email, password=req.password, display_name=req.display_name,
            tenant_id=principal.tenant_id, role=req.role, actor=principal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"user": user}


@app.post("/api/admin/users/{user_id}/disable")
def admin_disable_user(user_id: str, runtime: Runtime = Depends(rt)):
    principal = require_permission("users.manage", runtime)
    try:
        return {"user": runtime.identity.disable_user(principal, user_id)}
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/users/{user_id}/role")
def admin_change_role(user_id: str, req: RoleChangeRequest,
                      runtime: Runtime = Depends(rt)):
    principal = require_permission("roles.manage", runtime)
    try:
        return {"user": runtime.identity.change_role(principal, user_id, req.role)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/migrate-legacy-namespace")
def admin_migrate_namespace(req: NamespaceMigrationRequest,
                            runtime: Runtime = Depends(rt)):
    """Deterministic single-user → multi-user migration: the OWNER adopts the
    pre-V8.5 namespace so every V8.4.4 memory, event, world entity, research
    session and portability record becomes theirs. Idempotent."""
    principal = require_permission("roles.manage", runtime)
    legacy = (req.legacy_namespace or runtime.settings.demo_user_id).strip()[:100]
    try:
        return runtime.identity.migrate_legacy_namespace(principal.user_id, legacy)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/admin/security-events")
def admin_security_events(limit: int = 100, runtime: Runtime = Depends(rt)):
    """Security/audit events for the caller's namespace, from the canonical
    EventBus. Admin-scoped; payloads contain identifiers, never secrets."""
    principal = require_permission("security.read", runtime)
    types = ["auth.registered", "auth.login", "auth.logout", "auth.failed",
             "session.revoked", "session.expired", "authorization.denied",
             "permission.changed", "user.created", "user.disabled",
             "export.accessed", "security.rate_limited",
             "security.suspicious_request", "admin.action"]
    events = runtime.cognition.bus.recent(
        principal.namespace, limit=min(max(limit, 1), 500), types=types)
    return {"events": [e.as_dict() for e in events]}


@app.get("/api/admin/rate-limit")
def admin_rate_limit_state(runtime: Runtime = Depends(rt)):
    require_permission("security.read", runtime)
    return runtime.rate_limiter.state()


# --------------------------------------------------------- v9.0.1 live surface
@app.post("/api/v9/surface/turns")
def start_surface_turn(req: SurfaceTurnRequest, runtime: Runtime = Depends(rt)):
    user_id = uid(runtime, req.user_id)
    existing = runtime.db.query_one(
        "SELECT user_id FROM conversations WHERE id=?", (req.thread_id,))
    if existing and existing["user_id"] != user_id:
        raise HTTPException(status_code=409,
                            detail="That conversation id is already in use.")
    correlation_id = f"turn_{uuid.uuid4().hex[:16]}"
    return runtime.cognition.surface_lifecycle.start_turn(
        user_id, req.thread_id, correlation_id)


@app.get("/api/v9/surface/turns/{correlation_id}")
def get_surface_turn(correlation_id: str, user_id: str | None = None,
                     runtime: Runtime = Depends(rt)):
    snapshot = runtime.cognition.surface_lifecycle.snapshot(
        uid(runtime, user_id), correlation_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Cognitive turn not found.")
    return snapshot


# ---------------------------------------------------------------------- chat
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, runtime: Runtime = Depends(rt)) -> ChatResponse:
    user_id = uid(runtime, req.user_id)
    # V8.5: thread ids are client-chosen but conversations are owned. If this
    # thread already belongs to another user, refuse — otherwise the LangGraph
    # checkpoint (keyed by thread id alone) would resume a foreign dialogue.
    existing_thread = runtime.db.query_one(
        "SELECT user_id FROM conversations WHERE id=?", (req.thread_id,))
    if existing_thread and existing_thread["user_id"] != user_id:
        raise HTTPException(status_code=409,
                            detail="That conversation id is already in use.")
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
    if req.correlation_id:
        live = runtime.cognition.surface_lifecycle.snapshot(
            user_id, req.correlation_id)
        if live is None or live["conversation"]["thread_id"] != req.thread_id:
            raise HTTPException(status_code=404, detail="Cognitive turn not found.")
        correlation_id = req.correlation_id
    else:
        correlation_id = f"turn_{uuid.uuid4().hex[:16]}"
        runtime.cognition.surface_lifecycle.start_turn(
            user_id, req.thread_id, correlation_id)
    if req.interaction_mode == "voice":
        runtime.cognition.bus.emit(
            user_id, "voice.session_started", "Started a browser voice turn.",
            subject_kind="conversation", subject_id=req.thread_id,
            correlation_id=correlation_id,
            payload={"transport": "browser_speech_recognition"})
    trace: dict[str, Any] | None = None
    try:
        trace = runtime.cognition.process_turn(
            user_id, req.message, conversation_id=req.thread_id,
            correlation_id=correlation_id)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("Cognitive loop failed for this turn: %s", exc)

    runtime.cognition.surface_lifecycle.transition(
        user_id, req.thread_id, correlation_id, "FORMING_RESPONSE", "ACTIVE")
    try:
        result = runtime.agent.run(user_id, req.thread_id, req.message,
                                   correlation_id=correlation_id)
        runtime.cognition.surface_lifecycle.transition(
            user_id, req.thread_id, correlation_id, "FORMING_RESPONSE", "COMPLETED")
    except Exception as exc:
        runtime.cognition.surface_lifecycle.transition(
            user_id, req.thread_id, correlation_id, "FORMING_RESPONSE", "FAILED",
            detail=f"Response generation failed: {type(exc).__name__}")
        raise
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

    if any("RESEARCH" in str(item.get("type", ""))
           for item in result.get("activity", [])):
        runtime.cognition.surface_lifecycle.transition(
            user_id, req.thread_id, correlation_id,
            "VERIFYING_EXTERNAL_INFORMATION", "COMPLETED")
    runtime.cognition.surface_lifecycle.transition(
        user_id, req.thread_id, correlation_id, "WAITING_FOR_USER", "ACTIVE",
        source_kind="conversation", source_id=req.thread_id)
    surface = runtime.surface.build(
        user_id, thread_id=req.thread_id, correlation_id=correlation_id,
        meaning=(trace or {}).get("meaning"), trace=trace, agent_result=result)
    if req.interaction_mode == "voice":
        runtime.cognition.bus.emit(
            user_id, "voice.session_ended", "Ended a browser voice turn.",
            subject_kind="conversation", subject_id=req.thread_id,
            correlation_id=correlation_id,
            payload={"response_available": True, "speech_output": "browser_optional"})

    return ChatResponse(
        answer=result["answer"], provider=result["provider"],
        recalled=result["recalled"], activity=result["activity"],
        thread_id=req.thread_id,
        correlation_id=trace["correlation_id"] if trace else correlation_id,
        cognition=_summarize_trace(trace) if trace else None,
        surface=surface)


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
        # ----------------------------------------------------------- v10.1
        # Canonical maintenance results for this turn. Every summary line is
        # derived from a persisted finding record — never invented reasoning.
        "maintenance": _summarize_maintenance(trace.get("maintenance")),
        # ----------------------------------------------------------- v10.2
        # Canonical governance results for this turn: signals actually derived,
        # proposals actually created/surfaced, measurements actually recorded.
        "policy_governance": _summarize_governance(trace.get("policy_governance")),
    }


def _summarize_governance(governance: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact, user-safe view of this turn's bounded policy governance."""
    if not governance:
        return None
    inspection = governance.get("inspection") or {}
    return {
        "status": governance.get("status"),
        "relevant": bool((governance.get("relevance") or {}).get("relevant")),
        "active_adaptations_matched": inspection.get("count", 0),
        "consulted": [
            {"adaptation_id": c.get("adaptation_id"), "consumer": c.get("consumer")}
            for c in (inspection.get("consulted") or [])[:5]],
        "signals": [
            {"signal": s.get("signal"), "verdict": s.get("verdict"),
             "target": s.get("target"), "reason": s.get("reason")}
            for s in (governance.get("signals") or [])[:6]],
        "proposals": [
            {"id": p.get("id"), "target": p.get("target"),
             "proposed_value": p.get("proposed_value"), "state": p.get("state"),
             "reason": p.get("reason")}
            for p in (governance.get("proposals") or [])[:5]],
        "measurements": [
            {"adaptation_id": m.get("adaptation_id"), "category": m.get("category")}
            for m in (governance.get("measurements") or [])[:5]],
    }


def _summarize_maintenance(maintenance: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact, user-safe view of this turn's bounded maintenance."""
    if not maintenance:
        return None
    relevance = maintenance.get("relevance") or {}
    return {
        "status": maintenance.get("status"),
        "relevant": bool(relevance.get("relevant")),
        "trigger_kind": relevance.get("trigger_kind"),
        "reasons": (relevance.get("reasons") or [])[:4],
        "findings": [
            {"kind": f.get("kind"), "classification": f.get("classification"),
             "summary": f.get("summary"), "finding_id": f.get("finding_id")}
            for f in (maintenance.get("findings") or [])[:8]],
        "proposals": [
            {"id": p.get("id"), "proposal_type": p.get("proposal_type"),
             "status": p.get("status"), "reason": p.get("reason"),
             "uncertainty": p.get("uncertainty")}
            for p in (maintenance.get("proposals") or [])[:8]],
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
    # V8.5: LangGraph checkpoints are keyed by thread id alone, so they are
    # only returned when this caller owns messages in the thread. Otherwise a
    # guessed thread id would read another user's conversation state.
    owns_thread = bool(rows) or bool(runtime.db.query_one(
        "SELECT 1 FROM conversations WHERE id=? AND user_id=?", (thread_id, u)))
    return {"thread_id": thread_id, "messages": [dict(r) for r in rows],
            "checkpoint_messages": runtime.agent.history(thread_id) if owns_thread else []}


@app.delete("/api/conversations/{thread_id}")
def delete_conversation(thread_id: str, user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    runtime.db.execute("DELETE FROM messages WHERE thread_id=? AND user_id=?", (thread_id, u))
    runtime.db.execute("DELETE FROM conversations WHERE id=? AND user_id=?", (thread_id, u))
    return {"deleted": thread_id}


# --------------------------------------------------------------- v9 semantics
@app.post("/api/v9/meaning/compile")
def compile_meaning(req: MeaningCompileRequest, runtime: Runtime = Depends(rt)):
    user_id = uid(runtime, req.user_id)
    correlation_id = f"meaning_{uuid.uuid4().hex[:12]}"
    return runtime.cognition.meaning.process(
        user_id, req.text, source=req.source, thread_id=req.thread_id,
        correlation_id=correlation_id, persist=req.persist)


@app.get("/api/v9/meaning/compilations")
def meaning_compilations(limit: int = 20, user_id: str | None = None,
                         runtime: Runtime = Depends(rt)):
    return {"compilations": runtime.cognition.meaning.recent(
        uid(runtime, user_id), limit=limit)}


@app.get("/api/v9/meaning/compilations/{compilation_id}")
def meaning_compilation(compilation_id: str, user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    item = runtime.cognition.meaning.compilation(uid(runtime, user_id), compilation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Meaning compilation not found.")
    return item


@app.get("/api/v9/cognitive-objects")
def cognitive_objects(type: str | None = None, status: str | None = None,
                      limit: int = 100, user_id: str | None = None,
                      runtime: Runtime = Depends(rt)):
    return {"objects": runtime.cognition.personal_state.list(
        uid(runtime, user_id), type=type, status=status, limit=limit)}


@app.post("/api/v9/cognitive-objects", status_code=201)
def create_cognitive_object(req: CognitiveObjectCreate,
                            runtime: Runtime = Depends(rt)):
    user_id = uid(runtime, req.user_id)
    return runtime.cognition.personal_state.create(user_id, req)


@app.get("/api/v9/cognitive-objects/{object_id}")
def cognitive_object(object_id: str, user_id: str | None = None,
                     runtime: Runtime = Depends(rt)):
    obj = runtime.cognition.personal_state.get(uid(runtime, user_id), object_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Cognitive object not found.")
    return obj


@app.patch("/api/v9/cognitive-objects/{object_id}")
def update_cognitive_object(object_id: str, req: CognitiveObjectUpdate,
                            user_id: str | None = None,
                            runtime: Runtime = Depends(rt)):
    obj = runtime.cognition.personal_state.update(uid(runtime, user_id), object_id, req)
    if obj is None:
        raise HTTPException(status_code=404, detail="Cognitive object not found.")
    return obj


@app.post("/api/v9/cognitive-objects/{object_id}/retire")
def retire_cognitive_object(object_id: str, body: dict[str, Any] = Body(default={}),
                            user_id: str | None = None,
                            runtime: Runtime = Depends(rt)):
    reason = str(body.get("reason") or "User requested retirement")[:500]
    obj = runtime.cognition.personal_state.retire(
        uid(runtime, user_id), object_id, reason=reason)
    if obj is None:
        raise HTTPException(status_code=404, detail="Cognitive object not found.")
    return obj


@app.post("/api/v9/relationships", status_code=201)
def create_semantic_relationship(req: RelationshipCreate,
                                 runtime: Runtime = Depends(rt)):
    user_id = uid(runtime, req.user_id)
    try:
        return runtime.cognition.personal_state.add_relationship(user_id, req)
    except KeyError:
        raise HTTPException(status_code=404, detail="One or both cognitive objects are unknown.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise HTTPException(status_code=409, detail="Relationship already exists.")
        raise


@app.get("/api/v9/relationships")
def semantic_relationships(object_id: str | None = None,
                           user_id: str | None = None,
                           runtime: Runtime = Depends(rt)):
    return {"relationships": runtime.cognition.personal_state.relationships(
        uid(runtime, user_id), object_id=object_id)}


@app.get("/api/v9/personal-state")
def personal_state(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return runtime.cognition.personal_state.current(uid(runtime, user_id))


@app.get("/api/v9/personal-state/versions/{version}")
def personal_state_version(version: int, user_id: str | None = None,
                           runtime: Runtime = Depends(rt)):
    state = runtime.cognition.personal_state.reconstruct(
        uid(runtime, user_id), version=version)
    if state is None:
        raise HTTPException(status_code=404, detail="Personal state version not found.")
    return state


@app.get("/api/v9/personal-state/reconstruct")
def reconstruct_personal_state(at: str, user_id: str | None = None,
                               runtime: Runtime = Depends(rt)):
    state = runtime.cognition.personal_state.reconstruct(uid(runtime, user_id), at=at)
    if state is None:
        raise HTTPException(status_code=404, detail="No personal state existed at that time.")
    return state


@app.get("/api/v9/personal-state/diff")
def personal_state_diff(from_version: int, to_version: int,
                        user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    diff = runtime.cognition.personal_state.diff(
        uid(runtime, user_id), from_version, to_version)
    if diff is None:
        raise HTTPException(status_code=404, detail="One or both state versions are unknown.")
    return diff


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
    ensure_owner(runtime, mem.user_id)
    return {"memory": mem.to_dict(), "versions": runtime.memory.versions(memory_id),
            "related": [runtime.memory.get(r).to_dict()
                        for r in mem.related_memory_ids if runtime.memory.get(r)]}


@app.patch("/api/memories/{memory_id}")
def update_memory(memory_id: str, req: MemoryUpdateRequest, runtime: Runtime = Depends(rt)):
    existing = runtime.memory.get(memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")
    ensure_owner(runtime, existing.user_id)
    try:
        mem = runtime.memory.update(memory_id, content=req.content, category=req.category,
                                    importance=req.importance, reason=req.reason)
    except KeyError:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"memory": mem.to_dict(), "versions": runtime.memory.versions(memory_id)}


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str, runtime: Runtime = Depends(rt)):
    existing = runtime.memory.get(memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")
    ensure_owner(runtime, existing.user_id)
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


# ------------------------------------------------ V8.4.4 portability/recovery
@app.post("/api/portability/v1/exports")
def create_portability_export(req: ExportCreateRequest, runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    try:
        return runtime.portability.create_export(user, req.domains)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/portability/v1/exports")
def list_portability_exports(user_id: str | None = None, limit: int = 50,
                             runtime: Runtime = Depends(rt)):
    return {"exports": runtime.portability.list_exports(uid(runtime, user_id), limit=limit)}


@app.get("/api/portability/v1/exports/{export_id}")
def inspect_portability_export(export_id: str, user_id: str | None = None,
                               runtime: Runtime = Depends(rt)):
    try:
        return runtime.portability.inspect_export(uid(runtime, user_id), export_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/portability/v1/exports/{export_id}/manifest")
def portability_export_manifest(export_id: str, user_id: str | None = None,
                                runtime: Runtime = Depends(rt)):
    try:
        inspected = runtime.portability.inspect_export(uid(runtime, user_id), export_id)
        return {"manifest": inspected["manifest"], "integrity": inspected["integrity"]}
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/portability/v1/exports/{export_id}/verify")
def verify_portability_export(export_id: str, user_id: str | None = None,
                              runtime: Runtime = Depends(rt)):
    try:
        return runtime.portability.verify_export(uid(runtime, user_id), export_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/portability/v1/exports/{export_id}/download")
def download_portability_export(export_id: str, user_id: str | None = None,
                                runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    try:
        path = runtime.portability.export_path(user, export_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # V8.5 audit: package downloads are security-sensitive reads.
    try:
        runtime.cognition.bus.emit(
            user, "export.accessed", "An export package was downloaded.",
            subject_kind="export", subject_id=export_id,
            payload={"request_id": get_request_id()})
    except Exception:  # pragma: no cover - audit must not block the download
        pass
    return FileResponse(path, media_type="application/zip", filename=f"{export_id}.zip")


@app.post("/api/portability/v1/imports")
async def stage_portability_import(file: UploadFile = File(...), user_id: str | None = None,
                                   runtime: Runtime = Depends(rt)):
    # Read one byte past the configured limit so oversized input is rejected
    # without unbounded buffering.
    limit = runtime.portability.limits["package_bytes"]
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail="Package exceeds the configured maximum size.")
    try:
        return runtime.portability.stage_import(uid(runtime, user_id), data, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/portability/v1/imports")
def list_portability_imports(user_id: str | None = None, limit: int = 50,
                             runtime: Runtime = Depends(rt)):
    rows = runtime.db.query(
        "SELECT id,filename,state,package_sha256,created_at,validated_at FROM portability_imports "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid(runtime, user_id), min(max(limit, 1), 200)))
    return {"imports": [dict(row) for row in rows]}


@app.get("/api/portability/v1/imports/{import_id}")
def inspect_portability_import(import_id: str, user_id: str | None = None,
                                runtime: Runtime = Depends(rt)):
    try:
        return runtime.portability.inspect_import(uid(runtime, user_id), import_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/portability/v1/imports/{import_id}/validate")
def validate_portability_import(import_id: str, user_id: str | None = None,
                                 runtime: Runtime = Depends(rt)):
    try:
        return runtime.portability.validate_import(uid(runtime, user_id), import_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/portability/v1/imports/{import_id}/dry-run")
def dry_run_portability_restore(import_id: str, req: RestoreDryRunRequest,
                                runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    try:
        return runtime.portability.dry_run(user, import_id, req.domains, req.resolutions)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/portability/v1/imports/{import_id}/conflicts")
def inspect_portability_conflicts(import_id: str, user_id: str | None = None,
                                  state: str | None = None,
                                  runtime: Runtime = Depends(rt)):
    try:
        conflicts = runtime.portability.list_conflicts(uid(runtime, user_id), import_id, state)
        if runtime.portability._import_row(uid(runtime, user_id), import_id) is None:
            raise KeyError("Import session not found.")
        return {"conflicts": conflicts}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/portability/v1/imports/{import_id}/restore")
@app.post("/api/portability/v1/imports/{import_id}/restore-selected")
def restore_portability_selected(import_id: str, req: RestoreApplyRequest,
                                 runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    try:
        return runtime.portability.apply_restore(user, import_id, confirm=req.confirm,
                                                 domains=req.domains, resolutions=req.resolutions)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "conflict" in str(exc).lower() else 400,
                            detail=str(exc)) from exc


@app.get("/api/portability/v1/restore-history")
def portability_restore_history(user_id: str | None = None, limit: int = 50,
                                runtime: Runtime = Depends(rt)):
    return {"operations": runtime.portability.restore_history(uid(runtime, user_id), limit)}


@app.get("/api/portability/v1/imports/{import_id}/explanation")
def portability_import_explanation(import_id: str, intent: str = "why",
                                   user_id: str | None = None,
                                   runtime: Runtime = Depends(rt)):
    try:
        return runtime.portability.explain(uid(runtime, user_id), "import", import_id, intent)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/reset")
def reset(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    if runtime.settings.auth_mode == "required":
        # The demo reset is a single-user convenience; in multi-user mode it
        # would be a destructive cross-namespace hazard, so it is disabled.
        raise HTTPException(status_code=403,
                            detail="Demo reset is disabled when authentication is required.")
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
    ensure_owner(runtime, events[0].user_id)
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
            "downstream": graph.downstream(node_kind, node_id, user_id=u),
            "upstream": graph.upstream(node_kind, node_id, user_id=u),
            "chain": graph.chain(node_kind, node_id, user_id=u),
            "impact": (graph.impact(u, node_id) if node_kind == "memory"
                       else {"detail": "Impact is only measured for memories."})}


@app.get("/api/decisions")
def decisions(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    return {"decisions": runtime.cognition.decisions.list(uid(runtime, user_id))}


@app.post("/api/decisions")
def record_decision(body: DecisionRequest, runtime: Runtime = Depends(rt)):
    return runtime.cognition.decisions.record(
        uid(runtime, body.user_id), body.question, chosen=body.chosen,
        alternatives=body.alternatives, expected_outcome=body.expectation,
        influenced_by=body.influenced_by)


@app.post("/api/decisions/{decision_id}/outcome")
def resolve_decision(decision_id: str, body: DecisionOutcomeRequest,
                     runtime: Runtime = Depends(rt)):
    result = runtime.cognition.decisions.resolve(
        uid(runtime, body.user_id), decision_id, body.actual_outcome,
        positive=body.positive, tradeoffs=body.tradeoffs, lesson=body.lesson,
        regret_evidence=body.regret_evidence)
    if result is None:
        raise HTTPException(404, "Unknown or already-resolved decision.")
    return result


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
    return {"policies": le.policies(u), "self_evaluation": le.self_evaluation(u),
            "experience_count": len(runtime.cognition.experiences.list(u, limit=500)),
            "knowledge": runtime.cognition.knowledge.stats(u)}


@app.post("/api/learning/consolidate")
def consolidate_learning(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Run policy mining plus V8.4.1 evidence-backed candidate maintenance."""
    u = uid(runtime, user_id)
    # Preserve the established top-level policy-learning response and add the
    # V8.4.1 evidence-backed result without breaking existing clients.
    policy_learning = runtime.cognition.learning.consolidate(u)
    return {**policy_learning,
            "experience_learning": runtime.cognition.knowledge.maintain(u)}


# ================================================================ V8.4.1
# Experiences are meaningful observed episodes; canonical observations remain
# their evidence. Skills and Principles share validation/use APIs but keep
# distinct kinds, thresholds and routes for an explicit product contract.
@app.get("/api/experiences")
def experiences(user_id: str | None = None, lifecycle: str | None = None,
                pattern_key: str | None = None, limit: int = 100,
                runtime: Runtime = Depends(rt)):
    return {"experiences": runtime.cognition.experiences.list(
        uid(runtime, user_id), lifecycle=lifecycle, pattern_key=pattern_key,
        limit=limit)}


@app.post("/api/experiences", status_code=201)
def create_experience(body: ExperienceCreateRequest,
                      runtime: Runtime = Depends(rt)):
    return runtime.cognition.experiences.create(
        uid(runtime, body.user_id), body.situation,
        evidence_ids=body.evidence_ids, action=body.action, outcome=body.outcome,
        success=body.success, observation=body.observation, context=body.context,
        intent=body.intent, need=body.need, consequences=body.consequences,
        confidence=body.confidence, scope_kind=body.scope_kind,
        scope_value=body.scope_value, pattern_key=body.pattern_key,
        source=body.source, provenance=body.provenance, thread_id=body.thread_id)


@app.get("/api/experiences/{experience_id}")
def get_experience(experience_id: str, user_id: str | None = None,
                   runtime: Runtime = Depends(rt)):
    item = runtime.cognition.experiences.get(uid(runtime, user_id), experience_id)
    if item is None:
        raise HTTPException(404, "Unknown experience.")
    return item


@app.post("/api/experiences/{experience_id}/lifecycle")
def transition_experience(experience_id: str, body: ExperienceLifecycleRequest,
                          runtime: Runtime = Depends(rt)):
    user = uid(runtime, body.user_id)
    if body.lifecycle == "validated":
        return runtime.cognition.experiences.validate(
            user, experience_id, reason=body.reason)
    if body.lifecycle == "active":
        return runtime.cognition.experiences.activate(
            user, experience_id, reason=body.reason)
    if body.lifecycle == "archived":
        return runtime.cognition.experiences.archive(
            user, experience_id, reason=body.reason)
    return runtime.cognition.experiences.transition(
        user, experience_id, body.lifecycle, reason=body.reason,
        evidence_ids=body.evidence_ids)


@app.get("/api/experiences/{experience_id}/provenance")
def experience_provenance(experience_id: str, user_id: str | None = None,
                          runtime: Runtime = Depends(rt)):
    try:
        return runtime.cognition.experiences.provenance(
            uid(runtime, user_id), experience_id)
    except KeyError:
        raise HTTPException(404, "Unknown experience.")


def _knowledge_list(kind: str, user_id: str | None, lifecycle: str | None,
                    limit: int, runtime: Runtime):
    return {f"{kind}s": runtime.cognition.knowledge.list(
        uid(runtime, user_id), kind=kind, lifecycle=lifecycle, limit=limit),
        "stats": runtime.cognition.knowledge.stats(uid(runtime, user_id))}


@app.get("/api/skills")
def skills(user_id: str | None = None, lifecycle: str | None = None,
           limit: int = 100, runtime: Runtime = Depends(rt)):
    return _knowledge_list("skill", user_id, lifecycle, limit, runtime)


@app.get("/api/principles")
def principles(user_id: str | None = None, lifecycle: str | None = None,
               limit: int = 100, runtime: Runtime = Depends(rt)):
    return _knowledge_list("principle", user_id, lifecycle, limit, runtime)


@app.post("/api/skills/candidates", status_code=201)
def create_skill_candidate(body: SkillCandidateRequest,
                           runtime: Runtime = Depends(rt)):
    return runtime.cognition.knowledge.propose_skill(
        uid(runtime, body.user_id), body.name, body.statement,
        trigger=body.trigger, procedure=body.procedure,
        expected_outcome=body.expected_outcome,
        supporting_experience_ids=body.supporting_experience_ids,
        counterexample_experience_ids=body.counterexample_experience_ids,
        context=body.context, preconditions=body.preconditions,
        scope_kind=body.scope_kind, scope_value=body.scope_value,
        pattern_key=body.pattern_key,
        generalization_hint=body.generalization_hint,
        source=body.source, provenance=body.provenance)


@app.post("/api/principles/candidates", status_code=201)
def create_principle_candidate(body: PrincipleCandidateRequest,
                               runtime: Runtime = Depends(rt)):
    return runtime.cognition.knowledge.propose_principle(
        uid(runtime, body.user_id), body.name, body.statement,
        supporting_skill_ids=body.supporting_skill_ids,
        supporting_experience_ids=body.supporting_experience_ids,
        counterexample_experience_ids=body.counterexample_experience_ids,
        application=body.application, expected_outcome=body.expected_outcome,
        scope_kind=body.scope_kind, scope_value=body.scope_value,
        pattern_key=body.pattern_key, generality=body.generality,
        source=body.source, provenance=body.provenance)


@app.post("/api/skills/retrieve")
def retrieve_skills(body: KnowledgeRetrievalRequest,
                    runtime: Runtime = Depends(rt)):
    return runtime.cognition.knowledge.retrieve(
        uid(runtime, body.user_id), body.query, kind="skill", scope=body.scope,
        current_world=body.current_world, limit=body.limit)


@app.post("/api/principles/retrieve")
def retrieve_principles(body: KnowledgeRetrievalRequest,
                        runtime: Runtime = Depends(rt)):
    return runtime.cognition.knowledge.retrieve(
        uid(runtime, body.user_id), body.query, kind="principle", scope=body.scope,
        current_world=body.current_world, limit=body.limit)


@app.get("/api/learning/usages")
def knowledge_usages(user_id: str | None = None, item_id: str | None = None,
                     pending: bool = False, limit: int = 100,
                     runtime: Runtime = Depends(rt)):
    u = uid(runtime, user_id)
    return {"usages": runtime.cognition.knowledge.usages(
        u, item_id=item_id, pending=pending, limit=limit)}


@app.post("/api/learning/usages/{usage_id}/outcome")
def knowledge_usage_outcome(usage_id: str, body: KnowledgeOutcomeRequest,
                            runtime: Runtime = Depends(rt)):
    try:
        return runtime.cognition.knowledge.record_outcome(
            uid(runtime, body.user_id), usage_id, verdict=body.verdict,
            detail=body.detail, evidence=body.evidence)
    except KeyError:
        raise HTTPException(404, "Unknown learned-knowledge usage.")


def _get_knowledge(kind: str, item_id: str, user_id: str | None,
                   runtime: Runtime):
    item = runtime.cognition.knowledge.get(uid(runtime, user_id), item_id)
    if item is None or item["kind"] != kind:
        raise HTTPException(404, f"Unknown {kind}.")
    return item


@app.get("/api/skills/{item_id}")
def get_skill(item_id: str, user_id: str | None = None,
              runtime: Runtime = Depends(rt)):
    return _get_knowledge("skill", item_id, user_id, runtime)


@app.get("/api/principles/{item_id}")
def get_principle(item_id: str, user_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    return _get_knowledge("principle", item_id, user_id, runtime)


@app.get("/api/skills/{item_id}/explanation")
def explain_skill(item_id: str, user_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    _get_knowledge("skill", item_id, user_id, runtime)
    return runtime.cognition.knowledge.explain(uid(runtime, user_id), item_id)


@app.get("/api/principles/{item_id}/explanation")
def explain_principle(item_id: str, user_id: str | None = None,
                      runtime: Runtime = Depends(rt)):
    _get_knowledge("principle", item_id, user_id, runtime)
    return runtime.cognition.knowledge.explain(uid(runtime, user_id), item_id)


def _validate_knowledge(kind: str, item_id: str, user_id: str | None,
                        runtime: Runtime):
    _get_knowledge(kind, item_id, user_id, runtime)
    return runtime.cognition.knowledge.validate(uid(runtime, user_id), item_id)


@app.post("/api/skills/{item_id}/validate")
def validate_skill(item_id: str, user_id: str | None = None,
                   runtime: Runtime = Depends(rt)):
    return _validate_knowledge("skill", item_id, user_id, runtime)


@app.post("/api/principles/{item_id}/validate")
def validate_principle(item_id: str, user_id: str | None = None,
                       runtime: Runtime = Depends(rt)):
    return _validate_knowledge("principle", item_id, user_id, runtime)


def _promote_knowledge(kind: str, item_id: str, user_id: str | None,
                       runtime: Runtime):
    _get_knowledge(kind, item_id, user_id, runtime)
    return runtime.cognition.knowledge.promote(uid(runtime, user_id), item_id)


@app.post("/api/skills/{item_id}/promote")
def promote_skill(item_id: str, user_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    return _promote_knowledge("skill", item_id, user_id, runtime)


@app.post("/api/principles/{item_id}/promote")
def promote_principle(item_id: str, user_id: str | None = None,
                      runtime: Runtime = Depends(rt)):
    return _promote_knowledge("principle", item_id, user_id, runtime)


def _use_knowledge(kind: str, item_id: str, body: KnowledgeUseRequest,
                   runtime: Runtime):
    _get_knowledge(kind, item_id, body.user_id, runtime)
    return runtime.cognition.knowledge.record_use(
        uid(runtime, body.user_id), item_id,
        influenced_kind=body.influenced_kind, influenced_id=body.influenced_id,
        how=body.how, context=body.context, weight=body.weight,
        arbitration_id=body.arbitration_id)


@app.post("/api/skills/{item_id}/use")
def use_skill(item_id: str, body: KnowledgeUseRequest,
              runtime: Runtime = Depends(rt)):
    return _use_knowledge("skill", item_id, body, runtime)


@app.post("/api/principles/{item_id}/use")
def use_principle(item_id: str, body: KnowledgeUseRequest,
                  runtime: Runtime = Depends(rt)):
    return _use_knowledge("principle", item_id, body, runtime)


def _correct_knowledge(kind: str, item_id: str, body: KnowledgeCorrectionRequest,
                       runtime: Runtime):
    _get_knowledge(kind, item_id, body.user_id, runtime)
    return runtime.cognition.knowledge.correct(
        uid(runtime, body.user_id), item_id, action=body.action,
        reason=body.reason, scope_kind=body.scope_kind,
        scope_value=body.scope_value, evidence=body.evidence)


@app.post("/api/skills/{item_id}/correction")
def correct_skill(item_id: str, body: KnowledgeCorrectionRequest,
                  runtime: Runtime = Depends(rt)):
    return _correct_knowledge("skill", item_id, body, runtime)


@app.post("/api/principles/{item_id}/correction")
def correct_principle(item_id: str, body: KnowledgeCorrectionRequest,
                      runtime: Runtime = Depends(rt)):
    return _correct_knowledge("principle", item_id, body, runtime)


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
    ensure_owner(runtime, steps[0].get("user_id"))
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
    ensure_owner(runtime, record.get("user_id"))
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


# ==========================================================================
# V8.3 — CONTINUOUS COGNITION
# ==========================================================================

# ------------------------------------------------------------------ missions
@app.get("/api/missions")
def list_missions(user_id: str | None = None, state: str | None = None,
                  open_only: bool = False, runtime: Runtime = Depends(rt)):
    """Long-running objectives that span conversations (§10)."""
    user = uid(runtime, user_id)
    return {"missions": runtime.cognition.missions.list(
        user, state=state, open_only=open_only)}


@app.post("/api/missions")
def create_mission(req: MissionCreateRequest, runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    mission = runtime.cognition.missions.create(
        user, req.title, description=req.description, scope=req.scope,
        constraints=req.constraints, success_criteria=req.success_criteria,
        priority=req.priority, evidence=req.evidence)
    return {"mission": mission}


@app.get("/api/missions/brief")
def mission_brief(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """What a returning user needs to know, including what has gone quiet."""
    user = uid(runtime, user_id)
    return {"brief": runtime.cognition.missions.resume_brief(user)}


@app.get("/api/missions/{mission_id}")
def get_mission(mission_id: str, user_id: str | None = None,
                runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    mission = runtime.cognition.missions.get(user, mission_id)
    if mission is None:
        raise HTTPException(404, "No such mission.")
    return {"mission": mission}


@app.get("/api/missions/{mission_id}/history")
def mission_history(mission_id: str, user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    """Every state change with its reason and evidence."""
    user = uid(runtime, user_id)
    if runtime.cognition.missions.get(user, mission_id) is None:
        raise HTTPException(404, "No such mission.")
    return {"history": runtime.cognition.missions.history(user, mission_id)}


@app.post("/api/missions/{mission_id}/state")
def set_mission_state(mission_id: str, req: MissionStateRequest,
                      runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    mission = runtime.cognition.missions.set_state(
        user, mission_id, req.state, reason=req.reason, evidence=req.evidence,
        blocked_reason=req.blocked_reason, waiting_on=req.waiting_on)
    if mission is None:
        raise HTTPException(404, "No such mission.")
    return {"mission": mission}


@app.post("/api/missions/{mission_id}/steps")
def add_mission_step(mission_id: str, req: MissionStepRequest,
                     runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    try:
        return runtime.cognition.missions.add_step(
            user, mission_id, req.summary, kind=req.kind,
            depends_on=req.depends_on)
    except KeyError:
        raise HTTPException(404, "No such mission.")


@app.post("/api/missions/steps/{step_id}/complete")
def complete_mission_step(step_id: str, user_id: str | None = None,
                          runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    mission = runtime.cognition.missions.complete_step(user, step_id)
    if mission is None:
        raise HTTPException(404, "No such mission step.")
    return {"mission": mission}


# -------------------------------------------------------------- observations
@app.get("/api/observations")
def list_observations(user_id: str | None = None, source: str | None = None,
                      epistemic_status: str | None = None,
                      runtime: Runtime = Depends(rt)):
    """Evidence the system actually recorded. Not the same as memory (§17)."""
    user = uid(runtime, user_id)
    return {
        "observations": runtime.cognition.observations.list(
            user, source=source, epistemic_status=epistemic_status),
        "stats": runtime.cognition.observations.stats(user),
    }


@app.post("/api/observations")
def record_observation(req: ObservationRequest, runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    return {"observation": runtime.cognition.observations.record(
        user, req.content, source=req.source, origin=req.origin,
        epistemic_status=req.epistemic_status, confidence=req.confidence,
        subject_kind=req.subject_kind, subject_id=req.subject_id)}


@app.get("/api/observations/evidence/{subject_kind}/{subject_id}")
def observation_evidence(subject_kind: str, subject_id: str,
                         user_id: str | None = None,
                         runtime: Runtime = Depends(rt)):
    """All evidence bearing on one subject, split by epistemic status."""
    user = uid(runtime, user_id)
    return runtime.cognition.observations.evidence_for(
        user, subject_kind, subject_id)


@app.post("/api/observations/{observation_id}/promote")
def promote_observation(observation_id: str, user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    """Explicitly turn evidence into a durable memory. Never automatic."""
    user = uid(runtime, user_id)
    try:
        return runtime.cognition.observations.promote(
            user, observation_id, runtime.memory)
    except KeyError:
        raise HTTPException(404, "No such observation.")


# ---------------------------------------------------------------- world v2
@app.get("/api/world/snapshot")
def world_snapshot(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Current world state with per-fact freshness (§9)."""
    user = uid(runtime, user_id)
    return runtime.cognition.world_v2.snapshot(user)


@app.get("/api/world/changes")
def world_changes(user_id: str | None = None, entity_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    """Change provenance — the history V8.2 declared but never wrote."""
    user = uid(runtime, user_id)
    return {"changes": runtime.cognition.world_v2.changes(
        user, entity_id=entity_id)}


@app.get("/api/world/stale")
def world_stale(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Facts due for re-confirmation. Stale does not mean false."""
    user = uid(runtime, user_id)
    return {"stale": runtime.cognition.world_v2.stale_facts(user),
            "note": ("A stale fact keeps its value; it is flagged for "
                     "re-checking, not treated as false.")}


@app.post("/api/world/reconcile")
def world_reconcile(req: WorldReconcileRequest, runtime: Runtime = Depends(rt)):
    """Offer a claim; get keep / supersede / merge / flag / downgrade (§8)."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.world_v2.reconcile(
        user, req.kind, req.label, state=req.state, detail=req.detail,
        confidence=req.confidence, explicit_correction=req.explicit_correction,
        evidence=req.evidence)


# ------------------------------------------------------------ time machine
@app.get("/api/history/coverage")
def history_coverage(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """The window history can actually answer for."""
    user = uid(runtime, user_id)
    return runtime.cognition.timemachine.coverage(user)


@app.get("/api/history/world")
def history_world(at: str, user_id: str | None = None,
                  runtime: Runtime = Depends(rt)):
    """Reconstruct world state at a past moment, from real records only."""
    user = uid(runtime, user_id)
    return runtime.cognition.timemachine.world_at(user, at)


@app.get("/api/history/missions")
def history_missions(at: str, user_id: str | None = None,
                     runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return runtime.cognition.timemachine.missions_at(user, at)


@app.get("/api/history/diff")
def history_diff(start: str, end: str, user_id: str | None = None,
                 runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return runtime.cognition.timemachine.diff(user, start, end)


# ------------------------------------------------------ background cognition
@app.get("/api/background")
def background_status(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Real cycle records, including the ones that found nothing (§14)."""
    user = uid(runtime, user_id)
    return runtime.cognition.background.status(user)


@app.post("/api/background/control")
def background_control(req: BackgroundControlRequest,
                       runtime: Runtime = Depends(rt)):
    """Enable / pause / disable background cognition."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.background.set_state(
        user, req.state, reason=req.reason)


@app.post("/api/background/run")
def background_run(req: BackgroundRunRequest, runtime: Runtime = Depends(rt)):
    """
    Run one bounded upkeep cycle. Rate-limited, deadline-bounded and
    non-destructive; a cycle that finds nothing says so.
    """
    user = uid(runtime, req.user_id)
    return runtime.cognition.background.run_cycle(
        user, trigger=req.trigger, force=req.force, deadline_s=req.deadline_s)


# ----------------------------------------------------------------- attention
@app.post("/api/attention/evaluate")
def attention_evaluate(req: AttentionRequest, runtime: Runtime = Depends(rt)):
    """IGNORE / MONITOR / PREPARE / MENTION / ASK / ACT — or DO_NOTHING (§15)."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.attention_v2.evaluate(
        user, req.topic, importance=req.importance, urgency=req.urgency,
        confidence=req.confidence, relevance=req.relevance,
        mission_id=req.mission_id)


@app.get("/api/attention/policy")
def attention_policy(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Learned silence policy, or INSUFFICIENT EVIDENCE (§16)."""
    user = uid(runtime, user_id)
    return runtime.cognition.attention_v2.silence_policy(user)


@app.get("/api/attention/suppressions")
def attention_suppressions(user_id: str | None = None,
                           runtime: Runtime = Depends(rt)):
    """Everything the system chose not to raise, and why."""
    user = uid(runtime, user_id)
    return {"suppressions": runtime.cognition.attention_v2.suppressions(user)}


@app.post("/api/attention/{intervention_id}/reaction")
def attention_reaction(intervention_id: str, req: AttentionReactionRequest,
                       runtime: Runtime = Depends(rt)):
    """Record a real reaction. Silence is never treated as acceptance."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.attention_v2.record_reaction(
        user, intervention_id, req.accepted, detail=req.detail)


# ---------------------------------------------------------------- simulation
@app.post("/api/simulation")
def run_simulation(req: SimulationRequest, runtime: Runtime = Depends(rt)):
    """Counterfactual over a frozen snapshot. Tagged SIMULATED (§20)."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.simulation.simulate(
        user, req.question, assumptions=req.assumptions)


@app.post("/api/simulation/{simulation_id}/commit")
def commit_simulation(simulation_id: str, req: SimulationCommitRequest,
                      runtime: Runtime = Depends(rt)):
    """Requires explicit confirmation AND concrete changes."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.simulation.commit(
        user, simulation_id, confirm=req.confirm, changes=req.changes)


# ----------------------------------------------------------------- documents
@app.get("/api/documents")
def list_documents(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"documents": runtime.cognition.documents.list(user),
            "capabilities": runtime.cognition.documents.capabilities()}


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...),
                          user_id: str | None = None,
                          runtime: Runtime = Depends(rt)):
    """
    Ingest a document. Formats without a parser are tracked as METADATA ONLY —
    their contents are never inferred.
    """
    user = uid(runtime, user_id)
    data = await file.read()
    document = runtime.cognition.documents.ingest(
        user, file.filename or "upload", data,
        media_type=file.content_type)
    document.pop("text", None)
    return {"document": document}


# -------------------------------------------------- connectors and research
@app.get("/api/connectors")
def list_connectors(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Declared interfaces. Nothing is connected in this build (§26)."""
    user = uid(runtime, user_id)
    runtime.cognition.connectors.declare_defaults(user)
    return runtime.cognition.connectors.status(user)


@app.get("/api/connectors/{name}/fetch")
def fetch_connector(name: str, user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    """Always NOT CONNECTED here — distinct from 'the source was empty'."""
    user = uid(runtime, user_id)
    return runtime.cognition.connectors.fetch(user, name)


@app.get("/api/research")
def research_status(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"status": runtime.cognition.research.status(user),
            "sessions": runtime.cognition.research.list(user)}


@app.post("/api/research")
def start_research(req: ResearchRequest, runtime: Runtime = Depends(rt)):
    """Tracks the question; produces no findings without a provider (§27)."""
    user = uid(runtime, req.user_id)
    return {"session": runtime.cognition.research.start(user, req.question)}


# --------------------------------------------- v8.4.3 connected research (v2)
#
# A distinct, additive namespace from the /api/research routes above, which
# remain the honest "no provider configured" state machine (V8.3 §27). These
# routes drive the REAL ResearchEngine: explicit http(s) URLs are fetched
# through an SSRF-defended pipeline and produce genuine, provenance-carrying
# evidence and claims. Every route is scoped to the caller's user_id and
# never resolves another user's session/source/evidence/claim by id.
@app.get("/api/research/v2/status")
def research_v2_status(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return runtime.cognition.research_engine.status(user)


@app.post("/api/research/v2")
def create_research_session(req: ResearchSessionCreateRequest,
                            runtime: Runtime = Depends(rt)):
    user = uid(runtime, req.user_id)
    return {"session": runtime.cognition.research_engine.start(user, req.question)}


@app.get("/api/research/v2")
def list_research_sessions(user_id: str | None = None, limit: int = 25,
                           runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"sessions": runtime.cognition.research_engine.list(user, limit=limit)}


@app.get("/api/research/v2/{session_id}")
def get_research_session(session_id: str, user_id: str | None = None,
                         runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    session = runtime.cognition.research_engine.get(user, session_id)
    if session is None:
        raise HTTPException(404, "Research session not found.")
    return {"session": session}


@app.post("/api/research/v2/{session_id}/fetch")
def fetch_research_source(session_id: str, req: ResearchFetchRequest,
                          runtime: Runtime = Depends(rt)):
    """Fetch one explicit URL. URLs are never invented by the backend."""
    user = uid(runtime, req.user_id)
    result = runtime.cognition.research_engine.fetch(user, session_id, req.url)
    if result.get("status") == "NOT_FOUND":
        raise HTTPException(404, result.get("detail", "Session not found."))
    return result


@app.post("/api/research/v2/{session_id}/finish")
def finish_research_session(session_id: str, user_id: str | None = None,
                            runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    session = runtime.cognition.research_engine.finish(user, session_id)
    if session is None:
        raise HTTPException(404, "Research session not found.")
    return {"session": session}


@app.get("/api/research/v2/{session_id}/sources")
def get_research_sources(session_id: str, user_id: str | None = None,
                         runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"sources": runtime.cognition.research_engine.sources(user, session_id)}


@app.get("/api/research/v2/{session_id}/fetches")
def get_research_fetches(session_id: str, user_id: str | None = None,
                         runtime: Runtime = Depends(rt)):
    """The fetch ledger — includes failed/blocked attempts (never hidden)."""
    user = uid(runtime, user_id)
    return {"fetches": runtime.cognition.research_engine.fetches(user, session_id)}


@app.get("/api/research/v2/{session_id}/evidence")
def get_research_evidence(session_id: str, user_id: str | None = None,
                          runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"evidence": runtime.cognition.research_engine.evidence(user, session_id)}


@app.get("/api/research/v2/{session_id}/claims")
def get_research_claims(session_id: str, user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"claims": runtime.cognition.research_engine.claims(user, session_id)}


@app.get("/api/research/v2/{session_id}/conflicts")
def get_research_conflicts(session_id: str, user_id: str | None = None,
                           runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"conflicts": runtime.cognition.research_engine.conflicts(user, session_id)}


@app.get("/api/research/v2/{session_id}/world-updates")
def get_research_world_updates(session_id: str, user_id: str | None = None,
                               runtime: Runtime = Depends(rt)):
    user = uid(runtime, user_id)
    return {"world_updates": runtime.cognition.research_engine.world_updates(user, session_id)}


@app.post("/api/research/v2/{session_id}/world-updates/propose")
def propose_research_world_update(session_id: str, req: ResearchWorldProposeRequest,
                                  runtime: Runtime = Depends(rt)):
    """Propose a bounded, capped-confidence World Model update from a claim.

    This never changes the World Model by itself — see the /apply route.
    """
    user = uid(runtime, req.user_id)
    try:
        update = runtime.cognition.research_engine.propose_world_update(
            user, session_id, req.claim_id, req.kind, req.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"world_update": update}


@app.post("/api/research/v2/world-updates/{update_id}/apply")
def apply_research_world_update(update_id: str, req: ResearchWorldApplyRequest,
                                runtime: Runtime = Depends(rt)):
    """Apply a PROPOSED world update. Requires explicit confirm=True."""
    user = uid(runtime, req.user_id)
    try:
        result = runtime.cognition.research_engine.apply_world_update(
            user, update_id, confirm=req.confirm)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


# ------------------------------------------------------------ V10 maintenance
# V10 routes use the same verified principal/permission boundary as every
# V8.5 cognitive route. `uid()` ignores caller-selected namespaces in required
# auth mode, and service lookups include both user and tenant scope.
def _v10_user(runtime: Runtime, *, write: bool = False, provided: str | None = None) -> str:
    require_permission(P_COGNITION_WRITE if write else P_COGNITION_READ, runtime)
    return uid(runtime, provided)


@app.get("/api/v10/cognitive-debt")
def v10_cognitive_debt(status: str | None = None, limit: int = 100,
                      user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return {"debt": runtime.cognition.cognitive_debt.list(user, status=status, limit=limit)}


@app.get("/api/v10/cognitive-debt/{debt_id}")
def v10_cognitive_debt_item(debt_id: str, user_id: str | None = None,
                           runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    item = runtime.cognition.cognitive_debt.get(user, debt_id)
    if item is None:
        raise HTTPException(404, "Cognitive debt item not found.")
    return item


@app.post("/api/v10/cognitive-debt/{debt_id}/acknowledge")
def v10_acknowledge_debt(debt_id: str, body: V10ActionRequest = Body(default=V10ActionRequest()),
                         runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    try:
        item = runtime.cognition.cognitive_debt.transition(
            user, debt_id, "ACKNOWLEDGED", reason=body.reason,
            correlation_id=get_request_id())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if item is None:
        raise HTTPException(404, "Cognitive debt item not found.")
    return item


@app.post("/api/v10/cognitive-debt/{debt_id}/resolve")
def v10_resolve_debt(debt_id: str, body: V10ActionRequest = Body(default=V10ActionRequest()),
                     runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    try:
        item = runtime.cognition.cognitive_debt.transition(
            user, debt_id, "RESOLVED", reason=body.reason,
            correlation_id=get_request_id())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if item is None:
        raise HTTPException(404, "Cognitive debt item not found.")
    return item


@app.post("/api/v10/cognitive-debt/{debt_id}/defer")
def v10_defer_debt(debt_id: str, body: V10ActionRequest = Body(default=V10ActionRequest()),
                   runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    try:
        item = runtime.cognition.cognitive_debt.transition(
            user, debt_id, "DEFERRED", reason=body.reason, until=body.until,
            correlation_id=get_request_id())
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if item is None:
        raise HTTPException(404, "Cognitive debt item not found.")
    return item


@app.get("/api/v10/contradictions")
def v10_contradictions(status: str | None = None, limit: int = 100,
                       user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return {"contradictions": runtime.cognition.contradictions.list(user, status=status, limit=limit)}


@app.get("/api/v10/contradictions/{record_id}")
def v10_contradiction(record_id: str, user_id: str | None = None,
                      runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    item = runtime.cognition.contradictions.get(user, record_id)
    if item is None:
        raise HTTPException(404, "Contradiction record not found.")
    return item


@app.post("/api/v10/contradictions/{record_id}/resolve")
def v10_resolve_contradiction(record_id: str, body: V10ActionRequest = Body(default=V10ActionRequest()),
                              runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    item = runtime.cognition.contradictions.get(user, record_id)
    if item is None:
        raise HTTPException(404, "Contradiction record not found.")
    try:
        return runtime.cognition.contradictions.resolve(
            user, record_id, status="RESOLVED_FINDING",
            classification=body.classification.value if body.classification else None,
            correlation_id=get_request_id())
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/v10/contradictions/{record_id}/dismiss")
def v10_dismiss_contradiction(record_id: str, body: V10ActionRequest = Body(default=V10ActionRequest()),
                              runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    if runtime.cognition.contradictions.get(user, record_id) is None:
        raise HTTPException(404, "Contradiction record not found.")
    return runtime.cognition.contradictions.resolve(
        user, record_id, status="DISMISSED", correlation_id=get_request_id())


@app.get("/api/v10/unknowns")
def v10_unknowns(status: str | None = None, limit: int = 100,
                 user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return {"unknowns": runtime.cognition.unknowns.list(user, status=status, limit=limit)}


@app.get("/api/v10/unknowns/{unknown_id}")
def v10_unknown(unknown_id: str, user_id: str | None = None,
                runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    item = runtime.cognition.unknowns.get(user, unknown_id)
    if item is None:
        raise HTTPException(404, "Unknown record not found.")
    return item


@app.post("/api/v10/unknowns/{unknown_id}/resolve")
def v10_resolve_unknown(unknown_id: str, body: V10ActionRequest = Body(default=V10ActionRequest()),
                       runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    try:
        item = runtime.cognition.unknowns.resolve(
            user, unknown_id, reason=body.reason or "", correlation_id=get_request_id())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if item is None:
        raise HTTPException(404, "Unknown record not found.")
    return item


@app.get("/api/v10/model-errors")
def v10_model_errors(limit: int = 100, user_id: str | None = None,
                     runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return {"model_errors": runtime.cognition.model_errors.list(user, limit=limit)}


@app.get("/api/v10/model-errors/{error_id}")
def v10_model_error(error_id: str, user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    item = runtime.cognition.model_errors.get(user, error_id)
    if item is None:
        raise HTTPException(404, "Model-error record not found.")
    return item


@app.post("/api/v10/model-errors")
def v10_record_model_error(body: ModelErrorCreate, runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    values = body.model_dump()
    values["classification"] = values["classification"].value if values.get("classification") else None
    try:
        return runtime.cognition.model_errors.record(user, **values,
                                                     correlation_id=get_request_id())
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/v10/cognitive-health")
def v10_cognitive_health(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return runtime.cognition.cognitive_health.compute(user, correlation_id=get_request_id())


@app.get("/api/v10/cognitive-health/explain")
def v10_cognitive_health_explain(user_id: str | None = None,
                                runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return runtime.cognition.cognitive_health.explain(user, correlation_id=get_request_id())


@app.post("/api/v10/maintenance/audit")
def v10_maintenance_audit(req: MaintenanceAuditRequest = Body(default=MaintenanceAuditRequest()),
                          runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    cid = req.correlation_id or f"v10_{uuid.uuid4().hex[:16]}"
    try:
        return runtime.cognition.self_maintenance.audit(
            user, correlation_id=cid, include_resolved=req.include_resolved)
    except Exception as exc:
        # The orchestrator persists FAILED before the exception reaches this
        # boundary; the API does not relabel it as successful maintenance.
        raise HTTPException(500, f"Maintenance audit failed: {str(exc)[:200]}")


@app.get("/api/v10/maintenance/runs/{correlation_id}")
def v10_maintenance_run(correlation_id: str, user_id: str | None = None,
                        runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    result = runtime.cognition.self_maintenance.run(user, correlation_id)
    if result is None:
        raise HTTPException(404, "Maintenance run not found.")
    return result


@app.get("/api/v10/maintenance/proposals")
def v10_maintenance_proposals(status: str | None = None, limit: int = 100,
                              user_id: str | None = None, runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return {"proposals": runtime.cognition.maintenance_proposals.list(user, status=status, limit=limit)}


@app.get("/api/v10/maintenance/proposals/{proposal_id}")
def v10_maintenance_proposal(proposal_id: str, user_id: str | None = None,
                             runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    item = runtime.cognition.maintenance_proposals.get(user, proposal_id)
    if item is None:
        raise HTTPException(404, "Maintenance proposal not found.")
    return item


@app.post("/api/v10/maintenance/proposals/{proposal_id}/confirm")
def v10_confirm_proposal(proposal_id: str, body: ProposalActionRequest = Body(default=ProposalActionRequest()),
                         runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    if not body.confirmation:
        raise HTTPException(400, "Explicit confirmation is required.")
    item = runtime.cognition.maintenance_proposals.confirm(
        user, proposal_id, reason=body.reason, correlation_id=get_request_id())
    if item is None:
        raise HTTPException(404, "Maintenance proposal not found.")
    # V10.1: a confirmed AND applied material update triggers exactly one
    # bounded re-audit (depth 1, idempotent by correlation). A re-audit
    # failure is reported inside the response; it never rolls back the
    # already-applied canonical mutation.
    reaudit = None
    try:
        reaudit = runtime.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, item, correlation_id=get_request_id())
    except Exception as exc:  # pragma: no cover - defensive; must not 500
        log.warning("Bounded re-audit failed after proposal %s: %s", proposal_id, exc)
        reaudit = {"status": "FAILED", "error": str(exc)[:200]}
    # `reaudit` is an additive field; existing clients that read only the
    # proposal fields keep working unchanged.
    return {**item, "reaudit": reaudit}


@app.post("/api/v10/maintenance/proposals/{proposal_id}/reject")
def v10_reject_proposal(proposal_id: str, body: ProposalActionRequest = Body(default=ProposalActionRequest()),
                        runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    item = runtime.cognition.maintenance_proposals.reject(
        user, proposal_id, reason=body.reason, correlation_id=get_request_id())
    if item is None:
        raise HTTPException(404, "Maintenance proposal not found.")
    return item


@app.post("/api/v10/maintenance/proposals/{proposal_id}/defer")
def v10_defer_proposal(proposal_id: str, body: ProposalActionRequest = Body(default=ProposalActionRequest()),
                       runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    item = runtime.cognition.maintenance_proposals.defer(
        user, proposal_id, until=body.until, reason=body.reason,
        correlation_id=get_request_id())
    if item is None:
        raise HTTPException(404, "Maintenance proposal not found.")
    return item


# --------------------------------------------------- v10.2 policy governance
# Additive governance views over the existing verified-principal boundary.
# Current effective policy remains readable only through the existing engine
# surface (/api/policy); these endpoints expose the governance lifecycle.
@app.get("/api/v10/governance/adaptations")
def v102_governance_adaptations(state: str | None = None, limit: int = 100,
                                user_id: str | None = None,
                                runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    return {"adaptations": runtime.cognition.policy_governance.list(
        user, state=state, limit=limit)}


@app.get("/api/v10/governance/adaptations/{adaptation_id}")
def v102_governance_adaptation(adaptation_id: str, user_id: str | None = None,
                               runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    item = runtime.cognition.policy_governance.get(user, adaptation_id)
    if item is None:
        raise HTTPException(404, "Governed adaptation not found.")
    return {**item,
            "history": runtime.cognition.policy_governance.history(user, adaptation_id)}


@app.get("/api/v10/governance/adaptations/{adaptation_id}/measurements")
def v102_governance_measurements(adaptation_id: str, user_id: str | None = None,
                                 runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, provided=user_id)
    try:
        return runtime.cognition.policy_governance.measurements(user, adaptation_id)
    except KeyError:
        raise HTTPException(404, "Governed adaptation not found.")


@app.post("/api/v10/governance/adaptations/{adaptation_id}/confirm")
def v102_confirm_adaptation(adaptation_id: str,
                            body: ProposalActionRequest = Body(default=ProposalActionRequest()),
                            runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    if not body.confirmation:
        raise HTTPException(400, "Explicit confirmation is required.")
    try:
        item = runtime.cognition.policy_governance.confirm(
            user, adaptation_id, confirmation=body.confirmation,
            reason=body.reason, correlation_id=get_request_id())
    except KeyError:
        raise HTTPException(404, "Governed adaptation not found.")
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300])
    if "blocked" in item:  # AutonomyGovernor refused; nothing was claimed
        raise HTTPException(409, "The autonomy governor blocked this confirmation.")
    # V10.2 mirror of the V10.1 confirm → bounded re-audit pattern: exactly one
    # bounded, observational re-evaluation of the adaptation's domain (depth 1,
    # terminal `::pgov1` correlation). A failure is reported additively and
    # never rolls back the already-applied canonical mutation.
    revalidation = None
    try:
        revalidation = runtime.cognition.policy_runtime.revalidate_after_confirmation(
            user, item, correlation_id=get_request_id())
    except Exception as exc:  # pragma: no cover - defensive; must not 500
        log.warning("Bounded governance re-evaluation failed for %s: %s",
                    adaptation_id, exc)
        revalidation = {"status": "FAILED", "error": str(exc)[:200]}
    return {**item, "revalidation": revalidation}


@app.post("/api/v10/governance/adaptations/{adaptation_id}/reject")
def v102_reject_adaptation(adaptation_id: str,
                           body: ProposalActionRequest = Body(default=ProposalActionRequest()),
                           runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    try:
        return runtime.cognition.policy_governance.reject(
            user, adaptation_id, reason=body.reason or "user rejection",
            correlation_id=get_request_id())
    except KeyError:
        raise HTTPException(404, "Governed adaptation not found.")
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300])


@app.post("/api/v10/governance/adaptations/{adaptation_id}/defer")
def v102_defer_adaptation(adaptation_id: str,
                          body: ProposalActionRequest = Body(default=ProposalActionRequest()),
                          runtime: Runtime = Depends(rt)):
    user = _v10_user(runtime, write=True)
    try:
        return runtime.cognition.policy_governance.defer(
            user, adaptation_id, reason=body.reason or "not now",
            correlation_id=get_request_id())
    except KeyError:
        raise HTTPException(404, "Governed adaptation not found.")
    except ValueError as exc:
        raise HTTPException(400, str(exc)[:300])


# -------------------------------------------------------------- maintenance
@app.get("/api/maintenance/review")
def maintenance_review(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Read-only memory diagnosis. Applies nothing (§23)."""
    user = uid(runtime, user_id)
    return runtime.cognition.maintenance.review(user)


@app.get("/api/maintenance/remedies")
def maintenance_remedies(runtime: Runtime = Depends(rt)):
    return runtime.cognition.maintenance.remedies()


# ------------------------------------------------------------ predictions v2
@app.get("/api/predictions/due")
def predictions_due(user_id: str | None = None, runtime: Runtime = Depends(rt)):
    """Predictions whose evaluation window passed. Evidence still required."""
    user = uid(runtime, user_id)
    return {"due": runtime.cognition.predictions.due_for_evaluation(user),
            "note": ("These need a real observation. Silence resolves nothing; "
                     "without evidence they become UNRESOLVED.")}


@app.post("/api/predictions/{prediction_id}/unresolved")
def prediction_unresolved(prediction_id: str, user_id: str | None = None,
                          runtime: Runtime = Depends(rt)):
    """Close a prediction honestly as UNRESOLVED (§19)."""
    user = uid(runtime, user_id)
    result = runtime.cognition.predictions.mark_unresolved(user, prediction_id)
    if result is None:
        raise HTTPException(404, "No such open prediction.")
    return {"prediction": result}


# ---------------------------------------------------- v8.4.2 explanation engine
@app.get("/api/explanations")
def list_explanations(user_id: str | None = None,
                      subject_kind: str | None = None,
                      subject_id: str | None = None,
                      limit: int = 50,
                      runtime: Runtime = Depends(rt)):
    """List persisted explanation snapshots for user."""
    user = uid(runtime, user_id)
    return {
        "explanations": runtime.cognition.explanation_engine.list_snapshots(
            user, subject_kind=subject_kind, subject_id=subject_id, limit=limit)
    }


@app.get("/api/explanations/{explanation_id}")
def get_explanation(explanation_id: str, user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    """Retrieve an auditable explanation snapshot by id."""
    user = uid(runtime, user_id)
    exp = runtime.cognition.explanation_engine.get_snapshot(user, explanation_id)
    if not exp:
        raise HTTPException(404, "Explanation snapshot not found.")
    return exp


@app.get("/api/explanations/subject/{subject_kind}/{subject_id}")
def explain_subject(subject_kind: str, subject_id: str,
                    intent: str = "why",
                    question: str | None = None,
                    depth: int = 2,
                    persist: bool = True,
                    user_id: str | None = None,
                    runtime: Runtime = Depends(rt)):
    """Generate or retrieve a full evidence-backed explanation graph for a subject."""
    user = uid(runtime, user_id)
    return runtime.cognition.explanation_engine.explain(
        user, subject_kind=subject_kind, subject_id=subject_id,
        query_intent=intent, question=question, depth=depth, persist=persist)


@app.get("/api/explanations/decision/{decision_id}")
def explain_decision_endpoint(decision_id: str,
                              intent: str = "why",
                              persist: bool = True,
                              user_id: str | None = None,
                              runtime: Runtime = Depends(rt)):
    """Explain why a decision was reached, its alternatives, influences, and outcomes."""
    user = uid(runtime, user_id)
    return runtime.cognition.explanation_engine.explain(
        user, subject_kind="decision", subject_id=decision_id,
        explanation_type="DECISION_INFLUENCE", query_intent=intent, persist=persist)


@app.get("/api/explanations/event/{event_id}")
def explain_event_endpoint(event_id: int,
                           intent: str = "why",
                           user_id: str | None = None,
                           runtime: Runtime = Depends(rt)):
    """Explain the context, triggers, and correlation behind a specific canonical event."""
    user = uid(runtime, user_id)
    row = runtime.db.query_one("SELECT * FROM cognitive_events WHERE id=? AND user_id=?", (event_id, user))
    if not row:
        raise HTTPException(404, "Cognitive event not found.")
    evt = dict(row)
    return runtime.cognition.explanation_engine.explain(
        user, subject_kind=evt.get("subject_kind") or "event",
        subject_id=evt.get("subject_id") or str(event_id),
        query_intent=intent, persist=True)


@app.post("/api/explanations/query")
def query_explanation(req: ExplanationQueryRequest, runtime: Runtime = Depends(rt)):
    """Run an ad-hoc explanation query with specific intent, depth, and subject."""
    user = uid(runtime, req.user_id)
    return runtime.cognition.explanation_engine.explain(
        user, subject_kind=req.subject_kind, subject_id=req.subject_id,
        explanation_type=req.explanation_type, query_intent=req.query_intent,
        question=req.question, depth=req.depth, persist=req.persist,
        correlation_id=req.correlation_id)

