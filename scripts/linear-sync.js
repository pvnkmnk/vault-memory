#!/usr/bin/env node
// linear-sync.js — bidirectional Linear <-> repo sync.
//
// Commands:
//   node scripts/linear-sync.js doctor        Verify the API key, list teams
//   node scripts/linear-sync.js pull          Mirror open Linear issues -> docs/LINEAR_MIRROR.md
//   node scripts/linear-sync.js push-github   Import open GitHub issues -> Linear (idempotent by title)
//
// Auth: process.env.LINEAR_API_KEY (required — no hardcoded fallback).
// GitHub side of push-github uses the `gh` CLI (works locally and in Actions).
//
// The pull mirror exists so the Linear state is recorded in git — agents and
// contributors without the API key can still see the tracker state.

const https = require('https');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const API_KEY = process.env.LINEAR_API_KEY;
const TEAM_KEY = process.env.LINEAR_TEAM_KEY || 'VAU'; // Linear team short key
const MIRROR_PATH = path.join(__dirname, '..', 'docs', 'LINEAR_MIRROR.md');

// Existing workspace UUIDs (created via scripts/linear-import-setup.js)
const PROJECT_V090 = '4e29347f-5bf1-4591-86d6-29c98491ba0b'; // v0.9.0 — Learning Loop
const GH_TITLE_PREFIX = 'GH #';

const PRIORITY = { 0: 'None', 1: 'Urgent', 2: 'High', 3: 'Normal', 4: 'Low' };

function gql(query, variables = {}) {
  if (!API_KEY) {
    console.error(
      'ERROR: LINEAR_API_KEY is not set.\n' +
      'Create a personal API key at Linear -> Settings -> Security & access,\n' +
      'then add it in the Freebuff Keys tab (Settings -> Environment) as LINEAR_API_KEY\n' +
      'or export it in your shell.'
    );
    process.exit(1);
  }
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ query, variables });
    const req = https.request(
      {
        hostname: 'api.linear.app',
        path: '/graphql',
        method: 'POST',
        headers: {
          Authorization: API_KEY,
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => (data += chunk));
        res.on('end', () => {
          try {
            const json = JSON.parse(data);
            if (json.errors) reject(new Error(json.errors.map((e) => e.message).join('; ')));
            else resolve(json.data);
          } catch (e) {
            reject(new Error(`Parse error: ${data.slice(0, 300)}`));
          }
        });
      }
    );
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

// Resolve the team id once per run (Query.team only accepts id, not key).
let _teamIdCache = null;
async function teamId() {
  if (_teamIdCache) return _teamIdCache;
  const data = await gql(`query { teams { nodes { id key } } }`);
  const team = data.teams.nodes.find((t) => t.key === TEAM_KEY);
  if (!team) throw new Error(`Team "${TEAM_KEY}" not found — set LINEAR_TEAM_KEY`);
  _teamIdCache = team.id;
  return _teamIdCache;
}

// ---------------------------------------------------------------------------
// doctor
// ---------------------------------------------------------------------------
async function doctor() {
  const data = await gql(`query {
    viewer { id name email }
    teams { nodes { id key name } }
  }`);
  console.log(`Authenticated as: ${data.viewer.name} <${data.viewer.email || 'no email'}>`);
  console.log('Teams:');
  for (const t of data.teams.nodes) {
    console.log(`  ${t.key} — ${t.name} (id: ${t.id})`);
  }
  const team = data.teams.nodes.find((t) => t.key === TEAM_KEY);
  if (!team) console.log(`\nNOTE: team key "${TEAM_KEY}" not found — set LINEAR_TEAM_KEY to override.`);
}

// ---------------------------------------------------------------------------
// pull — Linear -> docs/LINEAR_MIRROR.md
// ---------------------------------------------------------------------------
const ISSUE_FIELDS = `
  identifier title priority updatedAt url
  state { name }
  assignee { name }
  project { name }
  labels { nodes { name } }
`;

async function fetchTeamIssues() {
  let cursor = null;
  const issues = [];
  const SAFETY_CAP = 5000; // guard against runaway loops; warn if ever hit
  for (let page = 0; page < 50; page++) {
    const data = await gql(
      `query($id: String!, $after: String) {
        team(id: $id) {
          issues(first: 100, after: $after,
            filter: { state: { name: { nin: ["Done", "Canceled", "Duplicate"] } } }) {
            nodes { ${ISSUE_FIELDS} }
            pageInfo { hasNextPage endCursor }
          }
        }
      }`,
      { id: await teamId(), after: cursor }
    );
    if (!data.team) throw new Error(`Team "${TEAM_KEY}" not found`);
    issues.push(...data.team.issues.nodes);
    if (!data.team.issues.pageInfo.hasNextPage) return issues;
    cursor = data.team.issues.pageInfo.endCursor;
  }
  if (issues.length >= SAFETY_CAP) {
    console.error(`WARNING: hit ${issues.length}-issue safety cap; mirror may be truncated.`);
  }
  return issues;
}

