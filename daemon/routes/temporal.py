# daemon/routes/temporal.py
"""Temporal query route handler."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.helpers.responses import server_error

logger = logging.getLogger("vault-memoryd")

temporal_router = APIRouter()


@temporal_router.get("/temporal")
async def temporal_query(
    entity: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Temporal query endpoint using DI container for database access."""
    if deps.settings.lite_mode:
        raise HTTPException(
            status_code=501, detail="Temporal query is not available in lite mode."
        )

    try:
        clauses = []
        params: list = []

        if entity:
            clauses.append("te.entity_name ILIKE %s")
            params.append(f"%{entity}%")

        if date_from:
            clauses.append("te.date >= %s")
            params.append(date_from)

        if date_to:
            clauses.append("te.date <= %s")
            params.append(date_to)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)

        with deps.postgres.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT te.entity_name, te.date, te.centrality, te.node_type,
                       te.vault_path, te.last_seen
                FROM temporal_entities te
                {where}
                ORDER BY te.date DESC
                LIMIT %s
                """,
                params,
            )
            rows = cursor.fetchall()

        entities = []
        for r in rows:
            entities.append({
                "entity_name": r["entity_name"],
                "date": r["date"].isoformat() if r["date"] else None,
                "centrality": r["centrality"],
                "node_type": r["node_type"],
                "vault_path": r["vault_path"],
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            })

        return {"entities": entities, "count": len(entities)}
    except Exception as e:
        logger.error("temporal_query error: %s", e)
        return server_error(
            "Temporal query failed", code="TEMPORAL_QUERY_FAILED", detail=str(e)
        )
