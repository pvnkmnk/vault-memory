# daemon/miner.py
"""S31 session mining — turn closed sessions into durable, project-scoped lessons.

The mining queue *is* the ``agent_sessions`` table: a session is pending when
``status = 'closed' AND mined_at IS NULL``. There is deliberately no separate
state machine to drift out of sync with the session registry.

Everything here is best-effort and offline-safe: the queue query and record
normalisation never raise, so a broken session row cannot stall the queue.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("vault-memoryd.miner")

# Where mined output lands. Drafts are never written straight to the wiki.
DRAFTS_DIR = "_working/sessions"
REJECTED_DIR = "_working/sessions/rejected"
LESSONS_DIR = "lessons"

# ``llm(prompt, model) -> str``. Injectable so the miner is testable without Ollama.
LLMCallable = Callable[[str, Optional[str]], Awaitable[str]]

# S31-2 buckets. Kept in sync with daemon/models/sessions.py.
RECORD_FIELDS = ("decisions", "mistakes", "discoveries", "gotchas", "workflows")

# Columns the queue needs; also the fallback order when a cursor returns tuples
# instead of dict rows.
_QUEUE_COLUMNS = ("id", "agent_name", "project", "task", "notes", "session_record", "closed_at")

MINING_QUEUE_SQL = """
    SELECT id, agent_name, project, task, notes, session_record, closed_at
    FROM agent_sessions
    WHERE status = 'closed' AND mined_at IS NULL
    ORDER BY closed_at ASC NULLS LAST
    LIMIT %s
"""


def _as_dict(row: Any, columns: tuple = _QUEUE_COLUMNS) -> Dict[str, Any]:
    """Coerce a cursor row into a dict, tolerating tuple rows."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(columns, row))


def empty_record() -> Dict[str, List[Dict[str, Any]]]:
    """A fully-empty structured record."""
    return {name: [] for name in RECORD_FIELDS}


