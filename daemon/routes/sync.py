# daemon/routes/sync.py
"""Sync-related route handlers."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.sync import SyncFileRequest, SyncBatchRequest, SyncDeltaRequest
from daemon.helpers.responses import bad_request, server_error
from daemon.helpers.validation import _safe_vault_path

logger = logging.getLogger("vault-memoryd")

sync_router = APIRouter()


class _SyncSocketHub:
    """Track websocket clients for lightweight sync event streaming."""

    def __init__(self):
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self._clients.discard(ws)

    async def broadcast(self, payload: dict):
        stale: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


_sync_hub = _SyncSocketHub()


async def _publish_sync_event(event: str, payload: dict):
    await _sync_hub.broadcast({"event": event, **payload})


@sync_router.post("/sync/file")
async def sync_file(
    req: SyncFileRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Sync a single file to the vault."""
    vault_root = Path(deps.settings.vault_path).resolve()
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
            await _publish_sync_event(
                "sync.file.completed",
                {"file_path": req.file_path, "status": "synced", "chunks": result},
            )
            return {"file_path": req.file_path, "status": "synced", "result": result}
        except Exception:
            await _publish_sync_event(
                "sync.file.failed",
                {"file_path": req.file_path, "status": "failed"},
            )
            return server_error("Sync failed", code="SYNC_FAILED")
    else:
        return server_error("Sync engine not available", code="SYNC_ENGINE_UNAVAILABLE")


@sync_router.post("/sync")
async def sync_batch(
    req: SyncBatchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Sync a batch of files from the vault."""
    vault_root = Path(deps.settings.vault_path).resolve()
    if req.vault_path and req.vault_path != str(vault_root):
        return bad_request("vault_path must match configured vault", code="UNAUTHORIZED_PATH")

    watcher = deps.watcher
    if not watcher or not watcher.engine:
        return server_error("Sync engine not available", code="SYNC_ENGINE_UNAVAILABLE")

    synced = 0
    failed = 0
    errors: list[str] = []
    for rel_path in req.paths:
        try:
            abs_path = _safe_vault_path(vault_root, rel_path)
            if not abs_path.exists():
                failed += 1
                errors.append(f"{rel_path}: file not found")
                await _publish_sync_event(
                    "sync.file.failed",
                    {"file_path": rel_path, "status": "failed", "reason": "file_not_found"},
                )
                continue
            chunk_count = await watcher.engine.sync_file(abs_path, caller="user")
            synced += 1
            await _publish_sync_event(
                "sync.file.completed",
                {"file_path": rel_path, "status": "synced", "chunks": chunk_count},
            )
        except Exception as e:
            failed += 1
            errors.append(f"{rel_path}: {e}")
            await _publish_sync_event(
                "sync.file.failed",
                {"file_path": rel_path, "status": "failed"},
            )

    await _publish_sync_event(
        "sync.batch.completed",
        {"status": "completed", "synced": synced, "failed": failed, "total": len(req.paths)},
    )
    return {"synced": synced, "failed": failed, "errors": errors}


@sync_router.post("/sync/delta")
async def sync_delta(
    req: SyncDeltaRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Get incremental sync changes since a timestamp."""
    vault_root = Path(deps.settings.vault_path).resolve()
    if req.vault_path and req.vault_path != str(vault_root):
        return bad_request("vault_path must match configured vault", code="UNAUTHORIZED_PATH")
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


@sync_router.websocket("/sync/ws")
async def sync_ws(websocket: WebSocket):
    """Realtime sync event stream over WebSocket."""
    expected_key = os.environ.get("VAULT_MEMORY_API_KEY")
    provided_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
    if expected_key and provided_key != expected_key:
        await websocket.close(code=4401, reason="Invalid API key")
        return

    await _sync_hub.connect(websocket)
    await websocket.send_json({"event": "sync.ws.connected", "status": "ok"})
    try:
        while True:
            # Keep connection open and allow optional client pings/messages.
            _ = await websocket.receive_text()
    except WebSocketDisconnect:
        _sync_hub.disconnect(websocket)
