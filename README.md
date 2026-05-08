# vault-memory

Local semantic memory daemon for Obsidian vaults.

`vault-memory` indexes Markdown and Canvas content into a hybrid retrieval stack (vector + keyword + graph + temporal) and exposes search/memory APIs over HTTP and MCP for agent tooling.

**Version:** 0.8.0 — Lite Mode + VaultPortal Plugin

## What This Repository Provides

- FastAPI daemon (`vault-memoryd`) for search, graph/temporal retrieval, session tracking, and bulk vault operations.
- Vault sync engine with full sync, file watching, and drift-aware reconciliation.
- MCP adapter (`vault-memory mcp`) exposing memory/search tools to MCP-compatible clients.
- PostgreSQL-backed graph/temporal metadata and Weaviate-backed semantic retrieval.

## Project Scope

This README is intentionally concise and focused on:
- system introduction,
- quick setup,
- architecture,
- dependencies.

For full installation, operations, runbooks, and troubleshooting, use [USER_GUIDE.md](J:/Repos/vault-memory-code/USER_GUIDE.md).

## Quick Setup

1. Clone and install:

```bash
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. Start infrastructure:

```bash
docker compose up -d
```

3. Configure vault path:

- Create `~/.vault-memory.json` or `<vault>/.vault-memory.json`.
- Minimum config:

```json
{
  "vault_path": "/absolute/path/to/ObsidianVault",
  "weaviate_url": "http://127.0.0.1:8080",
  "pg_connection_string": "dbname=vault_memory user=vault password=vault_local host=localhost",
  "port": 5051
}
```

4. Initial indexing:

```bash
vault-memory sync --full --vault /absolute/path/to/ObsidianVault
```

5. Start daemon and verify:

```bash
vault-memory daemon start
vault-memory health --watch
```

## Codex Desktop Setup (Windows)

Use the bootstrap script when working in Codex Desktop on Windows:

```powershell
.\scripts\setup-codex-windows.ps1
```

Common flags:

- `-Lite` installs `.[lite]` extras.
- `-SkipPluginDeps` skips `obsidian-plugin` npm install.
- `-StartServices` runs `docker compose up -d`.

## Architecture

### High-Level Components

- `daemon/main.py`: HTTP API, middleware, auth, lifecycle wiring.
- `daemon/sync_watcher.py`: full sync, watcher queue, drift state.
- `daemon/retrieval.py`: retrieval orchestration, fusion, GARS ranking.
- `daemon/heartbeat.py`: centrality + topic hub refresh jobs.
- `daemon/pg_client.py`: pooled PostgreSQL access.
- `daemon/weaviate_client.py`: Weaviate schema + upsert/delete operations.
- `cli/mcp_adapter.py`: MCP JSON-RPC to daemon bridge.

### Data/Request Flow

1. Vault files are parsed/chunked by sync engine.
2. Chunks are embedded and written to Weaviate.
3. Metadata/relationships/state are written to PostgreSQL.
4. Query path executes dense + sparse + optional graph/temporal retrieval.
5. Results are fused/ranked and returned via HTTP or MCP.

### Primary Stores

- Weaviate: chunk vectors + searchable chunk metadata.
- PostgreSQL: relationships, hubs, sync state, workflow/session metadata.

## Runtime Dependencies

### Core

- Python 3.11+
- FastAPI / Uvicorn
- Weaviate (Docker)
- PostgreSQL 16 (Docker)
- sentence-transformers + CrossEncoder
- watchdog
- click + rich + httpx

### Environment Variables

- `VAULT_PATH`
- `VAULT_MEMORY_PORT`
- `WEAVIATE_URL`
- `PG_CONNECTION_STRING`
- `EMBEDDING_MODEL`
- `RERANKER_MODEL`
- `HEARTBEAT_INTERVAL_SECONDS`
- `VAULT_MEMORY_API_KEY`

## API Surface (Summary)

Authenticated (API key required except health/readiness):
- `/search`
- `/search_siblings`
- `/graph`
- `/graph/canvas_export` (GET)
- `/temporal`
- `/sessions` (POST/GET)
- `/sessions/{id}` (PATCH)
- `/sessions/cleanup` (POST)
- `/sessions/{id}/attribution` (GET)
- `/cognify`
- `/promote`
- `/lint`
- `/sync/file` (POST)
- `/sync` (POST)
- `/sync/delta` (POST)
- `/sync/ws` (WebSocket)
- `/bulk/import`
- `/bulk/export`
- `/bulk/delete` (uses `paths` request field)
- `/bulk/queue` (POST)
- `/bulk/status/{job_id}` (GET)
- `/bulk/cancel/{job_id}` (DELETE)
- `/me/usage` (GET)
- `/health/detailed` (GET)

Public:
- `/health`
- `/ready`
- `/docs` (Swagger UI)
- `/redoc` (ReDoc UI)
- `/openapi.json` (OpenAPI spec)

## Operational Notes (0.8.0)

- `/temporal` now runs fully through DI (`deps.postgres`) with no global DB references.
- `/search_siblings` uses `ANY(%s)` list binding for safe Postgres array filtering.
- `/bulk/import` and `/bulk/delete` degrade safely when watcher is unavailable.
- `bulk_delete` redacts invalid/forbidden path errors (`"Invalid or forbidden path"`).
- Rate limiting includes periodic stale-key eviction to prevent unbounded in-memory growth.
- Audit logging skips probe endpoints (`/health`, `/ready`, `/metrics`).
- `CanvasParser` emits real newlines for file-backed node content.
- Docker Compose now includes resource limits for Weaviate and Postgres services.

## Documentation

- Full setup and operations: [USER_GUIDE.md](J:/Repos/vault-memory-code/USER_GUIDE.md)
- Scoring details: [docs/SCORING.md](J:/Repos/vault-memory-code/docs/SCORING.md)
- Sync model: [docs/SLIM_SYNC.md](J:/Repos/vault-memory-code/docs/SLIM_SYNC.md)
- Agent-facing repo notes: [AGENTS.md](J:/Repos/vault-memory-code/AGENTS.md)
