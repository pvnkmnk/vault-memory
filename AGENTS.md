# AGENTS.md — vault-memory

## What This Is

Python daemon providing semantic memory layer for Obsidian vaults. Exposes MCP tools to agents and serves a FastAPI daemon on port 5051.

## Quick Commands

```bash
# Start services (required for most operations)
docker compose up -d

# Run the daemon
vault-memory daemon start

# MCP adapter (for agents)
vault-memory mcp

# Sync vault
vault-memory sync --full --vault ~/path/to/vault

# Run tests
pytest tests/ -v
pytest tests/ -q --basetemp .pytest_tmp

# Syntax check
python -m py_compile daemon/main.py cli/mcp_adapter.py

# IMPORTANT: Run full test suite at the end of every sprint
```

## Key Paths

| Path | Purpose |
|------|---------|
| `daemon/main.py` | FastAPI server, MCP tool handlers |
| `daemon/dependencies.py` | DI container — typed service dependencies |
| `daemon/retrieval.py` | Search (vector, BM25, graph, temporal) |
| `daemon/sync_watcher.py` | File watcher, drift detection |
| `daemon/pg_client.py` | PostgreSQL client with connection pooling |
| `cli/mcp_adapter.py` | MCP stdio adapter (17 tools) |
| `init_db.sql` | PostgreSQL schema |
| `docker-compose.yml` | Weaviate + Postgres stack |

## Directory Structure (Wiki Layer Pattern)

- `Sources/` — Immutable raw material (articles, papers, transcripts). Never modified by agents.
- `Knowledge/` — LLM-promoted wiki pages (populated by `/promote`).
- `_working/` — Agent staging buffer (temporary working space).

## Architecture

- **Python 3.11+ required** — uses `match/case`, `re.Match`, structural pattern matching
- **DI Pattern**: Use `Dependencies` class from `daemon/dependencies.py`; do not use globals
- **DB Access**: Always use `with deps.postgres.cursor() as cursor:` context manager
- **API Key**: Set `VAULT_MEMORY_API_KEY` env var (required for production, optional in dev mode)

## Critical Gotchas

- Schema uses `id` as PK, not `session_id`
- Env vars override config file settings
- Canvas content uses real newlines, not literal `\n`
- `/bulk/import` and `/bulk/delete` return 410 — use sync/search flows instead

## Sprint Plan (Conductor)

Active: **S10 Bug Fixes**

| Sprint | Title | Status |
|--------|-------|--------|
| S10 | Bug fixes | PLANNED |
| S11 | Wiki layer | PLANNED |
| S12 | Topology | PLANNED |
| S13 | Token efficiency | PLANNED |
| S14 | Git integration | PLANNED |
| S15 | Modernize | PLANNED |
| S16 | Observability | PLANNED |
| S17 | Security hardening | PLANNED |
| S18 | Lite mode | PLANNED |
| S19 | Obsidian plugin | PLANNED |

Sprint details: `docs/sprints/`

## OpenCode Plugins

Configured in `.opencode/opencode.json`:
- `superpowers` — provides brainstorming, systematic-debugging, requesting-code-review skills
- `oh-my-opencode-slim`

## MCP Tools (17)

1. `search` — 4-strategy vault search
2. `search_siblings` — topic sibling traversal
3. `graph` — entity relationship traversal
4. `temporal` — date-range history
5. `health` — daemon status
6-17. Memory tools: attach_block, list_blocks, read_batch, write_working, delete_working, trigger_lookup, project_state, session_register, session_close, cognify, promote, vault_lint

## Agent Session Protocol

1. Call `memory/project_state` with project slug
2. Call `memory/session_register` with agent_name, project, task description
3. Read `AGENTS.md`

During: use `search` before generating, `memory/cognify` before writing knowledge, `memory/write_working` for drafts, `memory/promote` for wiki-quality content.

Session end: promote if wiki-quality → update STATE.md → call `memory/session_close`.