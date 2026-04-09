# vault-memory: Karpathy-Informed Codebase Review
**Branch: `codex/sprints` | Repo: `pvnkmnk/vault-memory`**

## Context

You are reviewing the `pvnkmnk/vault-memory` repository on the `codex/sprints` branch — an
always-on, local-first memory layer for Obsidian, built as a Python monorepo with a FastAPI
daemon, CLI, MCP stdio adapter, Weaviate vector store, PostgreSQL semantic graph, and
Ollama-powered triple extraction (cognify). The system is at v0.5.0 with 8 completed audit
sprints (S1–S8) on this branch.

A comparative analysis against Andrej Karpathy's `llm-wiki` pattern
(https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) identified that
vault-memory now has **production-grade infrastructure** across auth, DI, connection pooling,
GARS scoring, session lifecycle, and 15 MCP tools — but still lacks three specific items that
close the "operational ritual layer" gap:

1. **`memory/promote` tool** — answer promotion loop (file good agent responses as wiki pages)
2. **`vault_lint` tool** — named, schedulable vault health check operation
3. **AGENTS.md Session Protocol section** — transform the schema file from a reference doc
   into a behavioural contract the agent reads and follows

Your job is to conduct a full architectural review and produce a concrete implementation plan
that closes exactly these three gaps without bloating the existing infrastructure.

---

## Step 1 — Codebase Inventory

Read and map every file listed below before doing anything else. For each, note its purpose,
current state, and any gaps relevant to the three target items.

**Root:**
```
AGENTS.md                    # Agent-readable schema — exists, needs Session Protocol section
.vault-memory.json           # Config
README.md
USER_GUIDE.md
VERIFIED_SPRINT_PLAN.md      # Historical sprint audit record (S1–S8)
UPDATED_SPRINT_PROMPT.md
```

**Daemon:**
```
daemon/main.py               # FastAPI routes — 46KB, all endpoints
daemon/retrieval.py          # 4-strategy search, GARS scoring, decay, context assembly
daemon/context_assembler.py  # Accordion context assembly — now integrated (S4 fix)
daemon/heartbeat.py          # Heartbeat scheduler
daemon/sync_watcher.py       # File watcher, drift detection CLI flags
daemon/health.py             # /health, /ready endpoints + mark_ready/mark_degraded
daemon/pg_client.py          # ThreadedConnectionPool with context managers
daemon/weaviate_client.py    # Weaviate upsert/query with full schema properties
daemon/dependencies.py       # Formal DI container (Dependencies class, S7)
daemon/embedder.py           # Embedding service
daemon/config.py             # Settings — env vars now highest priority (S2 fix)
daemon/validate_write.py     # Write gate validation
```

**CLI / MCP:**
```
cli/mcp_adapter.py           # MCP stdio adapter — 15 tools, full implementation
```

**Docs:**
```
docs/SCORING.md              # GARS algorithm specification
docs/CONTEXT_ASSEMBLY.md     # Context assembler spec
docs/SKILL_SCHEMA.md         # Skill schema (now superseded by AGENTS.md?)
docs/SLIM_SYNC.md            # Sync architecture
docs/STATE_TEMPLATE.md       # STATE.md template spec
docs/sprints/SPRINT_MODERNIZE_NO_COMPAT.md
```

**Other:**
```
init_db.sql                  # PostgreSQL schema — entities, relationships, sessions, chunks
tests/                       # Test suite (added S7)
pyproject.toml               # Dependencies, version 0.5.0
docker-compose.yml           # Weaviate + Postgres services
```

---

## Step 2 — Current MCP Tool Surface Audit

List every tool defined in `cli/mcp_adapter.py` TOOLS array. For each, produce a table row:

| Tool Name | Operation Type (Ingest/Query/Lint/Session/Util) | Agent-Clarity Score 1–5 | Gap vs. Karpathy |
|---|---|---|---|

The 15 known tools are:
`search`, `search_siblings`, `graph`, `temporal`, `health`,
`memory/attach_block`, `memory/list_blocks`, `memory/read_batch`,
`memory/write_working`, `memory/delete_working`, `memory/trigger_lookup`,
`memory/project_state`, `memory/session_register`, `memory/session_close`,
`memory/cognify`

After the table, answer: which Karpathy operation types are **unrepresented** in this tool
surface? (Expected answer: Lint and answer Promotion.)

---

## Step 3 — Gap Analysis: Three Target Items

Evaluate each of the three gaps with file-level evidence. For each, produce:
- **Verdict**: Implemented / Partial / Missing
- **Closest existing code**: exact file + function/route name
- **What is missing**: precisely what needs to be written or changed
- **Estimated complexity**: Low / Medium / High

