# vault-memory — User Guide

> Complete step-by-step guide: installation, configuration, first sync, daemon operations, Obsidian plugin, MCP agent setup, and operational runbook.

---

## 📋 Table of Contents

1. [Before You Begin](#1-before-you-begin)
2. [Installation](#2-installation)
3. [Configuration](#3-configuration)
4. [Starting the Services](#4-starting-the-services-docker)
5. [First-Time Full Sync](#5-first-time-full-sync)
6. [Starting the Daemon](#6-starting-the-daemon)
7. [Common Workflows](#7-common-workflows)
8. [Using the CLI](#8-using-the-cli)
9. [Advanced Configuration](#9-advanced-configuration)
10. [Installing the Obsidian Plugin](#10-installing-the-obsidian-plugin)
11. [MCP Agent Integration](#11-mcp-agent-integration)
12. [Search Query Reference](#12-search-query-reference)
13. [Operational Runbook](#13-operational-runbook)
14. [Troubleshooting](#14-troubleshooting)
15. [Architecture Deep Dive](#15-architecture-deep-dive)

---

## 1. Before You Begin

### ✅ Prerequisites Checklist

Before installing, verify you have:

- [ ] **Python 3.11+** installed (`python --version`)
- [ ] **Docker Desktop** installed and running
- [ ] **Git** installed (`git --version`)
- [ ] **4GB+ RAM** available (8GB recommended)
- [ ] **10GB+ disk space** for Docker images and model cache
- [ ] **Obsidian vault** ready (or create a new one)

### 🖥️ System Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 4GB | 8GB |
| Disk | 10GB | 50GB+ (for large vaults) |
| CPU | Any modern x86-64 | Multi-core for faster sync |
| OS | macOS 12+, Ubuntu 20.04+, WSL2 | Native Linux or macOS |

### 🛠️ Recommended Tools

| Tool | Purpose | Install |
|------|---------|---------|
| `pyenv` | Python version management | [pyenv](https://github.com/pyenv/pyenv) |
| `pipx` | Isolated CLI tool installation | `pip install pipx` |
| `jq` | JSON parsing for CLI | `brew install jq` or `apt install jq` |

---

## 2. Installation

### Step 2.1 — Clone the Repository

```bash
git clone https://github.com/pvnkmnk/vault-memory.git
cd vault-memory
```

### Step 2.2 — Create Virtual Environment

```bash
# Create environment
python3.11 -m venv .venv

# Activate (macOS/Linux)
source .venv/bin/activate

# Activate (Windows/WSL2)
# .venv\Scripts\activate
```

Verify Python version:
```bash
python --version
# Python 3.11.x
```

### Step 2.3 — Install Package

```bash
pip install -e .
```

This installs:
- `vault-memoryd` — daemon CLI entry point
- `vault-memory` — user CLI entry point
- All dependencies (FastAPI, Weaviate v4, sentence-transformers, psycopg2, etc.)

Verify installation:
```bash
vault-memory --help
# Usage: vault-memory [OPTIONS] COMMAND [ARGS]...
```

### Step 2.4 — (Optional) Install with pipx

For system-wide access without activating venv:

```bash
pipx install -e .
```

---

## 3. Configuration

### Step 3.1 — Create Config File

Copy the example config to your vault root:

```bash
# Option A: Vault-specific config
cp .vault-memory.json /path/to/your/ObsidianVault/.vault-memory.json

# Option B: Global default (home directory)
cp .vault-memory.json ~/.vault-memory.json
```

### Step 3.2 — Edit Configuration

Open `.vault-memory.json` and set your values:

```json
{
  "vault_path": "/Users/yourname/ObsidianVault",
  "weaviate_url": "http://127.0.0.1:8080",
  "pg_connection_string": "dbname=vault_memory user=vault password=vault_local host=localhost",
  "embedding_model": "sentence-transformers/e5-large",
  "reranker_model": "mixedbread-ai/mxbai-rerank-large-v1",
  "port": 5051,
  "heartbeat_interval_seconds": 900
}
```

**Config Field Reference:**

| Field | Default | Description |
|-------|---------|-------------|
| `vault_path` | `~/ObsidianVault` | Absolute path to your Obsidian vault |
| `weaviate_url` | `http://127.0.0.1:8080` | Weaviate REST API URL |
| `pg_connection_string` | See above | PostgreSQL connection string |
| `embedding_model` | `e5-large` | HuggingFace model for embeddings |
| `reranker_model` | `mxbai-rerank-large-v1` | Cross-encoder for reranking |
| `port` | `5051` | Daemon HTTP API port |
| `heartbeat_interval_seconds` | `900` | Background job interval (15 min) |

### Step 3.3 — Environment Variable Overrides

Every config field can be overridden via environment variables:

```bash
export VAULT_PATH="/path/to/vault"
export VAULT_MEMORY_PORT=5051
export WEAVIATE_URL="http://127.0.0.1:8080"
export PG_CONNECTION_STRING="dbname=vault_memory user=vault ..."
export EMBEDDING_MODEL="sentence-transformers/e5-large"
export RERANKER_MODEL="mixedbread-ai/mxbai-rerank-large-v1"
export VAULT_MEMORY_API_KEY="your-secret-api-key"
```

> **Priority:** Environment variables > `.vault-memory.json` (vault root) > `~/.vault-memory.json`

---

## 4. Starting the Services (Docker)

Weaviate and PostgreSQL run as Docker containers. The daemon and CLI run natively.

### Step 4.1 — Start Containers

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

### Step 4.2 — Verify Health

```bash
docker compose ps
```

Both containers must show `healthy`:
```
NAME                       STATUS
vault-memory-weaviate      Up 30 seconds (healthy)
vault-memory-postgres      Up 30 seconds (healthy)
```

> **Note:** If showing `starting`, wait 15–30 seconds and check again. Weaviate takes longer on first start.

### Step 4.3 — Manual Verification (Optional)

```bash
# Test Weaviate
curl http://localhost:8080/v1/.well-known/ready
# {"status":"200 OK"}

# Test PostgreSQL
docker exec vault-memory-postgres pg_isready -U vault -d vault_memory
# localhost:5432 - accepting connections
```

### Container Management Commands

```bash
docker compose stop          # Stop (data preserved)
docker compose start         # Restart stopped containers
docker compose restart       # Full restart
docker compose down          # Stop + remove containers
docker compose down -v       # ⚠️ Stop + DELETE all data
docker compose logs -f       # Tail all logs
docker compose logs weaviate # Weaviate only
```

---

## 5. First-Time Full Sync

The full sync walks your entire vault, chunks every Markdown file, generates embeddings, and upserts into Weaviate and PostgreSQL. This runs once — after that, the file watcher handles incremental updates.

### Step 5.1 — Run Full Sync

```bash
# Using configured vault path
vault-memory sync --full

# Or specify vault path explicitly
vault-memory sync --full --vault /path/to/your/vault
```

### What You'll See

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
│ ⠸ Indexing vault ██████████████░░░░  640/847  76%  128s │
│   Files processed:  640    Chunks created:   7,480        │
│   Files skipped:    0      Rate:             5.0 files/s  │
│   Current: Music/Production/DAW-Setup.md                  │
╰──────────────────────────────────────────────────────────╯
```

### First-Run Model Download

Models are downloaded from HuggingFace on first run:

| Model | Size | Download Time |
|-------|------|---------------|
| `e5-large` | ~1.3GB | 3–8 minutes |
| `mxbai-rerank-large-v1` | ~560MB | 1–3 minutes |

Models cache in `~/.cache/huggingface/` — subsequent starts take 10–20 seconds.

### Sync Options

```bash
vault-memory sync --full                        # Normal sync
vault-memory sync --full --force                # Re-index everything
vault-memory sync --full --batch-size 10        # Smaller batches
vault-memory sync --full --output file          # JSON report
vault-memory sync --full --no-check             # Skip health checks
vault-memory sync --check-drift                 # Show hot/cold drift
vault-memory sync --drift-only                  # Re-index drifted only
```

### Sync State File

The sync engine writes `.vault-memory-sync-state.json` to your vault root. This tracks file content hashes so unchanged notes are skipped. **Do not delete this file** — if you do, the next sync will re-index everything.

---

## 6. Starting the Daemon

The daemon (`vault-memoryd`) is the always-on process that:
- Serves HTTP API on `127.0.0.1:5051`
- Keeps embedding models warm in memory
- Watches your vault for file changes
- Runs hourly reconciliation

### Step 6.1 — Start Daemon

```bash
vault-memory daemon start
```

The daemon starts in the background and writes a PID file to `~/.vault-memory/daemon.pid`.

### Step 6.2 — Check Status

```bash
vault-memory health
```

Expected output:
```json
{
  "liveness": {"status": "alive", "uptime_seconds": 4.2},
  "readiness": {"status": "ready", "last_index": "2026-04-07T11:20:00Z"}
}
```

If `status` is `starting` or `indexing`, wait 15–30 seconds and retry.

### Step 6.3 — Watch Until Ready

```bash
vault-memory health --watch
# Polls every 2s until status is 'ready'
```

### Daemon Lifecycle Commands

```bash
vault-memory daemon start      # Start in background
vault-memory daemon stop       # Graceful shutdown
vault-memory daemon restart    # Stop + start
vault-memory daemon status     # Show PID + uptime
vault-memory daemon logs       # Tail daemon logs
```

### Foreground Mode (Development)

```bash
vault-memoryd
# or
uvicorn daemon.main:app --host 127.0.0.1 --port 5051 --log-level info
```

---

## 7. Common Workflows

### 🔍 Daily Search Workflow

```bash
# Basic semantic search
vault-memory search -q "music production workflow"

# Scoped to project
vault-memory search -q "architecture decisions" -p djinn-netrunner

# Include topic siblings
vault-memory search -q "agentic ai" --siblings

# Temporal query
vault-memory search -q "notes from last week"

# Get more results
vault-memory search -q "roadmap" --top-k 20
```

### 📝 Session Workflow

```bash
# Register new session
vault-memory session start \
  --agent "claude-code" \
  --project "djinn-netrunner" \
  --task "Implement GARS scoring"

# List active sessions
vault-memory session list --status active

# Close session
vault-memory session close --id "session-uuid" --notes "Completed implementation"
```

### 🔄 Maintenance Workflow

```bash
# Check for drift
vault-memory sync --check-drift

# Re-index drifted files only
vault-memory sync --drift-only

# Run heartbeat manually
vault-memory heartbeat --mode daily

# Soft-flag stale notes
vault-memory prune --max-age 90 --dry-run
vault-memory prune --max-age 90
```

### 🏗️ Project Setup Workflow

```bash
# 1. Create project structure
mkdir -p "05 Dev Projects/my-new-project"

# 2. Create identity file
cat > "05 Dev Projects/my-new-project/my-new-project.md" << 'EOF'
---
trust: high
maturity: tree
---
# My New Project

## Architecture
...
EOF

# 3. Create state file
cat > "05 Dev Projects/my-new-project/STATE.md" << 'EOF'
---
trust: high
maturity: sapling
---
# Current State

**Status:** Initial setup
**Last Action:** Project created
**Next Action:** Define requirements
EOF

# 4. Create roadmap
cat > "05 Dev Projects/my-new-project/ROADMAP.md" << 'EOF'
# Roadmap

- [ ] Define requirements
- [ ] Design architecture
- [ ] Implement MVP
- [ ] Test and validate
EOF

# 5. Sync to index
vault-memory sync --full
```

---

## 8. Using the CLI

### Search Commands

```bash
# Basic search
vault-memory search -q "query string"

# Project-scoped
vault-memory search -q "architecture" -p project-name

# Include graph traversal
vault-memory search -q "related to entity" --graph

# Include temporal
vault-memory search -q "changed last week" --temporal

# All strategies
vault-memory search -q "why did this change" --graph --temporal

# Filter by tag
vault-memory search -q "synth" --tag music --tag production

# Output formats
vault-memory search -q "query" --format text     # Human readable
vault-memory search -q "query" --format json     # Machine readable
vault-memory search -q "query" --format clips    # Token-efficient

# Disable decay scoring
vault-memory search -q "query" --no-decay
```

### Graph Commands

```bash
# Find related entities
vault-memory graph --entity "djinn-netrunner"

# Specific relationship
vault-memory graph --entity "rentFalcon" --rel depends_on
```

### Temporal Commands

```bash
# Date range query
vault-memory temporal --entity "Music" --start 2026-01-01 --end 2026-04-07

# Recent changes
vault-memory temporal --entity "Projects/djinn-netrunner/Architecture.md"
```

### Health Commands

```bash
vault-memory health               # One-shot check
vault-memory health --watch       # Poll until ready
vault-memory health --format json # JSON output
```

---

## 9. Advanced Configuration

### Connection Pooling

The PostgreSQL client uses connection pooling for better performance:

```python
# In your code or custom scripts
from daemon.pg_client import PostgresClient

# Custom pool settings
pg = PostgresClient(
    connection_string="...",
    min_connections=2,      # Minimum pool size
    max_connections=10,     # Maximum pool size
    max_idle_time=300.0,    # Connection timeout (seconds)
)

# Use context manager for safe access
with pg.cursor() as cursor:
    cursor.execute("SELECT * FROM table")
    rows = cursor.fetchall()
```

### Custom Embedding Models

Edit `.vault-memory.json`:

```json
{
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "reranker_model": "cross-encoder/ms-marco-MiniLM-L-6-v2"
}
```

> **Note:** Changing models requires re-indexing from scratch.

### API Key Authentication

Set a secure API key:

```bash
export VAULT_MEMORY_API_KEY="your-secure-random-key"
```

All endpoints except `/health` and `/ready` require this key in the `X-API-Key` header.

### Heartbeat Configuration

The heartbeat runs background maintenance jobs:

```json
{
  "heartbeat_interval_seconds": 900
}
```

| Interval | Use Case |
|----------|----------|
| `300` (5 min) | Active development |
| `900` (15 min) | Normal use |
| `3600` (1 hour) | Large vaults, low activity |

### Cron Setup

For scheduled maintenance:

```bash
chmod +x homelab-bridge/heartbeat.sh
crontab -e
```

Add:
```
# Daily at 6 AM
0 6 * * * /path/to/vault/homelab-bridge/heartbeat.sh --mode=daily

# Weekly on Sunday at 9 AM
0 9 * * 0 /path/to/vault/homelab-bridge/heartbeat.sh --mode=weekly
```

---

## 10. Installing the Obsidian Plugin

The Obsidian plugin provides:
- Automatic daemon startup when Obsidian opens
- Status bar indicator (🟢 ready / 🟡 indexing / 🔴 down)
- Settings tab for configuration

### Step 10.1 — Build Plugin

```bash
cd obsidian-plugin
npm install
npm run build
```

Produces `main.js` in `obsidian-plugin/`.

### Step 10.2 — Install to Obsidian

```bash
# Create plugin directory
mkdir -p /path/to/vault/.obsidian/plugins/vault-memory

# Copy files
cp obsidian-plugin/main.js /path/to/vault/.obsidian/plugins/vault-memory/
cp obsidian-plugin/manifest.json /path/to/vault/.obsidian/plugins/vault-memory/
cp obsidian-plugin/styles.css /path/to/vault/.obsidian/plugins/vault-memory/
```

### Step 10.3 — Enable in Obsidian

1. Open Obsidian
2. **Settings → Community Plugins**
3. Turn off **Safe Mode** if prompted
4. Find **Vault Memory** in list
5. Toggle **on**

### Step 10.4 — Configure Plugin

1. **Settings → Vault Memory**
2. Set **Daemon Path** to full path of `vault-memoryd`
3. Set **Port** to `5051`
4. Toggle **Auto-start daemon** on

Status bar shows:
- `⚡ VM: ready` — daemon running, index current
- `⟳ VM: indexing` — processing file change
- `✗ VM: down` — daemon not running (click to start)

---

## 11. MCP Agent Integration

The MCP adapter exposes vault-memory as a Model Context Protocol server — compatible with Claude Desktop, Cursor, Cline, and any MCP-compliant agent.

### Step 11.1 — Test MCP Adapter

```bash
vault-memory mcp
# Starts listening on stdin for JSON-RPC
# Ctrl+C to exit
```

### Step 11.2 — Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vault-memory": {
      "command": "/path/to/.venv/bin/vault-memory",
      "args": ["mcp"],
      "env": {
        "VAULT_PATH": "/path/to/your/ObsidianVault",
        "VAULT_MEMORY_API_KEY": "your-secret-key"
      }
    }
  }
}
```

Find the correct path:
```bash
which vault-memory
```

### Step 11.3 — Cursor Configuration

Add to `.cursor/mcp.json` in project root:

```json
{
  "mcpServers": {
    "vault-memory": {
      "command": "vault-memory",
      "args": ["mcp"],
      "env": {
        "VAULT_MEMORY_API_KEY": "your-secret-key"
      }
    }
  }
}
```

### Step 11.4 — Verify MCP

In Claude Desktop, try:
> "Search my vault for notes about music production"

Claude will call the `search` MCP tool and surface results.

### Available MCP Tools

| Tool | Description |
|------|-------------|
| `search` | 4-strategy retrieval with GARS + decay |
| `search_siblings` | Topic sibling traversal |
| `graph` | Entity relationship traversal |
| `temporal` | Time-range history |
| `health` | Daemon status |
| `cognify` | Extract triples via Ollama LLM |
| `memory/*` | Session memory block operations |

---

## 12. Search Query Reference

### Query Intent Detection

vault-memory automatically classifies queries:

| Intent | Trigger Words | Strategies |
|--------|---------------|------------|
| Simple | (none) | Dense + Sparse |
| Entity | "related to", "connected to" | Dense + Sparse + Graph |
| Temporal | "last week", "since", "in 2025" | Dense + Sparse + Temporal |
| Causal | "why", "what caused", "led to" | All four |

### Time Expressions

| Expression | Resolves To |
|------------|-------------|
| `last week` | Past 7 days |
| `last month` | Past 30 days |
| `this year` | Jan 1 → today |
| `in 2025` | 2025-01-01 → 2025-12-31 |
| `2025-01..2025-03` | Explicit range |

### Project Scoping

```bash
vault-memory search -q "architecture" -p djinn-netrunner
vault-memory search -q "workflow" -p music
```

### Result Formats

**Text** (default):
```
1. Projects/djinn-netrunner/Architecture.md [score: 0.923]
   Strategy: dense, sparse
   Tags: #dev #architecture
   Modified: 2026-03-15
   Snippet: "The agent runtime uses a dual-mode design..."
```

**JSON**:
```json
{"results": [{"vault_path": "...", "score": 0.923, "snippet": "..."}]}
```

**Clips** (token-efficient):
```json
{"path": "...", "score": 0.923, "snippet": "...", "sources": ["dense"]}
```

---

## 13. Operational Runbook

### Daily Use

Standard workflow after initial setup:

1. `docker compose up -d` — start services (or auto-start)
2. `vault-memory daemon start` — start daemon (or auto-start via plugin)
3. Use Obsidian normally — file watcher handles sync
4. Query anytime via CLI, HTTP, or MCP

### Startup Sequence

```
docker compose up -d          (Weaviate + PostgreSQL)
       ↓ wait for healthy
vault-memory daemon start     (loads models, starts watcher)
       ↓ wait for ready (~15s)
vault-memory health           (confirm status: ready)
```

### Forced Re-index

Re-index everything (after schema changes or model upgrade):

```bash
vault-memory daemon stop
vault-memory sync --full --force
vault-memory daemon start
```

### Model Upgrade

1. Stop daemon: `vault-memory daemon stop`
2. Update `.vault-memory.json` with new model
3. Delete sync state: `rm /path/to/vault/.vault-memory-sync-state.json`
4. Reset Weaviate: `docker compose down -v && docker compose up -d`
5. Re-sync: `vault-memory sync --full`
6. Restart daemon: `vault-memory daemon start`

⚠️ **Required:** Re-index from scratch when changing embedding models — vectors from different models are incompatible.

### Backup and Restore

**Backup PostgreSQL:**
```bash
docker exec vault-memory-postgres pg_dump -U vault vault_memory > backup.sql
```

**Backup sync state:**
```bash
cp /path/to/vault/.vault-memory-sync-state.json ~/backups/
```

**Restore PostgreSQL:**
```bash
docker exec -i vault-memory-postgres psql -U vault vault_memory < backup.sql
```

Weaviate data is reproducible from vault — restore by re-running `vault-memory sync --full --force`.

---

## 14. Troubleshooting

### 🔴 Daemon Won't Start

**Symptom:** `vault-memory daemon start` returns immediately.

**Check:**
```bash
vault-memory daemon logs
# Look for Python traceback
```

**Common causes:**
- Weaviate/PostgreSQL not healthy: `docker compose ps`
- Wrong vault path: check `.vault-memory.json`
- Port 5051 in use: `lsof -i :5051`

---

### 🟡 Readiness Stuck at `starting` or `degraded`

**Symptom:** `vault-memory health` shows degraded status.

**Check:**
```bash
curl http://localhost:5051/ready
# Check 'reason' field
```

**Common causes:**
- Weaviate not reachable: `curl http://localhost:8080/v1/.well-known/ready`
- PostgreSQL not reachable: `docker exec vault-memory-postgres pg_isready -U vault`
- Models still downloading: wait and retry

---

### 🐌 Full Sync Very Slow

**Symptom:** Rate below 1 file/sec.

**Solutions:**
- Reduce batch size: `vault-memory sync --full --batch-size 5`
- Use lighter model: `"embedding_model": "sentence-transformers/all-MiniLM-L6-v2"`
- Check power (macOS throttles on battery)

---

### 🔍 Search Returns No Results

**Symptom:** `vault-memory search -q "..."` returns empty.

**Check in order:**
1. Daemon ready? `vault-memory health`
2. Vault synced? Check `.vault-memory-sync-state.json` exists
3. Weaviate has data? `curl http://localhost:8080/v1/objects?limit=1`
4. Query too narrow? Try removing `-p` filter

---

### 🐳 Weaviate Container Unhealthy

**Symptom:** `docker compose ps` shows Weaviate as `unhealthy`.

**Fix:**
```bash
docker compose logs weaviate | tail -50
# Common fix: increase Docker Desktop RAM to 4GB+
docker compose restart weaviate
```

---

### 🐍 ModuleNotFoundError

**Symptom:** `ModuleNotFoundError: No module named 'daemon'`

**Fix:**
```bash
cd /path/to/vault-memory
source .venv/bin/activate
pip install -e .
```

---

### 🚫 Port 5051 Conflict

**Symptom:** `Address already in use`.

**Fix:**
```bash
# Find conflicting process
lsof -i :5051

# Kill or change port in .vault-memory.json
# "port": 5052
```

---

### 🔐 API Key Errors

**Symptom:** `401 Unauthorized` on endpoints.

**Fix:**
```bash
export VAULT_MEMORY_API_KEY="your-secret-key"
# Or pass in request header: X-API-Key: your-secret-key
```

---

## 15. Architecture Deep Dive

### Dual-Mode Design

vault-memory serves two consumers:

**Mode A: HTTP Daemon** — For humans via CLI, other services, Obsidian plugin.
```
vault-memory search -q "..."  →  HTTP POST /search  →  vault-memoryd  →  results
```

**Mode B: MCP Adapter** — For AI agents (Claude, Cursor, Cline).
```
Agent  →  stdin JSON-RPC  →  vault-memory mcp  →  HTTP POST /search  →  vault-memoryd
```

The MCP adapter is a thin proxy — the daemon does all heavy lifting.

### Chunking Strategy

Notes are split using semantic-boundary-aware approach:

1. Split at H1/H2 section boundaries
2. If still too large, split at H3–H6
3. Then at double-newline paragraph breaks
4. Then at sentence boundaries
5. Hard token limit (512) as last resort

Each chunk overlaps next by 15% (~77 tokens) to avoid cutting mid-thought.

### RRF Fusion Formula

```
RRF_score(doc) = Σ over strategies [ weight / (60 + rank) ]
```

Key properties:
- Only rank positions matter (not raw scores)
- Document ranked #3 in both dense and sparse beats #1 in only one
- k=60 constant prevents rank-1 domination

### PostgreSQL Schema

| Table | Purpose |
|-------|---------|
| `temporal_entities` | One row per entity with valid_from/valid_to |
| `relationships` | Graph edges (source → target, type) |
| `vault_entity_links` | Maps chunk UUIDs to vault paths |
| `workflow_history` | Versioned note content snapshots |
| `sync_state` | File hash registry for incremental sync |
| `topic_hubs` | High-centrality nodes for sibling traversal |
| `agent_sessions` | Session registry for agent tracking |

### Weaviate Collection

`VaultNote` collection stores one object per chunk:
- Deterministic UUID: `sha1(vault_path::chunk_index)`
- Raw chunk text in `content`
- Custom vector from `e5-large`
- Metadata: `vault_path`, `project`, `folder`, `tags`, `date_modified`, `status`
- Inverted index on `content` for BM25

---

<div align="center">

**Questions?** Check [README.md](README.md) for overview or [AGENTS.md](AGENTS.md) for internals.

</div>
