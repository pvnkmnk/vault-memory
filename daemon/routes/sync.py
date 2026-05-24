# daemon/routes/sync.py
"""Sync-related route handlers."""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.sync import SyncFileRequest, SyncDeltaRequest
from daemon.helpers.responses import bad_request, server_error
from daemon.helpers.validation import (
    _canonicalize_vault_root,
    _safe_vault_path,
    _validate_requested_vault_root,
)

logger = logging.getLogger("vault-memoryd")

sync_router = APIRouter()


@sync_router.post("/sync/file")
async def sync_file(
    req: SyncFileRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Sync a single file to the vault."""
    vault_root = _canonicalize_vault_root(deps.settings.vault_path)
    try:
        abs_path = _safe_vault_path(vault_root, req.file_path)
    except Exception:
        return bad_request("Invalid file path", code="INVALID_FILE_PATH")

    if not abs_path.exists():
        return bad_request("File does not exist", code="FILE_NOT_FOUND")

    watcher = deps.watcher
    if watcher and watcher.engine:
        try:
            result = await watcher.engine.sync_file(abs_path, caller="user")
            return {"file_path": req.file_path, "status": "synced", "result": result}
        except Exception:
            return server_error("Sync failed", code="SYNC_FAILED")
    else:
        return server_error("Sync engine not available", code="SYNC_ENGINE_UNAVAILABLE")


@sync_router.post("/sync/delta")
async def sync_delta(
    req: SyncDeltaRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Get incremental sync changes since a timestamp."""
    vault_root = _canonicalize_vault_root(deps.settings.vault_path)
    request_error = _validate_requested_vault_root(req.vault_path, vault_root)
    if request_error:
        return request_error
    try:
        since = datetime.fromisoformat(req.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    except ValueError:
        return bad_request("Invalid since timestamp", code="INVALID_TIMESTAMP")

    try:
        with deps.postgres.cursor() as cursor:
            cursor.execute(
                """
                SELECT file_path, content_hash, modified_at, is_deleted
                FROM sync_state
                WHERE vault_path = %s
                AND modified_at > %s
                ORDER BY modified_at ASC
                LIMIT %s
                """,
                (str(vault_root), since, req.limit),
            )
            rows = cursor.fetchall()

        changed = []
        deleted = []
        for r in rows:
            entry = {
                "file_path": r["file_path"],
                "content_hash": r["content_hash"],
                "modified_at": r["modified_at"].isoformat() if r["modified_at"] else None,
            }
            if r.get("is_deleted"):
                deleted.append(entry)
            else:
                changed.append(entry)

        next_cursor = None
        if len(rows) >= req.limit:
            next_cursor = rows[-1]["modified_at"].isoformat() if rows[-1]["modified_at"] else None

        return {
            "changed": changed,
            "deleted": deleted,
            "total": len(changed) + len(deleted),
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
        }
    except Exception as e:
        logger.error("sync_delta error: %s", e)
        return server_error("Delta sync failed", code="DELTA_SYNC_FAILED")