### Gap A — `memory/promote` Tool

The answer promotion loop: when an agent produces a high-quality synthesis, comparison, or
analysis, it should be filed back into the vault as a permanent named wiki page — not staged
in `_working/` for heartbeat promotion, but written directly as a mature page, cognified for
triple extraction, cross-linked to referenced entities, and appended to a unified audit log.

Check: Does `memory/write_working` + `memory/cognify` cover this? What is the gap between
"staged draft with maturity:seed" and "promoted wiki page with maturity:evergreen"? Where
should the `memory/promote` tool live in `mcp_adapter.py` and what daemon endpoint should
it call?

### Gap B — `vault_lint` Tool

The health check operation: find orphan entities (no inbound graph edges), contradiction
triples (same subject+relationship, conflicting object values), stale nodes (decay_profile=
active but updated_at older than configurable threshold), concept mentions in notes that
lack their own entity page, and cross-reference gaps (entity page exists but is never linked
from other notes).

Check: Does `daemon/sync_watcher.py`'s drift detection cover any of this? Does
`daemon/retrieval.py` expose any orphan/contradiction queries? What queries against
`init_db.sql`'s schema would be needed? Should `daemon/lint.py` be a new file, or should
lint logic extend an existing module? Should the lint report be filed to the vault as
`lint-YYYY-MM-DD.md`?

### Gap C — AGENTS.md Session Protocol Section

AGENTS.md currently documents quick commands, key paths, gotchas, sprint history, and
available MCP tools — written from an engineer's perspective. Karpathy's schema file is
prescriptive: it specifies what the agent MUST do at session start and end, what page
conventions to follow, and what the three named operations are.

Check: Does the current AGENTS.md contain any numbered session ritual? Does it specify what
the agent should do at session start (call `memory/project_state` + `memory/session_register`)
and session end (call `memory/promote` on synthesis + `memory/session_close`)? Does it define
page conventions (entity page vs. concept page vs. comparison page vs. lint report)? Does it
define what "wiki-quality" means for promotion eligibility?

---

## Step 4 — `init_db.sql` Schema Review for Lint Queries

Read `init_db.sql` and identify:

1. Which tables and columns support **orphan detection** (entities with zero inbound
   relationship edges)
2. Which tables support **contradiction detection** (duplicate subject+relationship_type
   rows with different object values)
3. Which columns support **staleness detection** (`updated_at`, `decay_profile`,
   `last_accessed`)
4. Is there a `maturity` column on entity/chunk records that `memory/promote` can set
   to `evergreen`?
5. Is there an existing `operation_log` or audit table? If not, note that `log.md` is the
   proposed solution.

---

## Step 5 — Daemon Route Inventory for Gaps

Read `daemon/main.py` and list every existing POST/GET/PATCH route. Then identify:

1. Is there a `/promote` route? If not, specify its request schema based on the
   `memory/promote` tool design.
2. Is there a `/lint` route? If not, specify its request schema.
3. Is there a `/log` route for reading/writing the unified audit log? If not, note it.
4. Do existing routes for `/cognify`, `/write`, `/sessions` provide sufficient building
   blocks for the `memory/promote` pipeline?

---

## Step 6 — Implementation Plan: P9 Sprint

Based on all findings, produce a new sprint file: `docs/sprints/SPRINT_P9_RITUAL_LAYER.md`

Structure it exactly as follows:

```markdown
# Sprint P9 — Operational Ritual Layer
**Branch:** codex/sprints
**Goal:** Close the three remaining Karpathy gaps: answer promotion, vault lint,
and AGENTS.md behavioural contract.

## Deliverables

### P9-A: memory/promote MCP Tool + /promote Daemon Route
**Files to modify:** `cli/mcp_adapter.py`, `daemon/main.py`
**New file (if needed):** none — compose existing cognify + write + log writer

Tool input schema:
- `text` (string, required) — agent-generated content to promote
- `title` (string, required) — wiki page title / filename
- `page_type` (enum: entity|concept|comparison|analysis) — determines frontmatter template
- `references` (array of strings) — entity names this page links to
- `vault_path` (string, required)
- `daemon_url` (string, optional)

Pipeline (in order):
1. Write page to vault at canonical path (NOT _working/) with maturity:evergreen,
   trust:agent-reviewed, decay-profile:active, agent-written:true
2. Call /cognify on page text — upsert extracted triples to PG
3. For each name in references: ensure wikilink [[name]] exists in page body
4. Append entry to log.md: `## [YYYY-MM-DD HH:MM] promote | {title} | refs: N triples: M`
5. Return: path written, triples extracted, references linked, log entry