async function pull() {
  const issues = await fetchTeamIssues();
  const generated = new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC';

  const byProject = {};
  for (const i of issues) {
    const p = (i.project && i.project.name) || 'No project';
    (byProject[p] = byProject[p] || []).push(i);
  }

  let md = `# Linear Mirror — open issues (Team ${TEAM_KEY})\n\n`;
  md += `> Generated by \`scripts/linear-sync.js pull\` on ${generated}.\n`;
  md += `> Read-only snapshot of the Linear tracker; the tracker itself remains the source of truth.\n\n`;
  md += `**${issues.length} open issue(s).**\n\n`;

  const projects = Object.keys(byProject).sort();
  for (const p of projects) {
    md += `## ${p}\n\n`;
    md += `| Issue | Title | State | Priority | Assignee | Updated |\n|---|---|---|---|---|---|\n`;
    const sorted = byProject[p].sort((a, b) =>
      (a.priority || 9) - (b.priority || 9) || a.identifier.localeCompare(b.identifier, undefined, { numeric: true })
    );
    for (const i of sorted) {
      const title = i.title.replace(/\|/g, '\\|');
      const state = i.state ? i.state.name : '';
      const pri = PRIORITY[i.priority] || 'None';
      const who = i.assignee ? i.assignee.name : '—';
      const updated = (i.updatedAt || '').slice(0, 10);
      md += `| [${i.identifier}](${i.url}) | ${title} | ${state} | ${pri} | ${who} | ${updated} |\n`;
    }
    md += '\n';
  }

  fs.writeFileSync(MIRROR_PATH, md);
  console.log(`Wrote ${MIRROR_PATH} (${issues.length} open issues across ${projects.length} project(s)).`);
}

// ---------------------------------------------------------------------------
// push-github — open GitHub issues -> Linear (idempotent by "GH #N:" prefix)
// ---------------------------------------------------------------------------
async function ghOpenIssues() {
  // Input precedence: --issues-file <path> | piped stdin | run gh ourselves.
  // The file/stdin paths keep GitHub auth with the caller's shell (Freebuff/1Password
  // etc. inject gh credentials only for direct calls); the gh fallback suits CI.
  const fileArg = process.argv.indexOf('--issues-file');
  if (fileArg !== -1 && process.argv[fileArg + 1]) {
    return JSON.parse(fs.readFileSync(process.argv[fileArg + 1], 'utf8'));
  }
  if (!process.stdin.isTTY) {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    const raw = Buffer.concat(chunks).toString('utf8').trim();
    if (raw) return JSON.parse(raw);
  }
  const out = execSync(
    'gh issue list --state open --limit 1000 --json number,title,body,labels,milestone,url',
    { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 }
  );
  const parsed = JSON.parse(out);
  if (parsed.length >= 1000) {
    console.error('WARNING: 1000-issue fetch limit reached; some open issues may not be imported.');
  }
  return parsed;
}

async function existingLinearTitles() {
  const issues = await fetchTeamIssues();
  return new Set(issues.map((i) => i.title));
}

async function getTeamId() {
  return teamId();
}

async function pushGithub() {
  const ghIssues = await ghOpenIssues();
  if (!ghIssues.length) {
    console.log('No open GitHub issues to import.');
    return;
  }
  const teamId = await getTeamId();
  const existing = await existingLinearTitles();
  let created = 0;
  let skipped = 0;

  for (const gh of ghIssues) {
    const title = `${GH_TITLE_PREFIX}${gh.number}: ${gh.title}`;
    if (existing.has(title)) {
      skipped++;
      continue;
    }
    const labels = (gh.labels || []).map((l) => l.name).join(', ');
    const milestone = gh.milestone ? gh.milestone.title : '';
    let description = `Imported from GitHub issue #${gh.number}.\n\n**GitHub:** ${gh.url}\n`;
    if (milestone) description += `**Milestone:** ${milestone}\n`;
    if (labels) description += `**Labels:** ${labels}\n`;
    description += `\n---\n\n${gh.body || '(no body)'}`;

    // v0.9.0 milestone maps to the existing Linear project
    const projectId = /v0\.9\.0/i.test(milestone) ? PROJECT_V090 : undefined;

    const result = await gql(
      `mutation($input: IssueCreateInput!) {
        issueCreate(input: $input) { success issue { identifier url } }
      }`,
      { input: { teamId, title, description, projectId } }
    );
    if (!result.issueCreate || result.issueCreate.success !== true) {
      throw new Error(`Linear rejected creation of "${title}"`);
    }
    created++;
    console.log(`  created: ${title} -> ${result.issueCreate.issue.identifier}`);
  }
  console.log(`\nImported ${created} issue(s), skipped ${skipped} already present.`);
}

// ---------------------------------------------------------------------------
async function main() {
  const cmd = process.argv[2] || 'pull';
  if (cmd === 'doctor') await doctor();
  else if (cmd === 'pull') await pull();
  else if (cmd === 'push-github') await pushGithub();
  else {
    console.error('Unknown command. Use: doctor | pull | push-github');
    process.exit(1);
  }
}

main().catch((e) => {
  console.error(`linear-sync failed: ${e.message}`);
  process.exit(1);
});
