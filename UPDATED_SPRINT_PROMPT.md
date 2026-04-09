# vault-memory — Updated Sprint Prompt (Audit: 2026-04-08)

Use this prompt instead of the prior recommendation file. It is based on the current code in this repository and keeps only issues that are still open.

## Scope

Goal: fix startup/runtime crashes, restore MCP/CLI functionality, repair scoring + sync correctness, and harden tests.

## Current Verification Snapshot

- `python -m py_compile daemon/main.py daemon/sync_watcher.py daemon/retrieval.py daemon/weaviate_client.py daemon/pg_client.py daemon/heartbeat.py cli/mcp_adapter.py cli/sync_command.py` passes.
- `pytest tests/test_syntax.py tests/test_regex.py -q` fails in this environment due temp-dir permission (`J:\Temp\pytest-of-idols`), and test quality issues remain.

## Priority Plan

### Sprint S1 — P0 Crash and Hard Failure Fixes

1. `daemon/main.py`: fix `CorrelationMiddleware` header retrieval.
- Current code calls `request.headers.get(...)` with 3 args and will raise `TypeError` on every request.

2. `daemon/main.py` + `daemon/sync_watcher.py`: fix watcher construction mismatch.
- Current lifespan builds `VaultSyncWatcher(vault_path=..., weaviate=..., postgres=..., embedder=...)`.
- Actual constructor is `VaultSyncWatcher(engine: SyncEngine)`.

3. `daemon/weaviate_client.py`: add missing `upsert_chunk()`.
- `SyncEngine.sync_file()` and `_sync_canvas()` call `await self.weaviate.upsert_chunk(...)`.
- `WeaviateClient` has only `batch_upsert`, so sync currently raises `AttributeError`.

4. `daemon/main.py`: fix `agent_sessions` column names in session endpoints.
- `session_register` uses `RETURNING session_id`.
- `session_patch` queries and updates `WHERE session_id = %s`.
- Schema uses `id` primary key, not `session_id`.

5. `cli/sync_command.py`: fix immediate drift-mode crashes.
- Name collision: Click arg `check_drift` shadows function `check_drift(...)`, then code calls the boolean.
- Invalid import: `from daemon.sync_engine import SyncEngine` (module does not exist).
- `engine.sync_file(full_path)` is async but not awaited in `reindex_drifted`.

### Sprint S2 — MCP Authentication + Endpoint Wiring

1. `cli/mcp_adapter.py`: propagate `VAULT_MEMORY_API_KEY` via `x-api-key` header to all protected daemon calls.
- Current adapter performs unauthenticated `httpx` calls.

2. `cli/mcp_adapter.py`: fix sibling endpoint mismatch.
- Adapter posts to `POST /siblings`.
- Daemon route is `POST /search_siblings`.

3. `cli/mcp_adapter.py`: repair regexes used for hub/tag extraction.
- `hub_pattern` and fallback tag regex are double-escaped and do not behave as intended.

### Sprint S3 — Sync and Schema Correctness

1. `daemon/sync_watcher.py`: assign computed embeddings in canvas path.
- `_sync_canvas()` computes embeddings but never writes them to `node.embedding` / `edge.embedding` before upsert.

2. `daemon/weaviate_client.py`: align stored schema and upsert payload with scoring metadata.
- Missing properties in schema and write payload: `importance`, `trust`, `maturity`, `decay_profile`, `agent_written`.

3. `daemon/weaviate_client.py`: add safe schema migration path for existing collections (add missing properties without dropping collection).

### Sprint S4 — Retrieval + Heartbeat Scoring Pipeline

1. `daemon/retrieval.py`: fix `_apply_gars()` SQL for actual schema.
- Uses non-existent `sync_state` columns (`name`, `in_degree`, `out_degree`).
- Must use `file_path` and/or relationship-derived degree counts.

2. `daemon/heartbeat.py`: fix `refresh_topic_hubs()` SQL.
- Contains `LIMIT 1` in hub build path.
- Uses invalid `RETURNING COUNT(*)` on INSERT.
- Current logic can never populate hubs correctly at scale.

### Sprint S5 — Security and Runtime Resilience

1. `daemon/sync_watcher.py`: fix `_sanitize_for_context()` patterns.
- Patterns are double-escaped raw strings and miss intended prompt-injection text.

2. `daemon/sync_watcher.py`: fix `MarkdownParser.TAG_RE`.
- Current pattern does not match normal inline tags like `#project`.

3. `daemon/sync_watcher.py`: fix watcher delete event scheduling.
- Uses `asyncio.run_coroutine_threadsafe(self._queue.put_nowait, ...)` with non-coroutine and no loop.

4. `daemon/main.py`: fix `RateLimitMiddleware` burst logic.
- Burst check currently uses full minute window, causing false burst rejections.

5. `daemon/main.py`: make `/cognify` non-blocking.
- Uses blocking `requests.post` inside async endpoint.
- Replace with `httpx.AsyncClient`.

6. `daemon/pg_client.py`: harden `_health_check()` connection return.
- Ensure borrowed connection is always returned to pool via `finally`, including error paths.

### Sprint S6 — Dead/Invalid API Surface Cleanup

1. `daemon/main.py`: remove or replace `/bulk/import`, `/bulk/export`, `/bulk/delete`.
- They query table `notes`, which is not in `init_db.sql`.
- Keep only if a proper schema + sync path is implemented.

### Sprint S7 — Test Suite Repair and Regression Coverage

1. `tests/test_syntax.py`: convert helper into real pytest tests (`test_*` functions, paramized modules).
2. `tests/test_regex.py`: replace tautological assertions with exact behavior checks for `TAG_RE` and `STATUS_RE`.
3. Add focused regressions:
- sanitizer pattern coverage,
- GARS/RRF behavior sanity,
- session endpoints with `id` PK semantics,
- MCP auth header propagation.

### Sprint S8 — Documentation and Operational Alignment

1. `AGENTS.md`: add new sprint log for S1-S7.
2. `README.md` / docs: align endpoint/tool docs with actual routes and auth requirements.
3. `docker-compose.yml`: add explicit resource limits for local safety.

## Suggested Execution Order

1. S1 (must pass before anything else).
2. S2 (unblocks real MCP usage).
3. S3 + S4 (core search quality/correctness).
4. S5 (security/reliability).
5. S6 (remove broken API surface).
6. S7 + S8 (tests/docs closeout).

## Exit Criteria

1. Daemon boots and serves `/health` and `/ready` without middleware crash.
2. Full sync and drift-only sync both run without `AttributeError`, import errors, or coroutine misuse.
3. MCP tool calls work with `VAULT_MEMORY_API_KEY` set.
4. Search results include non-null trust/maturity/importance metadata.
5. Heartbeat populates `topic_hubs` with more than one row for non-trivial graphs.
6. Test suite collects real tests and passes in a writable temp-dir environment.
