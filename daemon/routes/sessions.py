# daemon/routes/sessions.py
"""Session management route handlers."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.sessions import (
    SessionRegisterRequest,
    SessionPatchRequest,
    SessionCleanupRequest,
    SessionLogRequest,
)
from daemon.helpers.responses import server_error, not_found, bad_request
from daemon.helpers.validation import _canonicalize_vault_root
from daemon.helpers.attribution import log_file_action, resolve_session

logger = logging.getLogger("vault-memoryd")

sessions_router = APIRouter()


@sessions_router.post("/sessions", status_code=201)
async def session_register(
    req: SessionRegisterRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Register a new agent session."""
    try:
        with deps.postgres.cursor() as cursor:
            now = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO agent_sessions
                    (agent_name, project, task, vault_path, plan_ref, vault_paths, status, started_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)
                RETURNING id AS session_id, started_at
                """,
                (
                    req.agent_name,
                    req.project,
                    req.task,
                    req.vault_path,
                    req.plan_ref,
                    req.vault_paths or [],
                    now,
                ),
            )
            row = cursor.fetchone()
        return {
            "session_id": str(row["session_id"]),
            "agent_name": req.agent_name,
            "project": req.project,
            "task": req.task,
            "started_at": row["started_at"].isoformat(),
            "status": "active",
        }
    except Exception as e:
        logger.error("session_register error: %s", e)
        return server_error(
            "Failed to register session", code="SESSION_CREATE_FAILED"
        )


@sessions_router.get("/sessions")
async def session_list(
    agent_name: Optional[str] = None,
    project: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Query agent sessions with filters."""
    try:
        clauses = []
        params: list = []
        if agent_name:
            clauses.append("agent_name = %s")
            params.append(agent_name)
        if project:
            clauses.append("project = %s")
            params.append(project)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM agent_sessions {where} ORDER BY started_at DESC LIMIT %s",
                params,
            )
            rows = cursor.fetchall()

        sessions = []
        for r in rows:
            sessions.append({
                "session_id": str(r["id"]),
                "agent_name": r["agent_name"],
                "project": r["project"],
                "task": r["task"],
                "status": r["status"],
                "started_at": r["started_at"].isoformat() if r["started_at"] else None,
                "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None,
            })
        return {"sessions": sessions, "count": len(sessions)}
    except Exception as e:
        logger.error("session_list error: %s", e)
        return server_error(
            "Failed to list sessions", code="SESSION_LIST_FAILED"
        )


@sessions_router.patch("/sessions/{session_id}")
async def session_patch(
    session_id: str,
    req: SessionPatchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Update an agent session."""
    try:
        updates = {}
        if req.status is not None:
            updates["status"] = req.status
        if req.closed_at is not None:
            updates["closed_at"] = req.closed_at
        if req.notes is not None:
            updates["notes"] = req.notes
        if req.session_record is not None:
            # Stored as a JSON string: Postgres casts text -> jsonb on insert,
            # and the SQLite (lite mode) backend keeps it as TEXT. One code
            # path, both backends.
            updates["session_record"] = req.session_record.model_dump_json()

        if not updates:
            return {"error": "No fields to update"}

        # S31-3: a session that is still open cannot have been mined, and one
        # that reopens must be re-mined — keep the queue honest.
        if req.status is not None and req.status != "closed":
            updates["mined_at"] = None

        set_clause = ", ".join(f"{k} = %s" for k in updates.keys())
        values = list(updates.values())

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"UPDATE agent_sessions SET {set_clause} WHERE id = %s RETURNING id",
                values + [session_id],
            )
            row = cursor.fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

        result = {"session_id": session_id, "updated": True, "fields": sorted(updates)}
        if req.session_record is not None:
            result["session_record_items"] = req.session_record.total_items()
            result["mined_at"] = None  # pending mining
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("session_patch error: %s", e)
        return server_error(
            "Failed to update session", code="SESSION_UPDATE_FAILED"
        )


@sessions_router.post("/sessions/mine")
async def sessions_mine(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
    limit: int = 5,
):
    """S31-3: distil closed sessions into lesson drafts (drains the queue once)."""
    if deps.settings.lite_mode:
        return bad_request(
            "Session mining requires Postgres and an LLM provider",
            code="MINING_UNAVAILABLE",
        )

    from daemon import miner

    try:
        vault_root = _canonicalize_vault_root(deps.settings.vault_path)
    except Exception:
        return bad_request("Invalid vault path", code="INVALID_VAULT_PATH")

    return await miner.mine_once(deps, vault_root, limit=max(1, min(int(limit), 50)))


@sessions_router.post("/sessions/cleanup")
async def sessions_cleanup(
    req: SessionCleanupRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Close stale sessions older than max_age_hours (default 24 hours)."""
    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_sessions
                SET status = 'closed', closed_at = now()
                WHERE status = 'active'
                AND started_at < now() - (%s || ' hours')::interval
                RETURNING id
                """,
                (req.max_age_hours,),
            )
            rows = cursor.fetchall()
        return {"closed": len(rows), "session_ids": [str(r["id"]) for r in rows]}
    except Exception as e:
        logger.error("sessions_cleanup error: %s", e)
        return server_error(
            "Session cleanup failed", code="SESSION_CLEANUP_FAILED"
        )


@sessions_router.get("/sessions/{session_id}/attribution")
async def session_attribution(
    session_id: str,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Get attribution data for a session — files created/modified/promoted.

    S31-1: reads ``sync_log``, which write endpoints append to whenever a
    request carries an ``X-Session-Id`` header.
    """
    if not await asyncio.to_thread(resolve_session, deps, session_id):
        return not_found("Session", session_id)

    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                SELECT file_path, action, agent_name, created_at
                FROM sync_log
                WHERE session_id = %s
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (session_id,),
            )
            rows = cursor.fetchall()

        actions = [
            {
                "file_path": r["file_path"],
                "action": r["action"],
                "agent_name": r.get("agent_name") if isinstance(r, dict) else None,
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
        by_action: dict = {}
        for entry in actions:
            by_action[entry["action"]] = by_action.get(entry["action"], 0) + 1

        return {
            "session_id": session_id,
            "source": "sync_log",
            "actions": actions,
            "by_action": by_action,
            "count": len(actions),
        }
    except Exception as e:
        logger.error("session_attribution error: %s", e)
        return server_error(
            "Attribution query failed", code="ATTRIBUTION_FAILED"
        )


@sessions_router.post("/sessions/{session_id}/log", status_code=201)
async def session_log(
    session_id: str,
    req: SessionLogRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Record one attributed file touch for a session.

    The MCP adapter calls this after a local write (``memory/write_working``,
    ``memory/delete_working``) so those touches land in ``sync_log`` alongside
    the daemon-side writes that carry the header directly.
    """
    session = await asyncio.to_thread(resolve_session, deps, session_id)
    if not session:
        return not_found("Session", session_id)

    logged = await asyncio.to_thread(
        log_file_action, deps, session, req.file_path, req.action
    )
    if not logged:
        return server_error("Failed to record attribution", code="ATTRIBUTION_LOG_FAILED")
    return {
        "session_id": session_id,
        "file_path": req.file_path,
        "action": req.action,
        "logged": True,
    }
