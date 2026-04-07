# vault-memory

> Always-on local memory layer for Obsidian — semantic search, knowledge graph, temporal reasoning, and MCP agent interface.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-compose-blue.svg)](docker-compose.yml)

---

## What It Does

`vault-memory` turns your Obsidian vault into a queryable, agentic memory system. Every note is chunked, embedded, and indexed the moment you save it. A persistent local daemon makes everything available under 200ms — to you, to AI agents via MCP, or to any HTTP client.

```
Your Obsidian Vault  →  vault-memoryd  →  Weaviate (vectors)
       ↓                      ↓          →  PostgreSQL (graph + time)
  file watcher          HTTP API          →  MCP stdio adapter
  (real-time sync)      /search           →  CLI (vault-memory)
                        /health
                        /graph
                        /temporal
```

---

## Architecture

| Component | Role |
|---|---|
| `vault-memoryd` | Always-on FastAPI daemon. Owns DB connections, models, file watcher. |
| `vault-memory` | CLI: search, daemon control, MCP adapter, full sync. |
| `obsidian-plugin` | TypeScript plugin: spawns daemon, health monitor, status bar. |
| Weaviate | Local vector DB — dense (nearVector) + sparse (BM25) retrieval. |
| PostgreSQL | Temporal KG — entity graph, workflow history, time-slice queries. |

### Four-Strategy Retrieval Pipeline

1. **Dense** — `sentence-transformers/e5-large` vector similarity (semantic meaning, synonyms)
2. **Sparse** — BM25 keyword search (exact terms, proper nouns, commands)
3. **Graph** — Multi-hop PostgreSQL traversal (entity relationships, wikilinks)
4. **Temporal** — Time-sliced workflow history ("what changed since January")

All four strategies run in parallel via `asyncio.gather`, fused with **Reciprocal Rank Fusion (k=60)**, then reranked by a cross-encoder.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory

# 2. Start services
docker compose up -d

# 3. Install
pip install -e .

# 4. Configure
cp .vault-memory.json.example .vault-memory.json
# Edit vault_path to point to your Obsidian vault

# 5. First-time full index
vault-memory sync --full

# 6. Start daemon
vault-memory daemon start

# 7. Search
vault-memory search -q "music production workflow"
```

See **[USER_GUIDE.md](USER_GUIDE.md)** for the complete step-by-step guide including Docker setup, Obsidian plugin installation, MCP agent configuration, and operational runbook.

---

## File Layout

```
vault-memory/
├── README.md
├── USER_GUIDE.md                    ← Complete setup + operations guide
│
├── daemon/                          # vault-memoryd (Python/FastAPI)
│   ├── main.py                      # FastAPI app, lifespan, HTTP routes
│   ├── health.py                    # /health + /ready endpoints
│   ├── retrieval.py                 # 4-strategy search pipeline + RRF
│   ├── sync_watcher.py              # File watcher, chunker, full sync engine
│   ├── weaviate_client.py           # Weaviate v4 batch upsert wrapper
│   ├── pg_client.py                 # PostgreSQL connection wrapper
│   ├── embedder.py                  # SentenceTransformer + CrossEncoder
│   └── config.py                    # Settings (env + .vault-memory.json)
│
├── cli/
│   ├── main.py                      # Click CLI entry point
│   ├── sync_command.py              # vault-memory sync --full
│   ├── mcp_adapter.py               # stdio JSON-RPC MCP adapter
│   └── proxy.py                     # HTTP → daemon proxy
│
├── obsidian-plugin/                 # TypeScript Obsidian plugin
│   ├── manifest.json
│   ├── src/
│   │   ├── main.ts
│   │   ├── daemon-manager.ts
│   │   ├── health-monitor.ts
│   │   ├── status-bar.ts
│   │   └── settings.ts
│   └── styles.css
│
├── docker-compose.yml               # Weaviate + PostgreSQL
├── init_db.sql                      # PostgreSQL schema
├── pyproject.toml                   # Python package + CLI entry points
└── .vault-memory.json               # User config
```

---

## API Reference

### `POST /search`
```json
{
  "query": "music production workflow",
  "project": "djinn-netrunner",
  "top_k": 5,
  "include_graph": false,
  "include_temporal": false,
  "time_range": {"start": "2026-01-01", "end": "2026-04-07"}
}
```

### `GET /health` — Liveness probe (always fast)
### `GET /ready` — Readiness probe (checks deps)
### `GET /graph?entity=NAME` — Graph traversal
### `GET /temporal?entity=NAME&start=DATE&end=DATE` — Time-slice query

---

## MCP Integration

```bash
# Start MCP stdio adapter (for Claude, Cursor, etc.)
vault-memory mcp
```

Add to your MCP client config:
```json
{
  "mcpServers": {
    "vault-memory": {
      "command": "vault-memory",
      "args": ["mcp"]
    }
  }
}
```

---

## Performance

| Operation | Latency |
|---|---|
| Simple semantic search | ~80ms |
| All four strategies | ~180ms |
| Incremental file sync | 0.5–2s |
| Full sync (1000 notes) | ~200s |

---

## Requirements

- Python 3.11+
- Docker + Docker Compose
- 4GB RAM minimum (8GB recommended for embedding models)
- macOS, Linux, or WSL2

---

## License

MIT — see [LICENSE](LICENSE)
