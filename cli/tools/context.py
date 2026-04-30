# cli/tools/context.py
"""Context-related MCP tools: memory/attach_block, memory/list_blocks, memory/read_batch, memory/write_working, memory/delete_working, memory/trigger_lookup, memory/project_state."""

import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import frontmatter as _fm
    _FRONTMATTER_AVAILABLE = True
except ImportError:
    _FRONTMATTER_AVAILABLE = False

from cli.mcp_client import _auth_headers, _sanitize_vault_relative_path, _token_est

# In-process session state for attached blocks
_attached_blocks: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "memory/attach_block",
        "description": "Attach a named memory block to this session. Special name 'today' auto-resolves to today's daily note. Blocks persist for the session duration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "block_name": {
                    "type": "string",
                    "description": "Block filename e.g. 'djinn-architecture.md', or reserved name 'today'",
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["block_name", "vault_path"],
        },
    },
    {
        "name": "memory/list_blocks",
        "description": "List all memory blocks currently attached to this session, with character and estimated token counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory/read_batch",
        "description": "Read multiple vault files in a single round-trip. Concatenates content with path headers and --- separators. Respects a token budget (default 8000); truncates gracefully. Replaces 4 separate attach_block calls at session start.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of vault-relative paths e.g. ['08 Meta/agent-context/identity-pvnkmnk.md', '05 Dev Projects/djinn/STATE.md']",
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "max_tokens": {
                    "type": "integer",
                    "description": "Token budget — truncates when exceeded (default 8000)",
                    "default": 8000,
                },
            },
            "required": ["paths", "vault_path"],
        },
    },
    {
        "name": "memory/write_working",
        "description": "Write a note to the _working/ buffer. Safe for agents — bypasses semantic write gate. Heartbeat will promote or prune. Filename is sanitized server-side.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename e.g. 'insight-2026-04-07.md' — directory components and special chars stripped automatically",
                },
                "content": {"type": "string", "description": "Note content (Markdown)"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Agent confidence level",
                    "default": "medium",
                },
                "maturity": {
                    "type": "string",
                    "enum": ["seed", "sapling"],
                    "description": "Maturity level (default: seed). Use sapling for reviewed output.",
                    "default": "seed",
                },
            },
            "required": ["filename", "content", "vault_path"],
        },
    },
    {
        "name": "memory/delete_working",
        "description": "Delete a file from _working/ only. Refuses any path outside the _working/ directory. Confirms existence before deleting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Filename to delete from _working/ e.g. 'stale-draft.md'",
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["filename", "vault_path"],
        },
    },
    {
        "name": "memory/trigger_lookup",
        "description": "Scan a message for keyword triggers and return recommended context blocks from triggers.md and skill files in 08 Meta/skills/.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "User message to scan for triggers"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["message", "vault_path"],
        },
    },
    {
        "name": "memory/project_state",
        "description": "Load the full session-start bundle for a project: identity, current state, roadmap, and semantic context. Returns combined content with token cost estimate. Auto-creates STATE.md from template if missing. Use at the start of every project session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project slug / folder name e.g. 'djinn-netrunner'",
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "daemon_url": {
                    "type": "string",
                    "description": "Vault-memory daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["project", "vault_path"],
        },
    },
]


# ---------------------------------------------------------------------------
# STATE.md canonical template
# ---------------------------------------------------------------------------

STATE_TEMPLATE = """\
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
"""


# ---------------------------------------------------------------------------
# memory/attach_block
# ---------------------------------------------------------------------------

