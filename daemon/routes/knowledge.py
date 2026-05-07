# daemon/routes/knowledge.py
"""Knowledge-related route handlers (cognify, promote, lint)."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Literal, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from daemon.dependencies import Dependencies, get_dependencies
from daemon.auth import verify_api_key
from daemon.models.knowledge import CognifyRequest, PromoteRequest, LintRequest
from daemon.helpers.responses import bad_request, server_error
from daemon.helpers.validation import _validate_vault_root, _slugify_title
from daemon.circuit_breaker import get_circuit_breaker, CircuitBreakerOpenError

logger = logging.getLogger("vault-memoryd")

knowledge_router = APIRouter()


# ── Cognify helpers ──────────────────────────────────────────────────────────

def _normalize_triples(raw_triples: Any) -> tuple[list[dict], int]:
    if not isinstance(raw_triples, list):
        return [], 0
    normalized: list[dict] = []
    invalid = 0
    for item in raw_triples:
        if not isinstance(item, dict):
            invalid += 1
            continue
        subject = str(item.get("subject", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        obj = str(item.get("object", "")).strip()
        if not subject or not predicate or not obj:
            invalid += 1
            continue
        normalized.append({
            "subject": subject[:512],
            "predicate": predicate[:128],
            "object": obj[:512],
        })
    return normalized, invalid


def _persist_cognify_triples(triples: list[dict], deps: Dependencies) -> dict:
    entities_written = 0
    relationships_written = 0
    if not triples:
        return {"persisted": True, "entities_written": 0, "relationships_written": 0, "persist_error": None}
    try:
        entity_names = sorted({t["subject"] for t in triples} | {t["object"] for t in triples})
        rel_rows = [(t["subject"], t["object"], t["predicate"].upper()) for t in triples]
        if deps.settings.lite_mode:
            inserted_entities: set[str] = set()
            with deps.postgres.cursor() as cursor:
                for source_name, target_name, relationship_type in rel_rows:
                    cursor.execute(
                        "INSERT OR IGNORE INTO triples (subject, predicate, object, source_file) VALUES (%s, %s, %s, %s)",
                        (source_name, relationship_type, target_name, None),
                    )
                    inserted = int(cursor.rowcount or 0)
                    if inserted:
                        relationships_written += inserted
                        inserted_entities.update((source_name, target_name))
            entities_written = len(inserted_entities)
            return {"persisted": True, "entities_written": entities_written, "relationships_written": relationships_written, "persist_error": None}
        from psycopg2.extras import execute_values
        with deps.postgres.cursor() as cursor:
            if entity_names:
                execute_values(
                    cursor,
                    "INSERT INTO temporal_entities (entity_name, node_type) VALUES %s ON CONFLICT (entity_name) DO NOTHING",
                    [(name, "entity") for name in entity_names],
                    template="(%s, %s)",
                )
                entities_written = int(cursor.rowcount or 0)
            if rel_rows:
                execute_values(
                    cursor,
                    """INSERT INTO relationships (source_name, target_name, relationship_type, edge_source)
                    SELECT v.source_name, v.target_name, v.relationship_type, 'body'
                    FROM (VALUES %s) AS v(source_name, target_name, relationship_type)
                    WHERE NOT EXISTS (SELECT 1 FROM relationships r WHERE r.source_name = v.source_name AND r.target_name = v.target_name AND r.relationship_type = v.relationship_type)""",
                    rel_rows,
                    template="(%s, %s, %s)",
                )
                relationships_written = int(cursor.rowcount or 0)
        return {"persisted": True, "entities_written": entities_written, "relationships_written": relationships_written, "persist_error": None}
    except Exception as e:
        logger.error("cognify persistence failed: %s", e)
        return {"persisted": False, "entities_written": entities_written, "relationships_written": relationships_written, "persist_error": str(e)}


async def _extract_triples_with_ollama(
    text: str,
    entity_types: Optional[List[str]] = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "llama3.2",
) -> dict:
    entity_filter = f"\nOnly extract entities of these types: {entity_types}" if entity_types else ""
    prompt = f"""Extract all entities and relationships from the following text.
Return ONLY a JSON array of triples in this exact format:
[{{"subject": "EntityName", "predicate": "relationship", "object": "EntityName"}}]

Do not include any explanation or markdown. Do not include null or empty values.
{entity_filter}

Text:
{text}
"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{ollama_url}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False, "format": "json"},
        )
        r.raise_for_status()
        response_data = r.json()
    response_text = response_data.get("response", "")
    try:
        raw = json.loads(response_text)
    except json.JSONDecodeError:
        json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if json_match:
            try:
                raw = json.loads(json_match.group())
            except json.JSONDecodeError:
                raw = []
        else:
            raw = []
    triples, invalid = _normalize_triples(raw)
    return {"triples": triples, "invalid_triples": invalid, "model": ollama_model}


# ── Promote helpers ──────────────────────────────────────────────────────────

def _canonical_promote_path(vault_root: Path, title: str, page_type: str) -> Path:
    knowledge_dir = vault_root / "Knowledge"
    title_slug = _slugify_title(title)
    if page_type == "entity":
        return knowledge_dir / f"{title_slug}.md"
    if page_type == "concept":
        return knowledge_dir / f"concept-{title_slug}.md"
    if page_type == "comparison":
        return knowledge_dir / f"compare-{title_slug}.md"
    return knowledge_dir / f"analysis-{title_slug}.md"


