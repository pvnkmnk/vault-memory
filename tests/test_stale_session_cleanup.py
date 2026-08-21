"""Regression tests for stale session cleanup (VAU-34 / S28-1)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from daemon.heartbeat import cleanup_stale_sessions


class _FakeCursor:
    def __init__(self, rowcount: int = 0):
        self.rowcount = rowcount
        self.queries = []
        self.params = []

    def execute(self, query, params=()):
        self.queries.append(query)
        self.params.append(params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePostgres:
    def __init__(self, cursor):
        self._cursor = cursor

    @contextmanager
    def cursor(self):
        yield self._cursor


@pytest.mark.asyncio
async def test_cleanup_stale_sessions_closes_old_sessions():
    cursor = _FakeCursor(rowcount=3)
    postgres = _FakePostgres(cursor)

    result = await cleanup_stale_sessions(postgres, max_age_hours=24)

    assert result == 3
    assert len(cursor.queries) == 1
    query = cursor.queries[0]
    assert "UPDATE agent_sessions" in query
    assert "SET status = 'closed'" in query
    assert "started_at < now()" in query
    assert "last_ping_at < now()" in query
    assert cursor.params[0] == (24, 24)
    assert "registered_at" not in query


@pytest.mark.asyncio
async def test_cleanup_stale_sessions_uses_custom_max_age():
    cursor = _FakeCursor(rowcount=0)
    postgres = _FakePostgres(cursor)

    await cleanup_stale_sessions(postgres, max_age_hours=72)

    assert cursor.params[0] == (72, 72)


@pytest.mark.asyncio
async def test_cleanup_stale_sessions_returns_zero_when_postgres_missing_cursor():
    postgres = MagicMock()
    del postgres.cursor

    result = await cleanup_stale_sessions(postgres, max_age_hours=24)

    assert result == 0


@pytest.mark.asyncio
async def test_cleanup_stale_sessions_returns_zero_on_exception():
    class _BrokenPostgres:
        @contextmanager
        def cursor(self):
            raise RuntimeError("connection lost")
            yield  # pragma: no cover

    result = await cleanup_stale_sessions(_BrokenPostgres(), max_age_hours=24)

    assert result == 0


def test_sessions_cleanup_endpoint_uses_max_age_hours(mock_dependencies):
    """POST /sessions/cleanup accepts max_age_hours and returns closed IDs."""
    from fastapi.testclient import TestClient
    from daemon.main import app, get_dependencies

    rows = [
        {"id": "11111111-1111-1111-1111-111111111111"},
        {"id": "22222222-2222-2222-2222-222222222222"},
    ]
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=False)
    mock_dependencies.postgres.cursor.return_value.__enter__ = MagicMock(
        return_value=cursor
    )
    mock_dependencies.postgres.cursor.return_value.__exit__ = MagicMock(
        return_value=False
    )

    app.dependency_overrides[get_dependencies] = lambda: mock_dependencies
    try:
        with patch("daemon.main.lifespan", MagicMock()):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/sessions/cleanup",
                json={"max_age_hours": 12},
                headers={"x-api-key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["closed"] == 2
    assert data["session_ids"] == [str(r["id"]) for r in rows]
    cursor.execute.assert_called_once()
    call_args = cursor.execute.call_args
    assert call_args[0][1] == (12,)


def test_sessions_cleanup_endpoint_rejects_invalid_max_age(mock_dependencies):
    """POST /sessions/cleanup rejects invalid max_age_hours."""
    from fastapi.testclient import TestClient
    from daemon.main import app, get_dependencies

    app.dependency_overrides[get_dependencies] = lambda: mock_dependencies
    try:
        with patch("daemon.main.lifespan", MagicMock()):
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/sessions/cleanup",
                json={"max_age_hours": 0},
                headers={"x-api-key": "test-key"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_memory_session_cleanup_mcp_tool():
    """memory/session_cleanup MCP tool calls /sessions/cleanup with max_age_hours."""
    from cli.tools.sessions import _memory_session_cleanup

    closed_ids = ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]
    with patch("cli.tools.sessions.httpx.post") as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"closed": 1, "session_ids": closed_ids}
        mock_post.return_value = mock_response

        result = _memory_session_cleanup(
            {"max_age_hours": 48, "daemon_url": "http://localhost:5051"},
            "http://localhost:5051",
        )

    assert result["closed"] == 1
    assert result["session_ids"] == closed_ids
    assert result["max_age_hours"] == 48
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert call_args[0][0] == "http://localhost:5051/sessions/cleanup"
    assert call_args[1]["json"] == {"max_age_hours": 48}
