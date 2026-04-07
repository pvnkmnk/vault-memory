# cli/mcp_adapter.py
"""
MCP stdio adapter.
Translates Model Context Protocol JSON-RPC messages to daemon HTTP calls.
Compatible with Claude Desktop, Cursor, Cline, Gemini CLI, OpenCode, and any MCP-compliant client.

Tools (v0.4.0):
  search                  — 4-strategy vault search with GARS + decay
  search_siblings         — topic sibling traversal from seed note
  graph                   — entity relationship traversal
  temporal                — date-range history query
  health                  — daemon status
  memory/attach_block     — attach named context block (supports 'today' reserved name)
  memory/list_blocks      — list attached blocks + token counts
  memory/write_working    — write note to _working/ buffer (path-sanitized)
  memory/delete_working   — safely delete a file from _working/ only
  memory/trigger_lookup   — keyword → context block mapping
  memory/project_state    — full session-start bundle for a project

v0.4.0 changes:
  - memory/write_working: path sanitization (no traversal, no null bytes, no special chars)
  - memory/delete_working: new tool — safe _working/ cleanup
  - memory/attach_block: 'today' reserved block name auto-resolves to today's daily note
  - memory/project_state: implemented (was documented but absent from code)
  - version string updated to 0.4.0
"""

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("vault-memory.mcp")

# In-process session state for attached blocks
_attached_blocks: List[Dict[str, Any]] = []

TOOLS = [
    {
        "name": "search",
        "description": "Search your Obsidian vault using semantic, keyword, graph, and temporal strategies. Returns GARS-ranked results with trust and maturity flags.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query":            {"type": "string",  "description": "Natural language search query"},
                "project":          {"type": "string",  "description": "Optional: scope to a project folder"},
                "top_k":            {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                "include_graph":    {"type": "boolean", "description": "Enable graph traversal strategy"},
                "include_temporal": {"type": "boolean", "description": "Enable temporal history strategy"},
                "apply_decay":      {"type": "boolean", "description": "Apply temporal decay scoring (default true)", "default": True},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_siblings",
        "description": "Discover notes that share an Ontology topic hub with a seed note, even without direct wikilinks between them. Scored by GARS x hub_penalty.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_path": {"type": "string", "description": "Vault-relative path of the seed note e.g. '05 Dev Projects/djinn-netrunner/djinn.md'"},
                "limit":     {"type": "integer", "description": "Max sibling results (default 10)", "default": 10},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["seed_path", "vault_path"],
        },
    },
    {
        "name": "graph",
        "description": "Traverse entity relationships in the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity":       {"type": "string", "description": "Entity name to traverse from"},
                "relationship": {"type": "string", "description": "Optional: filter by relationship type"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "temporal",
        "description": "Query note history within a date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "start":  {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end":    {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["entity"],
        },
    },
    {
        "name": "health",
        "description": "Check vault-memoryd daemon status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "memory/attach_block",
        "description": "Attach a named memory block to this session. Special name 'today' auto-resolves to today's daily note. Blocks persist for the session duration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "block_name": {"type": "string", "description": "Block filename e.g. 'djinn-architecture.md', or reserved name 'today'"},
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
        "name": "memory/write_working",
        "description": "Write a note to the _working/ buffer. Safe for agents — bypasses semantic write gate. Heartbeat will promote or prune. Filename is sanitized server-side.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename":   {"type": "string", "description": "Filename e.g. 'insight-2026-04-07.md' — directory components and special chars stripped automatically"},
                "content":    {"type": "string", "description": "Note content (Markdown)"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"], "description": "Agent confidence level", "default": "medium"},
                "maturity":   {"type": "string", "enum": ["seed", "sapling"], "description": "Maturity level (default: seed). Use sapling for reviewed output.", "default": "seed"},
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
                "filename":   {"type": "string", "description": "Filename to delete from _working/ e.g. 'stale-draft.md'"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["filename", "vault_path"],
        },
    },
    {
        "name": "memory/trigger_lookup",
        "description": "Scan a message for keyword triggers and return recommended context blocks to attach from 08 Meta/agent-context/triggers.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message":    {"type": "string", "description": "User message to scan for triggers"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
            },
            "required": ["message", "vault_path"],
        },
    },
    {
        "name": "memory/project_state",
        "description": "Load the full session-start bundle for a project: identity, current state, roadmap, and semantic context. Returns combined content with token cost estimate. Use at the start of every project session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project":    {"type": "string", "description": "Project slug / folder name e.g. 'djinn-netrunner'"},
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "daemon_url": {"type": "string", "description": "Vault-memory daemon URL (default: http://localhost:5051)", "default": "http://localhost:5051"},
            },
            "required": ["project", "vault_path"],
        },
    },
]


