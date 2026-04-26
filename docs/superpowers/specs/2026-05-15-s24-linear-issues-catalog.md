# S24 Vault Cleanup Sprint — Linear Issue Catalog

**Date:** 2026-05-15
**Spec:** `docs/superpowers/specs/2026-05-15-vault-cleanup-sprint-design.md`
**Status:** Ready for Linear import

---

## How to Import

**Option A: Manual** — Create each issue in Linear manually using the fields below.
**Option B: Script** — Run `node scripts/linear-import-setup.js` after setting `LINEAR_API_KEY`, then manually add the S24 issues below.

---

## Issue Definitions

### Bugs (B1–B7)

| ID | Title | Priority | Type | Labels | Description |
|----|-------|----------|------|--------|-------------|
| S24-B1 | Fix Postgres health check cache stampede | P1 | Bug | `sprint/s24`, `type/bug` | `daemon/backends/postgres_client.py` — `ping()` caches True for 30s even if connection dropped. Invert cache: cache failures not successes. Track consecutive failures, recover after N successes. |
| S24-B2 | ~~WriteValidator dead code~~ | — | — | — | **RESOLVED** — `daemon/validate_write.py` deleted, comment removed from `daemon/__init__.py`. No callers. |
| S24-B3 | Handle SQLite RuntimeError in call chain | P2 | Bug | `sprint/s24`, `type/bug` | `daemon/backends/sqlite_client.py:273,330` — `RuntimeError` on uninitialized connection propagates as 500. Add `SQLiteNotInitialized` exception class, catch in routes → 503. |
| S24-B4 | Fix bulk import 410 message for lite mode | P3 | Bug | `sprint/s24`, `type/bug` | `daemon/main.py:1640-1650` — bulk_import returns 410 Gone (wrong). Return 422 with `{ mode: 'lite' }` and correct message directing to /sync. |
| S24-B5 | Fix GraphCanvas D3 event handler memory leak | P1 | Bug | `sprint/s24`, `type/bug` | `obsidian-plugin/src/views/GraphCanvas.ts:567-569` — window event handlers accumulate on each view open. Store bound refs, remove in onClose(), call `simulation.stop()`. |
| S24-B6 | StatusBar smart polling with backoff | P2 | Bug | `sprint/s24`, `type/bug` | `obsidian-plugin/src/views/StatusBar.ts` — polls /health every 30s unconditionally. Add exponential backoff, reset on user interaction, pause when tab hidden (document.visibilityState). |
| S24-B7 | AutoSyncEngine file loss on sync failure | P2 | Bug | `sprint/s24`, `type/bug` | `obsidian-plugin/src/components/AutoSyncEngine.ts:104-113` — `pendingFiles.clear()` before API call loses files on failure. Move clear to after success, add 3-attempt retry with backoff. |

### Architecture (A1–A5)

| ID | Title | Priority | Type | Labels | Description |
|----|-------|----------|------|--------|-------------|
| S24-A1 | Remove PostgresBackend/PostgresClient alias | P3 | Chore | `sprint/s24`, `type/chore` | `daemon/backends/postgres_client.py:119` + `daemon/pg_client.py` — both define `PostgresClient` with different interfaces. `pg_client.py` used by heartbeat/retrieval; `backends/postgres_client.py` used by main/db_abstraction. **Fix: consolidate to single canonical source.** Option A (recommended): consolidate to `backends/`, update heartbeat/retrieval imports. Option B: consolidate to `pg_client.py`, update main/db_abstraction imports. Remove the duplicate class definition.
| S24-A2 | ~~Stale validate_write TODO~~ | — | — | — | **RESOLVED** — Comment removed from `daemon/__init__.py` as part of B2. |
| S24-A3 | Add DI container to CLI | P2 | Feature | `sprint/s24`, `type/feature` | `cli/sync_command.py`, `cli/main.py` — CLI uses globals, can't mock for tests. Create `cli/dependencies.py` mirroring daemon's `Dependencies` pattern. Pass to all commands. |
| S24-A4 | Add circuit breaker for Ollama/Weaviate | P1 | Feature | `sprint/s24`, `type/feature` | `daemon/embedder.py`, `daemon/weaviate_client.py` — no circuit breaker, one timeout blocks permanently. Add CLOSED→OPEN→HALF_OPEN with 60s cooldown, 3-failure threshold. Expose state in /health. |
| S24-A5 | Add rate limiter metrics | P3 | Chore | `sprint/s24`, `type/chore` | `daemon/main.py:1135` — rate limiter has no metrics. Expose `rate_limiter_keys_current`, `rate_limiter_evictions_total`, `rate_limiter_hits_total`, `rate_limiter_misses_total` via /metrics or /health. |

