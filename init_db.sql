-- init_db.sql
-- Runs once on first postgres container start
-- v0.8.0 — Full schema with Canvas entities, session attribution, GARS scoring
--
-- Schema Overview:
--
--   temporal_entities ──┐
--                        ├── relationships ──┐
--   canvas_entities ────┘                    │
--                                            │
--   vault_entity_links ── vault_chunks (Weaviate)
--                                            │
--   sync_state ──────── file hash registry ──┘
--
--   workflow_history ─── versioned content snapshots
--   agent_sessions ──── multi-agent coordination
--   topic_hubs ──────── high-centrality nodes for sibling traversal
--
-- Data Flow:
--   1. Sync watcher chunks markdown/canvas files → Weaviate (vectors) + PG (metadata)
--   2. Cognify extracts triples → temporal_entities + relationships
--   3. Canvas pipeline extracts entities → canvas_entities → relationships (edge_source='canvas')
--   4. Heartbeat recalculates centrality → updates temporal_entities + sync_state
--   5. Search queries fuse vector, BM25, graph, and temporal strategies (GARS scoring)

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ---------------------------------------------------------------------------
-- temporal_entities
-- Core knowledge graph nodes. Supports ontology-aware traversal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS temporal_entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_name    TEXT        NOT NULL,
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to       TIMESTAMPTZ,
    properties     JSONB,
    change_summary TEXT,
    -- GARS: degree centrality (0.0–1.0, normalized by graph size)
    -- Updated by background centrality recalc job or heartbeat.
    centrality     FLOAT       NOT NULL DEFAULT 0.0,
    -- GARS: node type for ontology traversal
    -- 'note' | 'topic' | 'moc' | 'entity'
    node_type      TEXT        NOT NULL DEFAULT 'note'
                               CHECK (node_type IN ('note','topic','moc','entity')),
    CONSTRAINT uq_entity_name UNIQUE (entity_name)
);

-- ---------------------------------------------------------------------------
-- relationships
-- Typed graph edges. Edge type affects GARS traversal weight.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationships (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id         UUID,
    source_name       TEXT NOT NULL,
    target_id         UUID,
    target_name       TEXT NOT NULL,
    -- Typed relationships: CAUSES | CONTRADICTS | EXTENDS | USES | REFERENCES | PART_OF
    relationship_type TEXT NOT NULL,
    -- edge_source mirrors cybaea edge typing:
    --   'frontmatter' = strong structural signal (topics:, project:)
    --   'body'        = weaker inline wikilink mention
    --   'implicit-folder' = injected by folder semantics (never shown in graph UI)
    edge_source       TEXT        NOT NULL DEFAULT 'body'
                                  CHECK (edge_source IN ('frontmatter','body','implicit-folder','canvas')),
    properties        JSONB,
    created_at        TIMESTAMPTZ DEFAULT now(),
    -- Deduplicate relationships by natural key (source + target + type + edge_source)
    CONSTRAINT uq_relationships_pair UNIQUE (source_name, target_name, relationship_type, edge_source)
);

-- ---------------------------------------------------------------------------
-- vault_entity_links
-- Maps vault file chunks → entity nodes.
-- Composite PK (vault_path, chunk_uuid) prevents duplicate mappings.
-- Used by search to trace results back to source files.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vault_entity_links (
    entity_id  UUID DEFAULT gen_random_uuid(),
    vault_path TEXT NOT NULL,              -- Relative path within vault
    chunk_uuid TEXT NOT NULL,              -- Weaviate object UUID
    created_at TIMESTAMPTZ DEFAULT now(),  -- When this link was created
    PRIMARY KEY (vault_path, chunk_uuid)
);

-- ---------------------------------------------------------------------------
-- workflow_history
-- Temporal history of vault file states.
-- Stores content snapshots for time-travel queries and change tracking.
-- valid_to is NULL for the current version.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflow_history (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_path     TEXT        NOT NULL,   -- File path
    content        TEXT,                   -- Full file content at this point in time
    change_summary TEXT,                   -- Human-readable description of what changed
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- When this version became current
    valid_to       TIMESTAMPTZ                        -- When this version was superseded (NULL = current)
);

