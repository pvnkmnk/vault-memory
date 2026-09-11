# tests/test_s24_reopened.py
"""Regression tests for the four reopened S24 issues (2026-09 triage).

- VAU-16 (S24-P2): refresh_topic_hubs must upsert/delete incrementally — never
  TRUNCATE (which left topic_hubs briefly empty for concurrent readers).
- VAU-12 (S24-A3): CLI sync path must build services through the CLI DI
  container (cli/dependencies.py) instead of importing clients inline.
- VAU-14 (S24-A5): RateLimitMiddleware must expose counters via get_metrics()
  and /metrics must render them when app.state.rate_limiter is set.
- VAU-10 (S24-B7): AutoSyncEngine must NOT clear pendingFiles before the sync
  call and must retry with backoff, clearing only after success.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# VAU-16 — incremental topic hub refresh
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Records executed SQL; returns canned rows for the COUNT queries."""

    def __init__(self):
        self.statements = []
        self.rowcount = 0
        self.insert_rowcount = 0
        self.delete_rowcount = 0
        self.count_results = {"topic_hubs": 0, "relationships": 1}

    def execute(self, query, params=None):
        self.statements.append((query, params))
        if "TRUNCATE" in query.upper():
            raise AssertionError("TRUNCATE topic_hubs reintroduced — VAU-16 regression")
        q = query.lower()
        if "from topic_hubs" in q:
            # Real pg_client uses RealDictCursor → dict rows.
            self._row = {"n": self.count_results["topic_hubs"], "degree_sum": 0}
        elif "from relationships" in q:
            self._row = {"n": self.count_results["relationships"]}
        else:
            self._row = None
        # Per-statement rowcount: upserts report rows written, deletes report
        # rows removed (the real cursor behaves this way).
        if q.lstrip().startswith("insert into topic_hubs"):
            self.rowcount = self.insert_rowcount
        elif q.lstrip().startswith("delete from topic_hubs"):
            self.rowcount = self.delete_rowcount

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePostgres:
    def __init__(self):
        self.cursor_obj = _FakeCursor()

    def cursor(self):
        return self.cursor_obj


def test_refresh_topic_hubs_never_truncates():
    from daemon.heartbeat import refresh_topic_hubs

    pg = _FakePostgres()
    result = asyncio.run(refresh_topic_hubs(pg))
    assert result == 0  # 0 prior hubs, 1 relationship row found (degenerate path)
    sqls = [q for q, _ in pg.cursor_obj.statements]
    assert not any("TRUNCATE" in s.upper() for s in sqls)
    # The qualifying-hubs write must be an upsert against the unique vault_path.
    assert any("ON CONFLICT (vault_path) DO UPDATE" in q for q in sqls)


def test_refresh_topic_hubs_upserts_qualifying_hubs():
    from daemon.heartbeat import refresh_topic_hubs

    pg = _FakePostgres()
    pg.cursor_obj.count_results = {"topic_hubs": 0, "relationships": 10}
    pg.cursor_obj.insert_rowcount = 3  # 3 qualifying hubs upserted
    pg.cursor_obj.delete_rowcount = 0  # nothing fell below the threshold
    result = asyncio.run(refresh_topic_hubs(pg, min_in_degree=4))
    assert result == 3
    sqls = [q for q, _ in pg.cursor_obj.statements]
    upserts = [q for q in sqls if "INSERT INTO topic_hubs" in q]
    deletes = [q for q in sqls if q.lstrip().startswith("DELETE FROM topic_hubs")]
    assert len(upserts) == 1
    assert len(deletes) == 1


def test_refresh_topic_hubs_handles_db_errors():
    from daemon.heartbeat import refresh_topic_hubs

    pg = MagicMock()
    pg.cursor.side_effect = RuntimeError("db down")
    # Must not raise — heartbeat is a background job.
    assert asyncio.run(refresh_topic_hubs(pg)) == 0


# ---------------------------------------------------------------------------
# VAU-12 — CLI DI container
# ---------------------------------------------------------------------------


def test_cli_dependencies_lazy_service_construction():
    from cli.dependencies import CliDependencies

    calls = []
    deps = CliDependencies(
        weaviate_url="http://x",
        pg_conn_str="db",
        embedding_model="m",
        reranker_model="r",
        weaviate_factory=lambda url: (calls.append("weaviate"), f"w:{url}")[1],
        postgres_factory=lambda cs: (calls.append("postgres"), f"p:{cs}")[1],
        embedder_factory=lambda em, rm: (calls.append("embedder"), f"e:{em}")[1],
        engine_factory=lambda vp, w, p, e: (calls.append("engine"), f"s:{vp}:{w}:{p}:{e}")[1],
    )
    # Nothing constructed yet.
    assert calls == []
    engine = deps.engine
    # Engine construction forces all four services, in dependency order.
    assert calls == ["weaviate", "postgres", "embedder", "engine"]
    assert engine.startswith("s:")
    # Same instances are reused on repeat access.
    assert deps.engine is engine


