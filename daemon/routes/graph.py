# daemon/routes/graph.py
"""Graph query route handler."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.helpers.responses import server_error

logger = logging.getLogger("vault-memoryd")

graph_router = APIRouter()


class GraphRequest(BaseModel):
    depth: int = Field(default=2, ge=1, le=6)
    limit: int = Field(default=200, ge=1, le=5000)
    source: Optional[str] = None


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


@graph_router.post("/graph")
async def graph_overview(
    req: GraphRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Return graph nodes/edges for UI rendering."""
    if deps.settings.lite_mode:
        raise HTTPException(
            status_code=501, detail="Graph query is not available in lite mode."
        )

    try:
        where_clause = ""
        params: list = []
        if req.source:
            where_clause = "WHERE edge_source = %s"
            params.append(req.source)

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT source_name, target_name, relationship_type, edge_source
                FROM relationships
                {where_clause}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params + [req.limit],
            )
            rows = cursor.fetchall()

        degree: dict[str, int] = {}
        edge_list: list[dict] = []
        for row in rows:
            source_name = row["source_name"]
            target_name = row["target_name"]
            degree[source_name] = degree.get(source_name, 0) + 1
            degree[target_name] = degree.get(target_name, 0) + 1
            edge_list.append(
                {
                    "source": source_name,
                    "target": target_name,
                    "type": row["relationship_type"],
                    "edge_source": row.get("edge_source", "body"),
                }
            )

        nodes = [
            {"id": name, "label": name, "connections": connections}
            for name, connections in degree.items()
        ]
        return {"nodes": nodes, "edges": edge_list, "count": len(edge_list)}
    except Exception as e:
        logger.error("graph_overview error: %s", e)
        return server_error(
            "Graph overview failed", code="GRAPH_OVERVIEW_FAILED", detail=str(e)
        )


@graph_router.get("/graph/canvas_export")
async def graph_canvas_export(
    source: Optional[str] = None,
    limit: int = 500,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Export relationship graph in Obsidian Canvas JSON format."""
    if deps.settings.lite_mode:
        raise HTTPException(
            status_code=501, detail="Canvas export is not available in lite mode."
        )
    safe_limit = max(1, min(limit, 5000))
    try:
        source_clause = ""
        params: list = []
        if source:
            source_clause = "WHERE edge_source = %s"
            params.append(source)

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT source_name, target_name, relationship_type, edge_source
                FROM relationships
                {source_clause}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params + [safe_limit],
            )
            rows = cursor.fetchall()

        node_ids: dict[str, str] = {}
        nodes: list[dict] = []
        edges: list[dict] = []

        def _node_id(name: str) -> str:
            if name not in node_ids:
                node_ids[name] = f"n{len(node_ids) + 1}"
                nodes.append(
                    {
                        "id": node_ids[name],
                        "type": "text",
                        "text": name,
                    }
                )
            return node_ids[name]

        for idx, row in enumerate(rows):
            source_name = row["source_name"]
            target_name = row["target_name"]
            source_id = _node_id(source_name)
            target_id = _node_id(target_name)
            edges.append(
                {
                    "id": f"e{idx + 1}",
                    "fromNode": source_id,
                    "toNode": target_id,
                    "label": row["relationship_type"],
                    "color": "4",
                    "source": row.get("edge_source", "body"),
                }
            )

        return {"nodes": nodes, "edges": edges, "count": len(edges)}
    except Exception as e:
        logger.error("graph_canvas_export error: %s", e)
        return server_error(
            "Canvas export failed", code="GRAPH_CANVAS_EXPORT_FAILED", detail=str(e)
        )