def _memory_attach_block(args: Dict) -> Dict:
    block_name = args["block_name"]
    vault_path = args["vault_path"]
    vault_root = Path(vault_path)

    if block_name == "today":
        today_str = date.today().strftime("%Y-%m-%d")
        candidates = [
            vault_root / "06 Daily" / f"{today_str}.md",
            vault_root / "Daily Notes" / f"{today_str}.md",
            vault_root / "Daily" / f"{today_str}.md",
            vault_root / f"{today_str}.md",
        ]
        block_file = None
        for c in candidates:
            if c.exists():
                block_file = c
                break
        if block_file is None:
            return {
                "error": f"Today's daily note not found ({today_str}.md). "
                f"Searched: 06 Daily/, Daily Notes/, Daily/, vault root."
            }
        display_name = f"today ({today_str})"
    else:
        blocks_dir = vault_root / "08 Meta" / "agent-context" / "memory-blocks"
        block_file = blocks_dir / block_name
        if not block_file.exists():
            return {"error": f"Block not found: {block_file}"}
        display_name = block_name

    content = block_file.read_text(encoding="utf-8")
    char_count = len(content)
    token_est = _token_est(content)

    existing = [b["name"] for b in _attached_blocks]
    if display_name not in existing:
        _attached_blocks.append(
            {
                "name": display_name,
                "content": content,
                "char_count": char_count,
                "token_est": token_est,
            }
        )
    total_tokens = sum(b["token_est"] for b in _attached_blocks)
    return {
        "attached": display_name,
        "char_count": char_count,
        "token_est": token_est,
        "session_total_tokens": total_tokens,
        "content": content,
    }


# ---------------------------------------------------------------------------
# memory/list_blocks
# ---------------------------------------------------------------------------

def _memory_list_blocks() -> Dict:
    total_chars = sum(b["char_count"] for b in _attached_blocks)
    total_tokens = sum(b["token_est"] for b in _attached_blocks)
    return {
        "attached_blocks": [
            {"name": b["name"], "char_count": b["char_count"], "token_est": b["token_est"]}
            for b in _attached_blocks
        ],
        "total_chars": total_chars,
        "total_tokens": total_tokens,
    }


# ---------------------------------------------------------------------------
# memory/read_batch
# ---------------------------------------------------------------------------

def _memory_read_batch(args: Dict) -> Dict:
    """
    Read multiple vault-relative paths in one call.
    Concatenates with path headers + --- separators.
    Respects max_tokens budget: truncates remaining files once exceeded.
    All paths are sanitized to prevent traversal outside vault_root.
    """
    paths = args["paths"]
    vault_path = args["vault_path"]
    max_tokens = int(args.get("max_tokens", 8000))
    vault_root = Path(vault_path)

    parts: List[str] = []
    files_included: List[str] = []
    files_truncated: List[str] = []
    running_tokens = 0
    budget_hit = False

    for raw_path in paths:
        if budget_hit:
            files_truncated.append(raw_path)
            continue

        resolved = _sanitize_vault_relative_path(raw_path, vault_root)
        if resolved is None:
            files_truncated.append(f"{raw_path} [TRAVERSAL_BLOCKED]")
            continue

        if not resolved.exists():
            files_truncated.append(f"{raw_path} [NOT_FOUND]")
            continue

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as e:
            files_truncated.append(f"{raw_path} [READ_ERROR: {e}]")
            continue

        file_tokens = _token_est(content)

        if running_tokens + file_tokens > max_tokens:
            # Partially include as much as fits
            remaining_chars = (max_tokens - running_tokens) * 4
            if remaining_chars > 0:
                content = content[:remaining_chars] + "\n... [truncated: token budget reached]"
                parts.append(f"### {raw_path}\n\n{content}")
                files_included.append(raw_path)
                running_tokens = max_tokens
            else:
                files_truncated.append(raw_path)
            budget_hit = True
            continue

        parts.append(f"### {raw_path}\n\n{content}")
        files_included.append(raw_path)
        running_tokens += file_tokens

    combined_content = "\n\n---\n\n".join(parts)
    return {
        "combined_content": combined_content,
        "files_included": files_included,
        "files_truncated": files_truncated,
        "total_tokens": running_tokens,
        "max_tokens": max_tokens,
        "budget_exhausted": budget_hit,
    }