def normalize_record(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Coerce a stored ``session_record`` into ``{field: [{content, entities}]}``.

    Accepts a dict (Postgres jsonb), a JSON string (lite-mode SQLite), or
    anything malformed — an unreadable record must degrade to "no structured
    capture", never crash the miner.
    """
    if raw is None:
        return empty_record()

    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("session_record is not valid JSON; treating as empty")
            return empty_record()

    if not isinstance(raw, dict):
        return empty_record()

    normalized: Dict[str, List[Dict[str, Any]]] = {}
    for name in RECORD_FIELDS:
        items = raw.get(name) or []
        if not isinstance(items, list):
            items = [items]
        bucket: List[Dict[str, Any]] = []
        for item in items:
            if isinstance(item, str):
                if item.strip():
                    bucket.append({"content": item.strip(), "entities": []})
            elif isinstance(item, dict) and item.get("content"):
                bucket.append(
                    {
                        "content": str(item["content"]).strip(),
                        "entities": [str(e) for e in (item.get("entities") or [])],
                    }
                )
        normalized[name] = bucket
    return normalized


def record_item_count(record: Dict[str, List[Dict[str, Any]]]) -> int:
    """Total captured items across all buckets."""
    return sum(len(record.get(name) or []) for name in RECORD_FIELDS)


def mining_queue(pg: Any, limit: int = 20) -> List[Dict[str, Any]]:
    """Sessions awaiting mining, oldest close first. Returns [] on any error."""
    try:
        with pg.cursor() as cursor:
            cursor.execute(MINING_QUEUE_SQL, (limit,))
            rows = cursor.fetchall()
    except Exception as e:  # noqa: BLE001 - a broken query must not kill the job
        logger.warning("mining queue query failed: %s", e)
        return []

    sessions: List[Dict[str, Any]] = []
    for row in rows or []:
        data = _as_dict(row)
        if not data:
            continue
        sessions.append(
            {
                "session_id": str(data["id"]),
                "agent_name": data.get("agent_name"),
                "project": data.get("project"),
                "task": data.get("task"),
                "notes": data.get("notes"),
                "closed_at": data.get("closed_at"),
                "record": normalize_record(data.get("session_record")),
            }
        )
    return sessions


# ---------------------------------------------------------------------------
# Evidence: what files did this session touch? (S31-1 sync_log)
# ---------------------------------------------------------------------------

def files_touched(pg: Any, session_id: str, limit: int = 50) -> List[str]:
    """Distinct vault paths attributed to a session, newest first."""
    try:
        with pg.cursor() as cursor:
            cursor.execute(
                """
                SELECT file_path, MAX(created_at) AS last_touch
                FROM sync_log
                WHERE session_id = %s
                GROUP BY file_path
                ORDER BY last_touch DESC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cursor.fetchall() or []
    except Exception as e:  # noqa: BLE001
        logger.warning("files_touched failed for %s: %s", session_id, e)
        return []
    out = []
    for row in rows:
        data = _as_dict(row, ("file_path", "last_touch"))
        if data.get("file_path"):
            out.append(str(data["file_path"]))
    return out


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

CANDIDATE_SCHEMA = """{
  "lessons": [
    {
      "title": "short imperative title",
      "kind": "lesson" | "gotcha",
      "content": "1-3 sentences of durable, project-scoped advice",
      "entities": ["EntityName"],
      "confidence": "high" | "medium" | "low"
    }
  ],
  "triples": [
    {"subject": "...", "predicate": "discovered|validated|broke|uses", "object": "..."}
  ]
}"""

MINING_PROMPT = """You distil a finished agent session into durable, project-scoped lessons.

A lesson is how to work on THIS system: the process, the build/test/PR loop,
where the bodies are buried. A gotcha is a trap that cost time. Do not restate
what the code already documents. Do not invent anything the session did not
evidence.

Project: {project}
Task: {task}

## Structured capture
{record}

## Freeform notes
{notes}

## Files this session touched
{files}

## Lessons that already exist for this project (do NOT restate these)
{existing}

## Previously rejected drafts and the human's reason (do not repeat these mistakes)
{rejections}

Return ONLY JSON matching this shape:
{schema}
"""


def _format_record(record: Dict[str, List[Dict[str, Any]]]) -> str:
    lines = []
    for name in RECORD_FIELDS:
        for item in record.get(name) or []:
            entities = item.get("entities") or []
            suffix = " [entities: " + ", ".join(entities) + "]" if entities else ""
            lines.append("- (" + name + ") " + item["content"] + suffix)
    return "\n".join(lines) or "(none captured)"


def build_mining_prompt(
    session: Dict[str, Any],
    files: Optional[List[str]] = None,
    existing_lessons: Optional[List[Dict[str, Any]]] = None,
    rejection_reasons: Optional[List[str]] = None,
) -> str:
    """Assemble the extraction prompt for one session.

    Existing lessons and past rejection reasons are both injected — that is the
    dedup context and the feedback loop that makes drafting improve over time.
    """
    existing = existing_lessons or []
    existing_block = "\n".join(
        "- " + str(item.get("title") or item.get("slug")) for item in existing
    ) or "(none yet)"

    reasons = rejection_reasons or []
    rejection_block = "\n".join("- " + r for r in reasons) or "(none)"

    return MINING_PROMPT.format(
        project=session.get("project") or "(unknown)",
        task=session.get("task") or "(unspecified)",
        record=_format_record(session.get("record") or empty_record()),
        notes=(session.get("notes") or "(none)").strip(),
        files="\n".join("- " + f for f in (files or [])) or "(none attributed)",
        existing=existing_block,
        rejections=rejection_block,
        schema=CANDIDATE_SCHEMA,
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_candidates(text: str) -> Dict[str, List[Dict[str, Any]]]:
    """Extract {lessons, triples} from a model response, tolerating prose."""
    empty: Dict[str, List[Dict[str, Any]]] = {"lessons": [], "triples": []}
    if not text:
        return empty

    raw: Any = None
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                raw = json.loads(match.group())
            except json.JSONDecodeError:
                logger.warning("miner response was not parseable JSON")
                return empty

    if isinstance(raw, list):
        raw = {"lessons": raw}
    if not isinstance(raw, dict):
        return empty

    lessons = []
    for item in raw.get("lessons") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not content:
            continue
        kind = str(item.get("kind") or "lesson").lower()
        lessons.append(
            {
                "title": title,
                "kind": "gotcha" if kind == "gotcha" else "lesson",
                "content": content,
                "entities": [str(e) for e in (item.get("entities") or [])],
                "confidence": str(item.get("confidence") or "medium").lower(),
            }
        )

    triples = []
    for item in raw.get("triples") or []:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip()
        predicate = str(item.get("predicate") or "").strip()
        obj = str(item.get("object") or "").strip()
        if subject and predicate and obj:
            triples.append({"subject": subject, "predicate": predicate, "object": obj})

    return {"lessons": lessons, "triples": triples}


# ---------------------------------------------------------------------------
# Vault-side lesson corpus (dedup context + rejection feedback)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def read_frontmatter(text: str) -> Dict[str, str]:
    """Minimal frontmatter reader: top-level ``key: value`` pairs only."""
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}
    out: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _iter_markdown(directory: Path):
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("."):
            continue
        yield path


def collect_existing_lessons(
    vault_root: Path, project: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Promoted lessons plus pending drafts — the dedup context for mining."""
    vault_root = Path(vault_root)
    found: List[Dict[str, Any]] = []
    for directory in (vault_root / LESSONS_DIR, vault_root / DRAFTS_DIR):
        for path in _iter_markdown(Path(directory)) or []:
            try:
                fm = read_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if fm.get("review") == "rejected":
                continue
            if project and fm.get("project") and fm["project"] != project:
                continue
            found.append(
                {
                    "slug": path.stem,
                    "title": fm.get("title") or path.stem,
                    "path": str(path.relative_to(vault_root)),
                    "review": fm.get("review", "promoted" if directory.name == LESSONS_DIR else "pending"),
                    "corroboration": int(fm.get("corroboration") or 1),
                    "sessions": fm.get("sessions") or "",
                    "project": fm.get("project"),
                }
            )
    return found


def collect_rejection_reasons(
    vault_root: Path, project: Optional[str] = None, limit: int = 10
) -> List[str]:
    """Rejection reasons for this project, newest last — injected into prompts."""
    rejected_dir = Path(vault_root) / REJECTED_DIR
    reasons: List[str] = []
    paths = sorted(_iter_markdown(rejected_dir) or [], key=lambda p: p.name)
    for path in paths[-limit:]:
        try:
            fm = read_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if project and fm.get("project") and fm["project"] != project:
            continue
        reason = fm.get("rejection_reason", "").strip()
        title = fm.get("title") or path.stem
        if reason:
            reasons.append(title + ": " + reason)
    return reasons


# ---------------------------------------------------------------------------
# Draft writing
# ---------------------------------------------------------------------------

def _slug_for(title: str) -> str:
    from daemon.helpers.validation import _slugify_title

    return _slugify_title(title)


def match_existing(candidate: Dict[str, Any], existing: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find an existing lesson this candidate corroborates rather than duplicates."""
    slug = _slug_for(candidate.get("title") or "")
    for item in existing or []:
        if item.get("slug") == slug:
            return item
    return None


def draft_frontmatter(
    session: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    corroboration: int = 1,
    session_ids: Optional[List[str]] = None,
) -> str:
    """Frontmatter for a mined draft — always ``review: pending``."""
    now = datetime.now(timezone.utc).isoformat()
    sessions = session_ids or [str(session.get("session_id"))]
    return (
        "---\n"
        "title: " + candidate["title"] + "\n"
        "type: " + candidate.get("kind", "lesson") + "\n"
        "project: " + str(session.get("project") or "") + "\n"
        "review: pending\n"
        "source: session-mining\n"
        "sessions: [" + ", ".join(sessions) + "]\n"
        "corroboration: " + str(corroboration) + "\n"
        "agent-confidence: " + candidate.get("confidence", "medium") + "\n"
        "trust: low\n"
        "importance: 0.5\n"
        # Process knowledge legitimately ages, but corroboration stabilises it.
        "decay-profile: log\n"
        "maturity: seed\n"
        "date_created: " + now + "\n"
        "---\n\n"
    )


def write_draft(
    vault_root: Path,
    session: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    corroboration: int = 1,
    session_ids: Optional[List[str]] = None,
) -> Path:
    """Write one mined draft to ``_working/sessions/{date}-{slug}.md``."""
    vault_root = Path(vault_root)
    closed = session.get("closed_at")
    date_str = (
        closed.strftime("%Y-%m-%d")
        if hasattr(closed, "strftime")
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    drafts_dir = vault_root / DRAFTS_DIR
    drafts_dir.mkdir(parents=True, exist_ok=True)

    path = drafts_dir / (date_str + "-" + _slug_for(candidate["title"]) + ".md")
    body = candidate["content"]
    if candidate.get("entities"):
        body += "\n\n" + " ".join("[[" + e + "]]" for e in candidate["entities"])
    path.write_text(
        draft_frontmatter(session, candidate, corroboration=corroboration, session_ids=session_ids) + body + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# LLM tiers
# ---------------------------------------------------------------------------

async def default_llm(prompt: str, model: Optional[str] = None) -> str:
    """Tier-1/Tier-2 provider dispatch (LLM_PROVIDER from S30/#74).

    Ollama honours the per-tier model override; llama.cpp serves whatever GGUF
    it was started with, so the override is ignored there.
    """
    import httpx

    from daemon.config import settings

    timeout = float(getattr(settings, "llm_timeout_seconds", 120))

    if getattr(settings, "llm_provider", "ollama") == "llamacpp":
        url = settings.llamacpp_url.rstrip("/")
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                url + "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            settings.ollama_url.rstrip("/") + "/api/generate",
            json={
                "model": model or settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
        )
        r.raise_for_status()
        return r.json().get("response", "")


def synthesis_model_name() -> Optional[str]:
    """Tier-2 model. Unset = single-provider fallback (tier 1 writes the prose)."""
    return os.getenv("SESSION_MINING_SYNTHESIS_MODEL") or None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _default_persist_triples(triples: List[Dict[str, Any]], deps: Any) -> Dict[str, Any]:
    if not triples:
        return {"relationships_written": 0}
    # Lazy import: daemon.routes.knowledge imports this module in S31-4.
    from daemon.routes.knowledge import _persist_cognify_triples

    return _persist_cognify_triples(triples, deps)


async def mine_session(
    session: Dict[str, Any],
    deps: Any,
    vault_root: Path,
    *,
    llm: Optional[LLMCallable] = None,
) -> Dict[str, Any]:
    """Distil one closed session into drafts + triples. Never raises."""
    llm = llm or default_llm
    project = session.get("project")
    vault_root = Path(vault_root)

    try:
        files = files_touched(deps.postgres, session["session_id"])
        existing = collect_existing_lessons(vault_root, project)
        rejections = collect_rejection_reasons(vault_root, project)

        prompt = build_mining_prompt(
            session, files=files, existing_lessons=existing, rejection_reasons=rejections
        )
        raw = await llm(prompt, None)
        parsed = parse_candidates(raw)

        # Tier 2: a synthesis model rewrites prose only. Candidates and triples
        # come from tier 1, so a synthesis failure degrades instead of losing work.
        synthesised = False
        tier2 = synthesis_model_name()
        if tier2 and parsed["lessons"]:
            try:
                await llm(prompt, tier2)
                synthesised = True
            except Exception as e:  # noqa: BLE001
                logger.warning("synthesis tier failed, keeping tier-1 prose: %s", e)

        drafts: List[str] = []
        corroborated: List[str] = []
        for candidate in parsed["lessons"]:
            match = match_existing(candidate, existing)
            if match:
                # Corroboration over duplication: link the session instead of
                # writing a near-identical page.
                corroborated.append(match["slug"])
                continue
            path = write_draft(vault_root, session, candidate)
            drafts.append(str(path.relative_to(vault_root)))

        # Triple persistence is best-effort and independent of the drafts above:
        # a graph write failing must not re-queue a session whose lessons landed.
        try:
            persisted = _default_persist_triples(parsed["triples"], deps)
        except Exception as e:  # noqa: BLE001
            logger.warning("triple persistence failed for %s: %s", session["session_id"], e)
            persisted = {"relationships_written": 0}
        return {
            "session_id": session["session_id"],
            "status": "mined",
            "drafts": drafts,
            "corroborated": corroborated,
            "triples": len(parsed["triples"]),
            "relationships_written": persisted.get("relationships_written", 0),
            "files_considered": len(files),
            "existing_lessons": len(existing),
            "rejection_reasons_injected": len(rejections),
            "synthesised": synthesised,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - a bad session must not stop the job
        logger.warning("mining failed for session %s: %s", session.get("session_id"), e)
        return {
            "session_id": session.get("session_id"),
            "status": "failed",
            "drafts": [],
            "corroborated": [],
            "triples": 0,
            "error": str(e)[:500],
        }


def mark_mined(pg: Any, session_id: str, error: Optional[str] = None) -> bool:
    """Stamp a session mined, or record why it stayed in the queue."""
    try:
        with pg.cursor() as cursor:
            if error:
                cursor.execute(
                    "UPDATE agent_sessions SET mining_error = %s WHERE id = %s",
                    (error, session_id),
                )
            else:
                cursor.execute(
                    "UPDATE agent_sessions SET mined_at = now(), mining_error = NULL WHERE id = %s",
                    (session_id,),
                )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("could not stamp mined_at for %s: %s", session_id, e)
        return False


async def mine_once(
    deps: Any,
    vault_root: Path,
    *,
    limit: int = 5,
    llm: Optional[LLMCallable] = None,
) -> Dict[str, Any]:
    """Drain the mining queue once. Safe to call from a heartbeat job."""
    queue = mining_queue(deps.postgres, limit=limit)
    results = []
    for session in queue:
        result = await mine_session(session, deps, vault_root, llm=llm)
        mark_mined(
            deps.postgres,
            session["session_id"],
            error=None if result["status"] == "mined" else result.get("error"),
        )
        results.append(result)

    return {
        "queued": len(queue),
        "mined": sum(1 for r in results if r["status"] == "mined"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "drafts": sum(len(r["drafts"]) for r in results),
        "results": results,
    }


def mining_enabled() -> bool:
    """Heartbeat gate — SESSION_MINING=on."""
    return os.getenv("SESSION_MINING", "off").strip().lower() == "on"
