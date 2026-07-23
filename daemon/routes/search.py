# daemon/routes/search.py
"""Search-related route handlers."""

import asyncio
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.search import SearchRequest
from daemon.retrieval import classify_query
from daemon.helpers.responses import server_error

logger = logging.getLogger("vault-memoryd")

search_router = APIRouter()
search_siblings_router = APIRouter()


@search_router.post("/search")
async def search(
    req: SearchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Search endpoint using proper dependency injection."""
    if deps.settings.lite_mode or deps.searcher_optional is None:
        raise HTTPException(
            status_code=501, detail="Search is not available in lite mode."
        )

    results = await deps.searcher_optional.search(
        query=req.query,
        project=req.project,
        top_k=req.top_k,
        include_graph=req.include_graph,
        include_temporal=req.include_temporal,
        time_range=req.time_range,
        vault_root=deps.settings.vault_path,
        token_budget=req.token_budget,
    )
    return {
        "results": [r.to_clip() for r in (results or [])],
        "intent": classify_query(req.query).value,
    }


@search_siblings_router.post("/search_siblings")
async def search_siblings(
    req: SearchRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Search siblings endpoint for topic traversal."""
    if deps.settings.lite_mode:
        raise HTTPException(
            status_code=501, detail="Search siblings not available in lite mode."
        )

    try:
        # Bolt: Collapse consecutive wildcards and limit length to 100 characters
        # to prevent ReDoS-style database resource exhaustion on ILIKE wildcards.
        sanitized_query = req.query[:100]
        sanitized_query = re.sub(r"%+", "%", sanitized_query)
        sanitized_query = re.sub(r"_+", "_", sanitized_query)

        # Bolt: Offload synchronous blocking DB queries to a thread pool via
        # asyncio.to_thread to keep the FastAPI main event loop responsive.
        def _fetch_siblings():
            with deps.postgres.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT DISTINCT r.target_name
                    FROM relationships r
                    JOIN temporal_entities te ON te.entity_name = r.source_name
                    WHERE te.centrality > 0.1
                    AND r.relationship_type IN ('RELATED_TO', 'PART_OF', 'DEPENDS_ON')
                    AND r.target_name ILIKE %s
                    LIMIT %s
                    """,
                    (f"%{sanitized_query}%", req.top_k),
                )
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_siblings)

        return {
            "siblings": [row["target_name"] for row in rows],
            "count": len(rows),
        }
    except Exception as e:
        logger.exception("search_siblings error")
        return server_error(
            "Failed to search siblings", code="SIBLING_SEARCH_FAILED"
        )
