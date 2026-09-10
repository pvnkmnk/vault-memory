# Contributing to vault-memory

Welcome! This guide covers everything you need to contribute to vault-memory — from initial setup to submitting your first PR.

---

## Quick Start

```bash
# 1. Clone and set up
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory
python3.11 -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .

# 2. Start infrastructure
docker compose up -d

# 3. Run tests
pytest tests/ -v

# 4. Start daemon
vault-memory daemon start
```

---

## Architecture Overview

vault-memory is a **semantic memory layer for Obsidian vaults**. It provides:

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Daemon** | FastAPI + Python 3.11+ | HTTP API on port 5051, file watcher, embedding pipeline |
| **CLI** | Click + MCP adapter | User commands + Model Context Protocol for AI agents |
| **Plugin** | TypeScript + Obsidian API | VaultPortal UI (search, graph, sync, settings) |
| **Storage** | PostgreSQL + Weaviate | Relational data + vector search |
| **Lite Mode** | SQLite only | No Docker required — reduced feature set |

### Key Directories

```
├── daemon/              # FastAPI server
│   ├── main.py          # Routes, middleware, lifespan
│   ├── dependencies.py  # DI container (typed service access)
│   ├── retrieval.py     # Search strategies (vector, BM25, graph, temporal)
│   ├── sync_watcher.py  # File watcher, chunking, Canvas parsing
│   ├── pg_client.py     # PostgreSQL client with connection pooling
│   ├── heartbeat.py     # Background maintenance jobs
│   ├── canvas_graph_pipeline.py  # Canvas → knowledge graph extraction
│   └── validate_write.py       # Pre-promotion duplicate guard
├── cli/                 # CLI commands + MCP adapter
│   └── mcp_adapter.py   # MCP stdio server (17 tools)
├── obsidian-plugin/     # Obsidian plugin (TypeScript)
│   └── src/
│       ├── main.ts      # Plugin entry point
│       ├── components/  # DaemonClient, AutoSyncEngine, ThemeObserver
│       └── views/       # SearchPanel, GraphCanvas, SyncHistoryPanel
├── tests/               # Unit + integration tests
├── docs/sprints/        # Sprint designs and conductor master
└── init_db.sql          # PostgreSQL schema
```

---

## Code Conventions

### Python (Daemon)

- **Python 3.11+ required** — uses `match/case`, `re.Match`, structural pattern matching
- **Async/sync boundary**: psycopg2 is synchronous — always use `asyncio.to_thread()` for DB calls
- **Dependency Injection**: access services through the `Dependencies` container, never globals

```python
from daemon.dependencies import Dependencies, get_dependencies

@app.get("/endpoint")
async def endpoint(deps: Dependencies = Depends(get_dependencies)):
    with deps.postgres.cursor() as cursor:
        cursor.execute("SELECT ...")
```

- **Error responses**: use `error_response()` helper — never expose stack traces or technical details to clients
- **Security**: use `--` separator in subprocess calls, validate all user input, redact paths in error messages

### TypeScript (Plugin)

- **No external UI frameworks** — vanilla DOM manipulation via Obsidian's `createEl` API
- **Error handling**: use `DaemonClient.translateError()` for user-friendly messages
- **Offline detection**: check `DaemonClient.isPersistentlyOffline()` before making requests
- **Theme awareness**: use CSS variables (`var(--interactive-accent)`) — the `ThemeObserver` handles dark/light mode

### Testing

```bash
# Run all tests
pytest tests/ -v

# Quick syntax check
python -m py_compile daemon/main.py

# TypeScript check
cd obsidian-plugin && npx tsc --noEmit
```

### Integration Tests (real PostgreSQL + Weaviate)

Unit tests mock heavy dependencies (`tests/conftest.py`) so they run anywhere in ~1s.
Real-service integration tests live in `tests/test_integration.py` and are marked
`integration`. They **auto-skip** when services are unreachable — to run them for real:

```bash
# Option A: Docker (standard)
docker compose up -d

# Option B: Native binaries (no Docker, lean systems)
# PostgreSQL:
service postgresql start
sudo -u postgres psql -c "CREATE USER vault WITH PASSWORD 'vault_local';"
sudo -u postgres createdb -O vault vault_memory
sudo -u postgres psql -d vault_memory -f init_db.sql
# Weaviate (single ~137MB binary, matching docker-compose version):
#   download weaviate-v<ver>-linux-amd64.tar.gz from weaviate/weaviate releases,
#   run: weaviate --host 127.0.0.1 --port 8080 --scheme http
#        (config: anonymous access, vectorizer none, persistence dataPath)

# Then run:
pytest tests/test_integration.py -m integration -v
```

Coverage: Weaviate batch-upsert/search performance, Postgres sync-state/query/batch
performance, end-to-end sync throughput, parallel batching. The e2e test additionally
requires `sentence-transformers` (skipped otherwise).

