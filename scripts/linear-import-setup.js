#!/usr/bin/env node
// linear-import-setup.js — creates labels and cycles in Linear workspace
// API key: process.env.LINEAR_API_KEY (required — no hardcoded fallback)
const https = require('https');

const API_KEY = process.env.LINEAR_API_KEY;
if (!API_KEY) {
  console.error('ERROR: LINEAR_API_KEY environment variable is required');
  process.exit(1);
}

const TEAM_ID = '9d788748-97d6-43f6-b978-be783010d6e5';
const PROJECT_V080 = '5759dfaa-8617-4f5d-90cf-5bb45bb0fedf';
const PROJECT_V090 = '4e29347f-5bf1-4591-86d6-29c98491ba0b';

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

async function run() {
  const results = { labels: [], cycles: [] };

  // --- Labels ---
  const labels = [
    { name: 'priority/p0', color: '#EB5757' },
    { name: 'priority/p1', color: '#F97316' },
    { name: 'priority/p2', color: '#EAB308' },
    { name: 'priority/p3', color: '#22C55E' },
    { name: 'sprint/s20-batch', color: '#4EA7FC' },
    { name: 'sprint/s20-enhancements', color: '#4EA7FC' },
    { name: 'sprint/s21', color: '#4EA7FC' },
    { name: 'sprint/s22', color: '#4EA7FC' },
    { name: 'sprint/s23', color: '#4EA7FC' },
    { name: 'type/docs', color: '#6B7280' },
  ];

  console.log('--- Creating Labels ---');
  for (const label of labels) {
    try {
      const data = await gql(
        `mutation CreateLabel($input: IssueLabelCreateInput!) {
           issueLabelCreate(input: $input) { success }
         }`,
        { input: { teamId: TEAM_ID, name: label.name, color: label.color } }
      );
      if (data.issueLabelCreate.success) {
        results.labels.push(label.name);
        console.log(`  ✓ ${label.name}`);
      }
    } catch (e) {
      console.log(`  ⚠ ${label.name}: ${e.message.split(';')[0]}`);
    }
  }

  // --- Cycles ---
  // Dates are in 2026 (future from today's date)
  const cycles = [
    { name: 'S20 Batch Optimization',         startsAt: '2026-07-27', endsAt: '2026-08-09' },
    { name: 'S20 Enhancements',               startsAt: '2026-08-10', endsAt: '2026-08-23' },
    { name: 'S21 Mobile Companion App',       startsAt: '2026-08-24', endsAt: '2026-09-13' },
    { name: 'S22 Collaborative Editing',      startsAt: '2026-09-14', endsAt: '2026-10-04' },
    { name: 'S23 Obsidian Canvas Integration', startsAt: '2026-10-05', endsAt: '2026-10-25' },
  ];

  // Note: Cycles are team-scoped. Link to project manually in Linear UI after creation.
  // Project IDs for reference:
  //   v0.8.0: 5759dfaa-8617-4f5d-90cf-5bb45bb0fedf
  //   v0.9.0: 4e29347f-5bf1-4591-86d6-29c98491ba0b

  console.log('\n--- Creating Cycles ---');
  for (const cycle of cycles) {
    try {
      const data = await gql(
        `mutation CreateCycle($input: CycleCreateInput!) {
           cycleCreate(input: $input) { cycle { id name number } }
         }`,
        { input: { teamId: TEAM_ID, name: cycle.name, startsAt: cycle.startsAt, endsAt: cycle.endsAt } }
      );
      const c = data.cycleCreate.cycle;
      results.cycles.push(`${c.name} (${c.id})`);
      console.log(`  ✓ ${c.name} — cycle #${c.number} (${c.id})`);
    } catch (e) {
      console.error(`  ✗ ${cycle.name}: ${e.message.split(';')[0]}`);
    }
  }

  console.log('\n=== Summary ===');
  console.log(`Labels: ${results.labels.length}/10`);
  console.log(`Cycles: ${results.cycles.length}/5`);
  console.log('\nProjects (pre-created):');
  console.log('  v0.8.0: 5759dfaa-8617-4f5d-90cf-5bb45bb0fedf');
  console.log('  v0.9.0: 4e29347f-5bf1-4591-86d6-29c98491ba0b');
  console.log('  Backlog: de938f5d-ec4a-4db9-8170-2b01af3cdd24');
  console.log('\nNote: Link cycles to projects manually in Linear UI.');
  console.log('Or via Settings → Projects → select project → Cycles tab.');
}

run().catch(e => { console.error(e); process.exit(1); });