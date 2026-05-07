# daemon/routes/sessions.py
"""Session management route handlers."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.sessions import SessionRegisterRequest, SessionPatchRequest
from daemon.helpers.responses import server_error

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
            "Failed to register session", code="SESSION_CREATE_FAILED", detail=str(e)
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
        return server_error(
            "Failed to list sessions", code="SESSION_LIST_FAILED", detail=str(e)
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

        if not updates:
            return {"error": "No fields to update"}

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

        return {"session_id": session_id, "updated": True}
    except HTTPException:
        raise
    except Exception as e:
        return server_error(
            "Failed to update session", code="SESSION_UPDATE_FAILED", detail=str(e)
        )


@sessions_router.post("/sessions/cleanup")
async def sessions_cleanup(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Close stale sessions older than 24 hours."""
    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                UPDATE agent_sessions
                SET status = 'closed', closed_at = now()
                WHERE status = 'active'
                AND started_at < now() - interval '24 hours'
                RETURNING id
                """
            )
            rows = cursor.fetchall()
        return {"closed": len(rows), "session_ids": [str(r["id"]) for r in rows]}
    except Exception as e:
        return server_error(
            "Session cleanup failed", code="SESSION_CLEANUP_FAILED", detail=str(e)
        )


@sessions_router.get("/sessions/{session_id}/attribution")
async def session_attribution(
    session_id: str,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Get attribution data for a session — files created/modified."""
    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                SELECT file_path, action, created_at
                FROM sync_log
                WHERE session_id = %s
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (session_id,),
            )
            rows = cursor.fetchall()

        return {
            "session_id": session_id,
            "actions": [
                {
                    "file_path": r["file_path"],
                    "action": r["action"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
            "count": len(rows),
        }
    except Exception as e:
        return server_error(
            "Attribution query failed", code="ATTRIBUTION_FAILED", detail=str(e)
        )
