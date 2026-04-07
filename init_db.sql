-- init_db.sql
-- Runs once on first postgres container start

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

CREATE TABLE IF NOT EXISTS temporal_entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_name    TEXT        NOT NULL,
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to       TIMESTAMPTZ,
    properties     JSONB,
    change_summary TEXT,
    CONSTRAINT uq_entity_name UNIQUE (entity_name)
);

CREATE TABLE IF NOT EXISTS relationships (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id         UUID,
    source_name       TEXT NOT NULL,
    target_id         UUID,
    target_name       TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    properties        JSONB,
    created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vault_entity_links (
    entity_id  UUID DEFAULT gen_random_uuid(),
    vault_path TEXT NOT NULL,
    chunk_uuid TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (vault_path, chunk_uuid)
);

CREATE TABLE IF NOT EXISTS workflow_history (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_path     TEXT        NOT NULL,
    content        TEXT,
    change_summary TEXT,
    valid_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sync_state (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path      TEXT        UNIQUE NOT NULL,
    content_hash   TEXT        NOT NULL,
    chunk_count    INT         DEFAULT 0,
    last_synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_temporal_entities_name     ON temporal_entities(entity_name);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_time     ON temporal_entities(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_props    ON temporal_entities USING gin(properties);
CREATE INDEX IF NOT EXISTS idx_vault_entity_links_path    ON vault_entity_links(vault_path);
CREATE INDEX IF NOT EXISTS idx_relationships_source       ON relationships(source_name, relationship_type);
CREATE INDEX IF NOT EXISTS idx_relationships_target       ON relationships(target_name, relationship_type);
CREATE INDEX IF NOT EXISTS idx_workflow_history_path_time ON workflow_history(vault_path, valid_from DESC);
CREATE INDEX IF NOT EXISTS idx_sync_state_path            ON sync_state(file_path);
CREATE INDEX IF NOT EXISTS idx_temporal_entities_name_trgm ON temporal_entities USING gin(entity_name gin_trgm_ops);

INSERT INTO sync_state (file_path, content_hash, chunk_count)
VALUES ('__init__', 'none', 0)
ON CONFLICT (file_path) DO NOTHING;
