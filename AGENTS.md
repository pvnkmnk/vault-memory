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
| `daemon/retrieval.py` | Search implementation (vector, BM25, graph, temporal) |
| `daemon/sync_watcher.py` | File watcher, drift detection |
| `cli/mcp_adapter.py` | MCP stdio adapter (exposes 9 tools) |
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
- **Version mismatch**: pyproject.toml showed 0.2.0, code showed 0.5.0 — FIXED (pyproject.toml now 0.5.0)

## Testing

```bash
# Run tests
pytest tests/ -v

# Quick syntax check
python -m py_compile daemon/main.py
python -m py_compile cli/mcp_adapter.py
```

## Version Mismatch Note

README shows **v0.5.0** but `pyproject.toml` shows **0.2.0** — version sync needed.

## MCP Tools Available

1. `search` — 4-strategy vault search
2. `search_siblings` — topic sibling traversal
3. `graph` — entity relationship traversal
4. `temporal` — date-range history
5. `health` — daemon status
6. `memory/attach_block` — attach context block
7. `memory/list_blocks` — list blocks + tokens
8. `memory/write_working` — write to `_working/`
9. `memory/trigger_lookup` — keyword → context
