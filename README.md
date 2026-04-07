# vault-memory

> Always-on local memory layer for Obsidian — semantic search, knowledge graph, temporal history, agentic write safety, and transferable session state.

**v0.2.0** — Write layer discipline · Temporal decay scoring · Memory block management · Heartbeat scheduler · Smart pruning · Session state protocol

---

## What It Does

`vault-memory` is a Python daemon that runs alongside your Obsidian vault and gives AI agents (Gemini CLI, OpenCode, Claude Code, Cursor) a production-grade memory system:

- **4-strategy retrieval** — dense vector (Weaviate) + BM25 sparse + knowledge graph (Postgres) + temporal history, fused with RRF
- **Temporal decay scoring** — recent notes outrank old ones; configurable per decay profile (`active` · `reference` · `identity` · `log`)
- **Write layer gate** — agents can only write to `_working/`; only the heartbeat process can promote to semantic memory
- **Trust + maturity system** — notes carry `trust: high|medium|low` and `maturity: seed|sapling|tree`; maturity gates heartbeat promotion; trust gates write access
- **Session state protocol** — structured `STATE.md` / `REQUIREMENTS.md` / `ROADMAP.md` / `plans/` per project; agents can cold-start any project in ~500 tokens
- **Memory blocks** — named, hot-swappable context blocks attached per session via MCP tools
- **Heartbeat scheduler** — daily + weekly reflection cycles that archive working memory and synthesize patterns
- **Soft pruning** — stale notes flagged (not deleted) for human review
- **MCP-native** — 9 tools exposed via stdio for any MCP-compliant agent

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
   MarkdownParser           ← frontmatter, tags, trust, maturity, importance
        │
    SyncEngine              ← chunk → embed → upsert
        │
   ┌────┴────┐
Weaviate    Postgres
(vectors)   (graph + history + sessions)
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
   (9 tools for agents)
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

**Exception — session state files:** `STATE.md`, `ROADMAP.md`, and `plans/*.md` inside `05 Dev Projects/` are writable by agents directly. These files are explicitly excluded from the write gate because they are designed to be overwritten each session. They do not enter the semantic index directly — they are synced by the watcher and indexed with `maturity: sapling` (STATE, ROADMAP) or `maturity: seed` (plans), which gates their heartbeat promotion.

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
| `active` | 30 days | Project notes, STATE.md, ROADMAP.md |
| `reference` | 90 days | Books, articles, research, REQUIREMENTS.md |
| `identity` | Never | `boot.md`, `pvnkmnk.md`, `triggers.md`, `{project}.md` |
| `log` | 7-day half-life, floor 0.1 | Session logs, plans |

The `log` profile is new in this version. Session logs are high-value when recent and gracefully fade rather than becoming permanent noise.

---

## Trust + Maturity System

These are two orthogonal axes that together gate what enters long-term memory.

**Trust** answers: *who wrote this and is it verified?*
- `trust: high` — human-authored or heartbeat-promoted
- `trust: medium` — partially reviewed
- `trust: low` — raw agent output, unreviewed

**Maturity** answers: *is this note structurally complete as a unit of knowledge?*
- `maturity: seed` — agent-written or first draft; importance **capped at 0.4** at index time
- `maturity: sapling` — partially reviewed; indexed at stated importance
- `maturity: tree` — fully reviewed, permanent knowledge; importance **floored at 0.8**

Maturity affects the **heartbeat promotion gate**, not the decay formula. The heartbeat decision matrix:

| agent-confidence | maturity | Heartbeat action |
|-----------------|----------|------------------|
| `high` | `tree` | Promote directly to target folder |
| `high` | `sapling` | Promote to `07 Inbox` for one human review |
| `high` | `seed` | Flag `needs-review` — good content, incomplete note |
| `medium` | `sapling` | Flag `needs-review` |
| `medium` | `seed` | Stay in `_working/`, attempt expansion next cycle |
| `low` | any | Flag `stale` |

---

## Session State Protocol

Vault-memory supports a structured per-project state file system that makes agent sessions fully transferable — any agent can cold-start any project cleanly.

### Per-project files (inside `05 Dev Projects/{project}/`)

| File | Nature | Agent can overwrite? | Decay profile |
|------|--------|---------------------|---------------|
| `{project}.md` | Permanent identity | No (staging.md required) | `identity` |
| `STATE.md` | Live rolling position | **Yes — full overwrite** | `active` |
| `REQUIREMENTS.md` | Scoped requirements | No (staging.md required) | `reference` |
| `ROADMAP.md` | Phase completion | Yes — tick phases | `active` |
| `plans/YYYY-MM-DD-{task}.md` | Pre-execution intent | Yes — created fresh | `log` |
| `Session Logs/YYYY-MM-DD.md` | Post-execution record | Yes — structured schema | `log` |

### Session start protocol (~500 tokens)

```
1. READ  {project}.md              → architecture, constraints
2. READ  STATE.md                  → current position
3. READ  ROADMAP.md                → phase status
4. CALL  memory/project_state      → semantic context bundle (Weaviate)
5. WRITE plans/YYYY-MM-DD-{task}.md → declare intent before touching code
```

### Session end protocol (required)

```
1. WRITE Session Logs/YYYY-MM-DD.md  → structured record
2. WRITE STATE.md                    → new position (full overwrite)
3. UPDATE ROADMAP.md                 → tick completed tasks
```

If a session is interrupted before completion, the agent must write `STATE.md` with `Current Position: SESSION INTERRUPTED — {what was in progress}` before stopping.

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
| `memory/project_state` | Full session-start bundle for a project (identity + state + roadmap + semantic context) |

### `memory/project_state` usage

```json
{ "tool": "memory/project_state", "input": { "project": "djinn-netrunner" } }
```

Returns:
```json
{
  "project_identity": "...",
  "current_state": "...",
  "roadmap_summary": "...",
  "semantic_context": [...],
  "token_cost": 487
}
```

### Named block conventions

| Block name | Content |
|---|---|
| `{project}-state` | STATE.md + ROADMAP.md phase table |
| `{project}-requirements` | REQUIREMENTS.md full content |
| `{project}-recent` | Last 3 session logs |
| `{project}-context` | Full identity + background context files |

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
decay-profile: active         # active | reference | identity | log
maturity: seed                # seed | sapling | tree  (gates heartbeat promotion)
status: active                # active | stale | needs-review | archive-candidate
---
```

**Maturity at index time:**
- `seed` → importance capped at `min(stated_importance, 0.4)`
- `sapling` → importance used as stated
- `tree` → importance floored at `max(stated_importance, 0.8)`

---

## Stack

- **Python 3.11+** with FastAPI + uvicorn
- **Weaviate** (vector store, BM25, hybrid) via Docker
- **PostgreSQL** (knowledge graph, temporal history, agent sessions) via Docker
- **sentence-transformers** (embedding + cross-encoder reranking)
- **watchdog** (real-time file watcher)
- **Ollama** (optional: local LLM for heartbeat)

---

## Related

- [creativebrain-obsidian-vault-template](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template) — the vault template this daemon is designed for
