# vault-memory

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Version: 0.5.0](https://img.shields.io/badge/version-0.5.0-green.svg)](./CHANGELOG.md)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://docker.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688.svg)](https://fastapi.tiangolo.com)

> **Always-on local memory layer for Obsidian** — semantic search, knowledge graph, temporal history, agentic write safety, and transferable session state.

**v0.5.0** — GARS scoring · Topic sibling traversal · Accordion context assembly · Slim-sync cold store · Split-brain buffer protection · Edge typing · Agent runtime dirs · Dependency Injection · Connection Pooling

---

## What It Does

`vault-memory` is a Python daemon that runs alongside your Obsidian vault and gives AI agents (Gemini CLI, OpenCode, Claude Code, Cursor) a **production-grade memory system**:

### Core Capabilities

| Feature | What It Does | Why It Matters |
|---------|--------------|----------------|
| **4-Strategy Retrieval** | Dense vector + BM25 sparse + knowledge graph + temporal history, fused with RRF | Finds relevant notes even when keywords don't match |
| **GARS Re-ranking** | Graph-Augmented Relevance Score combines similarity, centrality, and activation | Surfaces structurally important notes that might be semantically weak |
| **Topic Sibling Traversal** | Discovers notes linked through shared Ontology topics | Finds conceptually related notes without direct wikilinks |
| **Accordion Context Assembly** | Relative-threshold tiers pack context windows at maximum density | No token waste, optimal LLM context utilization |
| **Temporal Decay Scoring** | Recent notes outrank old ones with configurable profiles | Keeps current projects prioritized over archived work |
| **Write Layer Gate** | Agents can only write to `_working/`; heartbeat promotes to semantic memory | Prevents agents from encoding bad reasoning into long-term memory |
| **Trust + Maturity System** | Notes carry trust levels and maturity stages | Quality gates prevent premature promotion of agent-written content |
| **Session State Protocol** | Structured state files per project | Agents can cold-start any project in ~500 tokens |
| **Slim-Sync Cold Store** | Drift detection with split-brain protection | Reliable sync between Weaviate and vault filesystem |
| **MCP-Native** | 9 tools exposed via stdio for any MCP-compliant agent | Universal compatibility with modern AI agents |

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Start infrastructure
docker compose up -d

# 3. Configure (edit with your vault path)
cp .vault-memory.json ~/ObsidianVault/.vault-memory.json

# 4. Initial sync (one-time, ~10-30 min depending on vault size)
vault-memory sync --full --vault ~/ObsidianVault

# 5. Start daemon
vault-memory daemon start

# 6. Verify
vault-memory health --watch
```

**That's it.** The daemon now watches your vault and serves semantic search on `localhost:5051`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Obsidian Vault (.md files)                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        VaultSyncWatcher                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │
│  │   Watchdog  │  │   Drift     │  │   Write     │  │   Hourly    │  │
│  │  (real-time)│  │  Detection  │  │   Layer     │  │   Reconcile │  │
│  └─────────────┘  │   (hashes)  │  │   Gate      │  │             │  │
│                   └─────────────┘  └─────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        MarkdownParser                                    │
│  • Frontmatter extraction  • Trust/Maturity tags  • Entity linking      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         SyncEngine                                         │
│  Chunk → Embed → Upsert (with split-brain buffer protection)              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌─────────────────────────────┐    ┌─────────────────────────────┐
│         Weaviate            │    │         PostgreSQL          │
│      (Vector Store)         │    │    (Knowledge Graph +      │
│  • Dense embeddings         │    │     Temporal History)       │
│  • BM25 sparse search       │    │  • Entity relationships     │
│  • Hybrid fusion            │    │  • Topic hubs               │
│                             │    │  • Session registry         │
│                             │    │  • Slim-sync state          │
└──────────────┬──────────────┘    └──────────────┬──────────────┘
               │                                  │
               └──────────────┬───────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        UnifiedSearch                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐ │
│  │  Dense   │ │  Sparse  │ │  Graph   │ │ Temporal │ │  Accordion   │ │
│  │ (vector) │ │ (BM25)   │ │  (GARS)  │ │ (dates)  │ │   Assembly   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘ │
│       └─────────────┴─────────────┴─────────────┴──────────────┘        │
│                              │                                          │
│                              ▼                                          │
│                    ┌─────────────────┐                                   │
│                    │   RRF Fusion    │                                   │
│                    │  + GARS Rerank  │                                   │
│                    └────────┬────────┘                                   │
│                             │                                           │
└─────────────────────────────┼─────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Daemon (:5051)                                │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐         │
│  │ /search │ │ /graph  │ │/temporal│ │/sessions│ │/cognify │         │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘         │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              MCP stdio adapter (9 tools for agents)               │   │
│  │  search · graph · temporal · health · memory/* · cognify · ...    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## GARS Scoring

Search results are re-ranked using **Graph-Augmented Relevance Score** after initial candidate retrieval:

```
GARS = (sim × W_sim) + (cent × W_cent) + (act × W_act)
```

| Component | Default | Meaning |
|-----------|---------|---------|
| `sim` | 0.70 | Blended vector + BM25 similarity |
| `cent` | 0.20 | Degree centrality (structural importance) |
| `act` | 0.10 | Neighbor activation (co-occurrence with other hits) |

BM25 scores are sigmoid-calibrated: `normalized = score / (score + 1.2)`

**Edge type weights:**

| Edge source | Weight | Example |
|-------------|--------|---------|
| `frontmatter` | 1.0× | `topics: [Agentic AI]` |
| `body` | 0.6× | `[[Agentic AI]]` wikilink |
| `implicit-folder` | 0.3× | Folder path as structural edge |

See [`docs/SCORING.md`](docs/SCORING.md) for full algorithm details.

---

## API Reference

### HTTP Endpoints

| Endpoint | Method | Description | Auth |
|----------|--------|-------------|------|
| `/health` | GET | Liveness probe | None |
| `/ready` | GET | Readiness probe (checks dependencies) | None |
| `/search` | POST | 4-strategy search with GARS reranking | API Key |
| `/graph` | GET | Entity relationship traversal | API Key |
| `/temporal` | GET | Date-range history query | API Key |
| `/sessions` | POST | Register agent session | API Key |
| `/sessions` | GET | Query sessions (filterable) | API Key |
| `/sessions/{id}` | PATCH | Update session (close, add notes) | API Key |
| `/search_siblings` | POST | Topic hub sibling traversal | API Key |
| `/cognify` | POST | Extract triples via Ollama LLM | API Key |

### Authentication

All endpoints except `/health` and `/ready` require API key authentication:

```bash
export VAULT_MEMORY_API_KEY="your-secret-key"

# Include in requests
curl -H "X-API-Key: $VAULT_MEMORY_API_KEY" http://localhost:5051/search \
  -X POST -d '{"query": "architecture"}'
```

---

## Performance

### Resource Requirements

| Component | Idle RAM | Peak RAM | Notes |
|-----------|----------|----------|-------|
| vault-memoryd | ~800MB | ~1.5GB | Models stay resident |
| Weaviate | ~400MB | ~1GB | Vector index overhead |
| PostgreSQL | ~100MB | ~300MB | Connection pooling |
| **Total** | **~1.3GB** | **~2.8GB** | + model cache |

### Throughput

| Operation | Typical Rate | Notes |
|-----------|--------------|-------|
| Full sync | 5-10 files/sec | Depends on embedding model |
| Incremental sync | Real-time | 2-second debounce |
| Search latency | 50-200ms | Includes GARS reranking |
| Graph traversal | 100-500ms | Depends on hop depth |

### Scaling Considerations

- **Connection Pooling**: 2-10 connections (configurable)
- **Batch Processing**: 20 files default batch size
- **Concurrent Searches**: Limited by connection pool
- **Model Caching**: Embeddings computed once, cached in Weaviate

---

## Write Layer Discipline

Agents encoding bad reasoning into long-term memory is the #1 failure mode in Obsidian-agent systems.

| Caller | Can write to | Trust Level |
|--------|-------------|-------------|
| `user` | Anywhere in vault | `trust: high` |
| `agent` | `_working/` only | `trust: low` |
| `heartbeat` | Meta directories only | `trust: high` |

**Session state exception:** `STATE.md`, `ROADMAP.md`, and `plans/*.md` are writable by agents directly (designed to be overwritten each session).

---

## Session State Protocol

Any agent can cold-start any project in ~500 tokens:

```
Session Start (~500 tokens):
1. READ  {project}.md              → architecture, constraints
2. READ  STATE.md                  → current position
3. READ  ROADMAP.md                → phase status
4. CALL  memory/project_state      → semantic context bundle
5. WRITE plans/YYYY-MM-DD-{task}.md → declare intent

Session End (required):
1. WRITE Session Logs/YYYY-MM-DD.md  → structured record
2. WRITE STATE.md                    → new position
3. UPDATE ROADMAP.md                 → tick completed tasks
```

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search` | 4-strategy vault search with GARS + decay |
| `search_siblings` | Topic sibling traversal from seed |
| `graph` | Entity relationship traversal |
| `temporal` | Date-range history query |
| `health` | Daemon status check |
| `memory/attach_block` | Attach named context block |
| `memory/list_blocks` | List blocks + token counts |
| `memory/write_working` | Write to `_working/` buffer |
| `memory/trigger_lookup` | Keyword → context recommendation |
| `memory/project_state` | Full session-start bundle |

### Configuration Example

```json
{
  "mcpServers": {
    "vault-memory": {
      "command": "/path/to/vault-memory",
      "args": ["mcp"],
      "env": {
        "VAULT_MEMORY_API_KEY": "your-secret-key"
      }
    }
  }
}
```

---

## Documentation

- **[`USER_GUIDE.md`](USER_GUIDE.md)** — Complete setup, configuration, and operational runbook
- **[`docs/SCORING.md`](docs/SCORING.md)** — GARS formula, edge weights, accordion assembly
- **[`docs/SLIM_SYNC.md`](docs/SLIM_SYNC.md)** — Cold store drift detection protocol
- **[`AGENTS.md`](AGENTS.md)** — Internal architecture reference for contributors

---

## Stack

- **Python 3.11+** — FastAPI, uvicorn, asyncio
- **Weaviate** — Vector store, BM25, hybrid search (Docker)
- **PostgreSQL** — Knowledge graph, temporal history, sessions (Docker)
- **sentence-transformers** — e5-large embeddings, cross-encoder reranking
- **watchdog** — Real-time file system monitoring
- **Ollama** — Optional local LLM for heartbeat and cognify

---

## Contributing

We welcome contributions! Please see:

1. Check existing issues and discussions first
2. For bugs: Include reproduction steps, vault size, and error logs
3. For features: Describe the use case and proposed API
4. Run tests: `pytest tests/ -v`
5. Follow existing code style (ruff, black)

### Development Setup

```bash
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d
pytest tests/ -v
```

---

## Related Projects

- **[creativebrain-obsidian-vault-template](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template)** — The vault template this daemon is designed for
- **[cybaea/obsidian-vault-intelligence](https://github.com/cybaea/obsidian-vault-intelligence)** — TypeScript Obsidian plugin whose Shadow Graph architecture informed this project

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Built for agents that need to remember.**

[Quick Start](#quick-start) · [User Guide](USER_GUIDE.md) · [API Reference](#api-reference) · [Issues](https://github.com/pvnkmnk/vault-memory/issues)

</div>