# ---------------------------------------------------------------------------
# memory/write_working
# ---------------------------------------------------------------------------

def _sanitize_filename(filename: str) -> Optional[str]:
    filename = os.path.basename(filename)
    filename = filename.replace("\x00", "")
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    filename = re.sub(r"[^\w\-. ]", "_", filename)
    filename = filename.strip(". ")
    if not filename:
        return None
    return filename


def _memory_write_working(args: Dict) -> Dict:
    import os

    filename = args["filename"]
    content = args["content"]
    vault_path = args["vault_path"]
    confidence = args.get("confidence", "medium")
    maturity = args.get("maturity", "seed")

    clean_filename = _sanitize_filename(filename)
    if clean_filename is None:
        return {
            "error": f"Invalid filename: '{filename}'. Only safe characters allowed (word chars, hyphens, dots, spaces)."
        }

    if not clean_filename.endswith(".md"):
        clean_filename += ".md"

    working_dir = Path(vault_path) / "_working"
    working_dir.mkdir(parents=True, exist_ok=True)
    out_path = working_dir / clean_filename

    now = datetime.now(timezone.utc).isoformat()
    frontmatter_block = f"""---
agent-written: true
agent-confidence: {confidence}
trust: low
importance: 0.5
decay-profile: active
maturity: {maturity}
date_created: {now}
status: working
---

"""

    full_content = frontmatter_block + content
    out_path.write_text(full_content, encoding="utf-8")
    return {
        "written": str(out_path),
        "filename_used": clean_filename,
        "original_filename": filename,
        "sanitized": clean_filename != filename,
        "confidence": confidence,
        "maturity": maturity,
        "note": "Staged in _working/. Heartbeat will promote or prune based on maturity + confidence.",
    }


# ---------------------------------------------------------------------------
# memory/delete_working
# ---------------------------------------------------------------------------

def _memory_delete_working(args: Dict) -> Dict:
    import os

    filename = args["filename"]
    vault_path = args["vault_path"]

    clean_filename = _sanitize_filename(filename)
    if clean_filename is None:
        return {"error": f"Invalid filename: '{filename}'."}

    working_dir = Path(vault_path) / "_working"
    target = (working_dir / clean_filename).resolve()
    working_resolved = working_dir.resolve()

    try:
        target.relative_to(working_resolved)
    except ValueError:
        return {
            "error": f"Security: resolved path '{target}' is outside _working/. Deletion refused."
        }

    if not target.exists():
        return {
            "deleted": False,
            "existed": False,
            "path": str(target),
            "note": "File does not exist in _working/.",
        }

    target.unlink()
    return {
        "deleted": True,
        "existed": True,
        "path": str(target),
        "note": "Deleted from _working/.",
    }


# ---------------------------------------------------------------------------
# memory/trigger_lookup
# ---------------------------------------------------------------------------

