# P2/P3 Sprint Plan — vault-memory v0.5.0

## Audit Summary (as of 2026-04-07)

This document records the current state of 16 features identified as incomplete or partially implemented, along with a prioritized sprint plan to complete them.

### Key Finding: Several features have been implemented since the audit was initiated.

Recent commits on Apr 7, 2026:
- **P2-D: search_siblings full implementation with API + fallback** (3dc39c5)
- **Implement search_siblings endpoint for topic hub traversal** (cee887f)
- **Fix /temporal endpoint** (a3736b5)
- **P3-D — daemon /cognify endpoint** (bebbe78, b3950cb)
- **sync_watcher.py: v0.5.0-p3 COMPLETE CanvasParser** (b9941d3)

---

## Feature Status Matrix

| # | Feature | Status | Evidence | Sprint |
|---|---------|--------|----------|--------|
| 1 | search_siblings | **IMPLEMENTED** | /search_siblings POST in main.py, recent commit P2-D | — |
| 2 | centrality lookup | **IMPLEMENTED** | /graph endpoint in main.py, centrality column in temporal_entities | — |
| 3 | search_by_centrality | **IMPLEMENTED** | /graph endpoint returns relationships with edge_source filtering | — |
| 4 | write_batch API | **NEEDS WORK** | No bulk write endpoint; sync_file() exists but not exposed as HTTP API | P2 |
| 5 | graph_traversal retrieval | **PARTIAL** | _strategy_graph() in retrieval.py but no standalone /graph_traversal endpoint | P2 |
| 6 | temporal retrieval | **IMPLEMENTED** | /temporal GET endpoint in main.py, _strategy_temporal() in retrieval.py | — |
| 7 | BM25 sigmoid calibration | **NOT IMPLEMENTED** | Formula in SCORING.md but _strategy_sparse() returns raw BM25 scores unscaled | P3 |
| 8 | centrality recalc on heartbeat | **MISSING** | No heartbeat job; health.py only has liveness/readiness endpoints | P3 |
| 9 | ContextAssembler | **MISSING** | No ContextAssembler class exists anywhere in the codebase | P3 |
| 10 | GARS formula weights | **PARTIAL** | SCORING.md documents GARS = (sim x W_sim) + (cent x W_cent) + (act x W_act) but retrieval.py uses RRF fusion without explicit GARS scoring | P3 |
| 11 | centrality_score column | **IMPLEMENTED** | EXISTS in sync_state table per init_db.sql | — |
| 12 | read_batch API | **IMPLEMENTED** | UnifiedSearch.search() in retrieval.py serves as batch read; /search POST in main.py | — |
| 13 | typed edge weights | **IMPLEMENTED** | EDGE_WEIGHTS dict in retrieval.py, edge_source column in relationships table | — |
| 14 | skill_file schema | **IMPLEMENTED** | SKILL_SCHEMA.md + trigger_lookup in mcp_adapter.py scans 08 Meta/skills/*.md | — |
| 15 | SLIM_SYNC protocol | **IMPLEMENTED** | SLIM_SYNC.md + cold_store_hash + buffer_in_flight in sync_state table | — |
| 16 | sync_watcher debounce | **IMPLEMENTED** | DEBOUNCE_SECONDS = 2.0, flush_debounced() + _debounce_loop() in sync_watcher.py | — |

---

## Sprint Breakdown

### P2 Sprint — API & Graph Completion (Immediate)

#### P2-1: write_batch HTTP API
**Goal:** Expose bulk write capability over HTTP for agent sessions.

**Current state:**
- `sync_file()` exists in `daemon/sync_watcher.py` as `SyncEngine.sync_file()`
- No HTTP endpoint to call it from outside the watcher

**Required changes:**
1. Add `POST /write_batch` endpoint in `daemon/main.py`
2. Accept array of `{vault_path, content, caller}` objects
3. Call `engine.sync_file()` for each item with appropriate caller context
4. Return success/failure per item

**Files to modify:**
- `daemon/main.py` — add endpoint + request model
- `daemon/sync_watcher.py` — ensure `SyncEngine` is accessible from main

**Priority:** HIGH — Required for multi-agent write coordination
**Estimated effort:** 2-3 hours

---

#### P2-2: /graph_traversal Endpoint
**Goal:** Standalone graph traversal endpoint for exploratory queries.

**Current state:**
- `_strategy_graph()` exists in `retrieval.py` as part of UnifiedSearch
- `/graph` endpoint exists but only returns direct relationships (1-hop)
- No multi-hop traversal exposed as standalone API

**Required changes:**
1. Add `POST /graph_traversal` endpoint in `daemon/main.py`
2. Accept `{entity, max_hops, relationship_filter, include_properties}`
3. Call `_strategy_graph()` or a dedicated traversal function
4. Return full traversal path with activation scores

**Files to modify:**
- `daemon/main.py` — add endpoint
- `daemon/retrieval.py` — potentially refactor `_strategy_graph()` to be callable standalone

**Priority:** MEDIUM — Nice-to-have for graph exploration
**Estimated effort:** 1-2 hours

---

### P3 Sprint — Scoring & Context Architecture (Follow-up)

#### P3-1: BM25 Sigmoid Calibration
**Goal:** Normalize raw BM25 scores before blending with semantic scores.

**Current state:**
- `SCORING.md` documents: `normalized_bm25 = score / (score + keyword_weight)`
- `keyword_weight` default = 1.2
- `_strategy_sparse()` in `retrieval.py` returns raw BM25 scores unscaled

**Required changes:**
1. In `_strategy_sparse()`, apply sigmoid normalization to each BM25 score
2. Make `keyword_weight` configurable via Settings
3. Document in SCORING.md that this is now implemented

**Formula:**
```
normalized_bm25 = raw_score / (raw_score + 1.2)
```

**Files to modify:**
- `daemon/retrieval.py` — modify `_strategy_sparse()`
- `daemon/config.py` — add `keyword_weight` setting
- `docs/SCORING.md` — update status

**Priority:** HIGH — Critical for score blending integrity
**Estimated effort:** 30 minutes

---

#### P3-2: Heartbeat Job with Centrality Recalc
**Goal:** Background job that periodically recalculates centrality scores for all nodes.

**Current state:**
- `temporal_entities.centrality` column exists (default 0.0)
- `sync_state.centrality_score` column exists (default 0.0)
- `topic_hubs` table exists for hub tracking
- `health.py` only has `/health` and `/ready` endpoints — no heartbeat
- No background task in `main.py` lifespan

**Required changes:**
1. Create `daemon/heartbeat.py` with centrality recalc logic:
   ```python
   centrality = degree(node) / (total_nodes - 1)
   ```
2. Add topic hub refresh (rebuild `topic_hubs` table)
3. Update `sync_state.centrality_score` from `temporal_entities.centrality`
4. Add heartbeat task to `lifespan()` in `main.py`
5. Schedule: every 15 minutes or configurable

**Files to create/modify:**
- `daemon/heartbeat.py` — NEW FILE
- `daemon/main.py` — add heartbeat task in lifespan
- `daemon/config.py` — add `heartbeat_interval_seconds`
- `init_db.sql` — already has schema, no changes needed

**Priority:** CRITICAL — Required for GARS scoring to function
**Estimated effort:** 3-4 hours

---

#### P3-3: ContextAssembler — Relative Accordion Logic
**Goal:** Pack LLM context window using tier-based accordion strategy.

**Current state:**
- No ContextAssembler exists anywhere
- SCORING.md documents the full algorithm:
  - Primary tier: >= 90% of top score -> full content
  - Supporting tier: >= 70% -> 500-char snippets
  - Structural tier: >= 35% -> headers only
  - Filtered: < 35% -> dropped
  - Sliding budget + expansion floor at 0.40

**Required changes:**
1. Create `daemon/context_assembler.py` with `ContextAssembler` class
2. Implement tier classification relative to top result's GARS score
3. Implement sliding budget carry-forward
4. Implement neighbor expansion with floor check
5. Integrate into search response — return assembled context alongside results

**Files to create/modify:**
- `daemon/context_assembler.py` — NEW FILE (~200 lines)
- `daemon/main.py` — add `POST /assemble` endpoint or integrate into search
- `docs/SCORING.md` — update implementation status

**Priority:** HIGH — Required for high-quality LLM context building
**Estimated effort:** 4-5 hours

---

#### P3-4: GARS Re-ranking Implementation
**Goal:** Implement the full GARS formula as the final scoring signal.

**Current state:**
- `SCORING.md` documents: `GARS = (sim x W_sim) + (cent x W_cent) + (act x W_act)`
- Default weights: W_sim=0.70, W_cent=0.20, W_act=0.10
- `retrieval.py` uses RRF fusion + cross-encoder reranking
- No explicit GARS calculation or weight blending
- `SCORING.md` also documents temporal decay layer on top of GARS

**Required changes:**
1. Add `gars_score()` function to `retrieval.py`
2. Compute `sim` component (already exists as fused score)
3. Fetch `cent` from `temporal_entities.centrality` (requires join)
4. Compute `act` from neighbor co-occurrence
5. Apply weights from config
6. Apply temporal decay layer: `final = GARS * 0.6 + recency * 0.3 + importance * 0.1`
7. Make weights configurable in `.vault-memory.json`

**Files to modify:**
- `daemon/retrieval.py` — add GARS scoring stage after RRF fusion
- `daemon/config.py` — add GARS weight settings
- `.vault-memory.json` — add weight config example
- `docs/SCORING.md` — update status

**Priority:** CRITICAL — This is the core scoring algorithm
**Estimated effort:** 4-5 hours

---

## Sprint Schedule

### Sprint P2 — Due 2026-04-08
- [ ] P2-1: write_batch HTTP API
- [ ] P2-2: /graph_traversal endpoint

### Sprint P3 — Due 2026-04-10
- [ ] P3-1: BM25 sigmoid calibration
- [ ] P3-2: Heartbeat job with centrality recalc
- [ ] P3-3: ContextAssembler
- [ ] P3-4: GARS re-ranking

---

## Implementation Order (Recommended)

1. **P3-1** (BM25 calibration) — Smallest change, highest immediate impact on score quality
2. **P3-2** (Heartbeat) — Unblocks centrality data for all downstream scoring
3. **P3-4** (GARS) — Core algorithm, depends on heartbeat data
4. **P3-3** (ContextAssembler) — Depends on GARS scores for tier classification
5. **P2-1** (write_batch) — Can be done anytime, independent
6. **P2-2** (graph_traversal) — Lowest priority, nice-to-have

---

## Dependencies Between Tasks

```
P3-2 (Heartbeat) --> P3-4 (GARS) --> P3-3 (ContextAssembler)
                         ^
                    P3-1 (BM25) — independent but needed for sim component

P2-1 (write_batch) — independent
P2-2 (graph_traversal) — independent
```

---

## Test Plan

### Unit Tests Needed
- `test_bars_score()` — verify GARS formula with known inputs
- `test_sigmoid_bm25()` — verify normalization curve
- `test_centrality_recalc()` — verify degree centrality calculation
- `test_context_assembler_tiers()` — verify tier boundaries

### Integration Tests Needed
- `/write_batch` — POST array of docs, verify all indexed
- `/graph_traversal` — multi-hop query returns correct paths
- Full search with GARS — verify scores reflect all three components

---

## Rollback Plan

If any P3 changes break search:
1. Revert `retrieval.py` to pre-GARS state (RRF-only)
2. Keep heartbeat running (data is useful regardless)
3. Re-enable in next patch

All changes are additive — no breaking schema changes.
mi
