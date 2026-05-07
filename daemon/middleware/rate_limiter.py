# daemon/middleware/rate_limiter.py
"""Rate limiting middleware with per-client tracking."""

import asyncio
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiting middleware.

    Uses sliding window algorithm with configurable limits per endpoint.
    S26-4: Tracks by API key for per-client rate limiting.
    """

    def __init__(self, app, requests_per_minute: int = 60, burst_size: int = 10):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self._requests: Dict[Tuple[str, str], list] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._daily_counts: Dict[str, int] = defaultdict(int)
        self._daily_reset_at = self._next_daily_reset()

    @staticmethod
    def _next_daily_reset() -> float:
        """Return timestamp of next midnight UTC."""
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return tomorrow.timestamp()

    def _get_client_key(self, request: Request) -> str:
        """Extract API key or fall back to IP (S26-4)."""
        api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
        if api_key:
            return f"key:{api_key[:8]}"
        return f"ip:{request.client.host if request.client else 'unknown'}"

    async def dispatch(self, request: Request, call_next):
        client_key = self._get_client_key(request)
        endpoint = f"{request.method}:{request.url.path}"
        key = (client_key, endpoint)

        now = time.time()

        if now >= self._daily_reset_at:
            async with self._lock:
                self._daily_counts.clear()
            self._daily_reset_at = self._next_daily_reset()

        async with self._lock:
            self._daily_counts[client_key] = self._daily_counts.get(client_key, 0) + 1

        window_start = now - 60
        burst_window_start = now - 2.0

        async with self._lock:
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > window_start
            ]

            recent_burst = [ts for ts in self._requests[key] if ts > burst_window_start]
            if len(recent_burst) >= self.burst_size:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Burst limit exceeded",
                        "code": "RATE_LIMIT_BURST",
                    },
                )

            if len(self._requests[key]) >= self.requests_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Rate limit exceeded",
                        "code": "RATE_LIMIT_WINDOW",
                    },
                )

            self._requests[key].append(now)

            if random.random() < 0.01:
                cutoff = now - 300
                stale_keys = [
                    k for k, v in self._requests.items() if not v or v[-1] < cutoff
                ]
                for k in stale_keys:
                    del self._requests[k]

        response = await call_next(request)
        async with self._lock:
            remaining = max(0, self.requests_per_minute - len(self._requests[key]))
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    def get_usage(self, client_key: str) -> dict:
        """Get usage stats for a client (S26-4)."""
        now = time.time()
        window_start = now - 60
        total_minute = 0
        for (key, _endpoint), timestamps in self._requests.items():
            if key == client_key:
                total_minute += sum(1 for ts in timestamps if ts > window_start)

        return {
            "requests_today": self._daily_counts.get(client_key, 0),
            "requests_this_minute": total_minute,
            "quota": self.requests_per_minute,
            "reset_at": datetime.fromtimestamp(
                self._daily_reset_at, tz=timezone.utc
            ).isoformat(),
        }


# Default rate limiter instance (60 req/min, burst of 20)
rate_limiter = RateLimitMiddleware(None, requests_per_minute=60, burst_size=20)
