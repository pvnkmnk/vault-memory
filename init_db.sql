-- init_db.sql
-- Runs once on first postgres container start
-- v0.3.0 — GARS scoring fields, topic sibling traversal support,
--           slim-sync cold store registry, split-brain buffer tracking

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
                                  CHECK (edge_source IN ('frontmatter','body','implicit-folder')),
    properties        JSONB,
    created_at        TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- vault_entity_links
-- Maps vault file chunks → entity nodes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vault_entity_links (
    entity_id  UUID DEFAULT gen_random_uuid(),
    vault_path TEXT NOT NULL,
    chunk_uuid TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (vault_path, chunk_uuid)
);

-- ---------------------------------------------------------------------------
-- workflow_history
-- Temporal history of vault file states.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflow_history (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_path     TEXT        NOT NULL,
    content        TEXT,
    change_summary TEXT,
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to       TIMESTAMPTZ
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
    last_synced_at     TIMESTAMPTZ DEFAULT now()
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
    closed_at      TIMESTAMPTZ
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
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_temporal_entities_name      ON temporal_entities(entity_name);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_time      ON temporal_entities(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_props     ON temporal_entities USING gin(properties);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_centrality ON temporal_entities(centrality DESC);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_type      ON temporal_entities(node_type);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_name_trgm ON temporal_entities USING gin(entity_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_vault_entity_links_path     ON vault_entity_links(vault_path);
CREATE INDEX IF NOT EXISTS idx_relationships_source        ON relationships(source_name, relationship_type);
CREATE INDEX IF NOT EXISTS idx_relationships_target        ON relationships(target_name, relationship_type);
CREATE INDEX IF NOT EXISTS idx_relationships_edge_source   ON relationships(edge_source);
CREATE INDEX IF NOT EXISTS idx_workflow_history_path_time  ON workflow_history(vault_path, valid_from DESC);
CREATE INDEX IF NOT EXISTS idx_sync_state_path             ON sync_state(file_path);
CREATE INDEX IF NOT EXISTS idx_sync_state_maturity         ON sync_state(maturity);
CREATE INDEX IF NOT EXISTS idx_sync_state_drift            ON sync_state(file_path) WHERE cold_store_hash IS NULL OR cold_store_hash != content_hash;
CREATE INDEX IF NOT EXISTS idx_agent_sessions_status       ON agent_sessions(status, project);
CREATE INDEX IF NOT EXISTS idx_topic_hubs_degree           ON topic_hubs(in_degree DESC);

-- ---------------------------------------------------------------------------
-- Seed rows
-- ---------------------------------------------------------------------------
INSERT INTO sync_state (file_path, content_hash, chunk_count, maturity, centrality_score)
VALUES ('__init__', 'none', 0, 'tree', 1.0)
ON CONFLICT (file_path) DO NOTHING;
