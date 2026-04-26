# AGENTS.md — vault-memory

## What This Is

Python daemon providing semantic memory layer for Obsidian vaults. Exposes MCP tools to agents and serves a FastAPI daemon on port 5051.

## Quick Commands

```bash
# Start services (Weaviate + Postgres)
docker compose up -d

# Run the daemon
vault-memory daemon start

# MCP adapter (for agents)
vault-memory mcp

# Sync vault
vault-memory sync --full --vault ~/path/to/vault

# Search
vault-memory search -q "query"
```

## Key Paths

| Path | Purpose |
|------|---------|
| `daemon/main.py` | FastAPI server, MCP tool handlers |
| `daemon/dependencies.py` | **DI container** — typed service dependencies |
| `daemon/retrieval.py` | Search implementation (vector, BM25, graph, temporal) |
| `daemon/sync_watcher.py` | File watcher, drift detection |
| `daemon/pg_client.py` | PostgreSQL client with connection pooling |
| `cli/mcp_adapter.py` | MCP stdio adapter (exposes 17 tools) |
| `init_db.sql` | PostgreSQL schema |
| `docs/SCORING.md` | GARS algorithm details |

## Critical Gotchas

- **Python 3.11+ required** — uses `match/case`, `re.Match`, structural pattern matching
- **Schema mismatch**: `init_db.sql` uses `id` as PK, not `session_id`
- **Syntax errors**: CLI/mcp_adapter.py had quote issues, daemon/main.py had indentation errors — FIXED in Sprint 1
- **Regex bugs**: Double-escaped `\\` in sync_watcher.py lines 146-147 (TAG_RE, STATUS_RE) — FIXED in Sprint 2
- **Async/sync mismatch**: sync_watcher.py line 567 used call_soon_threadsafe incorrectly — FIXED in Sprint 2
- **Config priority bug**: Env vars were overridden by config file (daemon/config.py) — FIXED in Sprint 2 (env vars now highest priority)
- **N+1 query**: search_siblings had loop with individual DB queries — FIXED in Sprint 3 (batched into single query)
- **Cursor leaks**: Verified — code already has proper try/except with cursor.close()
- **ContextAssembler integration**: Was orphaned (context_assembler.py existed but never used) — FIXED in Sprint 4 (integrated into retrieval pipeline with token_budget param)
- **GARS Scoring**: Was partial (RRR fusion used but explicit GARS formula was missing) — FIXED in Sprint 4 (added explicit formula + _apply_gars method)
- **Drift Detection CLI**: Was documented but not implemented — FIXED in Sprint 4 (added --check-drift, --drift-only flags)
- **HeartbeatService**: Was importing non-existent class (HeartbeatService vs HeartbeatJob) — FIXED in Sprint 5 (added proper wrapper)
- **Correlation IDs**: Added middleware for request tracing — IMPLEMENTED in Sprint 5
- **Health router import**: Was importing non-existent router from health.py — FIXED in Sprint 5 (added router with /health, /ready endpoints + mark_ready/mark_degraded functions)
- **Authentication**: No API key protection — ADDED in this session (verify_api_key dependency with VAULT_MEMORY_API_KEY env var)
- **Version mismatch**: pyproject.toml showed 0.2.0, code showed 0.5.0 — FIXED (pyproject.toml now 0.7.0)
- **Lite Mode**: Added SQLite-only mode — IMPLEMENTED in S18 (no PostgreSQL/Weaviate required)
- **Connection Pooling**: Single shared connection — FIXED in Sprint 6 (added ThreadedConnectionPool with context managers)
- **DI Framework**: Ad-hoc service access via globals — FIXED in Sprint 7 (formal DI container with `Dependencies` class)

## Dependency Injection (Sprint 7)

The daemon now uses a formal DI container pattern:

```python
from daemon.dependencies import Dependencies, get_dependencies

@app.get("/endpoint")
async def endpoint(deps: Dependencies = Depends(get_dependencies)):
    # Access services through typed properties
    weaviate = deps.weaviate
    postgres = deps.postgres
    embedder = deps.embedder
    searcher = deps.searcher
    settings = deps.settings
    
    # Use context manager for database access
    with deps.postgres.cursor() as cursor:
        cursor.execute("SELECT ...")
```

**Benefits:**
- Type-safe service access with Protocol definitions
- Easy testing — mock Dependencies container
- Automatic connection lifecycle management
- No global variables in endpoint code

## Testing

```bash
# Run tests
pytest tests/ -v
pytest tests/ -q --basetemp .pytest_tmp

# Quick syntax check
python -m py_compile daemon/main.py
python -m py_compile cli/mcp_adapter.py
```

## Version Mismatch Note

`pyproject.toml` and runtime code are now aligned at **0.7.0**.

## MCP Tools Available

1. `search` — 4-strategy vault search
2. `search_siblings` — topic sibling traversal
3. `graph` — entity relationship traversal
4. `temporal` — date-range history
5. `health` — daemon status
6. `memory/attach_block` — attach context block
7. `memory/list_blocks` — list blocks + tokens
8. `memory/read_batch` — read multiple vault files in one round-trip
9. `memory/write_working` — write to `_working/`
10. `memory/delete_working` — delete a file from `_working/`
11. `memory/trigger_lookup` — keyword → context
12. `memory/project_state` — full session-start bundle for a project
13. `memory/session_register` — register an agent session
14. `memory/session_close` — close a registered agent session
15. `memory/cognify` — Ollama LLM triple extraction for knowledge graph
16. `memory/promote` — promote wiki-quality synthesis to permanent vault page
17. `vault_lint` — vault health check (orphans, contradictions, stale nodes, missing pages)

