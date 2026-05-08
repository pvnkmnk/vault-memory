#!/usr/bin/env node
// linear-add-labels.js — adds sprint labels to issues
const https = require('https');

const API_KEY = process.env.LINEAR_API_KEY;
if (!API_KEY) {
  console.error('ERROR: LINEAR_API_KEY environment variable is required');
  process.exit(1);
}

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

// Sprint label IDs
const SPRINT_LABELS = {
  'S24': 'e9809879-0ccf-437f-bfe0-062e4bc474c1',
  'S25': '367812b2-5246-40a3-ab62-4e80feefb463',
  'S26': '90755bc9-5a70-4c4e-881c-c47b089ab8ff',
  'S27': '6654a992-1617-4dd6-8bfb-8e2a00ce9de8',
  'S28': '949f6939-cd20-497b-bbea-ad39f685151e',
  'S29': 'aea92d4b-ccf5-4026-90fb-d2b61656d30c',
};

// Issue number to sprint label mapping
function getSprintLabelId(issueIdentifier) {
  const num = parseInt(issueIdentifier.replace('VAU-', ''));
  if (num >= 5 && num <= 19) return SPRINT_LABELS['S24'];
  if (num >= 20 && num <= 24) return SPRINT_LABELS['S25'];
  if (num >= 25 && num <= 29) return SPRINT_LABELS['S26'];
  if (num >= 30 && num <= 33) return SPRINT_LABELS['S27'];
  if (num >= 34 && num <= 37) return SPRINT_LABELS['S28'];
  if (num >= 38 && num <= 41) return SPRINT_LABELS['S29'];
  return null;
}

async function addLabelToIssue(issueIdentifier, labelId) {
  try {
    await gql(
      `mutation UpdateIssue($id: String!, $labelIds: [String!]) {
         issueUpdate(id: $id, input: { labelIds: $labelIds }) { success }
       }`,
      { id: issueIdentifier, labelIds: [labelId] }
    );
    console.log(`  ✓ ${issueIdentifier} → ${Object.entries(SPRINT_LABELS).find(([k,v]) => v === labelId)[0]}`);
    return true;
  } catch (e) {
    console.log(`  ⚠ ${issueIdentifier}: ${e.message.split(';')[0]}`);
    return false;
  }
}

async function run() {
  console.log('=== Adding Sprint Labels to Issues ===\n');

  // Get all issues
  const response = await gql(
    `{ issues(first: 100) { nodes { identifier } } }`
  );
  
  const issues = response.issues.nodes
    .map(n => n.identifier)
    .filter(id => id.startsWith('VAU-'))
    .sort((a, b) => parseInt(a.replace('VAU-', '')) - parseInt(b.replace('VAU-', '')));

  console.log(`Found ${issues.length} issues to label\n`);

  let success = 0;
  for (const issueId of issues) {
    const labelId = getSprintLabelId(issueId);
    if (labelId) {
      const result = await addLabelToIssue(issueId, labelId);
      if (result) success++;
    }
  }

  console.log(`\n=== Complete ===`);
  console.log(`Successfully labeled: ${success}/${issues.length} issues`);
}

run().catch(e => { console.error(e); process.exit(1); });