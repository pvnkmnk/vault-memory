# vault-memory — Product Requirements Document

> **Status:** Accepted — 2026-09-10
> **Milestone:** v0.9.0 "Learning Loop" (issues #75–#85)
> **Supersedes:** implicit scope from Sprints S21–S29 planning notes
> **Companion:** [DESIGN_BOUNDARIES.md](DESIGN_BOUNDARIES.md) — what we deliberately do *not* build

---

## 1. Vision

**vault-memory is a self-improving knowledge layer for Obsidian vaults that learns from two sources — human-fed material and agent sessions — and consolidates what it learns into durable lessons, skills, and tooling for the agents that come next.**

The problem it exists to solve: you solve a hard problem on a Tuesday. Three months later — in a different repo, with a different agent — you solve it again from scratch, because the answer lived in a chat log no one will ever find. Meanwhile the best material you have (documents, articles, references) sits inert in a folder, invisible to every agent you work with.

vault-memory fixes both halves:

1. **The human loop** — a place to put documents, links, and resources; the system ingests them, compiles them into the knowledge base, and makes them available to agents.
2. **The agent loop** — every agent session is registered, captured, and *mined* after it closes; the lessons of what was done, what broke, and how work actually gets done on this system flow back to the next agent at session start.

Neither loop alone is sufficient. Documents without session mining are a static reference library. Sessions without human sources are a record of working with incomplete material. Together — and consolidated monthly into skills and MCP guidance — they make the vault *compound*: every session and every source makes the next unit of work cheaper and better-informed.

## 2. The Learning Loop (core architecture)

```
            ┌──────────────────────────────────────────────────┐
            │                    SOURCES                       │
            │  Human: inbox/ (docs, links, PDFs, pasted text)  │
            │  Agent: session records (closed sessions)        │
            └───────────────┬──────────────────────────────────┘
                            ▼
            ┌──────────────────────────────────────────────────┐
            │                 INGEST / MINE                    │
            │  S32-1: compile sources → raw/ + wiki pages      │
            │  S31-3: distill closed sessions → lesson drafts  │
            │  Both: extract triples → knowledge graph         │
            └───────────────┬──────────────────────────────────┘
                            ▼
            ┌──────────────────────────────────────────────────┐
            │              REVIEW GATE (human)                 │
            │  _working/ drafts → promote | reject(+reason)    │
            │  corroboration merges; rejections feed prompts   │
            └───────────────┬──────────────────────────────────┘
                            ▼
            ┌──────────────────────────────────────────────────┐
            │            CONSOLIDATED KNOWLEDGE                │
            │  wiki pages · lessons/ · skills/ · graph edges   │
            └──────┬──────────────────────────────┬────────────┘
                   ▼                              ▼
            ┌──────────────┐              ┌────────────────────┐
            │ SERVE        │              │ REPORT             │
            │ project_state│              │ daily 00:00        │
            │ returns top-K│              │ weekly Sun 23:00   │
            │ lessons at   │              │ monthly 1st →      │
            │ session start│              │ skills/MCP review  │
            └──────────────┘              └────────────────────┘
```

Two properties make this a *loop* rather than a pipeline:

- **Session start is session end's customer.** `memory/project_state` returns the lessons previous sessions paid for.
- **Rejection is fuel.** A rejected lesson draft stores its rejection reason, and that reason is injected into future mining prompts — the system gets measurably better at drafting, not just bigger.

## 3. Requirements

### 3.1 Session mining (S31) — learn from agent work

| ID | Requirement | Issue |
|----|-------------|-------|
| S31-1 | `sync_log` table exists; write endpoints accept `X-Session-Id`; attribution endpoint returns real data | #75 |
| S31-2 | `session_close` accepts structured record: decisions, mistakes, discoveries, gotchas, workflows | #76 |
| S31-3 | Closed sessions are mined (two-tier local LLM) into lesson/gotcha drafts in `_working/sessions/` + graph triples | #77 |
| S31-4 | Review gate: promote/reject with reason; corroboration merging; auto-promote policy (default off) | #78 |
| S31-5 | `memory/project_state` returns top-K project lessons ranked by recency × corroboration × trust | #79 |
| S31-6 | Benchmark: plain agent vs vault-memory on structural questions, networkx ground truth, published methodology | #80 |

### 3.2 Human ingestion (S32-1) — learn from human sources

| Requirement | Detail | Issue |
|-------------|--------|-------|
| Inbox | `inbox/` directory + `POST /ingest` + `vault-memory ingest <path-or-url>` + plugin Quick Ingest | #81 |
| Fetchers | markdown, plain text, PDF, URL (readability-extracted), pasted text | #81 |
| Archival | every source archived immutable under `raw/` with provenance frontmatter | #81 |
| Compilation | LLM distills sources into wiki pages per existing page conventions; wikilinks woven; triples extracted; manifest-tracked delta processing | #81 |
| Claim tagging | extracted / inferred / ambiguous, so lint can flag speculation drift | #81 |
| Conflict safety | contradiction with a high-trust page flags for review — never silent overwrite | #81 |

### 3.3 Digest cadence (S32-2/3/4) — the system reports on itself

| Cadence | Content | Output | Issue |
|---------|---------|--------|-------|
| **Daily** (00:00) | 24h page changes, sessions closed + lessons mined, pending drafts, lint flags, ingestion queue | `digests/{date}.md`, low-noise, LLM writes only a 3-sentence summary | #82 |
| **Weekly** (Sun 23:00) | 7-day velocity per project, corroboration events, promoted lessons, emerging entities, contradictions, sources processed — plus in-depth LLM synthesis with links | `digests/{ISO-week}.md` | #83 |
| **Monthly** (1st) | Long-form review **and consolidation**: cluster the month's corroborated lessons → propose updated workflows, new `skills/` files, updated MCP usage notes — all drafted to `_working/consolidation/` for human review | `digests/{month}.md` + draft skill files | #84 |

### 3.4 Skills layer (S32-5) — the loop's distribution format

`vault-memory skills export` generates agent-skills-compatible SKILL.md bundles from promoted lessons and entity workflow sections, so file-aware agents (Claude Code, Cursor, Codex, OpenCode) benefit even without the daemon running. Refreshed automatically after each monthly consolidation. (#85)

## 4. Non-goals

See [DESIGN_BOUNDARIES.md](DESIGN_BOUNDARIES.md) for the evidence-backed list — including: no cloud service, no numeric confidence scores on prose knowledge, no silent auto-merge of contradictions, no OKF conformance yet, no vector/graph search for agent-facing *lesson lookup* below the measured corpus crossover.

## 5. Success criteria (v0.9.0 exit)

1. A session's gotcha, mined on day 1, appears in the next session's `project_state` on day 2 — demonstrated end-to-end in CI, then once in real use.
2. A document dropped in `inbox/` is searchable, graph-linked, and agent-visible within one digest cycle.
3. At least one skill file exists that was proposed by monthly consolidation and promoted by a human.
4. The benchmark (#80) publishes real numbers with methodology, including failures.
5. Zero documented-but-nonexistent features (the S30 standard).

## 6. Trust model (read this before extending the loop)

- **Humans gate everything durable.** Mining, ingestion, and consolidation all produce *drafts*. Only `promote` writes to the wiki.
- **Provenance is mandatory.** Every mined edge carries `edge_source='session:{id}'`; every compiled page carries source frontmatter. Deletion of a session/source cascades to flagged (not silent) re-evaluation.
- **Corroboration beats volume.** One session claiming something is a draft; two independent sessions corroborating is a lesson; a human promoting is knowledge.
- **Lessons decay by recency, facts by contradiction.** Process knowledge (`log` profile) legitimately ages; corroborated lessons stabilize. Factual pages only change through the review gate.
