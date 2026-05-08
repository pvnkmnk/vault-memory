"""Regression tests for session cleanup behavior."""

import importlib
import sys
import types
from pathlib import Path

import pytest


_DAEMON_DIR = Path(__file__).resolve().parents[1] / "daemon"
if "daemon" not in sys.modules:
    daemon_pkg = types.ModuleType("daemon")
    daemon_pkg.__path__ = [str(_DAEMON_DIR)]
    sys.modules["daemon"] = daemon_pkg

# Avoid importing daemon/__init__.py during tests; it imports optional modules.
cleanup_stale_sessions = importlib.import_module("daemon.heartbeat").cleanup_stale_sessions

if "daemon.routes" not in sys.modules:
    routes_pkg = types.ModuleType("daemon.routes")
    routes_pkg.__path__ = [str(_DAEMON_DIR / "routes")]
    sys.modules["daemon.routes"] = routes_pkg
sessions_cleanup = importlib.import_module("daemon.routes.sessions").sessions_cleanup


class _FakeCursor:
    def __init__(self, rowcount=0, rows=None):
        self.rowcount = rowcount
        self._rows = rows or []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self._rows


class _CursorContext:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakePostgres:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return _CursorContext(self._cursor)


@pytest.mark.asyncio
async def test_cleanup_stale_sessions_uses_started_or_last_ping():
    """Ensure cleanup query references real schema columns and updates stale sessions."""
    fake_cursor = _FakeCursor(rowcount=3)
    fake_db = _FakePostgres(fake_cursor)

    closed_count = await cleanup_stale_sessions(fake_db, max_age_hours=12)

    assert closed_count == 3
    assert "registered_at" not in fake_cursor.sql.lower()
    assert "started_at" in fake_cursor.sql.lower()
    assert "last_ping_at" in fake_cursor.sql.lower()
    assert fake_cursor.params == (12,)


@pytest.mark.asyncio
async def test_cleanup_stale_sessions_returns_zero_without_cursor():
    """Cleanup should no-op when backend does not expose cursor API."""
    closed_count = await cleanup_stale_sessions(object(), max_age_hours=24)
    assert closed_count == 0


@pytest.mark.asyncio
async def test_sessions_cleanup_route_closes_rows_and_returns_ids():
    """Route-level cleanup returns count and ids for closed sessions."""
    fake_cursor = _FakeCursor(
        rows=[{"id": "abc"}, {"id": "def"}],
    )
    deps = type("Deps", (), {"postgres": _FakePostgres(fake_cursor)})()

    response = await sessions_cleanup(deps=deps, _auth="test-key")

    assert response["closed"] == 2
    assert response["session_ids"] == ["abc", "def"]
    assert "started_at < now() - interval '24 hours'" in fake_cursor.sql