**Success criterion:** An agent can call memory/promote and the vault gains a permanent,
indexed, cognified, cross-linked wiki page in one tool call.

---

### P9-B: vault_lint MCP Tool + /lint Daemon Route + daemon/lint.py
**New file:** `daemon/lint.py`
**Files to modify:** `cli/mcp_adapter.py`, `daemon/main.py`

`daemon/lint.py` must implement:
```python
async def run_lint(pg, weaviate, vault_root: Path, stale_days: int = 30) -> LintReport:
    orphans: list        # entities with zero inbound relationship edges
    contradictions: list # (subject, rel_type, [conflicting objects])
    stale_nodes: list    # active nodes not updated in stale_days
    missing_pages: list  # entity names appearing in notes but lacking their own page
    unlinked_pages: list # entity pages that are never wikilinked from other notes
```

Daemon route: `POST /lint` — returns LintReport as JSON
MCP tool: `vault_lint`
- Input: `vault_path`, `stale_days` (default 30), `file_report` (bool, default true)
- Output: structured lint report + optional filed `lint-YYYY-MM-DD.md` in vault

**Success criterion:** An agent (or human) can call vault_lint and receive a filed,
actionable health report with counts and specific items for each check type.

---

### P9-C: AGENTS.md Session Protocol Section
**File to modify:** `AGENTS.md` (append new section — do NOT rewrite existing content)

Add the following section after the existing MCP Tools list:

## Agent Session Protocol

Every agent working in this vault MUST follow this ritual. No exceptions.

### Session Start (in order)
1. Call `memory/project_state` with your project slug — loads identity, STATE.md, roadmap,
   and semantic context in one call
2. Call `memory/session_register` with agent_name, project, and a one-line task description
3. Read AGENTS.md (this file) to load operational conventions

### During Session
- Use `search` for any question that might be answered by existing vault knowledge
- Use `memory/cognify` before writing any new knowledge to extract triples first
- Use `memory/write_working` for drafts, scratch work, and uncertain output
- Use `memory/promote` for any synthesis, analysis, or comparison that is wiki-quality

### Wiki-Quality Threshold (promote vs. write_working)
Promote if ALL of the following are true:
- [ ] The content answers a question that will recur
- [ ] The content synthesises across multiple sources or sessions
- [ ] You are confident (confidence: high) in the accuracy
- [ ] The content would be useful to a future agent starting fresh

Write to _working/ if any of the above is false.

### Page Conventions
| Page Type    | When to use                                     | Filename convention        | maturity          |
|--------------|-------------------------------------------------|----------------------------|-------------------|
| entity       | A named thing (project, person, tool, concept)  | `{name}.md`                | sapling→evergreen |
| concept      | An idea or pattern without a fixed name         | `concept-{slug}.md`        | sapling           |
| comparison   | Side-by-side analysis of two+ things            | `compare-{a}-vs-{b}.md`    | evergreen         |
| analysis     | Deep dive on a single topic                     | `analysis-{slug}.md`       | evergreen         |
| lint report  | vault_lint output                               | `lint-YYYY-MM-DD.md`       | seed              |

### Session End (in order)
1. Call `memory/promote` for any response in this session that meets wiki-quality threshold
2. Update STATE.md with current position, last decision, and next action
3. Call `memory/session_close` with your session_id

---

## Priority Order
P9-C first (no code change, highest leverage per effort)
→ P9-A second (closes compounding loop)
→ P9-B third (closes health ritual)

## Success Criteria — P9 Complete When:
1. An agent reading AGENTS.md has a complete behavioural contract
2. `memory/promote` files a permanent, cognified, cross-linked wiki page in one call
3. `vault_lint` returns a structured health report with orphans, contradictions,
   stale nodes, and missing pages — and files it as lint-YYYY-MM-DD.md
4. All three new tools are documented in the MCP Tools section of AGENTS.md
```

---

## Output Format

Produce four artifacts in order. Do not modify any existing files except as noted.

1. **Architecture Map** — annotated file tree, one line per file, current state + gap flag
2. **MCP Tool Audit Table** — as specified in Step 2
3. **Gap Analysis** — three sections (A/B/C), verdict + evidence + missing piece + complexity
4. **`docs/sprints/SPRINT_P9_RITUAL_LAYER.md`** — the full sprint plan file, ready to commit

Write only `docs/sprints/SPRINT_P9_RITUAL_LAYER.md` as a new file. Flag any files that should
be refactored in the Architecture Map but do not change them during this review.