def _memory_trigger_lookup(args: Dict) -> Dict:
    message = args["message"].lower()
    vault_path = args["vault_path"]
    vault_root = Path(vault_path)

    # --- 1. Classic triggers.md table scan ---
    trigger_file = vault_root / "08 Meta" / "agent-context" / "triggers.md"
    recommended: List[Dict] = []

    if trigger_file.exists():
        triggers_raw = trigger_file.read_text(encoding="utf-8")
        rows = re.findall(r"\|([^|]+)\|([^|]+)\|([^|]+)\|", triggers_raw)
        for pattern_cell, block_cell, mode_cell in rows:
            pattern_cell = pattern_cell.strip()
            block_cell = block_cell.strip()
            mode_cell = mode_cell.strip()
            if pattern_cell.startswith("-") or pattern_cell.lower() == "keyword pattern":
                continue
            sub_patterns = [p.strip().replace("\\", "") for p in pattern_cell.split("|")]
            if any(sp and sp in message for sp in sub_patterns):
                recommended.append(
                    {
                        "block": block_cell,
                        "mode": mode_cell,
                        "matched_pattern": pattern_cell,
                        "source": "triggers.md",
                    }
                )

    # --- 2. Skill file scan ---
    skill_recommendations: List[Dict] = []
    skills_dir = vault_root / "08 Meta" / "skills"
    if skills_dir.exists() and _FRONTMATTER_AVAILABLE:
        for skill_file in skills_dir.glob("*.md"):
            try:
                post = _fm.load(str(skill_file))
                triggers = post.metadata.get("trigger", [])
                if isinstance(triggers, str):
                    triggers = [triggers]
                if any(kw.lower() in message for kw in triggers):
                    skill_recommendations.append(
                        {
                            "skill_file": str(skill_file.relative_to(vault_root)),
                            "capability": post.metadata.get("capability", ""),
                            "mcp_tool": post.metadata.get("mcp_tool", ""),
                            "prompt_template": post.metadata.get("prompt_template", ""),
                            "matched_triggers": [kw for kw in triggers if kw.lower() in message],
                        }
                    )
            except Exception as e:
                logger.debug("Skill file parse error %s: %s", skill_file, e)

    return {
        "recommended_blocks": recommended,
        "skill_recommendations": skill_recommendations,
        "always_attach": ["identity-pvnkmnk.md"],
    }


# ---------------------------------------------------------------------------
# memory/project_state
# ---------------------------------------------------------------------------

def _memory_project_state(args: Dict, daemon_url: str) -> Dict:
    import httpx

    project = args["project"]
    vault_path = args["vault_path"]
    daemon = args.get("daemon_url", daemon_url)

    project_dir = Path(vault_path) / "05 Dev Projects" / project
    result: Dict[str, Any] = {
        "project": project,
        "project_identity": None,
        "current_state": None,
        "roadmap_summary": None,
        "semantic_context": [],
        "missing_files": [],
        "state_created": False,
        "token_cost": 0,
    }

    # 1. Project identity file
    identity_path = project_dir / f"{project}.md"
    if identity_path.exists():
        result["project_identity"] = identity_path.read_text(encoding="utf-8")
    else:
        result["missing_files"].append(f"{project}.md")

    # 2. STATE.md — auto-create from STATE_TEMPLATE constant if missing
    state_path = project_dir / "STATE.md"
    if state_path.exists():
        result["current_state"] = state_path.read_text(encoding="utf-8")
    else:
        filled_template = STATE_TEMPLATE.format(project=project)
        project_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(filled_template, encoding="utf-8")
        result["current_state"] = filled_template
        result["state_created"] = True
        result["missing_files"].append("STATE.md (auto-created from STATE_TEMPLATE)")

    # 3. ROADMAP.md — first 60 lines
    roadmap_path = project_dir / "ROADMAP.md"
    if roadmap_path.exists():
        lines = roadmap_path.read_text(encoding="utf-8").splitlines()
        result["roadmap_summary"] = "\n".join(lines[:60])
    else:
        result["missing_files"].append("ROADMAP.md")

    # 4. Semantic context from daemon
    try:
        r = httpx.post(
            f"{daemon}/search",
            json={"query": project, "project": project, "top_k": 5, "apply_decay": True},
            timeout=15.0,
            headers=_auth_headers,
        )
        r.raise_for_status()
        result["semantic_context"] = r.json().get("results", [])
    except Exception as e:
        logger.warning("project_state semantic search failed: %s", e)
        result["semantic_context"] = []

    # 5. Token cost estimate
    total_chars = sum(
        [
            len(result["project_identity"] or ""),
            len(result["current_state"] or ""),
            len(result["roadmap_summary"] or ""),
            sum(len(str(r)) for r in result["semantic_context"]),
        ]
    )
    result["token_cost"] = total_chars // 4
    return result


def get_tools() -> list:
    """Return the context tool definitions."""
    return TOOLS
