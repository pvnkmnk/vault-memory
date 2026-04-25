# Sprint S20: Plugin Enhancements

**Version Target:** 0.8.0  
**Status:** PLANNED  
**Depends on:** S19  
**Blocks:** None  
**Estimated files changed:** 8-12  
**Estimated lines changed:** 500-800  
**Risk:** LOW  
**Assigned to:** TBD

## Goal

Enhance VaultPortal plugin with missing MCP tool exposures, improved UX, and community submission prep (marketplace listing, BRAT install support).

## Findings from S19 Audit

| Gap | Severity | Description |
|-----|----------|-------------|
| S20-A | P1 | Plugin missing 6+ MCP tools (bulk, promote, cognify, lint, session) |
| S20-B | P1 | No daily notes integration (calendar, today views) |
| S20-C | P2 | Search history/favorites not persisted |
| S20-D | P2 | No backlinks panel or outgoing links view |
| S20-E | P3 | No mobile/Tablet optimized layout |
| S20-F | P3 | No community plugin marketplace README |

## Changes

### obsidian-plugin/src/views/ (EXISTING)

#### SearchPanel.ts
- **What:** Expose bulk operations, promote, cognify as commands
- **Why:** Users need these tools in UI, not just MCP
- **Lines:** +80 lines
- **Features:**
  - Bulk import/export buttons
  - Promote selected content
  - Cognify current file
  - Vault lint report trigger

#### GraphCanvas.ts
- **What:** Add outgoing links panel, node detail sidebar
- **Why:** Full graph navigation without leaving Obsidian
- **Lines:** +60 lines
- **Features:**
  - Click node → sidebar with backlinks/outlinks
  - Filter by relationship type
  - Export graph as PNG/SVG

#### New: DailyNotesView.ts
- **What:** Daily note integration with context assembly
- **Why:** Users want daily notes with semantic memory
- **Lines:** +100 lines
- **Features:**
  - Templated daily notes with previous context
  - Today + yesterday links auto-inserted
  - Mood/productivity capture

### obsidian-plugin/src/components/ (EXISTING)

#### DaemonClient.ts
- **What:** Add missing MCP tool methods
- **Why:** Expose full daemon API to UI
- **Lines:** +120 lines
- **Methods to add:**
  - `promoteFile(title, text, pageType)` → POST /promote
  - `cognifyFile(path)` → POST /cognify  
  - `runLint(staleDays)` → POST /lint
  - `bulkImport(notes[])` → POST /bulk/import
  - `bulkExport(filters)` → POST /bulk/export
  - `getSessionHistory()` → GET /sessions
  - `registerSession(agent, project, task)` → POST /sessions

#### AutoSyncEngine.ts
- **What:** Add conflict resolution, sync queue management
- **Why:** Better handling of concurrent edits
- **Lines:** +40 lines

### obsidian-plugin/ (NEW)

#### README.md (MARKETPLACE)
- **What:** Community plugin marketplace listing README
- **Why:** Required for Obsidian approved plugins
- **Lines:** +150 lines
- **Requirements:**
  - Screenshots/demo GIF
  - Installation via BRAT
  - Feature list with icons
  - Changelog link
  - Support/issue links

#### .github/workflows/release.yml
- **What:** Automated release workflow
- **Why:** Publish to marketplace releases
- **Lines:** +60 lines

## Implementation Order

1. **DaemonClient.ts** — Add missing MCP tool methods (foundation)
2. **SearchPanel.ts** — Add bulk/promote/cognify UI buttons
3. **New: DailyNotesView.ts** — Daily note integration
4. **GraphCanvas.ts** — Outgoing links panel, export
5. **README.md (MARKETPLACE)** — Community listing
6. **.github/workflows/release.yml** — Automated releases
7. **AutoSyncEngine.ts** — Conflict resolution

## Rollback Procedure

Each change is standalone. Remove new files, restore previous DaemonClient.ts.

## Verification

```bash
cd obsidian-plugin
npm run build
# ✅ Build successful

# Test in Obsidian:
# 1. Test bulk import via search panel button
# 2. Test cognify on current file
# 3. Test daily note template insertion
# 4. Verify graph export works
```

## Next Sprint Options

- S21: Mobile companion app
- S22: Collaborative editing (CRDT-based)
- S23: Obsidian Canvas integration
- S24: AI assistant integration (local LLM chat)