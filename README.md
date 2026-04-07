# vault-memory

> Always-on local memory layer for Obsidian — semantic search, knowledge graph, temporal history, and agentic write safety.

**v0.2.0** — Write layer discipline · Temporal decay scoring · Memory block management · Heartbeat scheduler · Smart pruning

---

## What It Does

`vault-memory` is a Python daemon that runs alongside your Obsidian vault and gives AI agents (Gemini CLI, OpenCode, Claude Code, Cursor) a production-grade memory system:

- **4-strategy retrieval** — dense vector (Weaviate) + BM25 sparse + knowledge graph (Postgres) + temporal history, fused with RRF
- **Temporal decay scoring** — recent notes outrank old ones; configurable per decay profile (`active` 30d · `reference` 90d · `identity` never)
- **Write layer gate** — agents can only write to `_working/`; only the heartbeat process can promote to semantic memory
- **Trust system** — every note carries `trust: high|medium|low` and `agent-written: true|false` flags surfaced in search results
- **Memory blocks** — named, hot-swappable context blocks attached per session via MCP tools
- **Heartbeat scheduler** — daily + weekly reflection cycles that archive working memory and synthesize patterns
- **Soft pruning** — stale notes flagged (not deleted) for human review
- **MCP-native** — 8 tools exposed via stdio for any MCP-compliant agent

---

## Architecture

```
Obsidian Vault (.md files)
        │
        ▼
  VaultSyncWatcher          ← watchdog real-time + hourly reconcile
        │
  Write Layer Gate          ← user | agent | heartbeat
        │
   MarkdownParser           ← frontmatter, tags, trust, importance
        │
    SyncEngine              ← chunk → embed → upsert
        │
   ┌────┴────┐
Weaviate    Postgres
(vectors)   (graph + history)
   └────┬────┘
  UnifiedSearch
  ├─ dense (vector)
  ├─ sparse (BM25)
  ├─ graph (entity traversal)
  ├─ temporal (date range)
  ├─ RRF fusion
  ├─ temporal decay
  └─ cross-encoder rerank
        │
  FastAPI daemon (:5051)
        │
   MCP stdio adapter
   (8 tools for agents)
```

---

## Write Layer Discipline

This is the most important architectural concept in v0.2.0. Agents encoding bad reasoning into long-term memory is the #1 failure mode in Obsidian-agent systems.

| Caller | Can write to | Notes |
|--------|-------------|-------|
| `user` | Anywhere in vault | Human writes; always `trust: high` |
| `agent` | `_working/` only | Session buffer; `trust: low`; heartbeat promotes or prunes |
| `heartbeat` | `08 Meta/agent-context/`, `08 Meta/heartbeat/`, `08 Meta/skills/` | Only scheduled process with semantic write access |

Attempting a semantic-layer write as `caller="agent"` raises `PermissionError`.

---

## Temporal Decay

Pure vector similarity over-weights old notes. Vault-memory applies:

```
score = semantic_score × 0.6 + recency × 0.3 + importance × 0.1
recency = exp(−age_days / decay_days)
```

Controlled by the `decay-profile` frontmatter field:

| Profile | Window | Use for |
|---------|--------|---------|
| `active` | 30 days | Project notes, session logs |
| `reference` | 90 days | Books, articles, research |
| `identity` | Never | `boot.md`, `pvnkmnk.md`, `triggers.md` |

---

## Quick Start

```bash
git clone https://github.com/pvnkmnk/vault-memory
cd vault-memory
docker compose up -d          # Weaviate + Postgres
pip install -e .
vault-memory sync --full --vault ~/path/to/your/vault
vault-memory daemon start
vault-memory health --watch
```

---

## CLI Reference

```bash
vault-memory search -q "djinn architecture"          # Search vault
vault-memory search -q "last week" --temporal        # Temporal search
vault-memory search -q "anything" --no-decay         # Disable decay scoring
vault-memory graph --entity "djinn-netrunner"        # Graph traversal
vault-memory temporal --entity "vault-memory" --start 2026-01-01
vault-memory prune --vault ~/vault --max-age 90 --dry-run
vault-memory prune --vault ~/vault --max-age 90      # Soft-flag stale notes
vault-memory heartbeat --mode daily --vault ~/vault  # Run heartbeat now
vault-memory heartbeat --mode weekly --vault ~/vault
vault-memory daemon start | stop | status | logs
vault-memory health
vault-memory mcp                                     # Start MCP stdio adapter
```

---

## MCP Tools (v0.2.0)

| Tool | Description |
|------|-------------|
| `search` | 4-strategy vault search with decay scoring |
| `graph` | Entity relationship traversal |
| `temporal` | Date-range history query |
| `health` | Daemon status |
| `memory/attach_block` | Attach named context block to session |
| `memory/list_blocks` | List attached blocks + token counts |
| `memory/write_working` | Write to `_working/` buffer (agent-safe) |
| `memory/trigger_lookup` | Keyword → context block recommendation |

Add to `opencode.json` or `CLAUDE.md`:
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

## Heartbeat Cron Setup

Copy `homelab-bridge/heartbeat.sh` from the [creativebrain-obsidian-vault-template](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template) repo into your vault's `homelab-bridge/` folder, then:

```bash
chmod +x homelab-bridge/heartbeat.sh
crontab -e
```

Add:
```
# Daily lightweight heartbeat at 6 AM
0 6 * * * /path/to/vault/homelab-bridge/heartbeat.sh --mode=daily

# Weekly deep review Sunday at 9 AM
0 9 * * 0 /path/to/vault/homelab-bridge/heartbeat.sh --mode=weekly
```

---

## Frontmatter Schema

All vault notes support these fields (injected automatically on agent writes):

```yaml
---
agent-written: false          # true if written by agent
agent-confidence: null        # high | medium | low
agent-source-episodes: []     # source session logs
trust: high                   # high | medium | low
importance: 1.0               # 0.0–1.0, affects decay scoring
decay-profile: active         # active | reference | identity
status: active                # active | stale | needs-review | archive-candidate
---
```

---

## Stack

- **Python 3.11+** with FastAPI + uvicorn
- **Weaviate** (vector store, BM25, hybrid) via Docker
- **PostgreSQL** (knowledge graph, temporal history) via Docker
- **sentence-transformers** (embedding + cross-encoder reranking)
- **watchdog** (real-time file watcher)
- **Ollama** (optional: local LLM for heartbeat)

---

## Related

- [creativebrain-obsidian-vault-template](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template) — the vault template this daemon is designed for