def test_cli_dependencies_close_is_idempotent():
    from cli.dependencies import CliDependencies

    closed = []

    class Closable:
        def close(self):
            closed.append(1)

    deps = CliDependencies(
        weaviate_factory=lambda url: Closable(),
        postgres_factory=lambda cs: Closable(),
        engine_factory=lambda vp, w, p, e: object(),
    )
    _ = deps.weaviate
    _ = deps.postgres
    deps.close()
    deps.close()
    assert len(closed) == 2  # once per closable, not duplicated
    # After close, properties rebuild lazily.
    deps.close()


def test_sync_command_no_longer_imports_service_classes_inline():
    """S24-A3: sync_command.py must build services via the DI factory, not import
    WeaviateClient/PostgresClient/EmbedderService/SyncEngine directly."""
    from pathlib import Path

    source = Path("cli/sync_command.py").read_text(encoding="utf-8")
    for banned in (
        "from daemon.weaviate_client import WeaviateClient",
        "from daemon.pg_client import PostgresClient",
        "from daemon.embedder import EmbedderService",
        "from daemon.sync_watcher import SyncEngine",
    ):
        assert banned not in source, f"sync_command.py regressed: {banned}"
    assert "build_cli_dependencies" in source


# ---------------------------------------------------------------------------
# VAU-14 — rate limiter metrics
# ---------------------------------------------------------------------------


def _client_with_limiter(app, limiter):
    from fastapi.testclient import TestClient
    from starlette.middleware.base import BaseHTTPMiddleware

    app.state.rate_limiter = limiter

    class _Wrapper(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await limiter.dispatch(request, call_next)

    app.add_middleware(_Wrapper)
    return TestClient(app)


def test_rate_limiter_get_metrics_counts_hits_and_blocks():
    from daemon.middleware.rate_limiter import RateLimitMiddleware

    limiter = RateLimitMiddleware(None, requests_per_minute=1, burst_size=1)
    assert limiter.get_metrics() == {
        "rate_limiter_keys_current": 0,
        "rate_limiter_hits_total": 0,
        "rate_limiter_blocked_total": 0,
        "rate_limiter_evictions_total": 0,
    }

    # Simulate dispatch directly: one admitted, one blocked.
    request = MagicMock()
    request.headers = {"x-api-key": "k1"}
    request.client.host = "1.2.3.4"
    request.url.path = "/search"

    async def call_next(req):
        from starlette.responses import Response

        return Response("ok")

    admitted = asyncio.run(limiter.dispatch(request, call_next))
    blocked = asyncio.run(limiter.dispatch(request, call_next))

    assert admitted.status_code == 200
    assert blocked.status_code == 429

    m = limiter.get_metrics()
    assert m["rate_limiter_hits_total"] == 1
    assert m["rate_limiter_blocked_total"] == 1
    assert m["rate_limiter_keys_current"] == 1
    assert m["rate_limiter_evictions_total"] == 0


def test_metrics_endpoint_renders_rate_limiter_series():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from daemon.health import router as health_router
    from daemon.middleware.rate_limiter import RateLimitMiddleware

    app = FastAPI()
    app.include_router(health_router)
    client = _client_with_limiter(app, RateLimitMiddleware(None, requests_per_minute=60, burst_size=20))

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert "vault_memory_rate_limiter_keys_current" in body
    assert "vault_memory_rate_limiter_hits_total" in body
    assert "vault_memory_rate_limiter_blocked_total" in body
    assert "vault_memory_rate_limiter_evictions_total" in body


def test_metrics_endpoint_works_without_rate_limiter_in_state():
    """Older apps without app.state.rate_limiter must still render /metrics."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from daemon.health import router as health_router

    app = FastAPI()  # no rate_limiter on state
    app.include_router(health_router)
    resp = TestClient(app).get("/metrics")
    assert resp.status_code == 200
    assert "vault_memory_rate_limiter_keys_current" not in resp.text


# ---------------------------------------------------------------------------
# VAU-10 — AutoSyncEngine queue preservation + retry
# ---------------------------------------------------------------------------


def test_autosync_source_keeps_files_queued_until_success():
    """S24-B7 (VAU-10): source must not clear pendingFiles before the sync call."""
    from pathlib import Path

    source = Path(
        "obsidian-plugin/src/components/AutoSyncEngine.ts"
    ).read_text(encoding="utf-8")
    # The pre-fix bug: pendingFiles.clear() immediately after Array.from(...)
    assert "const filesToSync = Array.from(this.pendingFiles);\n    this.pendingFiles.clear();" not in source
    # Retry contract present.
    assert "maxAttempts = 3" in source
    # Queue is only drained on success.
    assert "this.pendingFiles.delete(f);" in source


def test_autosync_retry_backoff_durations():
    source_lines = open(
        "obsidian-plugin/src/components/AutoSyncEngine.ts", encoding="utf-8"
    ).read()
    # 1s, 2s, 4s exponential backoff: 1000 * 2**(attempt-1)
    assert "1000 * Math.pow(2, attempt - 1)" in source_lines
