# cli/tools/sessions.py
"""Session-related MCP tools: memory/session_register, memory/session_close."""

import httpx
from datetime import datetime, timezone
from typing import Any, Dict

from cli.mcp_client import _auth_headers

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
        r = httpx.post(f"{daemon_url}/sessions", json=payload, timeout=10.0, headers=_auth_headers)
        r.raise_for_status()
        data = r.json()
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
                headers=_auth_headers,
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

    try:
        r = httpx.patch(
            f"{daemon_url}/sessions/{session_id}",
            json={"status": "closed", "closed_at": datetime.now(timezone.utc).isoformat()},
            timeout=10.0,
            headers=_auth_headers,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "session_id": session_id,
            "status": "closed",
            "started_at": data.get("started_at"),
            "closed_at": data.get("closed_at"),
            "duration_s": data.get("duration_s"),
            "note": "Session closed successfully.",
        }
    except Exception as e:
        return {"error": f"session_close PATCH failed: {e}", "session_id": session_id}


def get_tools() -> list:
    """Return the session tool definitions."""
    return TOOLS
