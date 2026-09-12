# daemon/routes/ingest.py
"""S32-1 human ingestion inbox routes.

Source paths are confined to the vault on purpose: the daemon holds an API key
and must not double as an arbitrary file-read (or local-network fetch) shim for
whoever presents it. The CLI copies an outside file into ``inbox/`` first, which
is also a better UX than silently archiving a file the human cannot see.

A source that collides with a high-trust page comes back in ``conflicts`` and is
never overwritten.
"""

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends

from daemon import ingest
from daemon.auth import verify_api_key
from daemon.dependencies import Dependencies, get_dependencies
from daemon.helpers.responses import bad_request, server_error
from daemon.helpers.validation import _canonicalize_vault_root
from daemon.models.ingest import IngestRequest, InboxRequest

logger = logging.getLogger("vault-memoryd")

ingest_router = APIRouter()


def _vault_root(deps: Dependencies):
    try:
        root = _canonicalize_vault_root(deps.settings.vault_path)
    except Exception:
        return None, bad_request("Invalid vault path", code="INVALID_VAULT_PATH")
    if not root.is_dir():
        return None, bad_request("Vault path does not exist", code="INVALID_VAULT_PATH")
    return root, None


def validate_vault_relative(root: Path, rel: str) -> str:
    """Check a vault-relative path and return it, still relative.

    Returns the *relative* form on purpose: the ingest pipeline takes relative
    paths only (``daemon.ingest.resolve_local_source`` re-validates every
    component and refuses absolutes), so converting to an absolute path here
    would only create a second, weaker containment rule to keep in sync.

    The path is rebuilt from components that each have to be their own basename
    (:func:`daemon.ingest.safe_relative_parts`), and the realpath is prefix-
    checked afterwards — so a symlink pointing out of the vault is caught too.
    """
    root_path = Path(root).resolve()
    if Path(rel).is_absolute():
        raise ValueError("path must be relative to the vault")

    try:
        parts = ingest.safe_relative_parts(rel)
    except ingest.IngestError as e:
        raise ValueError(str(e))

    normalized = "/".join(parts)
    try:
        real = root_path.joinpath(*parts).resolve()
    except OSError:
        raise FileNotFoundError("source file not found")
    if not str(real).startswith(str(root_path) + os.path.sep):
        raise ValueError("path is outside the configured vault")
    if not real.is_file():
        raise FileNotFoundError("source file not found")
    return normalized


@ingest_router.post("/ingest", status_code=201)
async def ingest_source(
    req: IngestRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Ingest one path, URL, or pasted text into the knowledge base."""
    root, error = _vault_root(deps)
    if error:
        return error

    value = ""
    if req.path:
        try:
            value = validate_vault_relative(root, req.path)
        except ValueError as e:
            logger.info("rejected ingest path: %s", e)
            return bad_request("path is outside the configured vault", code="UNAUTHORIZED_PATH")
        except FileNotFoundError:
            return bad_request("source file not found", code="SOURCE_NOT_FOUND")
    elif req.url:
        value = req.url

    try:
        result = await ingest.ingest(
            deps, root, value, text=req.text, force=req.force
        )
    except Exception:
        logger.exception("ingest failed")
        return server_error("Ingest failed", code="INGEST_FAILED")

    if result.get("status") == "failed":
        return bad_request(result.get("error") or "Ingest failed", code=result.get("code") or "INGEST_FAILED")
    return result


@ingest_router.post("/ingest/inbox")
async def ingest_inbox_route(
    req: InboxRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Drain the ``inbox/`` directory."""
    root, error = _vault_root(deps)
    if error:
        return error

    try:
        return await ingest.ingest_inbox(
            deps,
            root,
            limit=req.limit,
            force=req.force,
            remove_after=req.remove,
        )
    except Exception:
        logger.exception("inbox ingest failed")
        return server_error("Inbox ingest failed", code="INGEST_FAILED")


@ingest_router.get("/ingest/manifest")
async def ingest_manifest(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """What has been ingested, keyed by source reference (the delta index)."""
    root, error = _vault_root(deps)
    if error:
        return error

    manifest = await asyncio.to_thread(ingest.read_manifest, root)
    sources = manifest.get("sources", {})
    pending = await asyncio.to_thread(ingest.inbox_sources, root)
    return {
        "count": len(sources),
        "sources": sources,
        "inbox_pending": [
            {"file": str(p.relative_to(root)), "already_ingested": False} for p in pending
        ],
    }
