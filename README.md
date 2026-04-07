# vault-memory

> Always-on local memory layer for Obsidian — semantic search, knowledge graph, temporal history, agentic write safety, and transferable session state.

**v0.3.0** — GARS scoring · Topic sibling traversal · Accordion context assembly · Slim-sync cold store · Split-brain buffer protection · Edge typing · Agent runtime dirs

---

## What It Does

`vault-memory` is a Python daemon that runs alongside your Obsidian vault and gives AI agents
(Gemini CLI, OpenCode, Claude Code, Cursor) a production-grade memory system:

- **4-strategy retrieval** — dense vector (Weaviate) + BM25 sparse + knowledge graph (Postgres) + temporal history, fused with RRF
- **GARS re-ranking** — Graph-Augmented Relevance Score combines vector similarity, degree centrality, and neighbor activation
- **Topic sibling traversal** — discovers notes linked through a shared Ontology topic even without direct wikilinks
- **Accordion context assembly** — relative-threshold tiers pack the context window at maximum density without token waste
- **Temporal decay scoring** — recent notes outrank old ones; configurable per decay profile (`active` · `reference` · `identity` · `log`)
- **Write layer gate** — agents can only write to `_working/`; only the heartbeat process can promote to semantic memory
- **Trust + maturity system** — notes carry `trust: high|medium|low` and `maturity: seed|sapling|tree`; maturity gates heartbeat promotion; trust gates write access
- **Session state protocol** — structured `STATE.md` / `REQUIREMENTS.md` / `ROADMAP.md` / `plans/` per project; agents can cold-start any project in ~500 tokens
- **Slim-sync cold store** — drift detection and split-brain buffer protection between Weaviate and vault filesystem
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
  VaultSyncWatcher          ← watchdog real-time + hourly reconcile + drift detection
        │
  Write Layer Gate          ← user | agent | heartbeat
        │
   MarkdownParser           ← frontmatter, tags, trust, maturity, importance
        │
    SyncEngine              ← chunk → embed → upsert (split-brain buffer protected)
        │
   ┌────┴────┐
Weaviate    Postgres
(vectors)   (graph + history + sessions + topic_hubs + slim-sync state)
   └────┬────┘
  UnifiedSearch
  ├─ dense (vector)
  ├─ sparse (BM25, sigmoid-calibrated)
  ├─ graph (GARS: sim + centrality + activation)
  ├─ topic sibling traversal (Ontology/ hub expansion)
  ├─ temporal (date range)
  ├─ RRF fusion
  ├─ temporal decay
  ├─ accordion context assembly
  └─ cross-encoder rerank
        │
  FastAPI daemon (:5051)
        │
   MCP stdio adapter
   (9 tools for agents)
