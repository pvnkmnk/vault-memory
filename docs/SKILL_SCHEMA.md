# Skill File Schema

## Overview

Skill files live in `08 Meta/skills/` and define reusable capabilities that can be invoked via `memory/trigger_lookup`. Each skill file is a standard Markdown note with YAML frontmatter describing its capability, keyword triggers, and MCP integration.

## Frontmatter Schema

```yaml
---
capability: "description of what this skill does"
trigger: ["keyword1", "keyword2"]
mcp_tool: "memory/write_working"
prompt_template: "path/to/template.md"
---
```

### Fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `capability` | `string` | Yes | Human-readable description of what this skill does. Displayed in `trigger_lookup` results. |
| `trigger` | `string[]` | Yes | List of keyword triggers. When any keyword appears in the user's message, this skill is recommended. Case-insensitive substring matching. |
| `mcp_tool` | `string` | No | Recommended MCP tool for this capability (e.g., `memory/write_working`, `memory/read_batch`). Used by the agent to select the right tool. |
| `prompt_template` | `string` | No | Vault-relative path to a prompt template file (e.g., `08 Meta/skills/templates/code-review.md`). Agent can load this template for structured output. |

## Example

```yaml
---
capability: "Code review — static analysis and architectural critique"
trigger: ["code review", "critique", "linter", "architecture"]
mcp_tool: "memory/write_working"
prompt_template: "08 Meta/skills/templates/code-review.md"
---

# Code Review

... skill content ...
```

## How `memory/trigger_lookup` Uses Skill Files

The `_memory_trigger_lookup()` function in `cli/mcp_adapter.py`:

1. Scans `08 Meta/skills/*.md` using `python-frontmatter`
2. Parses each file's frontmatter to extract `capability`, `trigger`, `mcp_tool`, `prompt_template`
3. Performs case-insensitive substring matching of `trigger` keywords against the user's message
4. Returns `skill_recommendations` array alongside classic `recommended_blocks` from `triggers.md`

Response shape:

```json
{
  "recommended_blocks": [...],
  "skill_recommendations": [
    {
      "skill_file": "08 Meta/skills/code-review.md",
      "capability": "Code review — static analysis and architectural critique",
      "mcp_tool": "memory/write_working",
      "prompt_template": "08 Meta/skills/templates/code-review.md",
      "matched_triggers": ["code review"]
    }
  ],
  "always_attach": ["identity-pvnkmnk.md"]
}
```

## Notes

- Requires: `python-frontmatter`
