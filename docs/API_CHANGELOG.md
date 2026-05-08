# API Changelog

All changes to the vault-memory REST API, tracked by version.

---

## [0.8.0] — 2026-04-30

### Added

- `POST /sync/file` — Manually trigger sync of a single file (was documented but missing; implemented in 0.8.0)
- `POST /sync/delta` — Incremental sync since timestamp with pagination and `force_full` support (S26-1)
- `POST /bulk/queue` — Queue bulk import jobs, returns `job_id` immediately (S26-2)
- `GET /bulk/status/{job_id}` — Poll bulk job progress with percentage (S26-2)
- `DELETE /bulk/cancel/{job_id}` — Cancel queued or processing bulk jobs (S26-2)
- `POST /bulk/export?stream=true` — NDJSON streaming export for bounded memory (S26-3)
- `GET /me/usage` — Rate limit usage stats for authenticated API key (S26-4)
- `GET /health/detailed` — Comprehensive subsystem health dashboard (S28-4)
- `POST /sessions/cleanup` — Manually trigger stale session cleanup (S28-1)
- `GET /sessions/{session_id}/attribution` — All content attributed to a session (S28-3)
- `/docs` — Swagger UI (enabled via `VAULT_MEMORY_ENABLE_DOCS=1`) (S26-5)
- `/redoc` — ReDoc documentation (enabled via `VAULT_MEMORY_ENABLE_DOCS=1`) (S26-5)
- `/openapi.json` — Raw OpenAPI 3.0 spec (enabled via `VAULT_MEMORY_ENABLE_DOCS=1`) (S26-5)
- `GET /graph?source=canvas` — Filter graph to Canvas-derived relationships only (S27-1)
- `X-RateLimit-Limit` and `X-RateLimit-Remaining` headers on all responses (S26-4)

### Changed

- Rate limiter now tracks by API key instead of IP (S26-4)
- `/promote` now validates content against high-trust notes before writing (S28-2)
- `edge_source` CHECK constraint expanded to include `'canvas'` (S27-1)
- Heartbeat cycle now includes stale session cleanup (S28-1)
- Version bumped to 0.8.0 in FastAPI app metadata
- Endpoint summary now includes previously undocumented `/bulk/import` and `/bulk/delete`

### Deprecated

- `GET /bulk/export` (full-mode) — use `POST /bulk/export?stream=true` instead (S26-3)

### Schema Changes

- Added `canvas_entities` table for Canvas-extracted entities (S27-1)
- Added `notes TEXT` column to `agent_sessions` (S28-3)

---

## [0.7.0] — 2026-04-25

### Added

- Lite mode (SQLite-only, no Docker required)
- Connection pooling for PostgreSQL (ThreadedConnectionPool)
- Formal DI container (`Dependencies` class)
- API key authentication (`VAULT_MEMORY_API_KEY`)
- Correlation ID middleware for request tracing
- HeartbeatService wrapper for background jobs
- Health router with `/health`, `/ready` endpoints
- `POST /sync/delta` endpoint for mobile sync
- Bulk operations queue (in-memory)
- Streaming bulk export (NDJSON)
- Per-client rate limiting
- OpenAPI documentation

### Changed

- Version aligned between `pyproject.toml` and runtime code (0.7.0)
- Env vars now highest priority (override config file)
- `search_siblings` SQL uses `ANY(%s)` list semantics
- Rate limiter performs periodic stale-key eviction
- Dev-mode API key warning moved to startup lifecycle log
- Audit middleware skips `/health`, `/ready`, `/metrics`
- `bulk_delete` redacts forbidden path validation errors
- Canvas parser uses real newlines for file-node content
- Postgres pool health-check returns connection to original pool on reinit
- Delete watcher events cancel pending upserts for same path
- Ripgrep fast-path only short-circuits for likely path/filename exact queries

### Fixed

- DI regression in `/temporal` endpoint
- `_check_dependencies` embedder health check
- `_sanitize_for_context` regex escaping
- `TAG_RE` double-escape fix
- Delete watcher thread handoff
- Rate-limit burst window
- `/cognify` switched to non-blocking `httpx.AsyncClient`
- PG health check connection return hardened
- Broken bulk endpoints (no longer query non-existent `notes` table)
- Syntax test collection
- Regex assertions made meaningful
- `mock_dependencies` uses `MagicMock(spec=Dependencies)`
- `docker-compose.yml` includes explicit resource limits

---

## [0.6.1] — 2026-04-15

### Added

- Observability layer (metrics, logging, tracing)
- Security hardening (argument injection fixes, error redaction)

---

## [0.6.0] — 2026-04-01

### Added

- Wiki layer with promotion workflow
- Topology-aware search
- Token-efficient context assembly
- Git integration
- Modernization pass

---

## Endpoint Summary (v0.8.0)

### Search & Retrieval

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/search` | 0.1.0 |
| `POST` | `/search_siblings` | 0.6.0 |
| `GET` | `/graph` | 0.1.0 |
| `GET` | `/temporal` | 0.1.0 |

### Sync

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/sync/file` | 0.1.0 |
| `POST` | `/sync/delta` | 0.8.0 |

### Bulk Operations

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/bulk/queue` | 0.8.0 |
| `GET` | `/bulk/status/{job_id}` | 0.8.0 |
| `DELETE` | `/bulk/cancel/{job_id}` | 0.8.0 |
| `POST` | `/bulk/import` | 0.1.0 |
| `POST` | `/bulk/export` | 0.1.0 |
| `POST` | `/bulk/export?stream=true` | 0.8.0 |
| `POST` | `/bulk/delete` | 0.1.0 |

### Sessions

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/sessions` | 0.6.0 |
| `GET` | `/sessions` | 0.6.0 |
| `PATCH` | `/sessions/{id}` | 0.6.0 |
| `GET` | `/sessions/{id}/attribution` | 0.8.0 |
| `POST` | `/sessions/cleanup` | 0.8.0 |

### Health & Usage

| Method | Endpoint | Since |
|--------|----------|-------|
| `GET` | `/health` | 0.5.0 |
| `GET` | `/ready` | 0.5.0 |
| `GET` | `/health/detailed` | 0.8.0 |
| `GET` | `/me/usage` | 0.8.0 |
| `GET` | `/metrics` | 0.6.1 |

### Knowledge

| Method | Endpoint | Since |
|--------|----------|-------|
| `POST` | `/promote` | 0.6.0 |
| `POST` | `/cognify` | 0.6.0 |
| `POST` | `/lint` | 0.6.0 |

### Documentation

| Method | Endpoint | Since |
|--------|----------|-------|
| `GET` | `/docs` | 0.8.0 |
| `GET` | `/redoc` | 0.8.0 |
| `GET` | `/openapi.json` | 0.8.0 |