def _ensure_reference_wikilinks(text: str, references: List[str]) -> tuple[str, List[str]]:
    missing = []
    for ref in references:
        marker = f"[[{ref}]]"
        if marker not in text:
            missing.append(ref)
    if not missing:
        return text, []
    lines = [text.rstrip(), "", "## References", ""]
    lines.extend([f"- [[{ref}]]" for ref in missing])
    return "\n".join(lines).rstrip() + "\n", missing


def _write_lint_report(report_dict: dict, vault_root: Path) -> str:
    run_at = report_dict.get("run_at", datetime.now(timezone.utc).isoformat())
    date_stamp = datetime.fromisoformat(run_at).strftime("%Y-%m-%d")
    out_path = vault_root / f"lint-{date_stamp}.md"
    summary = report_dict.get("summary", {})
    lines = [
        f"# Vault Lint Report ({date_stamp})", "",
        f"- Run At: {run_at}", f"- Stale Days: {report_dict.get('stale_days', 30)}", "",
        "## Summary", "",
        f"- Total Issues: {summary.get('total_issues', 0)}",
        f"- Orphans: {summary.get('orphans', 0)}",
        f"- Contradictions: {summary.get('contradictions', 0)}",
        f"- Stale Nodes: {summary.get('stale_nodes', 0)}",
        f"- Missing Pages: {summary.get('missing_pages', 0)}",
        f"- Unlinked Pages: {summary.get('unlinked_pages', 0)}", "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


async def _write_text_async(path: Path, content: str) -> None:
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


async def _append_text_async(path: Path, content: str) -> None:
    def _append():
        with path.open("a", encoding="utf-8") as f:
            f.write(content)
    await asyncio.to_thread(_append)


# ── Route handlers ───────────────────────────────────────────────────────────

@knowledge_router.post("/cognify")
async def cognify(
    req: CognifyRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Extract entities and relationships from text using Ollama LLM."""
    ollama_cb = get_circuit_breaker("ollama")
    try:
        async def _do_extract():
            return await _extract_triples_with_ollama(
                req.text, req.entity_types, deps.settings.ollama_url, deps.settings.ollama_model,
            )

        if ollama_cb:
            extract_result = await ollama_cb.execute(_do_extract)
        else:
            extract_result = await _do_extract()

        triples = extract_result["triples"]
        persist_result = {"persisted": False, "entities_written": 0, "relationships_written": 0, "persist_error": None}
        if req.persist and triples:
            persist_result = _persist_cognify_triples(triples, deps)
        return {**extract_result, "persistence": persist_result}
    except CircuitBreakerOpenError:
        return {"error": "Ollama circuit breaker open - service temporarily unavailable", "triples": [], "invalid_triples": 0, "model": deps.settings.ollama_model}
    except httpx.ConnectError:
        return {"error": "Ollama unavailable", "triples": [], "invalid_triples": 0, "model": deps.settings.ollama_model}
    except Exception as e:
        logger.error("cognify error: %s", e)
        return server_error("Cognify failed", code="COGNIFY_FAILED")


@knowledge_router.post("/promote", status_code=201)
async def promote(
    req: PromoteRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Promote wiki-quality content to permanent vault page."""
    vault_root = Path(deps.settings.vault_path).expanduser().resolve()
    if req.vault_path and req.vault_path != str(vault_root):
        return bad_request("vault_path must match configured vault", code="UNAUTHORIZED_PATH")
    vault_error = _validate_vault_root(vault_root, deps)
    if vault_error:
        return vault_error

    if not deps.settings.lite_mode and deps.embedder is not None:
        from daemon.validate_write import WriteValidator
        validator = WriteValidator(
            embedder=deps.embedder,
            postgres=deps.postgres,
            vault_root=vault_root,
        )
        is_unique, reason = await validator.validate(req.text, str(vault_root))
        if not is_unique:
            return bad_request(f"Content rejected: {reason}", code="NEAR_DUPLICATE")

    target_path = _canonical_promote_path(vault_root, req.title, req.page_type)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    text_with_refs, missing_refs = _ensure_reference_wikilinks(req.text, req.references)
    frontmatter = f"---\ntitle: {req.title}\ntype: {req.page_type}\ncreated: {datetime.now(timezone.utc).isoformat()}\n---\n\n"
    full_content = frontmatter + text_with_refs

    await _write_text_async(target_path, full_content)

    watcher = deps.watcher
    if watcher and watcher.engine:
        await watcher.engine.sync_file(target_path, caller="user")

    return {
        "path": str(target_path),
        "title": req.title,
        "page_type": req.page_type,
        "missing_references": missing_refs,
    }


@knowledge_router.post("/lint")
async def lint(
    req: LintRequest,
    deps: Dependencies = Depends(get_dependencies),
    _auth: str = Depends(verify_api_key),
):
    """Run vault lint check."""
    from daemon.lint import run_lint

    vault_root = Path(deps.settings.vault_path).expanduser().resolve()
    if req.vault_path and req.vault_path != str(vault_root):
        return bad_request("vault_path must match configured vault", code="UNAUTHORIZED_PATH")
    vault_error = _validate_vault_root(vault_root, deps)
    if vault_error:
        return vault_error

    report = await run_lint(deps.postgres, vault_root, req.stale_days)
    payload = {
        "run_at": report.run_at,
        "stale_days": report.stale_days,
        "orphans": report.orphans,
        "contradictions": report.contradictions,
        "stale_nodes": report.stale_nodes,
        "missing_pages": report.missing_pages,
        "unlinked_pages": report.unlinked_pages,
        "summary": report.summary,
    }
    report_path = _write_lint_report(payload, vault_root)
    return {**payload, "report_path": report_path}
