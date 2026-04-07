# STATE.md — Canonical Template Reference

This document is the canonical reference for the `STATE.md` file that every project under `05 Dev Projects/{project}/` should maintain.

Agents can read this file to understand the expected structure. The `memory/project_state` MCP tool auto-creates a `STATE.md` from `STATE_TEMPLATE` in `cli/mcp_adapter.py` when one is missing, and returns `state_created: true` so the caller knows it is a fresh file.

---

## Template

```markdown
---
decay-profile: active
maturity: sapling
status: active
---

# State — {project}

**Last Session:** (none yet)
**Current Position:** Not started
**Current Decision:** (none)
**Open Blockers:** (none)
**Next Action:** Review {project}.md and REQUIREMENTS.md
```

---

## Field Definitions

| Field | Purpose | Example |
|---|---|---|
| `Last Session` | ISO date + brief summary of what was done | `2026-04-07 — implemented read_batch` |
| `Current Position` | Where in the project/task we are right now | `P2-C done, starting P2-D` |
| `Current Decision` | The active architectural or implementation decision being held | `Using ripgrep fast-path before full pipeline` |
| `Open Blockers` | Things blocking forward progress | `Need to confirm edge_source values in prod DB` |
| `Next Action` | The immediate next concrete step | `Run migration v0.3.1, test graph weights` |

---

## Frontmatter Fields

| Key | Value | Notes |
|---|---|---|
| `decay-profile` | `active` | State files should always be active — they decay fast to stay current |
| `maturity` | `sapling` | STATE.md is reviewed / agent-updated, not raw seed |
| `status` | `active` \| `archived` | Set to `archived` when project is complete |

---

## Usage by Agents

1. Call `memory/project_state` at session start — it loads and auto-creates STATE.md.
2. After significant work, call `memory/write_working` with a STATE-patch note, or directly overwrite STATE.md.
3. Always update `Last Session` and `Next Action` at the end of a session so the next agent (or you) can resume cleanly.
4. `Current Decision` captures the _active working assumption_ — what you are committed to right now, so you don't re-litigate it next session.