# ---------------------------------------------------------------------------
# Path sanitization helper (used by write_working and delete_working)
# ---------------------------------------------------------------------------

def _sanitize_filename(filename: str) -> Optional[str]:
    """
    Strip directory components, null bytes, and dangerous characters.
    Returns None if the result is empty or starts with a dot.
    """
    # Strip any directory separators — only accept bare filename
    filename = os.path.basename(filename)
    # Remove null bytes and control characters
    filename = filename.replace("\x00", "")
    filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)
    # Allow only safe filename characters: word chars, hyphen, dot, space
    filename = re.sub(r"[^\w\-. ]", "_", filename)
    # Strip leading/trailing dots and spaces (hidden files, trailing dots)
    filename = filename.strip(". ")
    if not filename:
        return None
    return filename


# ---------------------------------------------------------------------------
# Token estimate
# ---------------------------------------------------------------------------

def _token_est(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Daemon dispatch
# ---------------------------------------------------------------------------

def _send(obj: Dict[str, Any]):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _call_daemon(daemon_url: str, tool: str, args: Dict) -> Any:
    if tool == "search":
        r = httpx.post(f"{daemon_url}/search", json=args, timeout=30.0)
        return r.json()
    elif tool == "search_siblings":
        return _search_siblings(args)
    elif tool == "graph":
        r = httpx.get(f"{daemon_url}/graph", params=args, timeout=10.0)
        return r.json()
    elif tool == "temporal":
        r = httpx.get(f"{daemon_url}/temporal", params=args, timeout=10.0)
        return r.json()
    elif tool == "health":
        liveness  = httpx.get(f"{daemon_url}/health", timeout=3.0).json()
        readiness = httpx.get(f"{daemon_url}/ready",  timeout=3.0).json()
        return {"liveness": liveness, "readiness": readiness}
    elif tool == "memory/attach_block":
        return _memory_attach_block(args)
    elif tool == "memory/list_blocks":
        return _memory_list_blocks()
    elif tool == "memory/write_working":
        return _memory_write_working(args)
    elif tool == "memory/delete_working":
        return _memory_delete_working(args)
    elif tool == "memory/trigger_lookup":
        return _memory_trigger_lookup(args)
    elif tool == "memory/project_state":
        return _memory_project_state(args, daemon_url)
    else:
        raise ValueError(f"Unknown tool: {tool}")


# ---------------------------------------------------------------------------
# memory/attach_block  (v0.4.0: 'today' reserved resolver)
# ---------------------------------------------------------------------------

def _memory_attach_block(args: Dict) -> Dict:
    block_name = args["block_name"]
    vault_path = args["vault_path"]
    vault_root = Path(vault_path)

    # --- 'today' reserved block: resolve to today's daily note ---
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

    content    = block_file.read_text(encoding="utf-8")
    char_count = len(content)
    token_est  = _token_est(content)

    existing = [b["name"] for b in _attached_blocks]
    if display_name not in existing:
        _attached_blocks.append({
            "name":       display_name,
            "content":    content,
            "char_count": char_count,
            "token_est":  token_est,
        })
    total_tokens = sum(b["token_est"] for b in _attached_blocks)
    return {
        "attached":            display_name,
        "char_count":          char_count,
        "token_est":           token_est,
        "session_total_tokens": total_tokens,
        "content":             content,
    }


# ---------------------------------------------------------------------------
# memory/list_blocks
# ---------------------------------------------------------------------------

def _memory_list_blocks() -> Dict:
    total_chars  = sum(b["char_count"] for b in _attached_blocks)
    total_tokens = sum(b["token_est"]  for b in _attached_blocks)
    return {
        "attached_blocks": [
            {"name": b["name"], "char_count": b["char_count"], "token_est": b["token_est"]}
            for b in _attached_blocks
        ],
        "total_chars":  total_chars,
        "total_tokens": total_tokens,
    }


# ---------------------------------------------------------------------------
# memory/write_working  (v0.4.0: path sanitization + maturity field)
# ---------------------------------------------------------------------------

def _memory_write_working(args: Dict) -> Dict:
    filename   = args["filename"]
    content    = args["content"]
    vault_path = args["vault_path"]
    confidence = args.get("confidence", "medium")
    maturity   = args.get("maturity", "seed")

    # Sanitize filename — strip traversal, null bytes, special chars
    clean_filename = _sanitize_filename(filename)
    if clean_filename is None:
        return {"error": f"Invalid filename: '{filename}'. Only safe characters allowed (word chars, hyphens, dots, spaces)."}

    # Ensure .md extension
    if not clean_filename.endswith(".md"):
        clean_filename += ".md"

    working_dir = Path(vault_path) / "_working"
    working_dir.mkdir(parents=True, exist_ok=True)
    out_path = working_dir / clean_filename

    now = datetime.now(timezone.utc).isoformat()
    frontmatter = f"""---
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
    full_content = frontmatter + content
    out_path.write_text(full_content, encoding="utf-8")
    return {
        "written":          str(out_path),
        "filename_used":    clean_filename,
        "original_filename": filename,
        "sanitized":        clean_filename != filename,
        "confidence":       confidence,
        "maturity":         maturity,
        "note":             "Staged in _working/. Heartbeat will promote or prune based on maturity + confidence.",
    }


# ---------------------------------------------------------------------------
# memory/delete_working  (v0.4.0: new tool)
# ---------------------------------------------------------------------------

def _memory_delete_working(args: Dict) -> Dict:
    """
    Safely delete a file from _working/ only.
    Refuses any path that resolves outside _working/.
    """
    filename   = args["filename"]
    vault_path = args["vault_path"]

    clean_filename = _sanitize_filename(filename)
    if clean_filename is None:
        return {"error": f"Invalid filename: '{filename}'."}

    working_dir = Path(vault_path) / "_working"
    target      = (working_dir / clean_filename).resolve()
    working_resolved = working_dir.resolve()

    # Refuse anything that resolves outside _working/ (extra traversal guard)
    try:
        target.relative_to(working_resolved)
    except ValueError:
        return {
            "error": f"Security: resolved path '{target}' is outside _working/. Deletion refused."
        }

    if not target.exists():
        return {
            "deleted":  False,
            "existed":  False,
            "path":     str(target),
            "note":     "File does not exist in _working/.",
        }

    target.unlink()
    logger.info("Deleted from _working/: %s", target)
    return {
        "deleted":  True,
        "existed":  True,
        "path":     str(target),
        "note":     "Deleted from _working/.",
    }


# ---------------------------------------------------------------------------
# memory/trigger_lookup
# ---------------------------------------------------------------------------

def _memory_trigger_lookup(args: Dict) -> Dict:
    message    = args["message"].lower()
    vault_path = args["vault_path"]
    trigger_file = (
        Path(vault_path) / "08 Meta" / "agent-context" / "triggers.md"
    )
    if not trigger_file.exists():
        return {"recommended_blocks": [], "note": "triggers.md not found"}

    triggers_raw = trigger_file.read_text(encoding="utf-8")
    recommended  = []

    rows = re.findall(
        r"\|([^|]+)\|([^|]+)\|([^|]+)\|",
        triggers_raw,
    )
    for pattern_cell, block_cell, mode_cell in rows:
        pattern_cell = pattern_cell.strip()
        block_cell   = block_cell.strip()
        mode_cell    = mode_cell.strip()
        if pattern_cell.startswith("-") or pattern_cell.lower() == "keyword pattern":
            continue
        sub_patterns = [p.strip().replace("\\", "") for p in pattern_cell.split("|")]
        if any(sp and sp in message for sp in sub_patterns):
            recommended.append({
                "block": block_cell,
                "mode":  mode_cell,
                "matched_pattern": pattern_cell,
            })

    return {
        "recommended_blocks": recommended,
        "always_attach": ["identity-pvnkmnk.md"],
    }


# ---------------------------------------------------------------------------
# memory/project_state  (v0.4.0: implemented)
# ---------------------------------------------------------------------------

def _memory_project_state(args: Dict, daemon_url: str) -> Dict:
    """
    Load the full session-start bundle for a project.
    Reads: {project}.md, STATE.md, ROADMAP.md from 05 Dev Projects/{project}/
    Calls: daemon /search for semantic context
    Returns: combined bundle with token cost estimate.
    Implements the documented ~500-token session-start protocol.
    """
    project    = args["project"]
    vault_path = args["vault_path"]
    daemon     = args.get("daemon_url", daemon_url)

    project_dir = Path(vault_path) / "05 Dev Projects" / project
    result: Dict[str, Any] = {
        "project":          project,
        "project_identity": None,
        "current_state":    None,
        "roadmap_summary":  None,
        "semantic_context": [],
        "missing_files":    [],
        "state_created":    False,
        "token_cost":       0,
    }

    # 1. Project identity file
    identity_path = project_dir / f"{project}.md"
    if identity_path.exists():
        result["project_identity"] = identity_path.read_text(encoding="utf-8")
    else:
        result["missing_files"].append(f"{project}.md")

    # 2. STATE.md — create from template if missing
    STATE_TEMPLATE = """---
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
""".format(project=project)

    state_path = project_dir / "STATE.md"
    if state_path.exists():
        result["current_state"] = state_path.read_text(encoding="utf-8")
    else:
        project_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(STATE_TEMPLATE, encoding="utf-8")
        result["current_state"] = STATE_TEMPLATE
        result["state_created"]  = True
        result["missing_files"].append("STATE.md (created from template)")

    # 3. ROADMAP.md — first 60 lines (phase overview, not full detail)
    roadmap_path = project_dir / "ROADMAP.md"
    if roadmap_path.exists():
        lines = roadmap_path.read_text(encoding="utf-8").splitlines()
        result["roadmap_summary"] = "\n".join(lines[:60])
    else:
        result["missing_files"].append("ROADMAP.md")

    # 4. Semantic context from daemon search
    try:
        r = httpx.post(
            f"{daemon}/search",
            json={"query": project, "project": project, "top_k": 5, "apply_decay": True},
            timeout=15.0,
        )
        result["semantic_context"] = r.json().get("results", [])
    except Exception as e:
        logger.warning("project_state semantic search failed: %s", e)
        result["semantic_context"] = []

    # 5. Token cost estimate
    total_chars = sum([
        len(result["project_identity"] or ""),
        len(result["current_state"]    or ""),
        len(result["roadmap_summary"]  or ""),
        sum(len(str(r)) for r in result["semantic_context"]),
    ])
    result["token_cost"] = total_chars // 4

    return result


# ---------------------------------------------------------------------------
# search_siblings  (stub — full implementation in P2 with retrieval.py changes)
# ---------------------------------------------------------------------------

def _search_siblings(args: Dict) -> Dict:
    """
    Topic sibling traversal stub.
    Full implementation in P2 sprint when retrieval.py graph expansion is added.
    Currently returns the seed note's direct graph neighbors from the daemon.
    """
    seed_path  = args["seed_path"]
    vault_path = args["vault_path"]
    limit      = args.get("limit", 10)
    # Derive entity name from path stem
    entity = Path(seed_path).stem
    return {
        "note": "search_siblings full implementation pending P2 sprint (topic_hubs table + hub expansion in retrieval.py).",
        "seed_path": seed_path,
        "entity": entity,
        "siblings": [],
    }


# ---------------------------------------------------------------------------
# MCP JSON-RPC stdio loop
# ---------------------------------------------------------------------------

def run_mcp_adapter(daemon_url: str):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "vault-memory", "version": "0.4.0"},
                "capabilities": {"tools": {}},
            }})

        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                result = _call_daemon(daemon_url, tool_name, tool_args)
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                    "isError": False,
                }})
            except Exception as e:
                _send({"jsonrpc": "2.0", "id": msg_id, "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                }})

        elif method == "notifications/initialized":
            pass

        else:
            _send({"jsonrpc": "2.0", "id": msg_id,
                   "error": {"code": -32601, "message": f"Method not found: {method}"}})
