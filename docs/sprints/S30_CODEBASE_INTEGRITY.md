# S30 — Codebase Integrity Sprint

**Date:** 2026-04-30
**Status:** Planned
**Type:** Quality & Architecture Sprint
**Goal:** Fix the gap between documented claims and actual code, reduce technical debt, and make the codebase maintainable for v1.0.

---

## Why This Sprint Exists

S24 was marked ✅ DONE with ~67% of items incomplete. S25-S29 built on top of an unstable foundation. This sprint fixes the foundation before v0.9.0 features.

**Principle:** No new features until the codebase is honest about what it delivers.

---

## Sprint Structure

| Track | Focus | Items | Priority |
|-------|-------|-------|----------|
| **T1** | Foundation | PostgresClient consolidation, README version | P0 |
| **T2** | Architecture | Split main.py monolith | P0 |
| **T3** | Missing S24 | Circuit breaker, pool metrics, slow queries, SQLite error, AutoSync fix, centrality optimization | P1 |
| **T4** | Reliability | Bulk queue persistence, rate limiter persistence, lite mode validation | P1 |
| **T5** | Testing | Integration test suite | P2 |

---

## T1: Foundation (Day 1)

### S30-1: Consolidate PostgresClient Classes

**Problem:** Three `PostgresClient` classes confuse contributors and create maintenance burden.

**Current state:**
| File | Class | Purpose |
|------|-------|---------|
| `daemon/pg_client.py` | `PostgresClient` | Original pooled client with `cursor()` context manager |
| `daemon/backends/postgres_client.py` | `PostgresClient(PostgresBackend)` | Backend abstraction layer |
| `daemon/dependencies.py` | `PostgresClient(Protocol)` | Type hint protocol |

