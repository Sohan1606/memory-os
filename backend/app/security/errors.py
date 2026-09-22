"""Production-safe error envelope.

Every non-2xx response carries:
- a STABLE machine error code (never a stack trace or internal path);
- a safe human message;
- the request/correlation id so the operator can find the detailed internal
  log line for the same failure.

Detailed diagnostics go to the server log only, redacted. Legacy `detail`
is preserved in the body so V8.4.4 clients and tests keep working.
"""
from __future__ import annotations

from typing import Any

# Stable error codes. Add — never repurpose.
E_VALIDATION = "VALIDATION_ERROR"
E_NOT_FOUND = "NOT_FOUND"
E_UNAUTHENTICATED = "UNAUTHENTICATED"
E_FORBIDDEN = "FORBIDDEN"
E_CSRF = "CSRF_REJECTED"
E_RATE_LIMITED = "RATE_LIMITED"
E_PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
E_CONFLICT = "CONFLICT"
E_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
E_INTERNAL = "INTERNAL_ERROR"

_STATUS_TO_CODE = {
    400: E_VALIDATION,
    401: E_UNAUTHENTICATED,
    403: E_FORBIDDEN,
    404: E_NOT_FOUND,
    409: E_CONFLICT,
    413: E_PAYLOAD_TOO_LARGE,
    422: E_VALIDATION,
    429: E_RATE_LIMITED,
    500: E_INTERNAL,
    503: E_UNAVAILABLE,
}

_SAFE_DEFAULT_MESSAGES = {
    E_UNAUTHENTICATED: "Authentication is required.",
    E_FORBIDDEN: "You do not have permission to do that.",
    E_NOT_FOUND: "Object not found.",
    E_RATE_LIMITED: "Too many requests. Please slow down.",
    E_PAYLOAD_TOO_LARGE: "The request body is too large.",
    E_INTERNAL: "An internal error occurred. It has been recorded.",
    E_UNAVAILABLE: "A required dependency is unavailable.",
    E_CSRF: "The request failed CSRF validation.",
}


def error_body(status: int, message: str | None, request_id: str | None,
               code: str | None = None) -> dict[str, Any]:
    resolved_code = code or _STATUS_TO_CODE.get(status, E_INTERNAL)
    safe_message = message or _SAFE_DEFAULT_MESSAGES.get(
        resolved_code, "The request could not be completed.")
    body: dict[str, Any] = {
        "error": {"code": resolved_code, "message": safe_message},
        # Back-compat: every established client/test reads `detail`.
        "detail": safe_message,
    }
    if request_id:
        body["error"]["request_id"] = request_id
        body["request_id"] = request_id
    return body
