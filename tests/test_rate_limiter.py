# tests/test_rate_limiter.py
"""Tests for the rate limiting middleware and /me/usage endpoint."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from daemon.middleware.rate_limiter import RateLimitMiddleware
from daemon.routes.usage import usage_router


@pytest.fixture(autouse=True)
def _ensure_no_leaked_api_key(monkeypatch):
    """Clear any API key leaked by other test modules so rate-limiter tests run in dev mode."""
    monkeypatch.delenv("VAULT_MEMORY_API_KEY", raising=False)


def _make_rate_limiter_middleware(rate_limiter_instance: RateLimitMiddleware):
    """Return a middleware class wrapping a shared RateLimitMiddleware instance."""
    class _RateLimitMiddlewareWrapper(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            return await rate_limiter_instance.dispatch(request, call_next)

    return _RateLimitMiddlewareWrapper


@pytest.fixture
def app_with_rate_limiter():
    """Create a FastAPI app with rate limiting and the usage route."""
    app = FastAPI()
    rate_limiter = RateLimitMiddleware(None, requests_per_minute=60, burst_size=20)
    app.state.rate_limiter = rate_limiter

    wrapper_cls = _make_rate_limiter_middleware(rate_limiter)
    app.add_middleware(wrapper_cls)
    app.include_router(usage_router)
    return app


@pytest.fixture
def client(app_with_rate_limiter):
    return TestClient(app_with_rate_limiter)


def test_usage_returns_current_request_count(client):
    """Usage endpoint reflects the current request, including the /me/usage call itself."""
    response = client.get("/me/usage", headers={"x-api-key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    # The /me/usage request itself is counted by the middleware
    assert data["requests_this_minute"] == 1
    assert data["requests_today"] == 1
    assert data["quota"] == 60


def test_usage_tracks_requests_through_middleware(client):
    """/me/usage must reflect requests handled by the active middleware instance."""
    # Make three successful requests through the middleware
    for _ in range(3):
        response = client.get("/me/usage", headers={"x-api-key": "test-key"})
        assert response.status_code == 200

    response = client.get("/me/usage", headers={"x-api-key": "test-key"})
    assert response.status_code == 200
    data = response.json()
    # 4 total requests to /me/usage in this test, all within the last minute
    assert data["requests_this_minute"] >= 4
    assert data["requests_today"] >= 4


def test_rate_limit_429_when_exceeding_window():
    """Middleware blocks requests once the per-minute window is exceeded."""
    app = FastAPI()
    rate_limiter = RateLimitMiddleware(None, requests_per_minute=2, burst_size=10)
    app.state.rate_limiter = rate_limiter

    wrapper_cls = _make_rate_limiter_middleware(rate_limiter)
    app.add_middleware(wrapper_cls)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app)

    # First two requests succeed
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 200
    # Third request is rate limited
    response = client.get("/ping")
    assert response.status_code == 429
    assert response.json()["code"] == "RATE_LIMIT_WINDOW"


def test_usage_uses_app_state_rate_limiter():
    """The usage endpoint reads from app.state.rate_limiter, not a stale global instance."""
    app = FastAPI()
    live_limiter = RateLimitMiddleware(None, requests_per_minute=60, burst_size=20)

    app.state.rate_limiter = live_limiter

    wrapper_cls = _make_rate_limiter_middleware(live_limiter)
    app.add_middleware(wrapper_cls)
    app.include_router(usage_router)

    client = TestClient(app)
    # Make a request through the live middleware
    client.get("/me/usage", headers={"x-api-key": "live"})

    response = client.get("/me/usage", headers={"x-api-key": "live"})
    assert response.status_code == 200
    data = response.json()
    # If usage.py used a stale global instance, this would be 0
    assert data["requests_this_minute"] >= 1
    assert data["requests_today"] >= 1


def test_usage_falls_back_to_ip_when_no_api_key(client):
    """Usage endpoint can report stats keyed by IP when no API key is supplied."""
    # No x-api-key header; should still succeed in dev mode
    response = client.get("/me/usage")
    assert response.status_code == 200
    data = response.json()
    assert "requests_this_minute" in data
    assert "requests_today" in data
