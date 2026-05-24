# daemon/routes/bulk.py
"""Bulk operation route handlers."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.bulk import BulkImportRequest, BulkExportRequest, BulkDeleteRequest, BulkQueueRequest
from daemon.helpers.responses import bad_request, server_error
from daemon.helpers.validation import (
    _canonicalize_vault_root,
    _safe_vault_path,
    _slugify_filename,
    _parse_iso_date,
)
from daemon.helpers.streaming import _export_stream_generator
from daemon.sync_watcher import MarkdownParser

logger = logging.getLogger("vault-memoryd")

bulk_router = APIRouter()

# In-memory bulk job store (S30-9 will add persistence)
_bulk_jobs: dict = {}
_bulk_job_lock = asyncio.Lock()


async def _process_bulk_job(job_id: str, notes: list, project: str, vault_root: Path, skip_duplicates: bool):
    """Background task to process bulk import job."""
    job = _bulk_jobs[job_id]
    job["status"] = "processing"
    job["started_at"] = datetime.now(timezone.utc).isoformat()

    imported = 0
    failed = 0
    errors = []

    for i, note in enumerate(notes):
        if job.get("cancelled"):
            job["status"] = "cancelled"
            job["done"] = imported
            job["failed"] = failed
            return

        try:
            content = (note.get("content") or "").strip()
            if not content:
                errors.append({"index": i, "error": "content is empty"})
                failed += 1
                continue

            title = (note.get("title") or f"bulk-note-{i + 1}").strip()
            tags = note.get("tags") or []
            metadata = note.get("metadata") or {}
            filename = f"{_slugify_filename(title)}.md"
            abs_path = _safe_vault_path(vault_root, str(Path(project) / filename))

            fm_lines = ["---"]
            if tags:
                fm_lines.append("tags:")
                for t in tags:
                    fm_lines.append(f"  - {str(t)}")
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    fm_lines.append(f"{k}: {v}")
            fm_lines.append("---")
            file_content = "\n".join(fm_lines) + "\n\n" + content + "\n"

            if skip_duplicates and abs_path.exists():
                existing = abs_path.read_text(encoding="utf-8", errors="replace")
                if existing == file_content:
                    continue

            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(file_content, encoding="utf-8")
            imported += 1
        except Exception:
            errors.append({"index": i, "error": "failed to write note"})
            failed += 1

        job["done"] = imported
        job["failed"] = failed

    job["status"] = "done"
    job["done"] = imported
    job["failed"] = failed
    job["errors"] = errors
    job["completed_at"] = datetime.now(timezone.utc).isoformat()


async def _cleanup_old_jobs():
    """Remove old completed jobs to cap memory usage."""
    async with _bulk_job_lock:
        terminal_jobs = {
            k: v for k, v in _bulk_jobs.items()
            if v["status"] in ("done", "cancelled", "failed")
        }
        if len(terminal_jobs) > 100:
            sorted_jobs = sorted(
                terminal_jobs.items(),
                key=lambda x: x[1].get("completed_at", "")
            )
            for job_id, _ in sorted_jobs[:len(terminal_jobs) - 100]:
                del _bulk_jobs[job_id]


@bulk_router.post("/bulk/import", status_code=201)
async def bulk_import(
    req: BulkImportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = _canonicalize_vault_root(deps.settings.vault_path)
    project_dir = req.project or "Bulk Import"
    imported = 0
    skipped = 0
    errors = []
    written_paths = []

    try:
        target_dir = _safe_vault_path(vault_root, project_dir)
    except Exception:
        return bad_request("Invalid project path", code="INVALID_PROJECT_PATH")

    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for i, note in enumerate(req.notes):
        try:
            content = (note.get("content") or "").strip()
            if not content:
                errors.append({"index": i, "error": "content is empty"})
                continue

            title = (note.get("title") or f"bulk-note-{timestamp}-{i + 1}").strip()
            tags = note.get("tags") or []
            metadata = note.get("metadata") or {}
            filename = f"{_slugify_filename(title)}.md"
            abs_path = target_dir / filename

            fm_lines = ["---"]
            if tags:
                fm_lines.append("tags:")
                for t in tags:
                    fm_lines.append(f"  - {str(t)}")
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    fm_lines.append(f"{k}: {v}")
            fm_lines.append("---")
            file_content = "\n".join(fm_lines) + "\n\n" + content + "\n"

            if req.skip_duplicates and abs_path.exists():
                existing = abs_path.read_text(encoding="utf-8", errors="replace")
                if existing == file_content:
                    skipped += 1
                    continue

            abs_path.write_text(file_content, encoding="utf-8")
            watcher = deps.watcher
            if watcher and watcher.engine:
                await watcher.engine.sync_file(abs_path, caller="user")
            else:
                logger.warning("bulk_import: watcher not running, skipping sync for %s", abs_path)
            imported += 1
            try:
                written_paths.append(str(abs_path.relative_to(vault_root)))
            except ValueError:
                written_paths.append(str(abs_path))
        except Exception:
            errors.append({"index": i, "error": "failed to import note"})

    return {
        "imported": imported,
        "skipped": skipped,
        "total": len(req.notes),
        "errors": errors,
        "paths": written_paths,
        "project": project_dir,
    }


@bulk_router.post("/bulk/queue", status_code=202)
async def queue_bulk_import(
    req: BulkQueueRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Queue a bulk import job. Returns job_id immediately."""
    job_id = str(uuid.uuid4())
    vault_root = _canonicalize_vault_root(deps.settings.vault_path)

    try:
        target_dir = _safe_vault_path(vault_root, req.project)
    except Exception:
        return bad_request("Invalid project path", code="INVALID_PROJECT_PATH")

    async with _bulk_job_lock:
        _bulk_jobs[job_id] = {
            "status": "queued",
            "total": len(req.notes),
            "done": 0,
            "failed": 0,
            "started_at": None,
            "completed_at": None,
            "project": req.project,
            "callback_url": req.callback_url,
            "errors": [],
        }

    asyncio.create_task(_process_bulk_job(job_id, req.notes, req.project, vault_root, req.skip_duplicates))
    return {"job_id": job_id, "status": "queued", "total": len(req.notes)}


