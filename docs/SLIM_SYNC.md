# Slim-Sync Cold Store Protocol

> How vault-memory prevents hot/cold drift and split-brain state between Weaviate and the vault filesystem.

## The Problem

Vault-memory maintains two representations of every indexed file:

- **Hot store (Weaviate)** — full-content vector index, used for all search operations
- **Cold store (vault file)** — the source `.md` file on disk, the canonical human-readable version

Without coordination, these can diverge: the Weaviate index reflects a stale version of a file that
has since been modified on disk, or a file is deleted from disk but not from the index. This is
**hot/cold drift**.

A second failure mode exists when two processes (the real-time `VaultSyncWatcher` and the manual
`vault-memory sync` command) attempt to serialize state simultaneously. This is **split-brain**.

---

## The Slim-Sync Solution

### Cold Store Tracking

Every row in `sync_state` carries two hashes:

| Column | Meaning |
|---|---|
| `content_hash` | SHA-256 of the file as last **read** from disk |
| `cold_store_hash` | SHA-256 of the file as last **confirmed indexed** in Weaviate |

When these match → hot and cold are in sync.  
When `cold_store_hash IS NULL` → file has never been indexed (cold only).  
When they mismatch → **drift detected** — file was modified after the last index run.

Drift is surfaced by the partial index on `sync_state`:
```sql
CREATE INDEX idx_sync_state_drift ON sync_state(file_path)
  WHERE cold_store_hash IS NULL OR cold_store_hash != content_hash;
```

The watcher queries this index at startup and after each hourly reconcile to find files needing re-index.

### Split-Brain Buffer Flag

The `buffer_in_flight` column in `sync_state` prevents concurrent writes:

```
1. Before writing: SET buffer_in_flight = TRUE  WHERE buffer_in_flight = FALSE
   → If update affects 0 rows: another process owns the slot → skip this file, retry next cycle
   → If update affects 1 row: this process owns the slot → proceed
2. Write chunk to Weaviate
3. On success: UPDATE cold_store_hash = content_hash, buffer_in_flight = FALSE
4. On failure: UPDATE buffer_in_flight = FALSE  (release lock without updating hash)
```

This is a lightweight optimistic lock. It does not use Postgres advisory locks so it works
correctly across both the FastAPI process and the CLI sync command running simultaneously.

---

## Content Stripping (Slim)

The cold store record in `sync_state` never stores actual note content — only hashes and
chunk metadata. This mirrors the cybaea approach of stripping `content: ""` from cold snapshots
before saving to disk, keeping cross-device sync payloads small.

Actual content lives only in Weaviate (hot) or on disk (vault file). The database is a
**coordination layer**, not a content store.

---

## Drift Detection in Practice

```bash
# Find all files with hot/cold drift
vault-memory sync --check-drift

# Re-index only drifted files (fast reconcile)
vault-memory sync --drift-only

# Full wipe and re-index (nuclear option)
vault-memory sync --full --force-wipe
```

---

## Model Hash Sharding

If the embedding model changes, all existing vectors are invalid. To handle this cleanly:

- `sync_state` rows are logically associated with the active model hash via `.vault-memory.json` `embedding_model`
- On model change, `vault-memory sync --full` performs a full wipe of the Weaviate collection
  and resets all `cold_store_hash` to NULL, forcing a complete re-index under the new model
- The old Weaviate collection is not deleted immediately — it is renamed with a `_stale_` prefix
  and pruned after a 24h safety window
