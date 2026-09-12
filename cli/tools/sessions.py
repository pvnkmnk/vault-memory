# cli/tools/sessions.py
"""Session-related MCP tools: memory/session_register, memory/session_close, memory/session_cleanup."""

import httpx
from datetime import datetime, timezone
from typing import Any, Dict

from cli import mcp_client
from cli.mcp_client import get_session_id, set_session_id


# S31-2: structured close capture. One item shape, five buckets, so the schema
# stays readable instead of repeating five near-identical blocks.
_RECORD_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string", "description": "What happened, one sentence"},
        "entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional entity names this relates to",
        },
    },
    "required": ["content"],
}


def _record_field(description: str) -> dict:
    return {"type": "array", "items": _RECORD_ITEM_SCHEMA, "description": description}


def _record_properties() -> dict:
    """The five structured buckets accepted by memory/session_close."""
    return {
        "decisions": _record_field("Choices made and why"),
        "mistakes": _record_field("What went wrong"),
        "discoveries": _record_field("Things learned about this system"),
        "gotchas": _record_field("Traps the next session should avoid"),
        "workflows": _record_field("Sequences that worked"),
    }


RECORD_FIELDS = tuple(_record_properties().keys())


def _build_session_record(args: Dict) -> Dict:
    """Collect structured buckets out of the tool args, accepting bare strings."""
    record: Dict[str, list] = {}
    for name in RECORD_FIELDS:
        raw = args.get(name)
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        bucket = []
        for item in items:
            if isinstance(item, str) and item.strip():
                bucket.append({"content": item.strip()})
            elif isinstance(item, dict) and item.get("content"):
                bucket.append(item)
        if bucket:
            record[name] = bucket
    return record


TOOLS = [
    {
        "name": "memory/session_register",
        "description": "Register an agent session in the daemon session registry. Returns a session_id for use with session_close. Call at the start of each agent task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Agent identifier e.g. 'claude-code', 'opencode'",
                },
                "project": {"type": "string", "description": "Project slug"},
                "task": {
                    "type": "string",
                    "description": "Brief description of the task being worked on",
                },
                "vault_path": {"type": "string", "description": "Absolute path to vault root"},
                "plan_ref": {
                    "type": "string",
                    "description": "Optional: reference to plan file e.g. 'ROADMAP.md'",
                },
                "vault_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: list of vault paths relevant to this session",
                },
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": ["agent_name", "project", "task", "vault_path"],
        },
    },
    {
        "name": "memory/session_close",
        "description": "Close a registered agent session. Accepts session_id, or agent_name + project combo to look up the active session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID returned by session_register",
                },
                "agent_name": {
                    "type": "string",
                    "description": "Agent name (used if session_id not provided)",
                },
                "project": {
                    "type": "string",
                    "description": "Project slug (used if session_id not provided)",
                },
                # S31-2: optional structured capture, distilled later by the miner.
                **_record_properties(),
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": [],
        },
    },
    {
        "name": "memory/session_cleanup",
        "description": "Close stale agent sessions older than max_age_hours. Returns the list of closed session IDs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_age_hours": {
                    "type": "integer",
                    "description": "Age threshold in hours (default: 24)",
                    "default": 24,
                },
                "daemon_url": {
                    "type": "string",
                    "description": "Daemon URL (default: http://localhost:5051)",
                    "default": "http://localhost:5051",
                },
            },
            "required": [],
        },
    },
]


def _memory_session_register(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    payload = {
        "agent_name": args["agent_name"],
        "project": args["project"],
        "task": args["task"],
        "vault_path": args["vault_path"],
        "plan_ref": args.get("plan_ref"),
        "vault_paths": args.get("vault_paths", []),
    }
    try:
        r = httpx.post(
            f"{daemon_url}/sessions",
            json=payload,
            timeout=10.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        data = r.json()
        # S31-1: remember this session so every later daemon call (and every
        # local _working/ write) is attributed to it. Cleared on session_close.
        set_session_id(data.get("session_id"))
        return {
            "session_id": data.get("session_id"),
            "agent_name": payload["agent_name"],
            "project": payload["project"],
            "task": payload["task"],
            "started_at": data.get("started_at"),
            "note": "Session registered. Call memory/session_close when done.",
        }
    except Exception as e:
        return {"error": f"session_register failed: {e}", "payload_sent": payload}


def _memory_session_close(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    session_id = args.get("session_id")

    # Resolve session_id from agent_name + project if not provided
    if not session_id:
        agent_name = args.get("agent_name")
        project = args.get("project")
        if not agent_name or not project:
            return {"error": "Provide session_id, or both agent_name and project."}
        try:
            r = httpx.get(
                f"{daemon_url}/sessions",
                params={"agent_name": agent_name, "project": project, "status": "active"},
                timeout=10.0,
                headers=mcp_client._auth_headers,
            )
            r.raise_for_status()
            sessions = r.json().get("sessions", [])
            if not sessions:
                return {
                    "error": f"No active session found for agent={agent_name} project={project}."
                }
            session_id = sessions[0]["session_id"]
        except Exception as e:
            return {"error": f"session_close lookup failed: {e}"}

    # S31-2: attach the structured capture when the caller supplied any of the
    # five buckets; freeform-only closes keep sending just notes.
    payload = {
        "status": "closed",
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }
    record = _build_session_record(args)
    if record:
        payload["session_record"] = record

    try:
        r = httpx.patch(
            f"{daemon_url}/sessions/{session_id}",
            json=payload,
            timeout=10.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        data = r.json()
        # S31-1: stop attributing further writes to a session that just closed.
        if get_session_id() == session_id:
            set_session_id(None)
        return {
            "session_id": session_id,
            "status": "closed",
            "started_at": data.get("started_at"),
            "closed_at": data.get("closed_at"),
            "duration_s": data.get("duration_s"),
            "record_items": data.get("session_record_items", 0),
            # NULL mined_at = this session is now sitting in the mining queue.
            "mined_at": data.get("mined_at"),
            "note": "Session closed successfully.",
        }
    except Exception as e:
        return {"error": f"session_close PATCH failed: {e}", "session_id": session_id}


def _memory_session_cleanup(args: Dict, daemon_url: str) -> Dict:
    daemon_url = args.get("daemon_url", daemon_url)
    max_age_hours = args.get("max_age_hours", 24)
    try:
        r = httpx.post(
            f"{daemon_url}/sessions/cleanup",
            json={"max_age_hours": max_age_hours},
            timeout=10.0,
            headers=mcp_client._auth_headers,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "closed": data.get("closed", 0),
            "session_ids": data.get("session_ids", []),
            "max_age_hours": max_age_hours,
            "note": "Stale sessions closed successfully.",
        }
    except Exception as e:
        return {"error": f"session_cleanup failed: {e}", "max_age_hours": max_age_hours}


def get_tools() -> list:
    """Return the session tool definitions."""
    return TOOLS
