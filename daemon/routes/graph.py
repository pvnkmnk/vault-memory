# daemon/routes/graph.py
"""Graph query route handler."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.helpers.responses import server_error

logger = logging.getLogger("vault-memoryd")

graph_router = APIRouter()


@graph_router.get("/graph")
async def graph_query(
    entity: str,
    relationship: Optional[str] = None,
    source: Optional[str] = None,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Graph query endpoint using DI container for database access.
    S27-1: Add source=canvas to filter Canvas-derived relationships.
    """
    if deps.settings.lite_mode:
        raise HTTPException(
            status_code=501, detail="Graph query is not available in lite mode."
        )

    try:
        params = [entity]
        rel_clause = ""
        source_clause = ""

        if relationship:
            rel_clause = "AND r.relationship_type = %s"
            params.append(relationship)

        if source:
            source_clause = "AND r.edge_source = %s"
            params.append(source)

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT r.source_name, r.target_name, r.relationship_type, r.edge_source
                FROM relationships r
                WHERE (r.source_name = %s OR r.target_name = %s)
                {rel_clause}
                {source_clause}
                ORDER BY r.relationship_type
                """,
                [entity, entity] + params[2:],
            )
            rows = cursor.fetchall()

        edges = []
        for r in rows:
            edges.append({
                "source": r["source_name"],
                "target": r["target_name"],
                "relationship": r["relationship_type"],
                "edge_source": r.get("edge_source", "body"),
            })

        return {"entity": entity, "edges": edges, "count": len(edges)}
    except Exception as e:
        logger.error("graph_query error: %s", e)
        return server_error(
            "Graph query failed", code="GRAPH_QUERY_FAILED", detail=str(e)
        )