## Sprint S1–S8 (Comprehensive Audit Fix — April 2026)

### Fixed
- S1: Correlation middleware header bug, `VaultSyncWatcher` constructor mismatch, missing `WeaviateClient.upsert_chunk`, session SQL `session_id`/`id` mismatch, drift CLI hard failures.
- S2: MCP adapter API key propagation to protected daemon routes, `/siblings` to `/search_siblings` endpoint alignment, sibling regex fixes.
- S3: Canvas embedding assignment before upsert, Weaviate schema properties + upsert payload for `importance`, `trust`, `maturity`, `decay_profile`, `agent_written`, safe property migration path.
- S4: GARS SQL now uses real schema (`file_path`) and relationship-derived activation/out-degree, heartbeat topic hub SQL fixed (`LIMIT 1` and invalid `RETURNING COUNT(*)` removed).
- S5: `_sanitize_for_context` regex escaping fixed, `TAG_RE` fixed, delete watcher thread handoff fixed, rate-limit burst window fixed, `/cognify` switched to non-blocking `httpx.AsyncClient`, pg health check connection return hardened.
- S6: Broken bulk endpoints no longer query non-existent `notes` table; they now return `410` with guidance to use sync/search flows.
- S7: Syntax test collection fixed, regex assertions made meaningful, sanitizer and MCP auth regression tests added.
- S8: Updated sprint prompt and docs to reflect current behavior and remaining operational constraints.

## Sprint S9 (Ritual Layer Hardening Follow-up — April 2026)

### Fixed
- `/temporal` DI regression fixed (`deps.postgres` used end-to-end).
- `_check_dependencies` embedder health check now uses `app.state.embedder`.
- `/search_siblings` SQL binding corrected (`ANY(%s)` list semantics).
- `/bulk/import` and `/bulk/delete` now null-guard watcher usage with warning logs.
- Rate limiter now performs periodic stale-key eviction to cap in-memory growth.
- Dev-mode API key warning moved to startup lifecycle log (not per request).
- Audit middleware skips `/health`, `/ready`, `/metrics`.
- `bulk_delete` redacts forbidden path validation errors.
- Canvas parser file-node content now uses real newlines.
- Postgres pool health-check now returns connection to original pool on reinit path.
- Delete watcher events now cancel pending upserts for the same path.
- Ripgrep fast-path only short-circuits for likely path/filename exact queries.
- `tests/conftest.py` `mock_dependencies` uses `MagicMock(spec=Dependencies)`.
- `docker-compose.yml` includes explicit resource limits for Weaviate/Postgres.

### Known remaining gaps
- Full integration tests still depend on local services (Postgres/Weaviate/Ollama) and are not fully exercised by the lightweight unit test subset.
- Pytest config warning for `asyncio_mode` appears in this environment due plugin/tooling mismatch.

---

## Agent Session Protocol

Every agent working in this vault MUST follow this ritual. No exceptions.

### Session Start (in order)

1. Call `memory/project_state` with your project slug — loads identity, STATE.md, roadmap, and semantic context in one call.
2. Call `memory/session_register` with `agent_name`, `project`, and a one-line task description — registers the session for audit trail.
3. Read `AGENTS.md` (this file) to load operational conventions.

### During Session

- Use `search` for any question that might be answered by existing vault knowledge before generating new content.
- Use `memory/cognify` before writing any new knowledge — extract triples first.
- Use `memory/write_working` for drafts, scratch work, and uncertain output.
- Use `memory/promote` for any synthesis, analysis, or comparison that is wiki-quality.

### Wiki-Quality Threshold (promote vs. write_working)

Promote if all of the following are true:

- [ ] The content answers a question that will recur
- [ ] The content synthesises across multiple sources or sessions
- [ ] You are confident (`confidence: high`) in the accuracy
- [ ] The content would be useful to a future agent starting fresh

Write to `_working/` if any of the above is false.

### Page Conventions

| Page Type | When to use | Filename convention | maturity |
|------|---------|---------|---------|
| entity | A named thing (project, person, tool) | `{name}.md` | sapling→tree |
| concept | An idea or pattern without a fixed name | `concept-{slug}.md` | sapling |
| comparison | Side-by-side analysis of two+ things | `compare-{a}-vs-{b}.md` | tree |
| analysis | Deep dive on a single topic | `analysis-{slug}.md` | tree |
| lint report | `vault_lint` output | `lint-YYYY-MM-DD.md` | seed |

### Session End (in order)

1. Call `memory/promote` for any response in this session that meets wiki-quality threshold.
2. Update `STATE.md` with current position, last decision, and next action.
3. Call `memory/session_close` with your `session_id`.

---

## Security Audit (Sentinel) - April 2026

### Fixed
- Fixed argument injection vulnerability in `_ripgrep_search` by using `--` separator.
- Fixed functional bug in `_ripgrep_search` where `-l` and `--json` flags were conflicting.
- Hardened `error_response` to hide technical details for ALL server-side errors (5xx).
