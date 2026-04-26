# S24 — Vault Cleanup Sprint Design

**Date:** 2026-05-15
**Status:** Draft
**Author:** Code analysis + brainstorming
**Type:** Sprint Design

## Overview

S24 is a single cleanup sprint covering bug fixes, tech debt reduction, and performance improvements across all subsystems (daemon, plugin, CLI, observability). No new features — just making the existing codebase more correct, resilient, and maintainable.

**Scope:** 17 issues (B1–B7 bugs, A1–A5 architecture, P1–P5 performance)

---

## B1: Fix Postgres Health Check Cache Stampede

**File:** `daemon/backends/postgres_client.py`
**Severity:** High
**Type:** Bug Fix

### Problem

`ping()` caches `True` for 30 seconds even if the actual connection has dropped. The cache check runs before every DB operation via `cursor()` context manager:

```python
def ping(self) -> bool:
    now = time.time()
    if now - self._last_health_check < self._health_check_interval:
        return True  # ← Stale positive — connection may have died since
    # ...
```

If the pool connection drops between checks, `ping()` still returns `True` for up to 30 seconds, causing all `cursor()` calls to fail with cryptic connection errors.

### Implementation

1. Invert the cache logic: **cache failures, not successes**
2. On any `cursor()` exception (connection error), increment `_consecutive_failures`
3. Only mark healthy after **N consecutive successful operations** (e.g., 3)
4. Add `_last_successful_operation: float` timestamp
5. On `cursor()` entry, if last success was >60s ago, do a live ping even if within the 30s window
6. Expose pool stats via `_pool_stats()` method for `/metrics`

### Test

- Mock `psycopg2.pool.ThreadedConnectionPool.getconn()` to raise `OperationalError` after 2 calls
- Verify `ping()` returns `False` on third consecutive failure
- Verify `ping()` recovers to `True` after 3 consecutive successes

---

## B2: Implement or Remove WriteValidator

**File:** `daemon/validate_write.py`
**Severity:** Medium
**Type:** Bug Fix / Dead Code

### Problem

`WriteValidator.validate()` always returns `(True, ...)` — it's a no-op scaffold that logs adjacency but never blocks writes. It has no unit tests and is never called in the normal sync flow.

### Implementation

**Option: Remove entirely** (recommended)

1. Delete `daemon/validate_write.py`
2. Remove from `daemon/__init__.py` (already commented out)
3. Remove from `Dependencies` if referenced there
4. Remove from `daemon/main.py` startup if instantiated

**Option: Implement properly** (if write guard is desired)

1. Actually call `WriteValidator.validate()` before promoting a file in `/promote`
2. Add semantic similarity threshold: if proposed text has >85% similarity to any `trust:high` note, log conflict and surface in lint report
3. Add unit tests
4. Add configuration flag `WRITE_VALIDATION_ENABLED=true/false` (default false)

### Test

- If removed: verify no import errors, no references remain in codebase
- If implemented: unit tests for threshold behavior, integration test for `/promote` path

**Decision: REMOVE** — verified no callers, no imports, no use case. Removed in this sprint.

---

## B3: Handle SQLite RuntimeError in Call Chain

**File:** `daemon/backends/sqlite_client.py:273, 330`
**Severity:** Medium
**Type:** Bug Fix

### Problem

`RuntimeError` is raised with message **both** `cursor()` and `execute()` when the SQLite connection is not initialized. These are `RuntimeError` (not `ValueError`) and are not caught anywhere in the call chain — they propagate as 500 errors to the client.

### Implementation

1. Change `RuntimeError` to `RuntimeError(...)` with a specific sub-class `SQLiteNotInitialized(RuntimeError)` for easier catching
2. Add try/catch in `daemon/main.py` around all routes that call SQLite-backed services — catch `SQLiteNotInitialized` and return `503 Service Unavailable` with clear message
3. In the CLI sync command, catch and display user-friendly error message
4. Add unit test: verify `503` response when SQLite not initialized

### Test

- Mock `SQLiteClient._db` as `None`
- Call `/memory/list_blocks`
- Assert response is `503` with JSON body `detail` containing the error message

---

## B4: Fix Bulk Import 410 Message for Lite Mode

**File:** `daemon/main.py:1640-1650`
**Severity:** Low
**Type:** UX Bug Fix

