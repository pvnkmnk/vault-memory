#!/usr/bin/env node
// linear-link-cycles.js — links issues to their respective sprint cycles
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

// Cycle IDs from import
const CYCLES = {
  'S24 Vault Cleanup': '21a122fb-7f66-4e77-b2e0-a698778b1410',
  'S25 Plugin UX & Polish': '5d1e7d2b-4017-4f51-a6cf-6dda539807e2',
  'S26 API & Data Infrastructure': '157c5684-7e1a-4ec1-ab73-261cd72ec2f4',
  'S27 Knowledge Graph & Canvas': '51f7feec-a770-49b3-9efc-a4b41202cbc4',
  'S28 Operations & Reliability': 'd59a2c90-1cdc-4b73-9503-44c1872896cf',
  'S29 Documentation Sprint': 'e27ec418-2626-444a-b4bd-113d0620589a',
};

// Issue to cycle mapping based on prefix
function getCycleId(issueIdentifier) {
  const num = parseInt(issueIdentifier.replace('VAU-', ''));
  if (num >= 5 && num <= 19) return CYCLES['S24 Vault Cleanup'];
  if (num >= 20 && num <= 24) return CYCLES['S25 Plugin UX & Polish'];
  if (num >= 25 && num <= 29) return CYCLES['S26 API & Data Infrastructure'];
  if (num >= 30 && num <= 33) return CYCLES['S27 Knowledge Graph & Canvas'];
  if (num >= 34 && num <= 37) return CYCLES['S28 Operations & Reliability'];
  if (num >= 38 && num <= 41) return CYCLES['S29 Documentation Sprint'];
  return null;
}

async function updateIssueCycle(issueIdentifier, cycleId) {
  try {
    await gql(
      `mutation UpdateIssue($id: String!, $cycleId: String) {
         issueUpdate(id: $id, input: { cycleId: $cycleId }) { success }
       }`,
      { id: issueIdentifier, cycleId }
    );
    console.log(`  ✓ ${issueIdentifier} → ${Object.keys(CYCLES).find(k => CYCLES[k] === cycleId)}`);
    return true;
  } catch (e) {
    console.log(`  ⚠ ${issueIdentifier}: ${e.message.split(';')[0]}`);
    return false;
  }
}

async function run() {
  console.log('=== Linking Issues to Cycles ===\n');

  // Get all issues first
  const response = await gql(
    `{ issues(first: 100) { nodes { identifier } } }`
  );
  
  const issues = response.issues.nodes
    .map(n => n.identifier)
    .filter(id => id.startsWith('VAU-'))
    .sort((a, b) => parseInt(a.replace('VAU-', '')) - parseInt(b.replace('VAU-', '')));

  console.log(`Found ${issues.length} issues to link\n`);

  let success = 0;
  for (const issueId of issues) {
    const cycleId = getCycleId(issueId);
    if (cycleId) {
      const result = await updateIssueCycle(issueId, cycleId);
      if (result) success++;
    }
  }

  console.log(`\n=== Complete ===`);
  console.log(`Successfully linked: ${success}/${issues.length} issues`);
}

run().catch(e => { console.error(e); process.exit(1); });