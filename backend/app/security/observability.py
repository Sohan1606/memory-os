"""Production observability: metrics, structured logs, request correlation.

Design rules (handoff §OBSERVABILITY / §PRIVACY):
- metrics are COUNTS and LATENCIES only. Label values are route templates,
  status codes and coarse categories — never user content, memory text,
  email addresses, tokens or namespaces;
- every request gets an X-Request-ID; handlers and error responses echo it;
- log records are single-line JSON when LOG_JSON=1, or standard logging
  otherwise, and are redacted through `redact` before emission.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict
from typing import Any

# --------------------------------------------------------------- redaction
# Applied to every diagnostic string that might travel to logs or error
# payloads. Matches common secret shapes; deliberately aggressive.
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|authorization|bearer)"
               r"([\"'\s:=]+)([^\s\"',;&]{4,})"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
)


def redact(text: str) -> str:
    out = text
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
    out = _SECRET_PATTERNS[1].sub("[REDACTED]", out)
    return out


class JsonLogFormatter(logging.Formatter):
    """Single-line JSON logs for production pipelines. Redacts messages."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(json_logs: bool) -> None:
    if not json_logs:
        return
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.handlers = [handler]


# ----------------------------------------------------------------- metrics
class Metrics:
    """In-process metrics registry (counters + latency histograms).

    Honest scope: this is single-process observability exported as JSON via
    the API — not a claim of a Prometheus deployment. Values reset on process
    restart and say so in the snapshot.
    """

    _LATENCY_BUCKETS_MS = (25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._counters: dict[str, int] = defaultdict(int)
        # route -> [count, total_ms, max_ms, bucket_counts]
        self._latency: dict[str, list[Any]] = {}

    # counters ----------------------------------------------------------
    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def count(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    # request accounting -------------------------------------------------
    def observe_request(self, route: str, method: str, status: int,
                        elapsed_ms: float) -> None:
        key = f"{method} {route}"
        with self._lock:
            self._counters["http.requests_total"] += 1
            self._counters[f"http.status.{status // 100}xx"] += 1
            if status == 401:
                self._counters["security.auth_failures"] += 1
            if status == 403:
                self._counters["security.authorization_denied"] += 1
            if status == 429:
                self._counters["security.rate_limited"] += 1
            if status >= 500:
                self._counters["http.errors_5xx"] += 1
            entry = self._latency.get(key)
            if entry is None:
                entry = [0, 0.0, 0.0, [0] * (len(self._LATENCY_BUCKETS_MS) + 1)]
                self._latency[key] = entry
            entry[0] += 1
            entry[1] += elapsed_ms
            entry[2] = max(entry[2], elapsed_ms)
            for i, bound in enumerate(self._LATENCY_BUCKETS_MS):
                if elapsed_ms <= bound:
                    entry[3][i] += 1
                    break
            else:
                entry[3][-1] += 1

    # snapshot ------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = {}
            for key, (count, total, peak, buckets) in sorted(self._latency.items()):
                routes[key] = {
                    "count": count,
                    "avg_ms": round(total / count, 2) if count else 0.0,
                    "max_ms": round(peak, 2),
                    "buckets_ms": dict(zip(
                        [str(b) for b in self._LATENCY_BUCKETS_MS] + ["+inf"], buckets)),
                }
            return {
                "scope": "single-process, in-memory; resets on restart",
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "counters": dict(sorted(self._counters.items())),
                "requests": routes,
            }


# One registry per process; created by Runtime so tests can replace it.
metrics = Metrics()
