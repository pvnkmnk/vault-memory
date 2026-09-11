// Flip Linear issues to Done by identifier (shorthand like VAU-16 works
// directly — Linear's `issue`/`issueUpdate` accept uuids AND human IDs).
// Usage: node scripts/linear-flip-done.mjs VAU-10 VAU-12 ...
// Credentials: LINEAR_API_KEY from the environment, falling back to
// .env.local (dotenv-style parse; values are never printed).
import { readFileSync } from 'node:fs';

if (!process.env.LINEAR_API_KEY) {
  try {
    for (const line of readFileSync('.env.local', 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && process.env[m[1]] === undefined) {
        process.env[m[1]] = m[2].replace(/^['"]|['"]$/g, '');
      }
    }
  } catch {
    // .env.local absent — surface the original error below.
  }
}

const KEY = process.env.LINEAR_API_KEY;
if (!KEY) {
  console.error('LINEAR_API_KEY not set in environment or .env.local');
  process.exit(1);
}
const IDENTIFIERS = process.argv.slice(2);
if (IDENTIFIERS.length === 0) {
  console.error('Usage: node scripts/linear-flip-done.mjs VAU-10 VAU-12 ...');
  process.exit(1);
}

function gql(query, variables = {}) {
  return fetch('https://api.linear.app/graphql', {
    method: 'POST',
    headers: { Authorization: KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  }).then(async (r) => {
    const json = await r.json();
    if (json.errors) throw new Error(json.errors.map((e) => e.message).join('; '));
    return json.data;
  });
}

// Fetch each issue directly by shorthand identifier — no pagination needed.
// A missing/unknown identifier surfaces as a GraphQL error per issue.
const issues = await Promise.all(
  IDENTIFIERS.map(async (ident) => {
    try {
      const data = await gql(
        `query($id: String!) {
          issue(id: $id) { id identifier title state { id name } team { id } }
        }`,
        { id: ident }
      );
      return data.issue;
    } catch (e) {
      console.error(`${ident}: lookup failed — ${e.message}`);
      return null;
    }
  })
);

const missing = IDENTIFIERS.filter((_, i) => issues[i] === null);
if (missing.length > 0) {
  console.error('Not found:', missing.join(', '));
  process.exit(1);
}

const teamId = issues[0].team.id;
const states = await gql(`query($team: ID!) {
  workflowStates(filter: { team: { id: { eq: $team } }, name: { eq: "Done" } }) { nodes { id name } }
}`, { team: teamId });
const doneState = states.workflowStates.nodes[0];
if (!doneState) { console.error('Done state not found'); process.exit(1); }

for (const issue of issues) {
  if (issue.state.name === 'Done') {
    console.log(`${issue.identifier}: already Done — skipped`);
    continue;
  }
  const res = await gql(`mutation($id: String!, $stateId: String!) {
    issueUpdate(id: $id, input: { stateId: $stateId }) { success }
  }`, { id: issue.id, stateId: doneState.id });
  console.log(`${issue.identifier}: ${issue.state.name} -> Done (success=${res.issueUpdate.success}) — ${issue.title}`);
}
