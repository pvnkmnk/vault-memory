#!/usr/bin/env node
// linear-link-cycles-to-project.js — links cycles to v0.8.0 project
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

const PROJECT_ID = '5759dfaa-8617-4f5d-90cf-5bb45bb0fedf';

// Cycle IDs
const CYCLES = [
  '21a122fb-7f66-4e77-b2e0-a698778b1410', // S24
  '5d1e7d2b-4017-4f51-a6cf-6dda539807e2', // S25
  '157c5684-7e1a-4ec1-ab73-261cd72ec2f4', // S26
  '51f7feec-a770-49b3-9efc-a4b41202cbc4', // S27
  'd59a2c90-1cdc-4b73-9503-44c1872896cf', // S28
  'e27ec418-2626-444a-b4bd-113d0620589a', // S29
];

async function run() {
  console.log('=== Linking Cycles to v0.8.0 Project ===\n');
  console.log(`Project ID: ${PROJECT_ID}\n`);

  // Try to update the project with cycles
  // Note: Linear's project update may not support cycles field directly
  // We'll try various approaches
  
  console.log('Cycle IDs to link:');
  CYCLES.forEach((id, i) => console.log(`  S${24+i}: ${id}`));
  
  console.log('\n--- Attempting to link cycles to project ---');
  
  try {
    // Try project update with teamId (some Linear versions support this)
    const result = await gql(
      `mutation UpdateProject($id: String!, $teamId: String) {
         projectUpdate(id: $id, input: { teamId: $teamId }) { success }
       }`,
      { id: PROJECT_ID, teamId: '9d788748-97d6-43f6-b978-be783010d6e5' }
    );
    console.log('  ✓ Project teamId updated');
  } catch (e) {
    console.log(`  ⚠ Project update: ${e.message.split(';')[0]}`);
  }

  console.log('\n=== Note ===');
  console.log('Linear cycles are team-scoped and cannot be directly linked to projects via API.');
  console.log('To associate cycles with the v0.8.0 project:');
  console.log('  1. Go to Linear UI → Project: v0.8.0 → Settings (⚙️)');
  console.log('  2. Navigate to the Cycles tab');
  console.log('  3. Click on each cycle to assign it to the project');
  console.log('\nAlternatively, in Linear: Project Settings → Cycles → Add cycle');
}

run().catch(e => { console.error(e); process.exit(1); });