**Implementation:**
1. Keep `daemon/pg_client.py:PostgresClient` as the canonical implementation
2. Delete `daemon/backends/postgres_client.py` entirely
3. Keep `daemon/dependencies.py:PostgresClient` as Protocol (it's correct)
4. Update all imports across the codebase:
   - `from daemon.backends.postgres_client import PostgresClient` → `from daemon.pg_client import PostgresClient`
   - `from daemon.backends import postgres_client` → remove
5. Delete `daemon/backends/` directory if empty after migration
6. Verify no references to `PostgresBackend` remain

**Files affected:**
- `daemon/pg_client.py` (canonical, may need minor updates)
- `daemon/backends/postgres_client.py` (DELETE)
- `daemon/backends/__init__.py` (UPDATE or DELETE)
- `daemon/dependencies.py` (UPDATE imports)
- `daemon/main.py` (UPDATE imports)
- `daemon/heartbeat.py` (UPDATE imports)
- `daemon/retrieval.py` (UPDATE imports)
- `daemon/sync_watcher.py` (UPDATE imports)
- `daemon/canvas_graph_pipeline.py` (UPDATE imports)
- `daemon/validate_write.py` (UPDATE imports)
- `daemon/health.py` (UPDATE imports)

**Verification:**
```bash
grep -r "from daemon.backends" daemon/
grep -r "PostgresBackend" daemon/
python -m py_compile daemon/*.py
```

---

### S30-2: Fix Version Inconsistency

**Problem:** README.md says 0.7.0, everything else says 0.8.0.

**Implementation:**
1. Update `README.md` line 7: `**Version:** 0.8.0 — Lite Mode + VaultPortal Plugin`
2. Update `README.md` "Operational Notes" section header: `## Operational Notes (0.8.0)`
3. Add missing endpoints to README API Surface section:
   - `/sync/delta`, `/sync/file`
   - `/bulk/queue`, `/bulk/status/{job_id}`, `/bulk/cancel/{job_id}`
   - `/sessions/cleanup`, `/sessions/{id}/attribution`
   - `/health/detailed`, `/me/usage`
   - `/docs`, `/redoc`, `/openapi.json`

**Files affected:**
- `README.md`

---

## T2: Architecture — Split main.py Monolith (Day 2-3)

### S30-3: Extract Routes from main.py

**Problem:** `daemon/main.py` is 2,568 lines with routes, middleware, models, and helpers all mixed together.

**Target structure:**
```
daemon/
├── main.py              # ~200 lines: app creation, middleware, lifespan, startup
├── routes/
│   ├── __init__.py      # Export all routers
│   ├── search.py        # /search, /search_siblings
│   ├── graph.py         # /graph
│   ├── temporal.py      # /temporal
│   ├── sessions.py      # /sessions, /sessions/{id}, /sessions/cleanup, /sessions/{id}/attribution
│   ├── knowledge.py     # /cognify, /promote, /lint
│   ├── sync.py          # /sync/file, /sync/delta
│   ├── bulk.py          # /bulk/import, /bulk/export, /bulk/queue, /bulk/status, /bulk/cancel, /bulk/delete
│   └── usage.py         # /me/usage
├── middleware/
│   ├── __init__.py
│   ├── rate_limiter.py  # RateLimitMiddleware class
│   ├── audit.py         # AuditLogMiddleware class
│   ├── correlation.py   # CorrelationMiddleware class
│   └── security.py      # SecurityHeadersMiddleware class
├── models/
│   ├── __init__.py      # Export all Pydantic models
│   ├── search.py        # SearchRequest, SearchResponse
│   ├── sessions.py      # SessionRegisterRequest, SessionPatchRequest
│   ├── bulk.py          # BulkImportRequest, BulkExportRequest, BulkDeleteRequest, BulkQueueRequest
│   ├── sync.py          # SyncFileRequest, SyncDeltaRequest
│   ├── knowledge.py     # CognifyRequest, PromoteRequest, LintRequest
│   └── error.py         # ErrorResponse
└── helpers/
    ├── __init__.py
    ├── responses.py     # error_response, bad_request, server_error, success_response
    ├── validation.py    # _slugify_filename, _safe_vault_path, _parse_iso_date, _slugify_title
    └── streaming.py     # _export_stream_generator
```

**Implementation approach:**
1. Create directory structure first
2. Extract middleware classes to `daemon/middleware/`
3. Extract Pydantic models to `daemon/models/`
4. Extract helper functions to `daemon/helpers/`
5. Extract route groups to `daemon/routes/` (each as FastAPI APIRouter)
6. Update `daemon/main.py` to import and include routers:
   ```python
   from daemon.routes import search_router, graph_router, sessions_router, ...
   app.include_router(search_router, prefix="/search", tags=["search"])
   app.include_router(graph_router, tags=["graph"])
   ...
   ```
7. Verify all imports resolve correctly
8. Run full syntax check

**Key constraints:**
- Each route file must use `router = APIRouter()` pattern
- All routes keep their `Dependencies = Depends(get_dependencies)` and `verify_api_key` patterns
- No circular imports between route files
- Middleware files must not import from routes
- Models must not import from routes or middleware

**Verification:**
```bash
python -m py_compile daemon/main.py daemon/routes/*.py daemon/middleware/*.py daemon/models/*.py daemon/helpers/*.py
```

---

## T3: Missing S24 Items (Day 4-5)

### S30-4: Circuit Breaker for External Services

**Problem:** One timeout in embedder or Weaviate blocks permanently. No fallback or recovery.

**Implementation:**
```python
# daemon/circuit_breaker.py
import time
import logging
from enum import Enum
from typing import Callable, Any, Optional

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject calls
    HALF_OPEN = "half_open" # Testing recovery

class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker '%s' transitioning to HALF_OPEN", self.name)
                return True
            return False
        # HALF_OPEN: allow one test call
        return True

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker '%s' recovered to CLOSED", self.name)
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker '%s' OPEN after %d failures",
                self.name, self.failure_count
            )

    async def execute(self, fn, *args, **kwargs):
        if not self.can_execute():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' is OPEN — service unavailable"
            )
        try:
            result = await fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise

class CircuitBreakerOpenError(Exception):
    pass
```

**Wire into:**
1. `daemon/embedder.py` — wrap `embed()` calls
2. `daemon/weaviate_client.py` — wrap `query()`, `upsert()`, `delete()` calls
3. Expose circuit state in `/health/detailed` response

**Files affected:**
- `daemon/circuit_breaker.py` (NEW)
- `daemon/embedder.py` (UPDATE)
- `daemon/weaviate_client.py` (UPDATE)
- `daemon/health.py` (UPDATE — add circuit state to response)

---

### S30-5: Pool Metrics & Slow Query Diagnostics

**Problem:** No visibility into connection pool health or slow queries.

**Implementation:**

**Pool metrics in `daemon/pg_client.py`:**
```python
def get_pool_stats(self) -> dict:
    """Return connection pool statistics."""
    pool = getattr(self, '_pool', None)
    if pool is None:
        return {"status": "not_initialized"}
    return {
        "size": pool.maxconn if hasattr(pool, 'maxconn') else "unknown",
        "used": len([c for c in pool._pool if not c.closed]) if hasattr(pool, '_pool') else "unknown",
        "available": pool.maxconn - len([c for c in pool._pool if not c.closed]) if hasattr(pool, '_pool') else "unknown",
        "errors_total": getattr(self, '_error_count', 0),
    }
```

**Slow query middleware in `daemon/middleware/slow_query.py`:**
```python
SLOW_QUERY_THRESHOLD_MS = float(os.getenv("SLOW_QUERY_THRESHOLD_MS", "500"))

class SlowQueryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000
        if duration_ms > SLOW_QUERY_THRESHOLD_MS:
            logger.warning(
                "SLOW_QUERY: %s %s took %.0fms",
                request.method, request.url.path, duration_ms
            )
            increment_slow_query_count(request.url.path, duration_ms)
        return response
```

**Add `/debug/slow-queries` endpoint:**
```python
@app.get("/debug/slow-queries")
async def get_slow_queries():
    return {
        "threshold_ms": SLOW_QUERY_THRESHOLD_MS,
        "recent_slow_queries": get_recent_slow_queries(),
        "total_slow_queries": get_total_slow_query_count(),
    }
```

**Files affected:**
- `daemon/pg_client.py` (ADD `get_pool_stats`)
- `daemon/middleware/slow_query.py` (NEW)
- `daemon/main.py` (ADD endpoint, wire middleware)
- `daemon/dependencies.py` (ADD slow query tracking)

---

### S30-6: SQLite RuntimeError Handling

**Problem:** `daemon/backends/sqlite_client.py` raises bare `RuntimeError` on uninitialized connection.

**Implementation:**
```python
# daemon/backends/sqlite_client.py
class SQLiteNotInitializedError(Exception):
    """Raised when SQLite operations are attempted before initialization."""
    pass

# Replace RuntimeError raises:
def _require_initialized(self):
    if self._conn is None:
        raise SQLiteNotInitializedError("SQLite connection not initialized — call initialize() first")

# In all methods that use self._conn:
def some_method(self):
    self._require_initialized()
    # ... rest of method
```

**Wire into error handler:**
```python
# In main.py or error handler:
except SQLiteNotInitializedError as e:
    return JSONResponse(
        status_code=503,
        content={"error": "Database not initialized", "code": "DB_NOT_INITIALIZED"}
    )
```

**Files affected:**
- `daemon/backends/sqlite_client.py` (ADD exception class, replace RuntimeError)
- `daemon/main.py` (ADD exception handler)

---

### S30-7: AutoSync File Loss Fix

**Problem:** `pendingFiles.clear()` happens BEFORE the sync API call. If sync fails, files are lost.

**Current code (line 118):**
```typescript
const filesToSync = Array.from(this.pendingFiles);
this.pendingFiles.clear();  // ← CLEARS BEFORE SYNC
try {
  const result = await this.client.syncFiles(filesToSync);
```

**Fix:**
```typescript
const filesToSync = Array.from(this.pendingFiles);
// Don't clear yet — keep as backup
try {
  const result = await this.client.syncFiles(filesToSync);
  // Only clear on success
  for (const f of filesToSync) {
    this.pendingFiles.delete(f);
  }
  // ... rest of success handling
} catch (e) {
  // Files remain in pendingFiles for retry
  this.updateStatus('error', this.pendingFiles.size, 0, String(e));
}
```

**Files affected:**
- `obsidian-plugin/src/components/AutoSyncEngine.ts` (lines 117-144)

---

### S30-8: Centrality Recalculation Optimization

**Problem:** heartbeat.py uses correlated subquery — O(n²) for centrality calculation.

**Current (heartbeat.py:58):**
```sql
UPDATE temporal_entities
SET centrality = (
    SELECT COUNT(*)::float / (SELECT COUNT(*) - 1 FROM temporal_entities)
    FROM relationships r
    WHERE r.source_name = temporal_entities.entity_name
)
```

**Fix — CTE + UPDATE FROM:**
```sql
WITH degree_counts AS (
    SELECT source_name, COUNT(*) as degree
    FROM relationships
    GROUP BY source_name
),
total AS (
    SELECT COUNT(*) - 1 as denom FROM temporal_entities
)
UPDATE temporal_entities te
SET centrality = COALESCE(dc.degree, 0)::float / NULLIF(t.denom, 0)
FROM degree_counts dc, total t
WHERE te.entity_name = dc.source_name;

-- Reset entities with no relationships
UPDATE temporal_entities
SET centrality = 0
WHERE centrality IS NULL;
```

**Also fix topic hub refresh (heartbeat.py:88):**
Replace `TRUNCATE topic_hubs` + full re-insert with:
```sql
INSERT INTO topic_hubs (vault_path, entity_name, in_degree, hub_penalty, last_updated)
SELECT ...
ON CONFLICT (vault_path) DO UPDATE SET
    in_degree = EXCLUDED.in_degree,
    hub_penalty = EXCLUDED.hub_penalty,
    last_updated = now()
WHERE topic_hubs.in_degree != EXCLUDED.in_degree;

-- Remove hubs that no longer qualify
DELETE FROM topic_hubs WHERE in_degree < %s;
```

**Files affected:**
- `daemon/heartbeat.py` (UPDATE `recalc_centrality` and `refresh_topic_hubs` SQL)

---

## T4: Reliability (Day 6)

### S30-9: Bulk Queue Persistence

**Problem:** In-memory bulk jobs lost on server restart.

**Implementation:**
```python
# daemon/bulk_queue.py
import json
import os
from pathlib import Path
from typing import Dict, Optional

class PersistentBulkQueue:
    def __init__(self, storage_dir: str = ".vault-memory-jobs"):
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, dict] = {}
        self._load_from_disk()

    def _job_path(self, job_id: str) -> Path:
        return self._storage_dir / f"{job_id}.json"

    def _load_from_disk(self):
        for path in self._storage_dir.glob("*.json"):
            try:
                job = json.loads(path.read_text())
                if job["status"] in ("queued", "processing"):
                    job["status"] = "queued"  # Reset processing jobs to queued
                self._jobs[job["job_id"]] = job
            except (json.JSONDecodeError, KeyError):
                path.unlink(missing_ok=True)

    def create_job(self, job_id: str, job_data: dict):
        self._jobs[job_id] = job_data
        self._save_job(job_id)

    def update_job(self, job_id: str, updates: dict):
        if job_id in self._jobs:
            self._jobs[job_id].update(updates)
            self._save_job(job_id)

    def get_job(self, job_id: str) -> Optional[dict]:
        return self._jobs.get(job_id)

    def cancel_job(self, job_id: str):
        if job_id in self._jobs:
            self._jobs[job_id]["status"] = "cancelled"
            self._save_job(job_id)

    def _save_job(self, job_id: str):
        path = self._job_path(job_id)
        path.write_text(json.dumps(self._jobs[job_id]))

    def cleanup_old_jobs(self, max_age_hours: int = 24):
        """Remove completed/failed jobs older than max_age_hours."""
        import time
        cutoff = time.time() - (max_age_hours * 3600)
        for job_id, job in list(self._jobs.items()):
            if job["status"] in ("done", "failed", "cancelled"):
                completed_at = job.get("completed_at", 0)
                if completed_at < cutoff:
                    del self._jobs[job_id]
                    self._job_path(job_id).unlink(missing_ok=True)
```

**Wire into:**
- Replace `_bulk_jobs` dict in `daemon/main.py` with `PersistentBulkQueue` instance
- Store in `app.state.bulk_queue`
- Update `/bulk/queue`, `/bulk/status`, `/bulk/cancel` to use queue methods

**Files affected:**
- `daemon/bulk_queue.py` (NEW)
- `daemon/main.py` (UPDATE bulk endpoints)

---

### S30-10: Rate Limiter Persistence

**Problem:** Rate limit state lost on restart, allowing immediate bypass.

**Implementation:**
```python
# daemon/rate_limiter_store.py
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple

class PersistentRateLimiterStore:
    def __init__(self, storage_dir: str = ".vault-memory-ratelimit"):
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._requests: Dict[Tuple[str, str], List[float]] = {}
        self._daily_counts: Dict[str, int] = {}
        self._daily_reset_at = 0.0
        self._load_from_disk()

    def _data_path(self) -> Path:
        return self._storage_dir / "state.json"

    def _load_from_disk(self):
        path = self._data_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                now = time.time()
                # Only load requests from last 5 minutes
                self._requests = {
                    tuple(k.split("|", 1)): [ts for ts in v if ts > now - 300]
                    for k, v in data.get("requests", {}).items()
                }
                # Reset daily counts if past midnight
                self._daily_counts = data.get("daily_counts", {})
                self._daily_reset_at = data.get("daily_reset_at", 0)
                if now >= self._daily_reset_at:
                    self._daily_counts.clear()
            except (json.JSONDecodeError, KeyError):
                pass

    def save(self):
        data = {
            "requests": {"|".join(k): v for k, v in self._requests.items()},
            "daily_counts": self._daily_counts,
            "daily_reset_at": self._daily_reset_at,
        }
        self._data_path().write_text(json.dumps(data))

    def periodic_save(self, interval: int = 60):
        """Save state every N seconds."""
        # Called from heartbeat or background task
        self.save()
```

**Wire into:**
- Replace in-memory dicts in `RateLimitMiddleware` with `PersistentRateLimiterStore`
- Add periodic save call in heartbeat cycle
- Save on shutdown in lifespan

**Files affected:**
- `daemon/rate_limiter_store.py` (NEW)
- `daemon/main.py` (UPDATE RateLimitMiddleware)
- `daemon/heartbeat.py` (ADD periodic save call)

---

### S30-11: Lite Mode Write Validation

**Problem:** WriteValidator skips in lite mode, allowing near-duplicate promotions.

**Implementation:**
```python
# daemon/validate_write.py — enhance validate() method
async def validate(self, proposed_text: str, proposed_path: str) -> Tuple[bool, str]:
    try:
        # Try vector-based validation first (full mode)
        embedding = await self._embedder.embed(proposed_text)
        if embedding is not None:
            # ... existing vector similarity check ...
            pass

        # Fallback: text-based duplicate detection (works in lite mode)
        return await self._text_based_check(proposed_text, proposed_path)

    except Exception as e:
        logger.warning('validate: skipped for %s — %s', proposed_path, e)
        return True, f'skip: {e}'

async def _text_based_check(self, proposed_text: str, proposed_path: str) -> Tuple[bool, str]:
    """Simple text overlap check for lite mode (no embeddings required)."""
    # Get high-trust file contents from vault
    vault_root = Path(self._settings.vault_path)
    proposed_words = set(proposed_text.lower().split())

    for md_file in vault_root.rglob("*.md"):
        if ".obsidian" in md_file.parts or "_working" in md_file.parts:
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace").lower()
            existing_words = set(content.split())
            overlap = len(proposed_words & existing_words) / max(len(proposed_words), 1)
            if overlap > 0.85:  # 85% word overlap = likely duplicate
                return False, f"Near-duplicate: {md_file.relative_to(vault_root)} ({overlap:.0%} word overlap)"
        except (OSError, UnicodeDecodeError):
            continue

    return True, "Content passes text-based validation"
```

**Files affected:**
- `daemon/validate_write.py` (ADD `_text_based_check` fallback)

---

## T5: Integration Tests (Day 7)

### S30-12: Integration Test Suite

**Problem:** Zero tests that verify the system actually works end-to-end.

**Implementation:**
```python
# tests/integration/test_search_pipeline.py
import pytest
from fastapi.testclient import TestClient
from daemon.main import app

@pytest.mark.integration
class TestSearchPipeline:
    """Tests that require real PostgreSQL + Weaviate."""

    def test_search_returns_results(self, client: TestClient, api_key: str):
        response = client.post("/search", json={"query": "test"}, headers={"X-API-Key": api_key})
        assert response.status_code == 200
        data = response.json()
        assert "results" in data

    def test_search_siblings_finds_related(self, client, api_key):
        response = client.post("/search_siblings", json={"query": "test"}, headers={"X-API-Key": api_key})
        assert response.status_code == 200

    def test_graph_endpoint(self, client, api_key):
        response = client.get("/graph", headers={"X-API-Key": api_key})
        assert response.status_code == 200

    def test_temporal_endpoint(self, client, api_key):
        response = client.get("/temporal", headers={"X-API-Key": api_key})
        assert response.status_code == 200

@pytest.mark.integration
class TestSessionLifecycle:
    def test_register_and_close_session(self, client, api_key):
        # Register
        resp = client.post("/sessions", json={"agent_name": "test", "project": "test", "task": "test"}, headers={"X-API-Key": api_key})
        assert resp.status_code == 201
        session_id = resp.json()["session_id"]

        # Close
        resp = client.patch(f"/sessions/{session_id}", json={"status": "closed"}, headers={"X-API-Key": api_key})
        assert resp.status_code == 200

    def test_attribution_endpoint(self, client, api_key):
        # ... test attribution ...
        pass

@pytest.mark.integration
class TestBulkOperations:
    def test_bulk_import_and_export(self, client, api_key):
        # ... test bulk flow ...
        pass

    def test_bulk_queue_status(self, client, api_key):
        # ... test queue polling ...
        pass

@pytest.mark.integration
class TestHealthEndpoints:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200

    def test_health_detailed(self, client, api_key):
        resp = client.get("/health/detailed", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "subsystems" in data
        assert "postgres" in data["subsystems"]
        assert "weaviate" in data["subsystems"]

@pytest.mark.integration
class TestRateLimiting:
    def test_rate_limit_enforced(self, client, api_key):
        # Send 61 requests in 1 minute
        for i in range(60):
            resp = client.post("/search", json={"query": "test"}, headers={"X-API-Key": api_key})
            assert resp.status_code == 200

        # 61st should be rate limited
        resp = client.post("/search", json={"query": "test"}, headers={"X-API-Key": api_key})
        assert resp.status_code == 429

    def test_usage_endpoint(self, client, api_key):
        resp = client.get("/me/usage", headers={"X-API-Key": api_key})
        assert resp.status_code == 200
        assert "requests_today" in resp.json()
```

**Test fixtures (`tests/integration/conftest.py`):**
```python
import pytest
from fastapi.testclient import TestClient
from daemon.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def api_key():
    return os.getenv("VAULT_MEMORY_API_KEY", "test-key")
```

**Run integration tests:**
```bash
# Requires Docker services running
docker compose up -d
pytest tests/integration/ -v -m integration
```

**Files affected:**
- `tests/integration/__init__.py` (NEW)
- `tests/integration/conftest.py` (NEW)
- `tests/integration/test_search_pipeline.py` (NEW)
- `tests/integration/test_session_lifecycle.py` (NEW)
- `tests/integration/test_bulk_operations.py` (NEW)
- `tests/integration/test_health_endpoints.py` (NEW)
- `tests/integration/test_rate_limiting.py` (NEW)

---

## Execution Order

```
Day 1: T1 (Foundation)
  ├─ S30-1: Consolidate PostgresClient
  └─ S30-2: Fix README version

Day 2-3: T2 (Architecture)
  └─ S30-3: Split main.py monolith

Day 4-5: T3 (Missing S24)
  ├─ S30-4: Circuit breaker
  ├─ S30-5: Pool metrics + slow queries
  ├─ S30-6: SQLite error handling
  ├─ S30-7: AutoSync file loss fix
  └─ S30-8: Centrality optimization

Day 6: T4 (Reliability)
  ├─ S30-9: Bulk queue persistence
  ├─ S30-10: Rate limiter persistence
  └─ S30-11: Lite mode validation

Day 7: T5 (Testing)
  └─ S30-12: Integration test suite
```

---

## Verification Checklist

After all items complete:

- [ ] `python -m py_compile daemon/**/*.py` passes
- [ ] `pytest tests/ -v` passes (unit tests)
- [ ] `pytest tests/integration/ -v -m integration` passes (requires Docker)
- [ ] `cd obsidian-plugin && npx tsc --noEmit` passes
- [ ] No references to `daemon.backends.postgres_client` remain
- [ ] No `RuntimeError("SQLite connection is not initialized")` remains
- [ ] `daemon/main.py` is under 300 lines
- [ ] README.md version matches pyproject.toml (0.8.0)
- [ ] Circuit breaker state visible in `/health/detailed`
- [ ] `/debug/slow-queries` endpoint returns data
- [ ] Bulk jobs survive server restart
- [ ] Rate limit state survives server restart
- [ ] Lite mode rejects near-duplicate promotions

---

## Out of Scope

- New features (S21 Mobile, S22 Collaborative, S23 Canvas)
- Redis integration (deferred — file-based persistence is sufficient for now)
- GraphQL API
- WebSocket support
- Mobile app development
- Canvas editor improvements

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| main.py refactoring breaks routes | Extract one route group at a time, verify after each |
| PostgresClient consolidation breaks imports | Run full syntax check after each file update |
| Circuit breaker causes false positives | Start with high threshold (5 failures, 120s recovery) |
| Integration tests require Docker | Mark with `@pytest.mark.integration`, skip in CI without Docker |
| Persistence files grow unbounded | Add cleanup job in heartbeat cycle (S30-9, S30-10) |
