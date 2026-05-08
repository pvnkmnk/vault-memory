#!/usr/bin/env node
// linear-backfill-s21-s23.js — creates issues for S21, S22, S23 and links them to existing cycles
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

async function createIssue(title, priority, cycleId) {
  try {
    // Linear uses numeric priority: 0=No priority, 1=Urgent, 2=High, 3=Normal, 4=Low
    const priorityMap = { 'P0': 1, 'P1': 2, 'P2': 3, 'P3': 4 };
    const input = {
      teamId: TEAM_ID,
      title,
      priority: priorityMap[priority] || 3,
      cycleId: cycleId
    };
    
    let result = await gql(
      `mutation CreateIssue($input: IssueCreateInput!) {
         issueCreate(input: $input) { success issue { identifier } }
       }`,
      { input }
    );
    
    if (result.issueCreate.success) {
      console.log(`  ✓ Created [${result.issueCreate.issue.identifier}] ${title}`);
      return true;
    }
    return false;
  } catch (e) {
    console.log(`  ⚠ Failed ${title}: ${e.message.split(';')[0]}`);
    return false;
  }
}

async function run() {
  console.log('=== Linear S21-S23 Backfill ===\n');

  const s21CycleId = 'ddc49b79-5279-426f-85ad-b7adb0c9b825';
  const s22CycleId = '3366bbff-8fe7-4a64-b767-9c12a2c2d1ef';
  const s23CycleId = 'f80af822-bd9a-4bdc-8d29-76a3ba811eaa';

  const sprints = [
    {
      name: 'S21 Mobile Companion App',
      cycleId: s21CycleId,
      issues: [
        { title: 'S21-A: Mobile-first responsive layout', priority: 'P1' },
        { title: 'S21-B: Touch-optimized search panel', priority: 'P1' },
        { title: 'S21-C: Swipe gestures for graph navigation', priority: 'P2' },
        { title: 'S21-D: Offline-first sync queue', priority: 'P1' },
        { title: 'S21-E: Notification framework for sync events', priority: 'P2' },
        { title: 'S21-F: Performance benchmarks for mobile', priority: 'P2' },
      ]
    },
    {
      name: 'S22 Collaborative Editing',
      cycleId: s22CycleId,
      issues: [
        { title: 'S22-A: CRDT-based merge strategy', priority: 'P1' },
        { title: 'S22-B: Conflict resolution UI', priority: 'P1' },
        { title: 'S22-C: Session-based locking mechanism', priority: 'P2' },
        { title: 'S22-D: Real-time sync WebSocket endpoint', priority: 'P1' },
        { title: 'S22-E: Operational transform for markdown', priority: 'P2' },
        { title: 'S22-F: Collaborative editing benchmarks', priority: 'P2' },
      ]
    },
    {
      name: 'S23 Obsidian Canvas Integration',
      cycleId: s23CycleId,
      issues: [
        { title: 'S23-A: Canvas file parser improvements', priority: 'P1' },
        { title: 'S23-B: Node relationship extraction from Canvas', priority: 'P1' },
        { title: 'S23-C: Canvas to knowledge graph pipeline', priority: 'P2' },
        { title: 'S23-D: Canvas rendering in VaultPortal plugin', priority: 'P2' },
      ]
    }
  ];

  let totalCount = 0;
  for (const sprint of sprints) {
    console.log(`\n--- Backfilling ${sprint.name} ---`);
    for (const issue of sprint.issues) {
      const success = await createIssue(issue.title, issue.priority, sprint.cycleId);
      if (success) totalCount++;
    }
  }

  console.log(`\n=== Backfill Complete: ${totalCount} issues created ===`);
}

run();
