# Scoring Architecture

> How vault-memory ranks search results — from raw similarity to GARS.

## Overview

vault-memory uses a **three-stage retrieve-and-rerank pipeline** inspired by the Shadow Graph architecture in
[cybaea/obsidian-vault-intelligence](https://github.com/cybaea/obsidian-vault-intelligence).

---

## Stage 1: Candidate Retrieval

Four parallel strategies produce an initial candidate pool:

| Strategy | Engine | Signal |
|---|---|---|
| Dense vector | Weaviate | Semantic meaning |
| Sparse BM25 | Weaviate | Exact + fuzzy keyword match |
| Graph traversal | Postgres | Entity relationship neighborhood |
| Temporal | Postgres | Date-range history |

Candidates are merged and deduplicated before scoring. Thresholds are kept permissive at this stage to ensure high recall.

**BM25 sigmoid calibration** — raw BM25 scores are unbounded, so they are normalized before blending:

```
normalized_bm25 = score / (score + keyword_weight)   # default keyword_weight = 1.2
```

This ensures BM25 scores approach `1.0` asymptotically, preserving ranking granularity without dominating semantic scores.

---

## Stage 2: Graph Analysis

For every candidate, the graph engine extracts two structural metrics:

**Centrality** — normalized degree centrality of the node in the knowledge graph:
```
centrality = degree(node) / (total_nodes - 1)
```
Cached in `sync_state.centrality_score` and refreshed by the heartbeat job.

**Activation** — neighbor co-occurrence score: fraction of the node's neighbors that also
appeared as candidates in Stage 1.
```
activation = |neighbors ∩ candidates| / |neighbors|
```

---

## Stage 3: GARS Re-ranking

**Graph-Augmented Relevance Score** is the final ranking signal:

```
GARS = (sim × W_sim) + (cent × W_cent) + (act × W_act)
```

Default weights (configurable in `.vault-memory.json`):

| Weight | Default | Meaning |
|---|---|---|
| `W_sim` | `0.70` | Vector + BM25 blended similarity |
| `W_cent` | `0.20` | Structural importance in the graph |
| `W_act` | `0.10` | Co-occurrence with other candidate hits |

Edge type affects traversal weight during activation scoring:
- `frontmatter` edges (e.g. `topics:`, `project:`) → strong structural signal, full weight
- `body` edges (inline wikilink mentions) → weaker signal, 0.6× weight
- `implicit-folder` edges → injected structural edges, 0.3× weight, never shown in UI

---

## Temporal Decay Layer

GARS feeds into the decay scoring layer on top:

```
final_score = GARS × 0.6 + recency × 0.3 + importance × 0.1
recency     = exp(−age_days / decay_days)
```

Decay profiles control the `decay_days` parameter:

| Profile | `decay_days` | Use for |
|---|---|---|
| `active` | 30 | STATE.md, ROADMAP.md, project notes |
| `reference` | 90 | Books, articles, REQUIREMENTS.md |
| `identity` | ∞ | boot.md, `{project}.md`, triggers.md |
| `log` | 10 (half-life 7d, floor 0.1) | Session logs, plans |

---

## Context Accordion Assembly

After GARS + decay ranking, the `ContextAssembler` packs the LLM context window using
**Relative Accordion Logic** — tiers are defined relative to the top result's score,
not by absolute thresholds. This means quality is consistent regardless of vault size.

| Tier | Threshold (% of top score) | Strategy |
|---|---|---|
| **Primary** | ≥ 90% | Full file content (10% budget cap per file) |
| **Supporting** | ≥ 70% | 500-char snippets around query terms |
| **Structural** | ≥ 35% | Headers only (TOC view), max 10 files |
| **Filtered** | < 35% | Dropped entirely — prevents hallucination-by-bloat |

**Sliding budget** — if a high-rank document is small, unused budget carries forward to
the next document in the list, maximizing context efficiency.

**Expansion floor** — neighbor expansion only triggers when the seed node has an
absolute GARS score ≥ 0.40, preventing noise expansion from weak matches.

---

## Topic Sibling Traversal

Notes can be discovered through a **shared topic node** even with no direct wikilink between them.
This is especially powerful with an `Ontology/` folder structure.

**Algorithm:**
1. Start with seed candidates from Stage 1
2. Find their direct graph neighbors
3. Identify neighbors that are **topic hubs**: `node_type = 'topic'` or `'moc'` AND `in_degree >= HUB_MIN_DEGREE` (default 5)
4. For each topic hub, collect **inbound neighbors** (all notes linking to that topic) — these are "siblings"
5. Score siblings using GARS, then apply a **logarithmic hub penalty** to dampen noise from massive hubs:

```
penalty = 1 / log2(in_degree + 2)
sibling_score = GARS × penalty
```

Topic hub state is maintained in the `topic_hubs` table and refreshed by the heartbeat centrality job.

---

## Maturity Effect on Scoring

Maturity gates importance at index time, which flows through to the final score via the `importance` term:

| Maturity | Importance at index time |
|---|---|
| `seed` | `min(stated_importance, 0.4)` — capped |
| `sapling` | stated as-is |
| `tree` | `max(stated_importance, 0.8)` — floored |
