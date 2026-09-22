"""Per-request security context.

The middleware in `app.main` binds the authenticated Principal and the
request id here; everything downstream (route handlers, `uid()`, cognitive
tools running inside the agent) reads the SAME context. This is what makes
namespace escape impossible: the namespace is resolved once, from the
verified session, and no later layer accepts a caller-chosen identity.

ContextVars are asyncio- and thread-safe per task, and FastAPI runs sync
handlers in a worker thread with the context copied, so the binding holds
for the full life of the request.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from .principal import Principal

current_principal: ContextVar[Optional[Principal]] = ContextVar(
    "memoryos_current_principal", default=None)
current_request_id: ContextVar[Optional[str]] = ContextVar(
    "memoryos_current_request_id", default=None)


def get_principal() -> Principal | None:
    return current_principal.get()


def get_request_id() -> str | None:
    return current_request_id.get()
