---
name: linear-graphql
description: Call the Linear GraphQL API from agent sessions in this repo — authenticate via LINEAR_API_KEY loaded from .env.local without tripping the Freebuff env guard, use shorthand issue IDs, avoid the IssueFilter/ID-typing schema traps, and flip issues or query state with scripts/linear-flip-done.mjs.
metadata:
  category: integrations
  language: javascript
  service: linear
---

# Linear GraphQL API from agent sessions

Use this skill whenever a task needs to read or mutate Linear data (issues,
states, projects) from a terminal session in this repo: closing issues after a
fix lands, filing follow-ups, checking tracker state.

## Knowledge

- **Endpoint**: `https://api.linear.app/graphql` (introspectable; browsable in
  Apollo Studio via linear.app/developers/graphql).
- **Auth**: personal API key passed raw — `Authorization: <API_KEY>`, no
  `Bearer` prefix. (OAuth2 uses `Bearer`, API keys do not — verified against
  Linear's official docs, 2026-09.)
- **Where the key lives**: Freebuff Settings → Environment (Keys tab) as
  `LINEAR_API_KEY`, merged into the gitignored `.env.local`.
- **Agent shells do NOT inherit Settings → Environment values.**
  `echo ${LINEAR_API_KEY:+SET}` prints nothing even when the key is
  configured. Expected, not a missing key.
- **The Freebuff workspace guard blocks any command line containing a raw
  secret value** ("Direct env and sensitive-file access is blocked"). It also
  blocks shell sourcing (`. ./.env.local`) and `node --env-file=.env.local`
  because those reference the env file from the command line. So the key must
  be loaded from inside the script itself.
- **Schema traps** (all verified live):
  1. `IssueFilter` has **no `identifier` field** — `issues(filter: { identifier:
     { in: [...] } })` fails with `Field "identifier" is not defined by type
     "IssueFilter"`. Use `issue(id: "VAU-16")` per issue instead; `issue` and
     `issueUpdate` accept shorthand human IDs (`VAU-16`) as well as UUIDs.
  2. Variables the schema types as `ID` (e.g. team id in
     `workflowStates(filter: { team: { id: { eq: $team } } })`) must be
     declared `ID!` in the query document — `String!` fails with a
     variable-type mismatch.
  3. GraphQL returns HTTP 200 even for partial failures — always check the
     `errors` array before assuming success.
- **Rate limits**: batch independent lookups with `Promise.all`, don't poll
  per-issue; Linear documents this explicitly.
- **Repo tooling**:
  | Script | Direction | Auth source |
  |---|---|---|
  | `scripts/linear-sync.js pull` | Linear → `docs/LINEAR_MIRROR.md` | env key (repo secret in CI) |
  | `scripts/linear-sync.js push-github` | GitHub issues → Linear | env key + `gh` CLI |
  | `scripts/linear-flip-done.mjs` | flip issues to Done by identifier | env key → local dotenv fallback |

  `linear-flip-done.mjs` lands with PR #92 (held for review at the time of
  writing). If it's absent from your checkout, create it from the loader +
  gql wrapper in the Instructions below.

## Instructions

### 1. Ensure the key exists

If `.env.local` already contains `LINEAR_API_KEY` (it does as of 2026-09),
skip this step. Otherwise ask the user to paste the key and merge it with:

```bash
freebuff-env set --file .env.local '{"LINEAR_API_KEY":"<user-pasted-key>"}'
```

Only pass values the user explicitly supplied. Never echo, log, or commit the
value; never put it in a shell command, PR body, or commit message.

### 2. Reuse the existing helper for the common case

```bash
node scripts/linear-flip-done.mjs VAU-10 VAU-12   # flip issues to Done
```

The script reads the key from the process environment, falling back to a
dotenv-style parse of `.env.local` inside the script (the guard-safe pattern).

### 3. For new operations, copy the loader + gql wrapper

```js
import { readFileSync } from 'node:fs';

if (!process.env.LINEAR_API_KEY) {
  try {
    for (const line of readFileSync('.env.local', 'utf8').split('\n')) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m && process.env[m[1]] === undefined) {
        process.env[m[1]] = m[2].replace(/^['"]|['"]$/g, '');
      }
    }
  } catch { /* absent file — surface the error below */ }
}

const KEY = process.env.LINEAR_API_KEY;

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

// Single issue by shorthand id — returns data.issue directly.
const { issue } = await gql(
  `query($id: String!) { issue(id: $id) { id identifier title state { id name } team { id } } }`,
  { id: 'VAU-16' }
);

// Resolve the team's Done state (note: $team is ID!, not String!).
const teamId = issue.team.id;
const { workflowStates } = await gql(
  `query($team: ID!) {
    workflowStates(filter: { team: { id: { eq: $team } }, name: { eq: "Done" } }) { nodes { id name } }
  }`,
  { team: teamId }
);

// Update by shorthand id or uuid — both accepted.
await gql(
  `mutation($id: String!, $stateId: String!) {
    issueUpdate(id: $id, input: { stateId: $stateId }) { success }
  }`,
  { id: issue.id, stateId: workflowStates.nodes[0].id }
);
```

Remember: `gql` returns the **`data` object** (`{ issue: {...} }`) — unwrap
the root field before use.

### 4. Housekeeping

- `docs/LINEAR_MIRROR.md` is a generated snapshot refreshed by the nightly
  `linear-mirror.yml` workflow. Don't fight the env-file guard to regenerate
  it locally; let CI do it.
- Keep `.env.local` untracked (gitignored). Never `git add -f` it.
- When Linear states are flipped alongside a code change, note it in the PR
  description so a review rejection reverts the state too.