### Performance (P1–P5)

| ID | Title | Priority | Type | Labels | Description |
|----|-------|----------|------|--------|-------------|
| S24-P1 | Optimize recalc_centrality SQL (O(n²) → O(n)) | P1 | Performance | `sprint/s24`, `type/performance` | `daemon/heartbeat.py:27-58` — correlated subquery per row (O(n²)). Replace with CTE + UPDATE...FROM. Benchmark: <500ms for 5000 entities. |
| S24-P2 | Incremental topic hub refresh | P2 | Performance | `sprint/s24`, `type/performance` | `daemon/heartbeat.py:60-90` — TRUNCATE + full re-insert every 15min. Use INSERT...ON CONFLICT DO UPDATE instead. Skip rebuild if `relationships.updated_at` shows no changes since last hub rebuild. |
| S24-P3 | Add slow-query diagnostics | P2 | Feature | `sprint/s24`, `type/feature` | `daemon/main.py` — no slow-query logging. Add SLOW_QUERY_THRESHOLD_MS (default 1s) as config. Middleware logs slow queries. Add /debug/slow-queries endpoint. Track per-endpoint p50/p95. |
| S24-P4 | Add connection pool health metrics | P3 | Chore | `sprint/s24`, `type/chore` | `daemon/backends/postgres_client.py` — no pool metrics. Add `_pool_stats()` method: size/used/available/waiting/errors_total. Expose in /health under `postgres.pool`. |
| S24-P5 | Stream bulk_export instead of loading all into memory | P1 | Performance | `sprint/s24`, `type/performance` | `daemon/main.py:1683-1700` — bulk_export loads all notes into memory → OOM for large vaults. Add Weaviate cursor streaming, pagination with `limit`+`cursor` params, `stream=true` for NDJSON. |

---

## Summary

| Category | Count | Issues |
|----------|-------|--------|
| Bugs | 7 → 5 active | B1, B3, B4, B5, B6, B7 (B2 resolved) |
| Architecture | 5 → 4 active | A1, A3, A4, A5 (A2 resolved) |
| Performance | 5 | P1, P2, P3, P4, P5 |
| **Total active** | **15** | |
| Resolved this session | 2 | B2 (WriteValidator removed), A2 (stale TODO removed) |

---

## Verification After Each Track

Run after completing each track, before moving to the next:

```bash
# Verify no regressions
pytest tests/ -q

# Syntax check all Python files
python -m py_compile daemon/main.py daemon/backends/*.py daemon/sync_watcher.py

# Type check (if mypy configured)
mypy daemon/ --ignore-missing-imports
```

## Manual Import Template

Copy each row into Linear manually:

```
Title: [title from table above]
Description: [description from table above]
Priority: [P1/P2/P3]
Type: [Bug/Feature/Chore/Performance]
Labels: sprint/s24, type/[bug|feature|chore|performance]
Status: Todo
```

---

## Implementation Order (Recommended)

**Track 1 (Daemon Core):** B1 → A4 → P1 → P2 → P5 *(circuit breaker first — bulk ops need it stable)*
**Track 2 (Plugin):** B5 → B6 → B7
**Track 3 (Observability):** P3 → P4 → A5 → B4
**Track 4 (Cleanup):** B3 → A1 → A3