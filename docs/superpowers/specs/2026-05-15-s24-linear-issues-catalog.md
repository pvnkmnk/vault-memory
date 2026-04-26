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
| **S24-B1** | Fix Postgres health check cache stampede | P1 | Bug | `sprint/s24`, `type/bug` | `ping()` caches True for 30s even if connection dropped. Invert cache: cache failures not successes. Track consecutive failures, recover after N successes. |
| **S24-B2** | ~~WriteValidator dead code~~ | — | — | — | **RESOLVED** — Removed in this sprint. `daemon/validate_write.py` deleted, comment removed from `daemon/__init__.py`. No callers. |
| **S24-B3** | Handle SQLite RuntimeError in call chain | P2 | Bug | `sprint/s24`, `type/bug` | `RuntimeError` raised in `sqlite_client.py:273,330` on uninitialized connection. Not caught anywhere → 500 to client. Add try/catch → 503 with clear message. |
| **S24-B4** | Fix bulk import 410 message for lite mode | P3 | Bug | `sprint/s24`, `type/bug` | Bulk endpoints return 410 Gone for SQLite backends. Message says deprecated (wrong). Should return 422 Unprocessable Entity with `{ mode: 'lite' }` field. |
| **S24-B5** | Fix GraphCanvas D3 event handler memory leak | P1 | Bug | `sprint/s24`, `type/bug` | `window.addEventListener` registered but never unregistered on view close. Store bound handler refs, remove in `onClose()`, stop D3 simulation. |
| **S24-B6** | StatusBar smart polling with backoff | P2 | Bug | `sprint/s24`, `type/bug` | StatusBar polls /health every 30s unconditionally. Add exponential backoff (30s→5min), reset on user interaction, pause when tab hidden. |
| **S24-B7** | AutoSyncEngine file loss on sync failure | P2 | Bug | `sprint/s24`, `type/bug` | `pendingFiles.clear()` happens before API call. If sync fails, files are lost. Move clear to after success, add retry queue, exponential backoff. |

### Architecture (A1–A5)

| ID | Title | Priority | Type | Labels | Description |
|----|-------|----------|------|--------|-------------|
| **S24-A1** | Remove PostgresBackend/PostgresClient alias | P3 | Chore | `sprint/s24`, `type/chore` | `PostgresBackend` exists alongside `daemon/pg_client.py` which also defines `PostgresClient`. One is dead code. Verify which is actually used, remove the other. |
| **S24-A2** | ~~Stale validate_write TODO~~ | — | — | — | **RESOLVED** — Comment removed from `daemon/__init__.py` as part of B2. |
| **S24-A3** | Add DI container to CLI | P2 | Feature | `sprint/s24`, `type/feature` | CLI uses direct imports and globals — can't mock for tests. Create `cli/dependencies.py` mirroring daemon pattern. Pass to all commands. |
| **S24-A4** | Add circuit breaker for Ollama/Weaviate | P1 | Feature | `sprint/s24`, `type/feature` | No circuit breaker — one Ollama timeout blocks cognify permanently. Add CLOSED→OPEN→HALF_OPEN pattern. Track failures, cooldown 60s, expose circuit state in /health. |
| **S24-A5** | Add rate limiter metrics | P3 | Chore | `sprint/s24`, `type/chore` | Rate limiter has no metrics — can't alert on approaching limits. Expose hits/misses/evictions/current_keys via /metrics or /health. |

### Performance (P1–P5)

| ID | Title | Priority | Type | Labels | Description |
|----|-------|----------|------|--------|-------------|
| **S24-P1** | Optimize recalc_centrality SQL (O(n²) → O(n)) | P1 | Performance | `sprint/s24`, `type/performance` | Current SQL uses correlated subquery per row. Replace with CTE + UPDATE...FROM pattern. Benchmark: <500ms for 5000 entities. |
| **S24-P2** | Incremental topic hub refresh | P2 | Performance | `sprint/s24`, `type/performance` | Every heartbeat truncates and re-inserts all topic_hubs. Use INSERT...ON CONFLICT DO UPDATE. Skip rebuild if no relationships changed since last run. |
| **S24-P3** | Add slow-query diagnostics | P2 | Feature | `sprint/s24`, `type/feature` | No slow-query logging — can't identify slow routes without profiling. Add SLOW_QUERY_THRESHOLD_MS middleware, /debug/slow-queries endpoint, per-endpoint p50/p95 tracking. |
| **S24-P4** | Add connection pool health metrics | P3 | Chore | `sprint/s24`, `type/chore` | No pool metrics exposed. Add `_pool_stats()` returning size/used/available/waiting/errors_total. Expose in /health under postgres.pool. |
| **S24-P5** | Stream bulk_export instead of loading all into memory | P1 | Performance | `sprint/s24`, `type/performance` | bulk_export loads all notes into memory → OOM for 10k+ note vaults. Add cursor pagination (limit+cursor params), streaming option (stream=true). |

---

## Summary

| Category | Count | Issues |
|----------|-------|--------|
| Bugs | 7 → 5 active | B1, B3, B4, B5, B6, B7 (B2 resolved) |
| Architecture | 5 → 4 active | A1, A3, A4, A5 (A2 resolved) |
| Performance | 5 | P1, P2, P3, P4, P5 |
| **Total active** | **14** | |
| Resolved this session | 2 | B2 (WriteValidator removed), A2 (stale TODO removed) |

---

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

**Track 1 (Daemon Core):** B1 → P1 → P2 → A4 (circuit breaker)
**Track 2 (Plugin):** B5 → B6 → B7
**Track 3 (Observability):** P3 → P4 → A5 → B4
**Track 4 (Cleanup):** B3 → A1 → A3 → P5