"""Bounded in-process rate limiting (sliding window).

Scope is honest: one process, in-memory counters, keyed by principal (or
client address for unauthenticated auth attempts). This protects the
endpoints the handoff names — auth, general APIs, research, portability and
expensive cognition — and every violation is observable: a counter increments
and a `security.rate_limited` audit event is emitted by the caller.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

# Category → settings attribute (per-minute limits).
CATEGORIES = {
    "auth": "rate_limit_auth_per_minute",
    "api": "rate_limit_api_per_minute",
    "research": "rate_limit_research_per_minute",
    "portability": "rate_limit_portability_per_minute",
    "expensive": "rate_limit_expensive_per_minute",
}

_WINDOW_SECONDS = 60.0
# Bound per-key memory: no key ever stores more timestamps than the largest
# limit we could enforce.
_MAX_EVENTS_PER_KEY = 2000


class RateLimiter:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def limit_for(self, category: str) -> int:
        attr = CATEGORIES.get(category, CATEGORIES["api"])
        return max(1, int(getattr(self.settings, attr)))

    def check(self, key: str, category: str) -> tuple[bool, int, int]:
        """Record one hit. Returns (allowed, limit, remaining)."""
        limit = self.limit_for(category)
        if not self.settings.rate_limit_enabled:
            return True, limit, limit
        now = time.monotonic()
        bucket_key = f"{category}:{key}"
        with self._lock:
            window = self._events[bucket_key]
            cutoff = now - _WINDOW_SECONDS
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= limit:
                return False, limit, 0
            if len(window) < _MAX_EVENTS_PER_KEY:
                window.append(now)
            return True, limit, limit - len(window)

    def state(self) -> dict:
        """Observable limiter state (counts only — no identifying keys)."""
        with self._lock:
            active = sum(1 for w in self._events.values() if w)
        return {
            "enabled": bool(self.settings.rate_limit_enabled),
            "window_seconds": _WINDOW_SECONDS,
            "limits_per_minute": {c: self.limit_for(c) for c in CATEGORIES},
            "active_buckets": active,
        }
