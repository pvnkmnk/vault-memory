# vault-memory — Sprint Master Plan

## Current Status (April 2026)

**Version:** 0.7.0 — Lite Mode + VaultPortal Plugin  
**Daemon:** FastAPI on port 5051  
**MCP Tools:** 17 tools exposed  
**Python:** 3.11+ required

---

## Completed Sprints

| Sprint | Status | Description |
|--------|--------|-------------|
| S1-S8 | ✅ | Comprehensive audit fixes |
| S9 | ✅ | Ritual layer hardening |
| S18 | ✅ | Lite Mode (SQLite-only) |
| S19 | ✅ | Obsidian Plugin (VaultPortal) |

---

## Version 0.7.0 Features

### Lite Mode (S18)
- SQLite-only backend (no PostgreSQL or Weaviate required)
- BM25 keyword search
- Reduced resource footprint (2GB RAM vs 4GB)
- Configurable via `lite_mode: true` in `.vault-memory.json`

### VaultPortal Plugin (S19)
- Full Obsidian integration
- In-client search panel
- Knowledge graph visualization
- Status bar indicator
- Automatic daemon startup

---

## Architecture

```
Obsidian → VaultPortal Plugin → vault-memory Daemon (port 5051)
                              ↓
                    ┌─────────┴─────────┐
                    │                   │
               Full Mode          Lite Mode
               (PG + Weaviate)    (SQLite only)
```

---

## Lite Mode vs Full Mode

| Feature | Full Mode | Lite Mode |
|---------|-----------|----------|
| Vector search | ✅ | ❌ |
| BM25 keyword | ✅ | ✅ |
| Graph traversal | ✅ | ❌ |
| Temporal queries | ✅ | ❌ |
| Cognify (LLM triples) | ✅ | ❌ |
| File watching | ✅ | ❌ |
| Heartbeat jobs | ✅ | ❌ |

---

## Verification

```bash
# Run tests
pytest tests/ -v

# Syntax check
python -m py_compile daemon/main.py cli/mcp_adapter.py

# Build plugin
cd obsidian-plugin && npm install && npm run build

# Test lite mode
VAULT_MEMORY_LITE=1 vault-memory daemon start
```