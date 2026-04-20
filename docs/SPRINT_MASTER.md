# vault-memory — Sprint Master Plan

## Current Status (April 2026)

**Version:** 0.5.1  
**Daemon:** FastAPI on port 5051  
**MCP Tools:** 17 tools exposed

---

## Completed Sprints

| Sprint | Status | Description |
|--------|--------|-------------|
| S1-S8 | ✅ | Comprehensive audit fixes |
| S9 | ✅ | Ritual layer hardening |

---

## Active Sprints

### Sprint S19: VaultPortal Plugin [IN PROGRESS]

**Plugin:** VaultPortal  
**ID:** vault-portal  
**Goal:** Full Obsidian integration (read + write + knowledge graph + temporal)

**Files:**
- `obsidian-plugin/manifest.json`
- `obsidian-plugin/package.json`
- `obsidian-plugin/src/main.ts`
- `obsidian-plugin/src/components/DaemonClient.ts`
- `obsidian-plugin/src/views/SearchPanel.ts`
- `obsidian-plugin/src/views/GraphCanvas.ts`
- `obsidian-plugin/src/views/StatusBar.ts`
- `obsidian-plugin/styles.css`

**Commands:**
- `search` — Search vault
- `graph` — View knowledge graph
- `cognify` — Extract triples
- `promote` — Promote to wiki

### Sprint S18: Lite Mode [COMPLETED]

**Goal:** SQLite-only backend  
**Approach:** Abstract Base Class (Option A)

**Files:**
- `daemon/db_abstraction.py` — DatabaseBackend protocol
- `daemon/backends/__init__.py` — Backend exports
- `daemon/backends/postgres_client.py` — PostgreSQL implementation
- `daemon/backends/sqlite_client.py` — SQLite implementation
- `daemon/config.py` — Added `lite_mode`, `sqlite_db_path`
- `daemon/main.py` — Backend selection based on config
- `pyproject.toml` — Added `aiosqlite` optional dependency

---

## Architecture

```
Obsidian → VaultPortal Plugin → vault-memory Daemon (port 5051)
```

---

## Verification

```bash
cd obsidian-plugin && npm install && npm run build
```