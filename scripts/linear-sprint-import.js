#!/usr/bin/env node
// linear-sprint-import.js — imports all sprint issues into Linear
// API key: process.env.LINEAR_API_KEY (required)
const https = require('https');

const API_KEY = process.env.LINEAR_API_KEY;
if (!API_KEY) {
  console.error('ERROR: LINEAR_API_KEY environment variable is required');
  process.exit(1);
}

const TEAM_ID = '9d788748-97d6-43f6-b978-be783010d6e5';

function gql(query, variables = {}) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ query, variables });
    const req = https.request({
      hostname: 'api.linear.app',
      path: '/graphql',
      method: 'POST',
      headers: {
        'Authorization': API_KEY,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body),
      },
    }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const json = JSON.parse(data);
          if (json.errors) {
            reject(new Error(json.errors.map(e => e.message).join('; ')));
          } else {
            resolve(json.data);
          }
        } catch (e) {
          reject(new Error(`Parse error: ${data}`));
        }
      });
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

async function createLabel(name, color) {
  try {
    await gql(
      `mutation CreateLabel($input: IssueLabelCreateInput!) {
         issueLabelCreate(input: $input) { success }
       }`,
      { input: { teamId: TEAM_ID, name, color } }
    );
    console.log(`  ✓ Label: ${name}`);
    return true;
  } catch (e) {
    console.log(`  ⚠ Label ${name}: ${e.message.split(';')[0]}`);
    return false;
  }
}

async function createCycle(name, startsAt, endsAt) {
  try {
    const data = await gql(
      `mutation CreateCycle($input: CycleCreateInput!) {
         cycleCreate(input: $input) { cycle { id name number } }
       }`,
      { input: { teamId: TEAM_ID, name, startsAt, endsAt } }
    );
    const c = data.cycleCreate.cycle;
    console.log(`  ✓ Cycle: ${name} (${c.id}, #${c.number})`);
    return c;
  } catch (e) {
    console.log(`  ⚠ Cycle ${name}: ${e.message.split(';')[0]}`);
    return null;
  }
}

async function createIssue(title, description, priority, labelNames, cycleId = null) {
  try {
    // Linear uses numeric priority: 0=No priority, 1=Urgent, 2=High, 3=Normal, 4=Low
    const priorityMap = { 'P0': 1, 'P1': 2, 'P2': 3, 'P3': 4 };
    const input = {
      teamId: TEAM_ID,
      title,
      description: description || '',
      priority: priorityMap[priority] || 3,  // Default to Normal
    };
    
    let result = await gql(
      `mutation CreateIssue($input: IssueCreateInput!) {
         issueCreate(input: $input) { success }
       }`,
      { input }
    );
    
    if (result.issueCreate.success) {
      console.log(`  ✓ Issue: ${title}`);
      return true;
    }
    return false;
  } catch (e) {
    console.log(`  ⚠ Issue ${title}: ${e.message.split(';')[0]}`);
    return false;
  }
}