-- ---------------------------------------------------------------------------
-- sync_state
-- Tracks indexed file state. maturity gates heartbeat promotion.
-- GARS: centrality_score cached here to avoid re-querying graph at search time.
-- Slim-sync: cold_store_hash tracks the last saved .msgpack-equivalent snapshot
--            (JSON cold store for vault-memory) to detect drift between hot
--            (Weaviate) and cold (vault file) representations.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_state (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path          TEXT        UNIQUE NOT NULL,
    content_hash       TEXT        NOT NULL,
    chunk_count        INT         DEFAULT 0,
    -- maturity: seed | sapling | tree
    -- seed    = agent-written, unreviewed (importance capped at 0.4 at index time)
    -- sapling = partially reviewed, one review pass needed (normal importance)
    -- tree    = fully reviewed, permanent knowledge (importance floor 0.8)
    maturity           TEXT        NOT NULL DEFAULT 'seed'
                                   CHECK (maturity IN ('seed','sapling','tree')),
    -- GARS centrality cache (0.0–1.0). Refreshed by heartbeat centrality job.
    centrality_score   FLOAT       NOT NULL DEFAULT 0.0,
    -- Slim-sync cold store: hash of the last written cold snapshot for this file.
    -- NULL = never cold-saved. Mismatch with content_hash = hot/cold drift detected.
    cold_store_hash    TEXT,
    -- Split-brain buffer flag: TRUE while the background buffer write for this
    -- file is in-flight. Prevents concurrent main-thread + watcher writes.
    buffer_in_flight   BOOLEAN     NOT NULL DEFAULT FALSE,
    last_synced_at     TIMESTAMPTZ DEFAULT now(),
    -- Soft-delete flag: TRUE when file is deleted from vault.
    -- Allows delta sync to report deletions to mobile clients.
    is_deleted         BOOLEAN     NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------------------
-- agent_sessions
-- Multi-agent coordination registry.
-- Mirrors cybaea WorkerLifecycleManager restart/state tracking.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_sessions (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name     TEXT        NOT NULL,   -- opencode | gemini-cli | perplexity | claude | etc.
    project        TEXT,                   -- project slug being worked on
    task           TEXT,                   -- current task description
    plan_ref       TEXT,                   -- path to plans/YYYY-MM-DD-{task}.md
    vault_paths    TEXT[],                 -- vault paths currently in scope
    status         TEXT        NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active','idle','closed')),
    started_at     TIMESTAMPTZ DEFAULT now(),
    last_ping_at   TIMESTAMPTZ DEFAULT now(),
    closed_at      TIMESTAMPTZ,
    notes          TEXT                     -- optional session notes/output
);

-- ---------------------------------------------------------------------------
-- topic_hubs
-- Tracks Ontology/ nodes that qualify as topic hubs for sibling traversal.
-- A node qualifies when its in-degree exceeds HUB_MIN_DEGREE (default 5).
-- Refreshed by heartbeat or triggered after centrality recalc.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS topic_hubs (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_path     TEXT        UNIQUE NOT NULL,  -- e.g. Ontology/Concepts/Agentic AI.md
    entity_name    TEXT        NOT NULL,
    in_degree      INT         NOT NULL DEFAULT 0,
    -- Logarithmic hub penalty weight applied during sibling expansion scoring:
    -- penalty = 1 / log2(in_degree + 2)  — dampens massive hubs (Daily Notes, MOCs)
    hub_penalty    FLOAT       NOT NULL DEFAULT 1.0,
    last_updated   TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- canvas_entities
-- Entities extracted from Obsidian Canvas files. Links canvas nodes to
-- the knowledge graph for Canvas-derived graph traversal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS canvas_entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canvas_path    TEXT        NOT NULL,       -- e.g. "project-diagram.canvas"
    node_id        TEXT        NOT NULL,       -- Canvas node id (e.g. "abc123")
    entity_name    TEXT,                       -- Extracted entity name
    entity_type    TEXT,                       -- e.g. 'concept', 'person', 'tool'
    node_text      TEXT,                       -- Original node text (truncated)
    extracted_at   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_canvas_entity UNIQUE (canvas_path, node_id)
);

CREATE INDEX IF NOT EXISTS idx_canvas_entities_path      ON canvas_entities(canvas_path);
CREATE INDEX IF NOT EXISTS idx_canvas_entities_entity    ON canvas_entities(entity_name);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
-- temporal_entities: name lookup, time range queries, GIN for JSONB properties,
--   centrality ranking, node type filtering, trigram for fuzzy search
CREATE INDEX IF NOT EXISTS idx_temporal_entities_name      ON temporal_entities(entity_name);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_time      ON temporal_entities(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_props     ON temporal_entities USING gin(properties);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_centrality ON temporal_entities(centrality DESC);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_type      ON temporal_entities(node_type);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_name_trgm ON temporal_entities USING gin(entity_name gin_trgm_ops);

-- vault_entity_links: reverse lookup from file to entities
CREATE INDEX IF NOT EXISTS idx_vault_entity_links_path     ON vault_entity_links(vault_path);

-- relationships: graph traversal (outgoing/incoming edges, edge source filtering)
CREATE INDEX IF NOT EXISTS idx_relationships_source        ON relationships(source_name, relationship_type);
CREATE INDEX IF NOT EXISTS idx_relationships_target        ON relationships(target_name, relationship_type);
CREATE INDEX IF NOT EXISTS idx_relationships_edge_source   ON relationships(edge_source);

-- workflow_history: time-ordered file history
CREATE INDEX IF NOT EXISTS idx_workflow_history_path_time  ON workflow_history(vault_path, valid_from DESC);

-- sync_state: file lookup, maturity filtering, drift detection (partial index)
CREATE INDEX IF NOT EXISTS idx_sync_state_path             ON sync_state(file_path);
CREATE INDEX IF NOT EXISTS idx_sync_state_maturity         ON sync_state(maturity);
CREATE INDEX IF NOT EXISTS idx_sync_state_drift            ON sync_state(file_path) WHERE cold_store_hash IS NULL OR cold_store_hash != content_hash;

-- agent_sessions: active session lookup by status + project
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status       ON agent_sessions(status, project);

-- topic_hubs: order by in-degree for hub selection
CREATE INDEX IF NOT EXISTS idx_topic_hubs_degree           ON topic_hubs(in_degree DESC);

-- canvas_entities: lookup by canvas file or extracted entity name
CREATE INDEX IF NOT EXISTS idx_canvas_entities_path      ON canvas_entities(canvas_path);
CREATE INDEX IF NOT EXISTS idx_canvas_entities_entity    ON canvas_entities(entity_name);

-- ---------------------------------------------------------------------------
-- Seed rows
-- ---------------------------------------------------------------------------
INSERT INTO sync_state (file_path, content_hash, chunk_count, maturity, centrality_score)
VALUES ('__init__', 'none', 0, 'tree', 1.0)
ON CONFLICT (file_path) DO NOTHING;