@bulk_router.get("/bulk/status/{job_id}")
async def get_bulk_status(job_id: str, _auth: str = Depends(verify_api_key)):
    """Get status of a queued bulk import job."""
    async with _bulk_job_lock:
        job = _bulk_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    total = job["total"]
    done = job["done"]
    progress_pct = round(done / total * 100, 1) if total > 0 else 0

    return {
        "job_id": job_id,
        "status": job["status"],
        "total": total,
        "done": done,
        "failed": job["failed"],
        "progress_pct": progress_pct,
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "project": job.get("project"),
        "errors": job.get("errors", [])[:10],
    }


@bulk_router.delete("/bulk/cancel/{job_id}")
async def cancel_bulk_job(job_id: str, _auth: str = Depends(verify_api_key)):
    """Cancel a queued or processing bulk import job."""
    async with _bulk_job_lock:
        job = _bulk_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if job["status"] in ("done", "cancelled", "failed"):
            return {"job_id": job_id, "status": job["status"], "note": "Job already completed or cancelled"}

        job["cancelled"] = True
        job["status"] = "cancelling"

    return {"job_id": job_id, "status": "cancelling"}


@bulk_router.post("/bulk/export")
async def bulk_export(
    req: BulkExportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = _canonicalize_vault_root(deps.settings.vault_path)
    parser = MarkdownParser()
    from_date = _parse_iso_date(req.date_from)
    to_date = _parse_iso_date(req.date_to)
    entity_paths = None

    if req.entity:
        try:
            with deps.postgres.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT vault_path FROM vault_entity_links WHERE entity_id::text = %s",
                    (req.entity,),
                )
                entity_paths = {row["vault_path"] for row in cursor.fetchall()}
        except Exception:
            return server_error("Bulk export failed", code="BULK_EXPORT_FAILED")

    if getattr(req, "stream", False):
        return StreamingResponse(
            _export_stream_generator(vault_root, req, parser, from_date, to_date, entity_paths),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="vault-export.ndjson"'},
        )

    notes = []
    for path in vault_root.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue
        try:
            rel_path = str(path.relative_to(vault_root))
            if req.project and not rel_path.startswith(req.project):
                continue
            if entity_paths is not None and rel_path not in entity_paths:
                continue

            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if from_date and mtime < from_date:
                continue
            if to_date and mtime > to_date:
                continue

            parsed = await parser.parse(path, caller="user")
            if req.tags:
                tags = set(parsed.get("tags") or [])
                if not tags.intersection(set(req.tags)):
                    continue

            notes.append({
                "id": rel_path,
                "title": path.stem,
                "content": parsed.get("body", ""),
                "project": parsed.get("project"),
                "tags": parsed.get("tags") or [],
                "metadata": {
                    "status": parsed.get("status"),
                    "trust": parsed.get("trust"),
                    "maturity": parsed.get("maturity"),
                    "importance": parsed.get("importance"),
                },
                "created_at": parsed.get("date_created"),
                "modified_at": parsed.get("date_modified"),
            })
            if len(notes) >= req.limit:
                break
        except Exception:
            continue

    return {
        "notes": notes,
        "count": len(notes),
        "filters": {
            "project": req.project,
            "tags": req.tags,
            "entity": req.entity,
            "date_from": req.date_from,
            "date_to": req.date_to,
            "limit": req.limit,
        },
    }


@bulk_router.post("/bulk/delete", status_code=200)
async def bulk_delete(
    req: BulkDeleteRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    vault_root = _canonicalize_vault_root(deps.settings.vault_path)
    deleted = 0
    not_found = []
    errors = []

    for note_path in req.paths:
        try:
            abs_path = _safe_vault_path(vault_root, note_path)
            if not abs_path.exists():
                not_found.append(note_path)
                continue
            abs_path.unlink()
            watcher = deps.watcher
            if watcher and watcher.engine:
                await watcher.engine.delete_file(abs_path)
            else:
                logger.warning("bulk_delete: watcher not running, skipping delete for %s", abs_path)
            deleted += 1
        except FileNotFoundError:
            not_found.append(note_path)
        except ValueError:
            errors.append({"path": note_path, "error": "Invalid or forbidden path"})
        except Exception:
            errors.append({"path": note_path, "error": "delete failed"})

    return {"deleted": deleted, "not_found": not_found, "errors": errors, "total_requested": len(req.paths)}