### CI (GitHub Actions)

`.github/workflows/integration.yml` runs two jobs on every PR/push to `main`:

- **Unit tests** — `pytest -m "not integration"` with the default conftest mocks (no services needed).
- **Integration tests** — Postgres 16 + Weaviate 1.36.8 + Ollama service containers plus a
  step-started llama.cpp `llama-server` (same image, GGUF and flags as the Compose `llm`
  profile), `init_db.sql` applied automatically, `llama3.2:1b` pulled for the LLM, then
  `pytest tests/test_integration.py tests/test_cognify_e2e.py -m integration`
  with `VAULT_MEMORY_REAL_SERVICES=1`.

Set `VAULT_MEMORY_REAL_SERVICES=1` locally (with `docker compose --profile llm up -d`
or native services — Ollama on 11434 and llama.cpp on 8081 are what the tests use —
plus the models pulled) to mirror the CI integration environment — it disables
conftest's heavy-dependency mocks so tests hit the real services. The `/cognify`
E2E tests (`tests/test_cognify_e2e.py`) skip themselves when their provider is
unavailable (`TEST_OLLAMA_MODEL`, default `llama3.2:1b`, via `/api/tags`; the
llama.cpp server via `/health` on 8081).

LLM backend for `/cognify` is provider-switchable (`LLM_PROVIDER=ollama|llamacpp`);
provider parsing/HTTP-building logic is unit-tested without a running LLM
(`tests/test_cognify_providers.py` — extraction mocked at the HTTP boundary),
while the llama.cpp end-to-end path (extraction + Postgres persistence through
the live `/cognify` route) is covered by the integration E2E test in
`tests/test_cognify_e2e.py`.

**Mocking pattern** for tests:
```python
from unittest.mock import MagicMock
from daemon.dependencies import Dependencies

mock_deps = MagicMock(spec=Dependencies)
mock_deps.postgres.cursor.return_value.__enter__.return_value.fetchall.return_value = [...]
```

---

## Git Workflow

### Branch Naming

Use the pattern `codex/<linear-id>` or `feature/<description>`:

```
codex/VAU-30-canvas-graph-pipeline
feature/streaming-bulk-export
fix/rate-limit-header-bug
```

### Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add POST /sync/delta endpoint for incremental sync
fix: resolve None guard in pg_client health check
docs: expand USER_GUIDE with lite mode section
test: add regression tests for write validator
```

### PR Checklist

Before submitting a PR:

- [ ] `pytest tests/ -v` passes (or explains why skipped)
- [ ] `python -m py_compile` passes for all changed `.py` files
- [ ] `npx tsc --noEmit` passes for plugin changes
- [ ] No secrets, API keys, or credentials in diff
- [ ] Commit message follows conventional format
- [ ] New endpoints have docstrings

---

## Debugging

### Daemon Debug Mode

```bash
# Verbose logging
RUST_LOG=debug vault-memory daemon start

# Foreground mode (see logs in real-time)
uvicorn daemon.main:app --host 127.0.0.1 --port 5051 --log-level debug
```

### Health Check

```bash
# Quick status
vault-memory health

# Detailed subsystem health (S28-4)
curl http://localhost:5051/health/detailed | jq

# Readiness probe
curl http://localhost:5051/ready | jq
```

### Lite Mode (No Docker)

```bash
export VAULT_MEMORY_LITE_MODE=true
vault-memory daemon start
# Uses SQLite only — no Weaviate/PostgreSQL required
```

### Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: No module named 'daemon'` | Not in venv or not installed | `pip install -e .` |
| `Address already in use` (port 5051) | Another process on port | `lsof -i :5051` or change port in config |
| `Connection refused` (Weaviate) | Docker not running | `docker compose up -d` |
| `401 Unauthorized` | API key not set | `export VAULT_MEMORY_API_KEY="..."` |
| `pytest asyncio_mode warning` | Plugin/tooling mismatch | Harmless — tests still run |

---

## Sprint Process

Sprints are tracked in `docs/sprints/`. Each sprint has a design document:

| Sprint | Title | Status |
|--------|-------|--------|
| S24 | Vault Cleanup | ✅ DONE |
| S25 | Plugin UX & Polish | ✅ DONE |
| S26 | API & Data Infrastructure | ✅ DONE |
| S27 | Knowledge Graph & Canvas | ✅ DONE |
| S28 | Operations & Reliability | ✅ DONE |
| S29 | Documentation | ✅ DONE |

See `docs/sprints/CONDUCTOR_MASTER.md` for the full roadmap.

---

## Need Help?

- **High-level overview**: [README.md](README.md)
- **User guide**: [USER_GUIDE.md](USER_GUIDE.md)
- **Agent conventions**: [AGENTS.md](AGENTS.md)
- **API docs**: Start daemon with `VAULT_MEMORY_ENABLE_DOCS=1` then visit `http://localhost:5051/docs`
