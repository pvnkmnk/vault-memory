"""Tests for health and metrics endpoints authentication."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from daemon.main import app


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """Set VAULT_MEMORY_API_KEY for tests in this module."""
    monkeypatch.setenv("VAULT_MEMORY_API_KEY", "test-secret-key")


def test_public_probes_unauthenticated():
    """Liveness (/health) and readiness (/ready) probes remain public without API key."""
    client = TestClient(app)
    res_health = client.get("/health")
    assert res_health.status_code == 200

    res_ready = client.get("/ready")
    assert res_ready.status_code == 200


def test_sensitive_endpoints_require_api_key():
    """/health/detailed and /metrics require valid x-api-key header when configured."""
    client = TestClient(app)

    # 1. Unauthenticated requests should return 401
    assert client.get("/health/detailed").status_code == 401
    assert client.get("/metrics").status_code == 401

    # 2. Requests with invalid API key should return 401
    invalid_headers = {"x-api-key": "invalid-key"}
    assert client.get("/health/detailed", headers=invalid_headers).status_code == 401
    assert client.get("/metrics", headers=invalid_headers).status_code == 401

    # 3. Requests with valid API key should succeed (200)
    valid_headers = {"x-api-key": "test-secret-key"}
    res_detailed = client.get("/health/detailed", headers=valid_headers)
    assert res_detailed.status_code == 200
    assert "subsystems" in res_detailed.json()

    res_metrics = client.get("/metrics", headers=valid_headers)
    assert res_metrics.status_code == 200
    assert "vault_memory_requests_total" in res_metrics.text
