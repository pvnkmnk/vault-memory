# Sprint S11: Wiki Layer

**Version Target:** 0.6.0  
**Status:** PLANNED  
**Depends on:** S10  
**Blocks:** S12, S14  
**Estimated files changed:** 5  
**Estimated lines changed:** 120  
**Risk:** MEDIUM  
**Assigned to:** orchestrator

## Goal
Implement the Karpathy llm-wiki pattern for knowledge management, including Sources/Knowledge/_working layers, index.md generation, and atomic ingest workflow.

## Findings Addressed
| ID | Source | Severity | Description |
|----|--------|----------|-------------|
| S11-A | Claude/Kimi | P2 | Raw sources layer convention needed |
| S11-B | Claude/Kimi | P1 | index.md navigation primitive |
| S11-C | Claude/Kimi | P1 | memory/ingest MCP tool for atomic workflow |
| S11-D | Claude/Kimi | P2 | Content-level contradiction detection |
| S11-E | Claude/Kimi | P1 | log.md append race condition |

## Changes

### AGENTS.md
- **What:** Document directory structure convention (Sources/Knowledge/_working)
- **Why:** Establish clear organization for wiki layer pattern
- **Lines:** Directory Structure section
- **Rollback procedure:** Remove added documentation

### AGENTS.md
- **What:** Add page_type: source validation and sources_only filter
- **Why:** Enable distinction between raw sources and promoted knowledge
- **Lines:** Page Conventions and search implementation
- **Rollback procedure:** Remove validation and filter logic

### daemon/main.py
- **What:** Implement _update_index_md function with proper locking
- **Why:** Maintain Knowledge/index.md as navigation primitive
- **Lines:** Index update function (~20 lines)
- **Rollback procedure:** Remove function and related locks

### daemon/main.py
- **What:** Add memory/rebuild_index MCP tool endpoint
- **Why:** Allow rebuilding index from promoted pages
- **Lines:** MCP tool registration and handler
- **Rollback procedure:** Remove tool endpoint

### cli/mcp_adapter.py
- **What:** Expose memory/rebuild_index tool through MCP adapter
- **Why:** Make index rebuild accessible to agents
- **Lines:** Tool definition addition
- **Rollback procedure:** Remove tool from adapter

### daemon/main.py
- **What:** Implement memory/ingest MCP tool for atomic ingest workflow
- **Why:** Full Karpathy cycle: read source → extract triples → promote → update index
- **Lines:** Ingest endpoint (~40 lines) + helper functions
- **Rollback procedure:** Remove ingest endpoint and helpers

### init_db.sql
- **What:** Add contradictions table for content-level conflict detection
- **Why:** Track when new knowledge contradicts existing claims
- **Lines:** Table creation SQL
- **Rollback procedure:** Drop contradictions table

### daemon/main.py
- **What:** Implement _detect_promote_contradictions function
- **Why:** Detect contradictions during promote/ingest operations
- **Lines:** Contradiction detection function (~20 lines)
- **Rollback procedure:** Remove detection function

### daemon/main.py
- **What:** Fix log.md append race condition with _log_lock
- **Why:** Prevent interleaved log entries from concurrent agents
- **Lines:** Log append section
- **Rollback procedure:** Remove lock protection

## Verification Steps
```bash
# Test memory/ingest tool via MCP
echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"memory/ingest","arguments":{"source_path":"Sources/test.md","title":"Test Ingest","page_type":"concept","vault_path":"/path/to/vault"}}}' | vault-memory mcp
# Expected: path_written, triples_extracted, index_updated=true

# Verify index.md update
vault-memory search -q "test" && cat ~/ObsidianVault/Knowledge/index.md | grep "Test"
# Expected: Entry found in index

# Test contradiction detection
# (Would require setting up conflicting knowledge)

# Run test suite
pytest tests/ -q && python -m py_compile daemon/main.py cli/mcp_adapter.py
# Expected: Zero failures
```