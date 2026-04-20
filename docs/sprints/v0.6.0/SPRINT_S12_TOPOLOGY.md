# Sprint S12: Topology Retrieval

**Version Target:** 0.6.0  
**Status:** PLANNED  
**Depends on:** S11  
**Blocks:** S13, S18  
**Estimated files changed:** 5  
**Estimated lines changed:** 200  
**Risk:** HIGH  
**Assigned to:** orchestrator

## Goal
Implement topology-aware retrieval using Leiden community detection to improve search relevance through god node proximity and community awareness.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S12-A | Claude/Kimi | P1 | New topology.py file for graph algorithms |
| S12-B | Claude/Kimi | P1 | Schema migration for relationship metadata |
| S12-C | Claude/Kimi | P2 | Rationale extraction from source documents |
| S12-D | Claude/Kimi | P2 | GRAPH_REPORT.md generation |
| S12-E | Claude/Kimi | P1 | Topology search strategy integration |
| S12-F | Claude/Kimi | P2 | Optional topology dependencies |

## Changes

### daemon/topology.py (NEW)
- **What:** Implement topology analysis with Leiden community detection, god nodes, and scoring
- **Why:** Enable topology-aware search strategies
- **Lines:** Entire new file (~120 lines)
- **Rollback procedure:** Remove file

### init_db.sql
- **What:** Add columns to relationships table: confidence_score, rationale, extracted_by, vault_path
- **Why:** Support topology algorithms with edge weights and provenance
- **Lines:** ALTER TABLE statements
- **Rollback procedure:** Drop added columns

### daemon/sync_watcher.py
- **What:** Extract and store rationale during sync/upsert operations
- **Why:** Capture why relationships were created for topology scoring
- **Lines:** Sync/watcher processing logic
- **Rollback procedure:** Remove rationale extraction

### daemon/main.py
- **What:** Integrate topology search strategy into retrieval pipeline
- **Why:** Make topology scoring available for search queries
- **Lines:** Search strategy registration and topology strategy function
- **Rollback procedure:** Remove topology strategy integration

### pyproject.toml
- **What:** Add optional topology dependencies: graspologic, networkx
- **Why:** Provide advanced community detection algorithms optionally
- **Lines:** [project.optional-dependencies] section
- **Rollback procedure:** Remove optional dependencies

## Verification Steps
```bash
# Install with topology extras
pip install "vault-memory[topology]"

# Test topology module imports
python -c "from daemon.topology import build_networkx_graph, detect_communities, find_god_nodes; print('OK')"

# Generate graph report
vault-memory heartbeat --mode daily --vault ~/ObsidianVault
cat ~/ObsidianVault/.vault-memory/GRAPH_REPORT.md | head -20
# Expected: Shows god nodes and statistics

# Run test suite
pytest tests/ -q
# Expected: Zero failures
```