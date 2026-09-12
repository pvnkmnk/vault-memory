# daemon/routes/lessons.py
"""S31-4 lesson review gate routes.

Nothing the miner produces reaches ``lessons/`` without a human (or an explicit
auto-promote policy) saying so. Rejection reasons are persisted, not discarded:
they are injected into the next mining prompt for the same project.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request

from daemon.auth import verify_api_key
from daemon.dependencies import Dependencies, get_dependencies
from daemon.helpers.responses import bad_request, not_found, server_error
from daemon.helpers.validation import _canonicalize_vault_root
from daemon.models.lessons import LessonPromoteRequest, LessonRejectRequest

logger = logging.getLogger("vault-memoryd")

lessons_router = APIRouter()


def _vault_root(deps: Dependencies):
    """``(root, None)`` or ``(None, error_response)``."""
    try:
        return _canonicalize_vault_root(deps.settings.vault_path), None
    except Exception:
        return None, bad_request("Invalid vault path", code="INVALID_VAULT_PATH")


def _draft_payload(draft) -> dict:
    return {
        "name": draft.rel_path,
        "slug": draft.slug,
        "title": draft.title,
        "kind": draft.kind,
        "project": draft.project,
        "review": draft.review,
        "corroboration": draft.corroboration,
        "sessions": draft.sessions,
        "entities": draft.entities,
    }


@lessons_router.get("/lessons/review")
async def list_lesson_review(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
    review: str = "pending",
    project: str = None,
):
    """The review queue: drafts awaiting a human decision (default ``pending``)."""
    from daemon import lessons

    root, error = _vault_root(deps)
    if error:
        return error

    wanted = None if review in ("", "all") else review
    drafts = await asyncio.to_thread(
        lessons.list_drafts, root, review=wanted, project=project
    )
    return {
        "review": review,
        "project": project,
        "count": len(drafts),
        "drafts": [_draft_payload(d) for d in drafts],
        "auto_promote_policy": lessons.auto_promote_policy(),
    }


@lessons_router.get("/lessons")
async def list_lessons(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
    project: str = None,
    top_k: int = 5,
    token_budget: int = None,
):
    """Ranked promoted lessons for a project — the learning-context primitive.

    Ranked by recency x corroboration x trust. ``token_budget`` bounds the
    combined body size; the list itself is never silently cut.
    """
    from daemon import lessons

    root, error = _vault_root(deps)
    if error:
        return error

    ranked = await asyncio.to_thread(
        lessons.rank_lessons,
        root,
        project=project,
        top_k=max(1, min(int(top_k), 50)),
        token_budget=token_budget,
    )
    return {
        "project": project,
        "count": len(ranked),
        "token_budget": lessons.DEFAULT_LESSON_TOKENS if token_budget is None else token_budget,
        "tokens_used": sum(item["tokens"] for item in ranked),
        "lessons": ranked,
    }


@lessons_router.post("/lessons/promote", status_code=201)
async def promote_lesson(
    req: LessonPromoteRequest,
    request: Request,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Accept a mined draft into ``lessons/`` (maturity seed → sapling)."""
    from daemon import lessons
    from daemon.helpers import attribution

    root, error = _vault_root(deps)
    if error:
        return error

    result = await asyncio.to_thread(
        lessons.promote_draft, root, req.name, reviewer=req.reviewer
    )
    if not result.get("ok"):
        return not_found(result.get("error") or "draft not found")

    session = attribution.session_from_request(request, deps)
    attribution.log_file_action(deps, session, result["path"], "promoted")
    return {**result, "attributed_to_session": session["id"] if session else None}


@lessons_router.post("/lessons/reject")
async def reject_lesson(
    req: LessonRejectRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Reject a draft and store the reason for the next mining prompt."""
    from daemon import lessons

    root, error = _vault_root(deps)
    if error:
        return error

    result = await asyncio.to_thread(
        lessons.reject_draft, root, req.name, req.reason, reviewer=req.reviewer
    )
    if not result.get("ok"):
        return bad_request(result.get("error") or "could not reject draft", code="REJECT_FAILED")
    return result


@lessons_router.post("/lessons/auto-promote")
async def auto_promote_lessons(
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Apply the configured auto-promote policy to the pending queue."""
    from daemon import lessons

    root, error = _vault_root(deps)
    if error:
        return error

    try:
        promoted = await asyncio.to_thread(lessons.apply_auto_promote, root)
    except Exception:
        logger.exception("auto-promote failed")
        return server_error("Auto-promote failed", code="AUTO_PROMOTE_FAILED")

    return {
        "policy": lessons.auto_promote_policy(),
        "promoted_count": len(promoted),
        "promoted": promoted,
    }
