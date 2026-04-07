# vault-memory — User Guide

> Complete step-by-step guide: installation, configuration, first sync, daemon operations, Obsidian plugin, MCP agent setup, and operational runbook.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Configuration](#3-configuration)
4. [Starting the Services (Docker)](#4-starting-the-services-docker)
5. [First-Time Full Sync](#5-first-time-full-sync)
6. [Starting the Daemon](#6-starting-the-daemon)
7. [Using the CLI](#7-using-the-cli)
8. [Installing the Obsidian Plugin](#8-installing-the-obsidian-plugin)
9. [MCP Agent Integration](#9-mcp-agent-integration)
10. [Search Query Reference](#10-search-query-reference)
11. [Operational Runbook](#11-operational-runbook)
12. [Troubleshooting](#12-troubleshooting)
13. [Architecture Deep Dive](#13-architecture-deep-dive)

---

## 1. Prerequisites

Before you begin, make sure the following are installed:

### Required

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://python.org) or `pyenv` |
| Docker Desktop | Latest | [docker.com](https://docker.com/products/docker-desktop) |
| Docker Compose | v2.20+ | Included with Docker Desktop |
| Git | Any | [git-scm.com](https://git-scm.com) |

### Recommended

| Tool | Why |
|---|---|
| `pyenv` | Manage Python versions cleanly |
| `pipx` | Install `vault-memory` as an isolated tool |
| Obsidian | The vault source — [obsidian.md](https://obsidian.md) |

### System Resources

- **RAM:** 4GB minimum, 8GB recommended (embedding models need ~2–3GB)
- **Disk:** ~2GB for Docker images + ~500MB per 10,000 indexed chunks
- **CPU:** Any modern x86-64 or Apple Silicon
- **OS:** macOS 12+, Ubuntu 20.04+, or WSL2 on Windows 11

---

## 2. Installation

### Step 2.1 — Clone the repository

```bash
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory
```

### Step 2.2 — Create a Python virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate          # Windows (WSL2)
```

Verify:
```bash
python --version
# Python 3.11.x
```

### Step 2.3 — Install the package

```bash
pip install -e .
```

This installs:
- `vault-memoryd` — the daemon CLI entry point
- `vault-memory` — the user CLI entry point
- All Python dependencies (FastAPI, Weaviate v4 client, sentence-transformers, psycopg2, Rich, Click, watchdog, httpx)

Verify:
```bash
vault-memory --help
# Usage: vault-memory [OPTIONS] COMMAND [ARGS]...
```

### Step 2.4 — (Optional) Install with pipx for global access

If you want `vault-memory` available system-wide without activating a venv:

```bash
pipx install -e .
```

---

## 3. Configuration

### Step 3.1 — Create your config file

Copy the example config to your vault root (or home directory):

```bash
cp .vault-memory.json /path/to/your/ObsidianVault/.vault-memory.json
```

Or create it in your home directory for a global default:

```bash
cp .vault-memory.json ~/.vault-memory.json
```

### Step 3.2 — Edit the config

Open `.vault-memory.json` and set your values:

```json
{
  "vault_path": "/Users/yourname/ObsidianVault",
  "weaviate_url": "http://127.0.0.1:8080",
  "pg_connection_string": "dbname=vault_memory user=vault password=vault_local host=localhost",
  "embedding_model": "sentence-transformers/e5-large",
  "reranker_model": "mixedbread-ai/mxbai-rerank-large-v1",
  "port": 5051
}
```

**Config field reference:**

| Field | Default | Description |
|---|---|---|
| `vault_path` | `~/ObsidianVault` | Absolute path to your Obsidian vault folder |
| `weaviate_url` | `http://127.0.0.1:8080` | Weaviate REST API URL (matches docker-compose.yml) |
| `pg_connection_string` | See above | PostgreSQL DSN (matches docker-compose.yml defaults) |
| `embedding_model` | `e5-large` | HuggingFace model name — downloaded on first run |
| `reranker_model` | `mxbai-rerank-large-v1` | Cross-encoder model — downloaded on first run |
| `port` | `5051` | Port for vault-memoryd HTTP API |

### Step 3.3 — Environment variable overrides

Every config field can be overridden with an environment variable:

```bash
export VAULT_PATH="/path/to/vault"
export VAULT_MEMORY_PORT=5051
export WEAVIATE_URL="http://127.0.0.1:8080"
export PG_CONNECTION_STRING="dbname=vault_memory user=vault ..."
export EMBEDDING_MODEL="sentence-transformers/e5-large"
export RERANKER_MODEL="mixedbread-ai/mxbai-rerank-large-v1"
```

Environment variables take precedence over `.vault-memory.json`.

---

## 4. Starting the Services (Docker)

Weaviate and PostgreSQL run as Docker containers. The vault-memoryd daemon and CLI run natively on your host.

### Step 4.1 — Start the containers

```bash
docker compose up -d
```

Expected output:
```
[+] Running 3/3
 ✔ Network vault-memory_default         Created
 ✔ Container vault-memory-postgres      Started
 ✔ Container vault-memory-weaviate      Started
```

### Step 4.2 — Verify both services are healthy

```bash
docker compose ps
```

Both containers must show `healthy` before proceeding:
```
NAME                       STATUS
vault-memory-weaviate      Up 30 seconds (healthy)
vault-memory-postgres      Up 30 seconds (healthy)
```

If a service shows `starting` — wait 15–30 seconds and check again. Weaviate takes longer on first start because it initialises its HNSW index structures.

### Step 4.3 — Verify Weaviate manually (optional)

```bash
curl http://localhost:8080/v1/.well-known/ready
# {"status":"200 OK"}
```

### Step 4.4 — Verify PostgreSQL manually (optional)

```bash
docker exec vault-memory-postgres pg_isready -U vault -d vault_memory
# localhost:5432 - accepting connections
```

### Managing the containers

```bash
docker compose stop          # Stop containers (data preserved)
docker compose start         # Restart stopped containers
docker compose restart       # Full restart
docker compose down          # Stop + remove containers (data preserved in volumes)
docker compose down -v       # ⚠ Stop + DELETE all indexed data (volumes removed)
docker compose logs -f       # Tail logs from both services
docker compose logs weaviate # Logs from Weaviate only
```

---

## 5. First-Time Full Sync

The full sync walks your entire vault, chunks every Markdown file, generates embeddings, and upserts everything into Weaviate and PostgreSQL. This only needs to run once — after that, the file watcher handles incremental updates automatically.

### Step 5.1 — Run the full sync

```bash
vault-memory sync --full
```

With a custom vault path:
```bash
vault-memory sync --full --vault /path/to/your/vault
```

### What you'll see

```
╭─── vault-memory sync --full ─────────────────────────────╮
│ Vault:  /Users/yourname/ObsidianVault                    │
│ Force:  False                                            │
│ Batch:  20 files                                         │
╰──────────────────────────────────────────────────────────╯

Checking services...
  ✓ Weaviate ready
  ✓ PostgreSQL ready

Loading models... (this takes 10–20s on first run)
  ✓ Models loaded

Found 847 Markdown files in /Users/yourname/ObsidianVault

╭─── Indexing ──────────────────────────────────────────────╮
│ ⠸ Indexing vault ██████████████░░░░  640/847  76%  128s  │
│   Files processed:  640    Chunks created:   7,480        │
│   Files skipped:    0      Rate:             5.0 files/s  │
│   Current: Music/Production/DAW-Setup.md                  │
╰──────────────────────────────────────────────────────────╯
```

### First-run model download

The first time you run `sync --full` (or start the daemon), the embedding and reranker models are downloaded from HuggingFace:

| Model | Size | Download time |
|---|---|---|
| `sentence-transformers/e5-large` | ~1.3GB | 3–8 min |
| `mixedbread-ai/mxbai-rerank-large-v1` | ~560MB | 1–3 min |

Models are cached in `~/.cache/huggingface/` after the first download — subsequent starts take 10–20 seconds.

### Sync options

```bash
vault-memory sync --full                        # Normal sync (skips unchanged files)
vault-memory sync --full --force                # Re-index everything (ignores hash cache)
vault-memory sync --full --batch-size 10        # Smaller batches (low-RAM machines)
vault-memory sync --full --output file          # Write JSON report to sync-report-TIMESTAMP.json
vault-memory sync --full --no-check             # Skip service health checks
```

### Sync state file

The sync engine writes `.vault-memory-sync-state.json` to your vault root. This tracks file content hashes so unchanged notes are skipped on subsequent runs. Do not delete this file — if you do, the next sync will re-index everything (equivalent to `--force`).

---

## 6. Starting the Daemon

The daemon (`vault-memoryd`) is the always-on process that:
- Serves the HTTP API on `127.0.0.1:5051`
- Keeps embedding models warm in memory
- Watches your vault for file changes (real-time sync)
- Runs hourly reconciliation

### Step 6.1 — Start the daemon

```bash
vault-memory daemon start
```

The daemon starts in the background and writes a PID file to `~/.vault-memory/daemon.pid`.

### Step 6.2 — Check it's ready

```bash
vault-memory health
```

Expected output:
```json
{
  "liveness":  {"status": "alive",  "uptime_seconds": 4.2},
  "readiness": {"status": "ready",  "last_index": "2026-04-07T11:20:00Z"}
}
```

If `status` is `starting` or `indexing` — the daemon is still loading models. Re-run `vault-memory health` after 15–30 seconds.

### Step 6.3 — Watch until ready (optional)

```bash
vault-memory health --watch
# Polls every 2s until status is 'ready'
```

### Daemon lifecycle commands

```bash
vault-memory daemon start    # Start in background
vault-memory daemon stop     # Graceful shutdown
vault-memory daemon restart  # Stop + start
vault-memory daemon status   # Show PID + uptime
vault-memory daemon logs     # Tail daemon logs
```

### Running the daemon in the foreground (development)

```bash
vault-memoryd
# or
uvicorn daemon.main:app --host 127.0.0.1 --port 5051 --log-level info
```

---

## 7. Using the CLI

### Search

```bash
# Basic semantic search
vault-memory search -q "music production workflow"

# Scoped to a project
vault-memory search -q "architecture decisions" -p djinn-netrunner

# Force all four strategies
vault-memory search -q "why did the workflow change" --graph --temporal

# Temporal query (auto-detected from query text)
vault-memory search -q "notes from last week"
vault-memory search -q "what changed in January"

# Filter by tag
vault-memory search -q "synth patches" --tag music --tag production

# Get more results
vault-memory search -q "rentFalcon roadmap" --top-k 10

# Output formats
vault-memory search -q "query" --format text     # Human readable (default)
vault-memory search -q "query" --format json     # Machine readable
vault-memory search -q "query" --format clips    # Token-efficient for agents
```

### Graph queries

```bash
# Find everything related to an entity
vault-memory graph --entity "djinn-netrunner"

# Specific relationship type
vault-memory graph --entity "rentFalcon" --rel depends_on
```

### Temporal queries

```bash
# Changes in a date range
vault-memory temporal --entity "Music" --start 2026-01-01 --end 2026-04-07

# Full workflow history for a note
vault-memory temporal --entity "Projects/djinn-netrunner/Architecture.md"
```

### Health checks

```bash
vault-memory health               # One-shot check
vault-memory health --watch       # Poll until ready
vault-memory health --format json # JSON output
```

---

## 8. Installing the Obsidian Plugin

The Obsidian plugin provides:
- Automatic daemon startup when Obsidian opens
- Status bar indicator (🟢 ready / 🟡 indexing / 🔴 down)
- Settings tab for vault path and port configuration

### Step 8.1 — Build the plugin

```bash
cd obsidian-plugin
npm install
npm run build
```

This produces `main.js` in the `obsidian-plugin/` folder.

### Step 8.2 — Install into Obsidian

```bash
# Create plugin directory in your vault
mkdir -p /path/to/your/vault/.obsidian/plugins/vault-memory

# Copy plugin files
cp obsidian-plugin/main.js /path/to/vault/.obsidian/plugins/vault-memory/
cp obsidian-plugin/manifest.json /path/to/vault/.obsidian/plugins/vault-memory/
cp obsidian-plugin/styles.css /path/to/vault/.obsidian/plugins/vault-memory/
```

### Step 8.3 — Enable in Obsidian

1. Open Obsidian
2. Go to **Settings → Community Plugins**
3. Turn off **Safe Mode** if prompted
4. Find **Vault Memory** in the list
5. Toggle it **on**

### Step 8.4 — Configure the plugin

1. Go to **Settings → Vault Memory**
2. Set the **Daemon Path** to the full path of `vault-memoryd` (e.g. `/Users/yourname/vault-memory/.venv/bin/vault-memoryd`)
3. Set **Port** to `5051` (or whatever you set in your config)
4. Toggle **Auto-start daemon** on

The status bar icon in the bottom-right of Obsidian will show:
- `⚡ VM: ready` — daemon running, index up to date
- `⟳ VM: indexing` — file change being processed
- `✗ VM: down` — daemon not running (click to start)

---

## 9. MCP Agent Integration

The MCP adapter exposes vault-memory as a Model Context Protocol server over stdio — compatible with Claude Desktop, Cursor, Cline, and any MCP-compatible agent.

### Step 9.1 — Test the MCP adapter

```bash
vault-memory mcp
# Starts listening on stdin for JSON-RPC messages
# Ctrl+C to exit
```

### Step 9.2 — Claude Desktop configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vault-memory": {
      "command": "/path/to/.venv/bin/vault-memory",
      "args": ["mcp"],
      "env": {
        "VAULT_PATH": "/path/to/your/ObsidianVault"
      }
    }
  }
}
```

Replace `/path/to/.venv/bin/vault-memory` with the output of:
```bash
which vault-memory
```

### Step 9.3 — Cursor configuration

Add to `.cursor/mcp.json` in your project root:

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

### Step 9.4 — Verify MCP is working

In Claude Desktop, try:
> "Search my vault for notes about music production"

Claude will call the `search` MCP tool and surface results from your vault.

### Available MCP tools

| Tool | Description |
|---|---|
| `search` | Full 4-strategy retrieval with RRF + reranking |
| `graph` | Entity relationship traversal |
| `temporal` | Time-range note history |
| `health` | Daemon status check |

---

## 10. Search Query Reference

### Query intent detection

vault-memory automatically classifies your query and activates the right strategies:

| Intent | Trigger words | Strategies used |
|---|---|---|
| Simple | (none) | Dense + Sparse |
| Entity | "related to", "connected to", "depends on" | Dense + Sparse + Graph |
| Temporal | "last week", "since", "in 2025", "changed" | Dense + Sparse + Temporal |
| Causal | "why", "what caused", "led to", "how did" | All four |
| Hybrid | temporal + entity signals combined | All four |

### Time expressions (auto-parsed)

| Expression | Resolved to |
|---|---|
| `last week` | Past 7 days |
| `last month` | Past 30 days |
| `this year` | Jan 1 → today |
| `in 2025` | 2025-01-01 → 2025-12-31 |
| `2025-01-01..2025-03-31` | Explicit date range |

### Project scoping

Scope any search to a specific project folder:

```bash
vault-memory search -q "architecture" -p djinn-netrunner
vault-memory search -q "workflow" -p music
vault-memory search -q "client notes" -p rentFalcon
```

### Result formats

**Text** (default, for humans):
```
1. Projects/djinn-netrunner/Architecture.md  [score: 0.923]
   Strategy: dense, sparse
   Tags: #dev #architecture
   Modified: 2026-03-15
   Snippet: "The agent runtime uses a dual-mode design..."
```

**JSON** (for pipelines):
```json
{"results": [{"vault_path": "...", "score": 0.923, "snippet": "..."}]}
```

**Clips** (token-efficient, for agents):
```json
{"path": "...", "score": 0.923, "snippet": "...", "sources": ["dense"]}
```

---

## 11. Operational Runbook

### Daily use

You don't need to do anything after initial setup. The standard workflow is:

1. `docker compose up -d` — start services (or set them to auto-start)
2. `vault-memory daemon start` — start daemon (or configure Obsidian plugin to auto-start)
3. Use Obsidian normally — the file watcher handles sync automatically
4. Query anytime via CLI, HTTP, or MCP

### Startup sequence

The correct startup order is:
```
docker compose up -d          (Weaviate + PostgreSQL)
       ↓ wait for healthy
vault-memory daemon start     (loads models, starts watcher)
       ↓ wait for ready (~15s)
vault-memory health           (confirm status: ready)
```

### Forced re-index

Re-index everything from scratch (e.g. after schema changes or model upgrade):

```bash
vault-memory daemon stop
vault-memory sync --full --force
vault-memory daemon start
```

### Upgrading models

To switch to a different embedding model:

1. Stop the daemon: `vault-memory daemon stop`
2. Update `.vault-memory.json` with the new model name
3. Delete the sync state: `rm /path/to/vault/.vault-memory-sync-state.json`
4. Drop and recreate Weaviate data: `docker compose down -v && docker compose up -d`
5. Re-run full sync: `vault-memory sync --full`
6. Restart daemon: `vault-memory daemon start`

⚠️ You must re-index from scratch when changing the embedding model — vectors from different models are not compatible.

### Backup and restore

**Backup:**
```bash
# Backup PostgreSQL
docker exec vault-memory-postgres pg_dump -U vault vault_memory > backup.sql

# Backup sync state
cp /path/to/vault/.vault-memory-sync-state.json ~/vault-memory-state.backup.json
```

**Restore PostgreSQL:**
```bash
docker exec -i vault-memory-postgres psql -U vault vault_memory < backup.sql
```

Weaviate data can be restored by re-running `vault-memory sync --full --force` — it is fully reproducible from your vault.

### Resource usage

| Component | Typical RAM | Peak RAM |
|---|---|---|
| vault-memoryd (idle) | ~800MB | ~1.5GB |
| Weaviate container | ~400MB | ~1GB |
| PostgreSQL container | ~100MB | ~300MB |
| Embedding model (e5-large) | ~1.3GB | ~2GB |
| Reranker model | ~600MB | ~1GB |
| **Total** | **~3.2GB** | **~5.8GB** |

---

## 12. Troubleshooting

### Daemon won't start

**Symptom:** `vault-memory daemon start` returns immediately with no output.

**Check:**
```bash
vault-memory daemon logs
# Look for Python traceback or import errors
```

**Common causes:**
- Weaviate or PostgreSQL not healthy: `docker compose ps`
- Wrong vault path in config: check `.vault-memory.json`
- Port 5051 already in use: `lsof -i :5051`

---

### Readiness stuck at `starting` or `degraded`

**Symptom:** `vault-memory health` shows `status: degraded`.

**Check:**
```bash
curl http://localhost:5051/ready
# Look at the 'reason' field
```

**Common causes:**
- Weaviate not reachable: `curl http://localhost:8080/v1/.well-known/ready`
- PostgreSQL not reachable: `docker exec vault-memory-postgres pg_isready -U vault`
- Models still downloading: wait and retry

---

### Full sync is very slow

**Symptom:** Rate below 1 file/s.

**Solutions:**
- Reduce batch size: `vault-memory sync --full --batch-size 5`
- Use a lighter embedding model in `.vault-memory.json`: `"embedding_model": "sentence-transformers/all-MiniLM-L6-v2"`
- Ensure you're not running on battery (macOS throttles CPU on battery)

---

### Search returns no results

**Symptom:** `vault-memory search -q "..."` returns empty list.

**Check in order:**
1. Is the daemon ready? `vault-memory health`
2. Has the vault been synced? Check `.vault-memory-sync-state.json` exists in vault root
3. Does Weaviate have data? `curl http://localhost:8080/v1/objects?limit=1`
4. Is the query scoping too narrow? Try removing `-p` project filter

---

### Weaviate container unhealthy

**Symptom:** `docker compose ps` shows Weaviate as `unhealthy`.

**Fix:**
```bash
docker compose logs weaviate | tail -50
# Common fix: increase Docker Desktop RAM limit to 4GB+
docker compose restart weaviate
```

---

### `ModuleNotFoundError` on CLI commands

**Symptom:** `ModuleNotFoundError: No module named 'daemon'`

**Fix:** Make sure you're in the project root and the venv is active:
```bash
cd /path/to/vault-memory
source .venv/bin/activate
pip install -e .
```

---

### Port 5051 conflict

**Symptom:** Daemon fails with `Address already in use`.

**Fix:**
```bash
# Find the conflicting process
lsof -i :5051

# Kill it or change the port in .vault-memory.json
# "port": 5052
```

---

## 13. Architecture Deep Dive

### The dual-mode design

vault-memory has two distinct modes that serve different consumers:

**Mode A: HTTP daemon** — For humans via CLI, for other local services, for the Obsidian plugin.
```
vault-memory search -q "..."  →  HTTP POST /search  →  vault-memoryd  →  results
```

**Mode B: MCP stdio adapter** — For AI agents (Claude, Cursor, Cline) that speak the Model Context Protocol.
```
Agent  →  stdin JSON-RPC  →  vault-memory mcp  →  HTTP POST /search  →  vault-memoryd
```

The MCP adapter is a thin proxy — it translates MCP tool calls into HTTP requests to the daemon. The daemon does all the heavy lifting.

### Chunking strategy

Notes are split using a semantic-boundary-aware approach:

1. Split at H1/H2 section boundaries first
2. If sections are still too large, split at H3–H6
3. Then at double-newline paragraph breaks
4. Then at sentence boundaries
5. Hard token limit (512 tokens) as last resort

Each chunk overlaps the next by 15% (~77 tokens at 512 token size) to avoid cutting mid-thought.

### RRF fusion formula

After all four strategies return their ranked lists, Reciprocal Rank Fusion merges them:

```
RRF_score(document) = Σ over strategies [ weight / (60 + rank_in_strategy) ]
```

Key properties:
- Raw scores are never compared across strategies (BM25 and cosine similarity are incomparable)
- Only rank positions matter
- A document ranked #3 in both dense and sparse beats one ranked #1 in only sparse
- The k=60 constant prevents rank-1 documents from completely dominating

### Sync modes

| Mode | Trigger | Scope | Use case |
|---|---|---|---|
| Full sync | Manual (`sync --full`) or first run | Entire vault | Initial index, forced re-index |
| Incremental | File system event (watchdog) | Single file | Real-time sync on save |
| Reconciliation | Hourly timer | Changed files only | Crash recovery, missed events |

The incremental watcher debounces events by 2 seconds per file — Obsidian can fire 3–5 events on a single save (modify + metadata update + index update). Without debouncing, one save would trigger three re-embeds.

### PostgreSQL schema

| Table | Purpose |
|---|---|
| `temporal_entities` | One row per entity/note, with `valid_from`/`valid_to` for temporal queries |
| `relationships` | Graph edges (source → target, relationship type) |
| `vault_entity_links` | Maps chunk UUIDs back to vault paths |
| `workflow_history` | Versioned note content snapshots for timeline queries |
| `sync_state` | File hash registry for incremental sync |

### Weaviate collection

The `VaultNote` collection stores one object per chunk with:
- A deterministic UUID (`sha1(vault_path::chunk_index)`) for safe upserts
- The raw chunk text in `content`
- A custom vector (from `e5-large`) — no built-in vectorizer
- Metadata: `vault_path`, `project`, `folder`, `tags`, `date_modified`, `status`, `chunk_index`, `chunk_total`
- An inverted index on `content` for BM25 keyword search

Detailed UUID generation ensures that re-indexing a file always overwrites the same Weaviate objects — no duplicates accumulate over time.
