"""
Production hardening for the HTTP transport: API-key auth, per-key token-bucket
rate limiting, and in-memory usage metering.

This is deliberately dependency-light (pure ASGI) so it can wrap the MCP
streamable-HTTP Starlette app without pulling in a web framework opinion.

Environment
-----------
* ``PRECISIONCALC_API_KEYS``  Comma-separated allowed keys. If EMPTY, the server
  runs in **open mode** (no auth) but still meters + rate-limits per client IP.
* ``PRECISIONCALC_RATE_LIMIT_PER_MIN``  Sustained requests/min (default 120).
* ``PRECISIONCALC_RATE_LIMIT_BURST``    Bucket capacity / burst (default 40).
* ``PRECISIONCALC_METRICS_PATH``        Path serving usage metrics JSON
  (default ``/metrics``; requires a valid key when keys are configured).

    # MONETIZATION NOTE: swap the in-memory bucket + meter for Redis to scale
    # horizontally and to bill per key. The interface below is the seam.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from observability import log_event

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


class TokenBucket:
    """Classic token-bucket limiter. Thread-safe."""

    def __init__(self, rate_per_min: float, burst: float):
        self.rate_per_sec = rate_per_min / 60.0
        self.capacity = burst
        self.tokens = burst
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def allow(self, cost: float = 1.0) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate_per_sec)
            self.updated = now
            if self.tokens >= cost:
                self.tokens -= cost
                return True
            return False


class UsageMeter:
    """In-memory per-identity usage counters."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_requests = 0
        self.total_rejected = 0
        self.by_identity: dict[str, int] = defaultdict(int)
        self.rejected_by_identity: dict[str, int] = defaultdict(int)
        self.started_at = time.time()

    def record(self, identity: str, allowed: bool) -> None:
        with self._lock:
            self.total_requests += 1
            self.by_identity[identity] += 1
            if not allowed:
                self.total_rejected += 1
                self.rejected_by_identity[identity] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "total_requests": self.total_requests,
                "total_rejected": self.total_rejected,
                "by_identity": dict(self.by_identity),
                "rejected_by_identity": dict(self.rejected_by_identity),
            }


class AuthRateLimitMiddleware:
    """Pure-ASGI middleware: API-key auth + rate limit + metering + /metrics."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app
        keys = os.getenv("PRECISIONCALC_API_KEYS", "").strip()
        self.api_keys = {k.strip() for k in keys.split(",") if k.strip()}
        self.require_auth = bool(self.api_keys)
        self.rate_per_min = float(os.getenv("PRECISIONCALC_RATE_LIMIT_PER_MIN", "120"))
        self.burst = float(os.getenv("PRECISIONCALC_RATE_LIMIT_BURST", "40"))
        self.metrics_path = os.getenv("PRECISIONCALC_METRICS_PATH", "/metrics")
        self.meter = UsageMeter()
        self._buckets: dict[str, TokenBucket] = {}
        self._buckets_lock = threading.Lock()
        log_event("http_security_configured", auth_required=self.require_auth,
                  rate_per_min=self.rate_per_min, burst=self.burst)

    def _bucket(self, identity: str) -> TokenBucket:
        with self._buckets_lock:
            b = self._buckets.get(identity)
            if b is None:
                b = TokenBucket(self.rate_per_min, self.burst)
                self._buckets[identity] = b
            return b

    @staticmethod
    def _header(scope: Scope, name: bytes) -> str | None:
        for k, v in scope.get("headers", []):
            if k == name:
                return v.decode("latin-1")
        return None

    def _identify(self, scope: Scope) -> tuple[str | None, bool]:
        """Return (identity, authorized)."""
        key = self._header(scope, b"x-api-key")
        if not key:
            auth = self._header(scope, b"authorization")
            if auth and auth.lower().startswith("bearer "):
                key = auth[7:].strip()
        if self.require_auth:
            if key and key in self.api_keys:
                return f"key:{key[:6]}\u2026", True
            return None, False
        # open mode -> identify by client IP
        client = scope.get("client")
        ip = client[0] if client else "anon"
        return f"ip:{ip}", True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        identity, authorized = self._identify(scope)

        if path == self.metrics_path:
            if self.require_auth and not authorized:
                await _json(send, 401, {"status": "error",
                                        "error": {"type": "unauthorized",
                                                  "message": "Valid API key required."}})
                return
            await _json(send, 200, {"status": "success", "usage": self.meter.snapshot()})
            return

        if not authorized:
            self.meter.record("unauthorized", allowed=False)
            log_event("auth_rejected", path=path, level=30)
            await _json(send, 401, {"status": "error",
                                    "error": {"type": "unauthorized",
                                              "message": "Missing or invalid API key.",
                                              "hint": "Send 'X-API-Key: <key>' or 'Authorization: Bearer <key>'."}})
            return

        allowed = self._bucket(identity).allow()
        self.meter.record(identity, allowed)
        if not allowed:
            log_event("rate_limited", identity=identity, path=path, level=30)
            await _json(send, 429, {"status": "error",
                                    "error": {"type": "rate_limited",
                                              "message": "Rate limit exceeded.",
                                              "hint": f"Limit ~{self.rate_per_min:.0f}/min. Retry shortly."}},
                        extra_headers=[(b"retry-after", b"2")])
            return

        await self.app(scope, receive, send)


async def _json(send: Send, status: int, body: dict[str, Any],
                extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    data = json.dumps(body).encode("utf-8")
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(data)).encode())]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": data})