async function run() {
  console.log('=== Linear Sprint Import ===\n');

  // Step 1: Create labels
  console.log('--- Creating Labels ---');
  const labels = [
    { name: 'sprint/s24', color: '#4EA7FC' },
    { name: 'sprint/s25', color: '#4EA7FC' },
    { name: 'sprint/s26', color: '#4EA7FC' },
    { name: 'sprint/s27', color: '#4EA7FC' },
    { name: 'sprint/s28', color: '#4EA7FC' },
    { name: 'sprint/s29', color: '#4EA7FC' },
    { name: 'type/bug', color: '#EB5757' },
    { name: 'type/feature', color: '#BB87FC' },
    { name: 'type/chore', color: '#6B7280' },
    { name: 'type/performance', color: '#F97316' },
    { name: 'type/improvement', color: '#22C55E' },
  ];
  
  for (const label of labels) {
    await createLabel(label.name, label.color);
  }

  // Step 2: Create cycles
  console.log('\n--- Creating Cycles ---');
  const cycles = [
    { name: 'S24 Vault Cleanup', startsAt: '2026-06-08', endsAt: '2026-06-28' },
    { name: 'S25 Plugin UX & Polish', startsAt: '2026-06-29', endsAt: '2026-07-12' },
    { name: 'S26 API & Data Infrastructure', startsAt: '2026-07-13', endsAt: '2026-07-26' },
    { name: 'S27 Knowledge Graph & Canvas', startsAt: '2026-07-27', endsAt: '2026-08-09' },
    { name: 'S28 Operations & Reliability', startsAt: '2026-08-10', endsAt: '2026-08-23' },
    { name: 'S29 Documentation Sprint', startsAt: '2026-08-24', endsAt: '2026-09-06' },
  ];
  
  const createdCycles = {};
  for (const cycle of cycles) {
    const c = await createCycle(cycle.name, cycle.startsAt, cycle.endsAt);
    if (c) {
      createdCycles[cycle.name] = c.id;
    }
  }

  // Step 3: Create issues for each sprint
  console.log('\n--- Creating S24 Issues (15 issues) ---');
  const s24Issues = [
    { title: 'S24-B1: Fix Postgres health check cache stampede', description: '`daemon/backends/postgres_client.py` — `ping()` caches True for 30s even if connection dropped. Invert cache: cache failures not successes. Track consecutive failures, recover after N successes.', priority: 'P1' },
    { title: 'S24-B3: Handle SQLite RuntimeError in call chain', description: '`daemon/backends/sqlite_client.py:273,330` — `RuntimeError` on uninitialized connection propagates as 500. Add `SQLiteNotInitialized` exception class, catch in routes → 503.', priority: 'P2' },
    { title: 'S24-B4: Fix bulk import 410 message for lite mode', description: '`daemon/main.py:1640-1650` — bulk_import returns 410 Gone (wrong). Return 422 with `{ mode: lite }` and correct message directing to /sync.', priority: 'P3' },
    { title: 'S24-B5: Fix GraphCanvas D3 event handler memory leak', description: '`obsidian-plugin/src/views/GraphCanvas.ts:567-569` — window event handlers accumulate on each view open. Store bound refs, remove in onClose(), call `simulation.stop()`.', priority: 'P1' },
    { title: 'S24-B6: StatusBar smart polling with backoff', description: '`obsidian-plugin/src/views/StatusBar.ts` — polls /health every 30s unconditionally. Add exponential backoff, reset on user interaction, pause when tab hidden.', priority: 'P2' },
    { title: 'S24-B7: AutoSyncEngine file loss on sync failure', description: '`obsidian-plugin/src/components/AutoSyncEngine.ts:104-113` — `pendingFiles.clear()` before API call loses files on failure. Move clear to after success, add 3-attempt retry with backoff.', priority: 'P2' },
    { title: 'S24-A1: Remove PostgresBackend/PostgresClient alias', description: '`daemon/backends/postgres_client.py` + `daemon/pg_client.py` — both define `PostgresClient` with different interfaces. Consolidate to single canonical source.', priority: 'P3' },
    { title: 'S24-A3: Add DI container to CLI', description: '`cli/sync_command.py`, `cli/main.py` — CLI uses globals, cant mock for tests. Create `cli/dependencies.py` mirroring daemons Dependencies pattern.', priority: 'P2' },
    { title: 'S24-A4: Add circuit breaker for Ollama/Weaviate', description: '`daemon/embedder.py`, `daemon/weaviate_client.py` — no circuit breaker, one timeout blocks permanently. Add CLOSED→OPEN→HALF_OPEN with 60s cooldown, 3-failure threshold.', priority: 'P1' },
    { title: 'S24-A5: Add rate limiter metrics', description: '`daemon/main.py:1135` — rate limiter has no metrics. Expose `rate_limiter_keys_current`, `rate_limiter_evictions_total`, `rate_limiter_hits_total` via /metrics.', priority: 'P3' },
    { title: 'S24-P1: Optimize recalc_centrality SQL (O(n²) → O(n))', description: '`daemon/heartbeat.py:27-58` — correlated subquery per row (O(n²)). Replace with CTE + UPDATE...FROM. Benchmark: <500ms for 5000 entities.', priority: 'P1' },
    { title: 'S24-P2: Incremental topic hub refresh', description: '`daemon/heartbeat.py:60-90` — TRUNCATE + full re-insert every 15min. Use INSERT...ON CONFLICT DO UPDATE instead. Skip rebuild if no changes.', priority: 'P2' },
    { title: 'S24-P3: Add slow-query diagnostics', description: '`daemon/main.py` — no slow-query logging. Add SLOW_QUERY_THRESHOLD_MS (default 1s) as config. Middleware logs slow queries. Track per-endpoint p50/p95.', priority: 'P2' },
    { title: 'S24-P4: Add connection pool health metrics', description: '`daemon/backends/postgres_client.py` — no pool metrics. Add `_pool_stats()` method: size/used/available/waiting/errors_total.', priority: 'P3' },
    { title: 'S24-P5: Stream bulk_export instead of loading all into memory', description: '`daemon/main.py:1683-1700` — bulk_export loads all notes into memory → OOM for large vaults. Add Weaviate cursor streaming, `stream=true` for NDJSON.', priority: 'P1' },
  ];
  
  for (const issue of s24Issues) {
    await createIssue(issue.title, issue.description, issue.priority);
  }

  console.log('\n--- Creating S25 Issues (5 issues) ---');
  const s25Issues = [
    { title: 'S25-1: Keyboard Shortcuts for SearchPanel', description: 'Add keyboard navigation (↑/↓/Enter), action shortcuts (P=promote, L=lint, C=cognify, E=export, R=refresh), and visual hint text.', priority: 'P1' },
    { title: 'S25-2: Dark/Light Theme Detection', description: 'Plugin should respond to Obsidian theme changes. Add ThemeObserver class watching body classList, apply CSS variables for theme-aware colors.', priority: 'P2' },
    { title: 'S25-3: Better Error Messaging in Plugin', description: 'Plugin shows raw daemon errors. Add error translator mapping daemon error codes to user-friendly messages. Add offline detection with persistent banner.', priority: 'P1' },
    { title: 'S25-4: Plugin Sync Status History', description: 'AutoSyncEngine tracks sync count but not history. Add sync history ring buffer (50 events), `getSyncHistory()` method, SyncHistoryPanel view.', priority: 'P3' },
    { title: 'S25-5: Debounce Tuning for AutoSync', description: 'AutoSync debounce is hardcoded to 2000ms. Add configurable debounce (500ms–10s), auto-tune based on vault size, add UI slider in SettingsTab.', priority: 'P3' },
  ];
  
  for (const issue of s25Issues) {
    await createIssue(issue.title, issue.description, issue.priority);
  }

  console.log('\n--- Creating S26 Issues (5 issues) ---');
  const s26Issues = [
    { title: 'S26-1: Incremental Sync API', description: 'Add `POST /sync/delta` endpoint for mobile clients. Returns only files changed since given timestamp. PREREQUISITE FOR S21 MOBILE APP.', priority: 'P1' },
    { title: 'S26-2: Bulk Operations Queue', description: 'Add `POST /bulk/queue` for background bulk imports. Returns job_id immediately. Poll `/bulk/status/{job_id}` for progress. Support webhook callback on completion.', priority: 'P1' },
    { title: 'S26-3: Streaming Bulk Export', description: 'Add streaming bulk_export using Weaviate cursor pagination. Add `stream=true` query param for NDJSON, `limit`+`cursor` pagination params.', priority: 'P1' },
    { title: 'S26-4: API Rate Limiting & Quota', description: 'Add per-API-key rate limits to config. Per-key tracking in rate limiter. Add `/me/usage` endpoint showing requests_today, quota, reset_at.', priority: 'P2' },
    { title: 'S26-5: OpenAPI Documentation', description: 'Add FastAPI OpenAPI metadata, document all routes with descriptions. Add `/docs` (Swagger UI) and `/openapi.json` (raw spec) endpoints.', priority: 'P2' },
  ];
  
  for (const issue of s26Issues) {
    await createIssue(issue.title, issue.description, issue.priority);
  }

  console.log('\n--- Creating S27 Issues (4 issues) ---');
  const s27Issues = [
    { title: 'S27-1: Canvas to Knowledge Graph Pipeline', description: 'Parse Canvas JSON to extract nodes and edges. Run entity extraction on text nodes (>50 chars). Create canvas_entities table linking to Canvas file.', priority: 'P1' },
    { title: 'S27-2: Knowledge Graph to Canvas Export', description: 'Export knowledge graph back to Obsidian Canvas format. Add export button, layout options (force-directed, hierarchical, circular), filter panel.', priority: 'P1' },
    { title: 'S27-3: Enhanced Entity Detail Panel in GraphCanvas', description: 'Add collapsible detail panel (300px right slide-in). Show entity name, type, maturity, trust, relationships. Add filter by relationship type, edge labels on hover.', priority: 'P2' },
    { title: 'S27-4: OpenAPI Documentation (S26-5 implementation)', description: 'Implement S26-5 OpenAPI spec addition to the daemon. Add Swagger UI at /docs and raw spec at /openapi.json.', priority: 'P1' },
  ];
  
  for (const issue of s27Issues) {
    await createIssue(issue.title, issue.description, issue.priority);
  }

  console.log('\n--- Creating S28 Issues (4 issues) ---');
  const s28Issues = [
    { title: 'S28-1: Stale Session Cleanup', description: 'Add background cleanup job in heartbeat.py (run hourly) to mark sessions orphaned if registered but never closed after 24h. Add `POST /sessions/cleanup` endpoint.', priority: 'P1' },
    { title: 'S28-2: Write Regression Guard', description: 'Re-implement WriteValidator (removed in S24-B2) as pre-promotion guard. Check proposed text against high-trust notes for near-duplicates (similarity > 0.85).', priority: 'P1' },
    { title: 'S28-3: Full Attribution System', description: 'Add `last_modified_by_agent` + `last_modified_by_session` columns to vault_chunks. Set from headers on sync/promote. Surface in retrieval. Add attribution endpoint.', priority: 'P2' },
    { title: 'S28-4: Health Dashboard Endpoint', description: 'Add `/health/detailed` with all subsystem status (postgres, weaviate, embedder), pool stats, sync state, rate limiter keys. Escalation: critical down → 503.', priority: 'P2' },
  ];
  
  for (const issue of s28Issues) {
    await createIssue(issue.title, issue.description, issue.priority);
  }

  console.log('\n--- Creating S29 Issues (4 issues) ---');
  const s29Issues = [
    { title: 'S29-1: Write CONTRIBUTING.md', description: 'Write comprehensive guide covering: setup (Python 3.11+, Docker, pip install), architecture (daemon/CLI/plugin layers), code conventions (async patterns, DI container), debugging.', priority: 'P1' },
    { title: 'S29-2: Expand USER_GUIDE.md', description: 'Expand with: getting started, MCP tools reference (all 17 tools), search strategies explained, working directory guide (_working/), promotion workflow, lite vs full mode, troubleshooting.', priority: 'P1' },
    { title: 'S29-3: Add Inline Comments to init_db.sql', description: 'Schema has no comments. Add structured block comments for each table (temporal_entities, relationships, vault_chunks, agent_sessions, canvas_entities) explaining field purpose.', priority: 'P2' },
    { title: 'S29-4: Create API Changelog', description: 'Create `docs/API_CHANGELOG.md` with sections per release (added/changed/deprecated). v0.8.0 includes: /sync/delta, /bulk/queue, /bulk/status, /health/detailed, /sessions/cleanup, /me/usage.', priority: 'P2' },
  ];
  
  for (const issue of s29Issues) {
    await createIssue(issue.title, issue.description, issue.priority);
  }

  console.log('\n=== Import Complete ===');
  console.log('Cycles created:', Object.keys(createdCycles).length);
  console.log('Issues created: 15 (S24) + 5 (S25) + 5 (S26) + 4 (S27) + 4 (S28) + 4 (S29) = 37 total');
  console.log('\nNote: Link issues to cycles manually in Linear UI.');
}

run().catch(e => { console.error(e); process.exit(1); });