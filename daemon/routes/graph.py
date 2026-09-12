# daemon/routes/graph.py
"""Graph query route handler."""

import asyncio
import json
import logging
import os
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


# S27-2: fully static SQL. Every optional filter is a bound named parameter
# rather than a concatenated clause, so the query text never varies with user
# input. `topic_hubs` is the one table holding an entity_name -> vault_path
# mapping (written by `refresh_topic_hubs`), which is what lets the export
# emit real Obsidian `file` nodes instead of text-only ones.
NODES_SQL = """
    SELECT te.entity_name   AS entity_name,
           te.node_type     AS node_type,
           MIN(th.vault_path) AS file_path
    FROM temporal_entities te
    LEFT JOIN topic_hubs th ON th.entity_name = te.entity_name
    WHERE (
            %(entity)s IS NULL
            OR te.entity_name = %(entity)s
            OR te.entity_name IN (
                 SELECT target_name FROM relationships WHERE source_name = %(entity)s
                 UNION
                 SELECT source_name FROM relationships WHERE target_name = %(entity)s
               )
          )
    GROUP BY te.entity_name, te.node_type, te.centrality
    ORDER BY (%(entity)s IS NOT NULL AND te.entity_name = %(entity)s) DESC,
             te.centrality DESC,
             te.entity_name
    LIMIT %(limit)s
"""

EDGES_SQL = """
    SELECT source_name, target_name, relationship_type, edge_source
    FROM relationships
    WHERE (%(entity)s IS NULL OR source_name = %(entity)s OR target_name = %(entity)s)
      AND (%(relationship)s IS NULL OR relationship_type = %(relationship)s)
      AND (%(source)s IS NULL OR edge_source = %(source)s)
    LIMIT %(limit)s
"""


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
        # Bolt: keep synchronous DB work off the event loop.
        def _fetch_rows():
            params = {
                "entity": req.entity,
                "relationship": req.relationship,
                "source": req.source,
                "limit": req.limit,
            }
            with deps.postgres.cursor() as cursor:
                cursor.execute(NODES_SQL, params)
                nodes = cursor.fetchall()
                cursor.execute(EDGES_SQL, params)
                edges = cursor.fetchall()
            return nodes, edges

        nodes, edges = await asyncio.to_thread(_fetch_rows)

        canvas = export_graph_to_canvas(nodes, edges, columns=req.columns)

        written_to = None
        if req.write_to:
            from daemon.ingest import IngestError, safe_relative_parts

            try:
                parts = safe_relative_parts(req.write_to)
            except IngestError as e:
                # A refused path is the caller's mistake, not a server fault.
                raise HTTPException(status_code=400, detail=str(e)) from e
            if not parts[-1].lower().endswith(".canvas"):
                parts[-1] = parts[-1] + ".canvas"

            vault_root = Path(deps.settings.vault_path)
            real_root = os.path.realpath(vault_root)
            target = vault_root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)

            # The component check does not see symlinks that already exist
            # under the vault: `target.parent` or the target itself can resolve
            # outside, and write_text follows symlinks. Compare resolved paths
            # against the resolved root (the same realpath + prefix idiom
            # daemon/ingest.py uses for reads) and refuse on escape.
            for candidate in (target.parent, target):
                if not os.path.lexists(candidate):
                    continue
                resolved = os.path.realpath(candidate)
                if resolved != real_root and not resolved.startswith(real_root + os.sep):
                    raise HTTPException(
                        status_code=400,
                        detail="Write path resolves outside the vault",
                    )

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
