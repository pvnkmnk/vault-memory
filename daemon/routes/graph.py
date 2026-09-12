# daemon/routes/graph.py
"""Graph query route handler."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from daemon.canvas_graph_pipeline import export_graph_to_canvas
from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.helpers.responses import server_error

logger = logging.getLogger("vault-memoryd")

graph_router = APIRouter()


class CanvasExportRequest(BaseModel):
    """Options for S27-2 (VAU-31): knowledge graph -> Obsidian Canvas."""

    entity: Optional[str] = Field(
        default=None, description="Scope the export to this entity and its neighbours"
    )
    relationship: Optional[str] = Field(
        default=None, description="Only export edges of this relationship type"
    )
    source: Optional[str] = Field(
        default=None, description="Only export edges with this edge_source (e.g. 'canvas')"
    )
    limit: int = Field(default=200, ge=1, le=2000, description="Max nodes and max edges")
    columns: int = Field(default=4, ge=1, le=20, description="Grid columns for the layout")
    write_to: Optional[str] = Field(
        default=None,
        description=(
            "Optional vault-relative path to save the .canvas file to. "
            "Confined to the vault."
        ),
    )


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

        # Bolt: Offload synchronous DB cursor execution to thread pool to prevent event loop blocking
        def _fetch_graph():
            with deps.postgres.cursor() as cursor:
                # Sentinel: Ensure positional SQL parameters match placeholders [entity, entity] + optional filters
                cursor.execute(
                    f"""
                    SELECT r.source_name, r.target_name, r.relationship_type, r.edge_source
                    FROM relationships r
                    WHERE (r.source_name = %s OR r.target_name = %s)
                    {rel_clause}
                    {source_clause}
                    ORDER BY r.relationship_type
                    """,
                    [entity, entity] + params[1:],
                )
                return cursor.fetchall()

        rows = await asyncio.to_thread(_fetch_graph)

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
            "Graph query failed", code="GRAPH_QUERY_FAILED"
        )


@graph_router.post("/graph/export/canvas")
async def export_canvas(
    req: CanvasExportRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """S27-2 (VAU-31): export a knowledge-graph slice as an Obsidian Canvas.

    The inverse of the canvas -> graph pipeline: nodes and typed relationships
    come back out as a `.canvas` document Obsidian can open. Pass ``write_to``
    to also save it into the vault; that path goes through the same
    component-wise containment check the ingest pipeline uses, so a caller
    cannot write outside the vault through this endpoint.
    """
    if deps.settings.lite_mode:
        raise HTTPException(
            status_code=501, detail="Canvas export is not available in lite mode."
        )

    try:
        edge_params: list = []
        edge_clauses = ""
        if req.entity:
            edge_clauses += " AND (source_name = %s OR target_name = %s)"
            edge_params.extend([req.entity, req.entity])
        if req.relationship:
            edge_clauses += " AND relationship_type = %s"
            edge_params.append(req.relationship)
        if req.source:
            edge_clauses += " AND edge_source = %s"
            edge_params.append(req.source)

        # Bolt: keep synchronous DB work off the event loop.
        def _fetch_rows():
            with deps.postgres.cursor() as cursor:
                if req.entity:
                    cursor.execute(
                        """
                        SELECT entity_name, node_type, NULL::text AS file_path
                        FROM temporal_entities
                        WHERE entity_name = %s
                           OR entity_name IN (
                                SELECT target_name FROM relationships WHERE source_name = %s
                                UNION
                                SELECT source_name FROM relationships WHERE target_name = %s
                              )
                        ORDER BY centrality DESC, entity_name
                        LIMIT %s
                        """,
                        (req.entity, req.entity, req.entity, req.limit),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT entity_name, node_type, NULL::text AS file_path
                        FROM temporal_entities
                        ORDER BY centrality DESC, entity_name
                        LIMIT %s
                        """,
                        (req.limit,),
                    )
                nodes = cursor.fetchall()

                cursor.execute(
                    "SELECT source_name, target_name, relationship_type, edge_source "
                    "FROM relationships WHERE 1=1" + edge_clauses + " LIMIT %s",
                    edge_params + [req.limit],
                )
                edges = cursor.fetchall()
            return nodes, edges

        nodes, edges = await asyncio.to_thread(_fetch_rows)

        canvas = export_graph_to_canvas(nodes, edges, columns=req.columns)

        written_to = None
        if req.write_to:
            # Same containment helper the ingest pipeline uses: every path
            # component must be its own basename, so '..' and absolute paths
            # are refused before anything touches the filesystem.
            from daemon.ingest import IngestError, safe_relative_parts

            try:
                parts = safe_relative_parts(req.write_to)
            except IngestError as e:
                # A refused path is the caller's mistake, not a server fault.
                raise HTTPException(status_code=400, detail=str(e)) from e
            if not parts[-1].lower().endswith(".canvas"):
                parts[-1] = parts[-1] + ".canvas"
            target = Path(deps.settings.vault_path).joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(canvas, indent=2, sort_keys=False) + "\n", encoding="utf-8"
            )
            written_to = "/".join(parts)

        return {
            "canvas": canvas,
            "node_count": len(canvas["nodes"]),
            "edge_count": len(canvas["edges"]),
            "written_to": written_to,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("export_canvas error: %s", e)
        return server_error("Canvas export failed", code="CANVAS_EXPORT_FAILED")