```

---

## GARS Scoring

Search results are re-ranked using a **Graph-Augmented Relevance Score** after the initial
candidate retrieval stage. This prevents high-centrality but semantically weak notes from
being drowned out, and prevents isolated but highly relevant notes from being over-promoted.

```
GARS = (sim × W_sim) + (cent × W_cent) + (act × W_act)
```

| Component | Default weight | Meaning |
|---|---|---|
| `sim` | 0.70 | Blended vector + BM25 similarity |
| `cent` | 0.20 | Degree centrality (structural importance) |
| `act` | 0.10 | Neighbor activation (co-occurrence with other hits) |

BM25 scores are sigmoid-calibrated before blending: `normalized = score / (score + 1.2)`
so unbounded keyword scores map cleanly onto the 0–1 scale.

Edge types carry different weights during activation scoring:

| Edge source | Traversal weight | Example |
|---|---|---|
| `frontmatter` | 1.0× | `topics: [Agentic AI]`, `project: djinn-netrunner` |
| `body` | 0.6× | `[[Agentic AI]]` in note body |
| `implicit-folder` | 0.3× | Folder path injected as structural edge |

See [`docs/SCORING.md`](docs/SCORING.md) for the full scoring reference.

---

## Topic Sibling Traversal

Notes can be discovered through a **shared Ontology topic** even with no direct wikilink between
them. This is the most powerful feature for vaults with an `Ontology/` or MOC structure.

**Example:** Notes A, B, and C all link to `Ontology/Concepts/Agentic AI.md`. A query for
"agent coordination" finds Note A directly. Topic sibling expansion then discovers B and C
as high-value candidates because they share the same conceptual parent.

**Hub penalty** — massive hubs (Daily Notes, generic MOCs with hundreds of inbound links)
are automatically dampened:
```
penalty = 1 / log2(in_degree + 2)
sibling_score = GARS × penalty
```

Topic hubs are tracked in the `topic_hubs` table (refreshed by heartbeat).
A node qualifies as a hub when `in_degree >= HUB_MIN_DEGREE` (default: 5, configurable).

---

## Accordion Context Assembly

The `ContextAssembler` packs the LLM context window using **Relative Accordion Logic**.
Tiers are defined relative to the top result's score — not by absolute thresholds —
so quality is consistent regardless of vault size or score distribution.

| Tier | Threshold vs. top | Strategy |
|---|---|---|
| **Primary** | ≥ 90% | Full file content (10% soft budget cap per file) |
| **Supporting** | ≥ 70% | 500-char snippets around query terms |
| **Structural** | ≥ 35% | Headers only (TOC view), max 10 files |
| **Filtered** | < 35% | Dropped — prevents hallucination-by-bloat |

Unused budget from small primary docs carries forward to the next document in the ranked list.
Neighbor expansion only triggers when the seed's absolute GARS ≥ 0.40.

---

## Slim-Sync Cold Store

Two hash columns in `sync_state` track hot/cold drift between Weaviate and the vault filesystem:

| Column | Meaning |
|---|---|
| `content_hash` | SHA-256 of file as last read from disk |
| `cold_store_hash` | SHA-256 of file as last confirmed indexed in Weaviate |

Mismatch = drift detected. A partial index on these two columns makes drift queries instant.
The `buffer_in_flight` flag prevents split-brain collisions when the watcher and CLI sync
run simultaneously — a lightweight optimistic lock without Postgres advisory locks.

See [`docs/SLIM_SYNC.md`](docs/SLIM_SYNC.md) for the full protocol.

---

## Write Layer Discipline

Agents encoding bad reasoning into long-term memory is the #1 failure mode in Obsidian-agent systems.

| Caller | Can write to | Notes |
|--------|-------------|-------|
| `user` | Anywhere in vault | Human writes; always `trust: high` |
| `agent` | `_working/` only | Session buffer; `trust: low`; heartbeat promotes or prunes |
| `heartbeat` | `08 Meta/agent-context/`, `08 Meta/heartbeat/`, `08 Meta/skills/` | Only scheduled process with semantic write access |

Attempting a semantic-layer write as `caller="agent"` raises `PermissionError`.

**Exception — session state files:** `STATE.md`, `ROADMAP.md`, and `plans/*.md` inside
`05 Dev Projects/` are writable by agents directly. These are excluded from the write gate
because they are designed to be overwritten each session. They are indexed with
`maturity: sapling` (STATE, ROADMAP) or `maturity: seed` (plans), gating heartbeat promotion.

---

## Temporal Decay

```
final_score = GARS × 0.6 + recency × 0.3 + importance × 0.1
recency     = exp(−age_days / decay_days)
```

| Profile | `decay_days` | Use for |
|---------|-------------|--------|
| `active` | 30 | Project notes, STATE.md, ROADMAP.md |
| `reference` | 90 | Books, articles, research, REQUIREMENTS.md |
| `identity` | ∞ | `boot.md`, `{project}.md`, `triggers.md` |
| `log` | 10 (half-life 7d, floor 0.1) | Session logs, plans |

---

## Trust + Maturity System

**Trust** — *who wrote this and is it verified?*
- `trust: high` — human-authored or heartbeat-promoted
- `trust: medium` — partially reviewed
- `trust: low` — raw agent output, unreviewed

**Maturity** — *is this note structurally complete?*
- `maturity: seed` — agent-written; importance **capped at 0.4** at index time
- `maturity: sapling` — partially reviewed; indexed at stated importance
- `maturity: tree` — fully reviewed; importance **floored at 0.8**

Heartbeat promotion matrix:

| agent-confidence | maturity | Heartbeat action |
|-----------------|----------|------------------|
| `high` | `tree` | Promote directly to target folder |
| `high` | `sapling` | Promote to `07 Inbox` for one human review |
| `high` | `seed` | Flag `needs-review` |
| `medium` | `sapling` | Flag `needs-review` |
| `medium` | `seed` | Stay in `_working/`, expand next cycle |
| `low` | any | Flag `stale` |

---

## Session State Protocol

Any agent can cold-start any project in ~500 tokens using the structured state file system.

### Per-project files (`05 Dev Projects/{project}/`)

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

If a session is interrupted: write `STATE.md` with `Current Position: SESSION INTERRUPTED — {what was in progress}`.

---

## Agent Runtime Dirs

To match multi-runtime agent environments (Gemini CLI, Goose, OpenCode, Claude), vault-memory
recognizes a per-runtime config directory convention:

```
vault-root/
  .agents/          ← generic agent config (AGENTS.md, skills)
  .gemini/          ← Gemini CLI system prompt + settings
  .goose/           ← Goose toolkit config
  .opencode/        ← OpenCode agent config
```

The vault-memory MCP daemon reads `AGENTS.md` (if present) as its base system prompt context,
injecting it as a high-priority memory block at session start. Per-runtime dirs override
with runtime-specific constraints.

See [`creativebrain-obsidian-vault-template`](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template)
for the reference AGENTS.md and skills layout.

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
vault-memory search -q "djinn architecture"           # GARS-ranked search
vault-memory search -q "agentic ai" --siblings        # Include topic sibling expansion
vault-memory search -q "last week" --temporal         # Temporal search
vault-memory search -q "anything" --no-decay          # Disable decay scoring
vault-memory graph --entity "djinn-netrunner"         # Graph traversal
vault-memory temporal --entity "vault-memory" --start 2026-01-01
vault-memory prune --vault ~/vault --max-age 90 --dry-run
vault-memory prune --vault ~/vault --max-age 90       # Soft-flag stale notes
vault-memory heartbeat --mode daily --vault ~/vault   # Run heartbeat now
vault-memory heartbeat --mode weekly --vault ~/vault
vault-memory sync --check-drift                       # Show hot/cold drift
vault-memory sync --drift-only                        # Re-index drifted files only
vault-memory daemon start | stop | status | logs
vault-memory health
vault-memory mcp                                      # Start MCP stdio adapter
```

---

## MCP Tools (v0.3.0)

| Tool | Description |
|------|-------------|
| `search` | 4-strategy vault search with GARS re-ranking + decay scoring |
| `search_siblings` | Topic sibling traversal from a seed note or query |
| `graph` | Entity relationship traversal |
| `temporal` | Date-range history query |
| `health` | Daemon status |
| `memory/attach_block` | Attach named context block to session |
| `memory/list_blocks` | List attached blocks + token counts |
| `memory/write_working` | Write to `_working/` buffer (agent-safe) |
| `memory/trigger_lookup` | Keyword → context block recommendation |
| `memory/project_state` | Full session-start bundle for a project (identity + state + roadmap + semantic context) |

### `search_siblings` usage

```json
{ "tool": "search_siblings", "input": { "seed_path": "05 Dev Projects/djinn-netrunner/djinn.md", "limit": 10 } }
```

Returns notes that share a topic hub with the seed note, scored by `GARS × hub_penalty`.

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

```bash
chmod +x homelab-bridge/heartbeat.sh
crontab -e
```

```
# Daily lightweight heartbeat at 6 AM
0 6 * * * /path/to/vault/homelab-bridge/heartbeat.sh --mode=daily

# Weekly deep review Sunday at 9 AM
0 9 * * 0 /path/to/vault/homelab-bridge/heartbeat.sh --mode=weekly
```

See [`creativebrain-obsidian-vault-template`](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template) for `heartbeat.sh`.

---

## Frontmatter Schema

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
- **PostgreSQL** (knowledge graph, temporal history, agent sessions, topic hubs, slim-sync state) via Docker
- **sentence-transformers** (embedding + cross-encoder reranking)
- **watchdog** (real-time file watcher)
- **Ollama** (optional: local LLM for heartbeat)

---

## Docs

- [`docs/SCORING.md`](docs/SCORING.md) — GARS formula, edge weights, accordion assembly, topic sibling algorithm
- [`docs/SLIM_SYNC.md`](docs/SLIM_SYNC.md) — cold store drift detection and split-brain buffer protocol
- [`USER_GUIDE.md`](USER_GUIDE.md) — setup, configuration, and agent integration guide

---

## Related

- [creativebrain-obsidian-vault-template](https://github.com/pvnkmnk/creativebrain-obsidian-vault-template) — the vault template this daemon is designed for
- [cybaea/obsidian-vault-intelligence](https://github.com/cybaea/obsidian-vault-intelligence) — TypeScript Obsidian plugin whose Shadow Graph architecture informed the GARS scoring system, topic sibling traversal, accordion context assembly, and slim-sync cold store in this project