### Problem

`bulk_import` and `bulk_export` return `410 Gone` for SQLite backends, but the message reads:

```json
{
  // ❌ Misleading — not deprecated, just not available in lite mode
  // ✓ Should say: bulk operations require full mode (Postgres + Weaviate)
  //             Available in lite mode? No. Use /sync for file-level sync instead.
}
```

### Implementation

1. Detect backend type from `Dependencies` settings
2. Return `422 Unprocessable Entity` (semantic: valid request, can't process here) instead of `410 Gone`
3. Response body: `detail: Bulk operations require full mode (PostgreSQL + Weaviate). Use /sync for file-level sync instead.`
4. Add a `mode` field to the error response: `{ mode: 'lite' | 'full' }`

### Test

- Call `bulk_import` when in lite mode
- Assert `422` status, correct message, `mode: 'lite'` field

---

## B5: Fix GraphCanvas D3 Event Handler Memory Leak

**File:** `obsidian-plugin/src/views/GraphCanvas.ts:331, 352, 356, 567-569`
**Severity:** High
**Type:** Bug Fix

### Problem

GraphCanvas registers window-level event handlers but never unregisters them on view close:

```typescript
// Set on window but never cleared:
(window as any)._vpSelectedNodeId = node.id;

// Registered but no cleanup:
window.addEventListener('keydown', this.handleKeyDown.bind(this));
window.addEventListener('resize', this.handleResize.bind(this));
```

Each time the view opens/closes, new handlers accumulate. After 10 view opens, 10× handlers are active.

### Implementation

1. Store bound handler references as instance properties (not anonymous binds)
2. In `onClose()`, remove all registered event listeners:

```typescript
private _keyHandler: ((e: KeyboardEvent) => void) | null = null;
private _resizeHandler: (() => void) | null = null;

private setupWindowHandlers() {
    this._keyHandler = this.handleKeyDown.bind(this);
    this._resizeHandler = this.handleResize.bind(this);
    window.addEventListener('keydown', this._keyHandler);
    window.addEventListener('resize', this._resizeHandler);
}

async onClose() {
    if (this._keyHandler) window.removeEventListener('keydown', this._keyHandler);
    if (this._resizeHandler) window.removeEventListener('resize', this._resizeHandler);
    if ((window as any)._vpSelectedNodeId) delete (window as any)._vpSelectedNodeId;
    // Also stop D3 simulation properly
    if (this.simulation) this.simulation.stop();
}
```

3. Stop D3 force simulation on close to free internal timers
4. Add `onunload` cleanup as backup

### Test

- Open GraphCanvas 3 times, close each time
- Verify handlers are removed (can test via spy)
- Verify `simulation.stop()` is called

---

## B6: StatusBar Smart Polling with Backoff

**File:** `obsidian-plugin/src/views/StatusBar.ts`
**Severity:** Medium
**Type:** Performance Fix

### Problem

StatusBar polls `/health` every 30 seconds unconditionally — even when the user hasn't interacted with the plugin for hours. This creates unnecessary network traffic and daemon load.

### Implementation

1. Add exponential backoff: start at 30s, double to 60s, 120s, max 5 minutes after each successful check
2. On any user interaction (click, search, sync), reset backoff to 30s
3. On daemon going offline, reset backoff to 10s (faster recovery)
4. Use a debounced visibility API check: only poll when Obsidian window is visible (`document.visibilityState === 'visible'`)
5. Consider replacing with WebSocket upgrade header when daemon supports it (future, not in this sprint)

### Test

- Mock 5 consecutive successful health checks, verify polling interval doubles
- Mock user interaction, verify backoff resets to 30s
- Mock `document.visibilityState = 'hidden'`, verify polling pauses

---

## B7: AutoSyncEngine File Loss on Sync Failure

**File:** `obsidian-plugin/src/components/AutoSyncEngine.ts:104-113`
**Severity:** Medium
**Type:** Bug Fix

### Problem

In `processPending()`, files are removed from `pendingFiles` **before** the API call:

```typescript
const filesToSync = Array.from(this.pendingFiles);
this.pendingFiles.clear(); // ← Cleared before API call
// ...
const result = await this.client.syncFiles(filesToSync);
// If this fails: filesToSync is empty, files are lost from pending set
```

If `syncFiles` throws, all files in `filesToSync` have been removed from `pendingFiles` and were never synced.

### Implementation

1. Move `pendingFiles.clear()` to **after** successful API response
2. On API failure, add `filesToSync` back to `pendingFiles` or keep them in a retry queue
3. Add exponential retry: 3 attempts with 1s, 2s, 4s backoff before giving up
4. On final failure, surface a Notice with the list of failed files so user knows what to retry manually

### Test

- Mock `client.syncFiles()` to throw after 2 attempts
- Verify files are not permanently lost from pending set
- Verify Notice shows failed file count after all retries exhausted

---

## A1: Remove PostgresBackend/PostgresClient Alias

**File:** `daemon/backends/postgres_client.py:119`
**Severity:** Low
**Type:** Architecture Cleanup

### Problem

`PostgresClient` is just `PostgresBackend` with a comment `'''Backwards compatible alias.'''` — suggests a half-done refactor where the abstraction was introduced but the alias wasn't cleaned up.

### Implementation

1. Check all usages of `PostgresClient` in the codebase — particularly `daemon/pg_client.py` which also defines its own `PostgresClient`
2. Decide: keep `PostgresClient` in `backends/postgres_client.py` and remove `daemon/pg_client.py` entirely, OR keep `daemon/pg_client.py` and deprecate the one in `backends/`
3. Choose one canonical location — prefer `backends/postgres_client.py` since it's the layered design
4. Update imports in `Dependencies` and `daemon/main.py` to use the canonical name
5. Remove duplicate if any remains

### Decision Gate

Check which `PostgresClient` is actually imported and used. Likely `daemon/pg_client.py` is the active one and `backends/postgres_client.py` is dead code. Verify before removing.

---

## A2: Remove Stale validate_write Import from __init__

**File:** `daemon/__init__.py:13`
**Severity:** Low
**Type:** Tech Debt

### Problem

Commented-out import with `TODO: Implement or remove` — a stale scaffold that's been there since at least Sprint 1 with no decision made.

### Implementation

1. ~~Verify `validate_write` is truly not imported anywhere else~~ — already done above (B2)
2. ~~Remove the commented-out line entirely~~ — already done above (B2)

### Test

- `python -c 'from daemon import *'` should not import `validate_write`
- No references to `validate_write` in daemon code (grep check)

---

## A3: Add DI Container to CLI

**File:** `cli/sync_command.py`, `cli/main.py`
**Severity:** Medium
**Type:** Architecture

### Problem

CLI uses direct imports and globals — can't easily test against mock dependencies or swap out the Postgres client. Makes CLI hard to test in CI.

### Implementation

1. Create `cli/dependencies.py` mirroring the daemon's `Dependencies` pattern — a dataclass holding all service instances
2. Modify `cli/sync_command.py` to accept a `Dependencies` instance (or build one from config)
3. In `cli/main.py`, build the DI container once at startup and pass it to all commands
4. In tests, pass a mock `Dependencies` with mocked Postgres/SQLite clients
5. Add `cli/__init__.py` re-exports for consistency with daemon pattern

### Test

- Write a test that mocks `Dependencies.postgres` and verifies `run_full_sync` behavior without a real DB

---

## A4: Add Circuit Breaker for Ollama/Weaviate

**File:** `daemon/main.py`, `daemon/embedder.py`
**Severity:** High
**Type:** Resilience

### Problem

No circuit breaker pattern — a single Ollama timeout blocks `cognify` permanently. Repeated requests keep trying and failing, wasting resources. Weaviate failures similarly cascade.

### Implementation

1. Add a simple in-memory circuit breaker to `daemon/embedder.py` (Ollama calls)
2. Circuit states: `CLOSED` (normal) → `OPEN` (failing) → `HALF_OPEN` (test after cooldown)
3. Track: `failure_count`, `last_failure_time`, `cooldown_seconds=60`
4. On exception: increment `failure_count`, if >3 in 60s → OPEN (skip calls, return 503)
5. After 60s in OPEN → HALF_OPEN (allow one test call)
6. On test call success → CLOSED (reset counters)
7. On test call failure → back to OPEN for another 60s
8. Add circuit state to `/health` response so monitoring can see it
9. Apply same pattern to Weaviate client (`daemon/weaviate_client.py`)

### Test

- Mock Ollama to fail 4 times
- Verify subsequent calls return `503` immediately without attempting Ollama
- Verify circuit recovers after cooldown period

---

## A5: Add Rate Limiter Metrics

**File:** `daemon/main.py`
**Severity:** Low
**Type:** Observability

### Problem

Rate limiter grows unbounded between stale-key eviction cycles. No metrics exposed — can't alert on approaching limits.

### Implementation

1. Add `_eviction_count: int` and `_current_keys: int` to the rate limiter
2. Expose `/metrics` endpoint (or add to existing `/health`) with:
   - `rate_limiter_keys_current`: current key count
   - `rate_limiter_evictions_total`: total evictions since startup
   - `rate_limiter_hits_total`: total hits
   - `rate_limiter_misses_total`: total misses
3. Add Prometheus-compatible format option

### Test

- Hit rate limiter 100 times
- Verify metrics reflect correct hit/miss counts
- Verify eviction count increments when stale keys are removed

---

## P1: Optimize recalc_centrality SQL (O(n²) → O(n))

**File:** `daemon/heartbeat.py:27-58`
**Severity:** High
**Type:** Performance

### Problem

Current SQL uses a correlated subquery per row inside the `UPDATE SET` — PostgreSQL evaluates the subquery once for every row in `temporal_entities`, making it O(n²) in entity count. For large vaults this is prohibitively slow.

### Implementation

Replace with a CTE + `UPDATE...FROM` pattern:

```sql
WITH degree_counts AS (
    SELECT source_name AS entity_name, COUNT(*) AS out_degree
    FROM relationships
    GROUP BY source_name
),
merged AS (
    SELECT ae.entity_name, COALESCE(dc.out_degree, 0) AS degree
    FROM temporal_entities ae
    LEFT JOIN degree_counts dc ON ae.entity_name = dc.entity_name
    WHERE ae.entity_name IN (SELECT entity_name FROM degree_counts)
)
UPDATE temporal_entities
SET centrality = CASE
    WHEN total <= 1 THEN 0.0
    ELSE m.degree::FLOAT / (total - 1)
END
FROM merged m
WHERE temporal_entities.entity_name = m.entity_name;
-- Run total count query separately to get 'total' value
```

Benchmark before and after with a vault containing 1000+ entities. Target: <500ms for 5000 entities.

### Test

- Populate `temporal_entities` with 1000 rows and `relationships` with 5000 edges
- Measure `recalc_centrality` execution time
- Assert <500ms for 5000 entities

---

## P2: Incremental Topic Hub Refresh

**File:** `daemon/heartbeat.py:60-90`
**Severity:** Medium
**Type:** Performance

### Problem

Every heartbeat cycle runs `TRUNCATE topic_hubs` + full re-insert. For large vaults with millions of relationships, this is O(n) every 15 minutes for data that rarely changes.

### Implementation

1. Add `hub_version` column to `topic_hubs` table (auto-increment, updated on each rebuild)
2. Add `last_relationship_change` tracking (add to `relationships` table: `updated_at TIMESTAMPTZ DEFAULT now()`)
3. On refresh: if no relationships have changed since last refresh (`updated_at` before last hub rebuild), skip the rebuild entirely
4. If partial change: use `INSERT ... ON CONFLICT (entity_name) DO UPDATE` instead of `TRUNCATE + INSERT`
5. Log when refresh is skipped vs when it's run

### Test

- Mock no relationship changes since last refresh
- Verify `refresh_topic_hubs` returns 0 and makes no DB writes
- Mock 5 changed relationships
- Verify only those 5 are updated (not full truncate)

---

## P3: Add Slow-Query Diagnostics

**File:** `daemon/main.py`
**Severity:** Medium
**Type:** Observability

### Problem

No slow-query logging — impossible to identify which routes are slow without manual profiling.

### Implementation

1. Add `SLOW_QUERY_THRESHOLD_MS=1000` config (default 1s)
2. Wrap all route handlers with a timing middleware that logs slow queries:

```python
@app.middleware
async def slow_query_logger(request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    if elapsed > SLOW_QUERY_THRESHOLD_MS:
        logger.warning(
            f'Slow query: {request.method} {request.url.path} took {elapsed:.1f}ms'
        )
    return response
```

3. Add `/debug/slow-queries` endpoint returning the last 100 slow query records
4. Track max/avg/p50/p95 latency per endpoint in memory

### Test

- Add artificial delay to one route handler in test
- Verify slow query is logged and appears in `/debug/slow-queries`

---

## P4: Add Connection Pool Health Metrics

**File:** `daemon/backends/postgres_client.py`
**Severity:** Low
**Type:** Observability

### Problem

No connection pool metrics exposed — can't see pool utilization, queue depth, or connection errors in monitoring.

### Implementation

1. Add `_pool_stats()` method to `PostgresBackend` returning:
   - `size`: total pool size (min + max)
   - `used`: currently checked-out connections
   - `available`: idle connections
   - `waiting`: requests waiting for a connection
   - `errors_total`: connection errors since startup
2. Expose these in `/health` under `postgres.pool`
3. Do the same for Weaviate client (connection stats if available)

### Test

- Check pool stats reflect actual pool state
- Simulate connection error and verify `errors_total` increments

---

## P5: Stream bulk_export Instead of Loading All Into Memory

**File:** `daemon/main.py:1683-1700`
**Severity:** High
**Type:** Performance

### Problem

`bulk_export` loads all notes into memory before returning — for vaults with 10,000+ notes this causes OOM.

### Implementation

1. Add `cursor=True` streaming to Weaviate query
2. Implement pagination in `bulk_export`: `limit` + `cursor` params, default 100 per page
3. Add streaming response option: `stream=true` query param returns a newline-delimited JSON stream
4. Document pagination: clients can fetch 100 notes at a time using cursor token

### Test

- Export with `limit=10` and `limit=10000` — verify memory usage stays bounded
- Test cursor-based pagination: fetch page 1, use cursor token to fetch page 2, verify no duplicates

---

## Issue Summary

| ID | Issue | Type | Severity | Est. Effort |
|----|-------|------|----------|-------------|
| B1 | Postgres health check cache stampede | Bug | High | Medium |
| B2 | WriteValidator dead code | Bug | Medium | Small |
| B3 | SQLite RuntimeError unhandled | Bug | Medium | Small |
| B4 | Bulk import 410 message misleading | Bug | Low | Small |
| B5 | GraphCanvas D3 event leak | Bug | High | Medium |
| B6 | StatusBar unconditional polling | Bug | Medium | Medium |
| B7 | AutoSync file loss on failure | Bug | Medium | Small |
| A1 | PostgresClient alias confusion | Arch | Low | Small |
| A2 | Stale validate_write TODO | Arch | Low | Trivial |
| A3 | CLI DI container missing | Arch | Medium | Medium |
| A4 | No circuit breaker | Arch | High | Medium |
| A5 | Rate limiter no metrics | Arch | Low | Small |
| P1 | Centrality recalc O(n²) | Perf | High | Medium |
| P2 | Topic hub refresh full truncate | Perf | Medium | Medium |
| P3 | No slow-query diagnostics | Perf | Medium | Small |
| P4 | Connection pool no metrics | Perf | Low | Small |
| P5 | bulk_export OOM for large vaults | Perf | High | Medium |

**Total: 16 issues — 7 High, 5 Medium, 4 Low**

---

## Implementation Order (Recommended)

Parallel tracks by agent capability:

**Track 1 (Daemon Core):** B1 → P1 → P2 → A4 (circuit breaker)
**Track 2 (Plugin):** B5 → B6 → B7
**Track 3 (Observability):** P3 → P4 → A5 → B4
**Track 4 (Cleanup):** B2 → B3 → A1 → A2 → A3 → P5

---

## Testing Strategy

- **Unit tests** for each fix (see test notes per issue above)
- **Integration tests** for cross-component issues (A4 circuit breaker, B7 file loss)
- **Performance benchmarks** for P1, P2, P5
- **No regression** on existing test suite — run `pytest tests/ -q` after each track
- **Manual smoke test**: start daemon, open plugin, trigger sync/search/cognify, verify no crashes

---

## Out of Scope (S25+)

- New features (F1–F12)
- User-facing UX improvements
- Documentation updates (D1–D5) — separate doc sprint
- Obsidian Canvas integration (S23)
- Mobile companion app (S21)