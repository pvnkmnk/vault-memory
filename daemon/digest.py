# daemon/digest.py
"""S32-2/3/4 — the digest ladder and the monthly consolidation.

Three windows over the same learning loop:

- **daily** (``digests/{YYYY-MM-DD}.md``, ``log`` decay) — a low-noise overview:
  what changed today, what was learned, what needs a human. Counts, titles, and
  links; the LLM writes three sentences at most.
- **weekly** (``digests/{YYYY}-W{WW}.md``) — the deep dive: page velocity per
  project, corroboration events, emerging entities, contradictions lint flagged,
  sources ingested, plus a longer synthesis.
- **monthly** (``digests/{YYYY-MM}.md``) — consolidation. Clusters the month's
  corroborated lessons into theme groups and proposes *skill* bundles, which
  land in ``_working/consolidation/`` for review. Nothing is promoted
  automatically; the review gate is S31-4's.

Every SQL read is best-effort: a missing table or an empty window produces an
empty section, never a failed digest. The LLM is injectable throughout.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("vault-memoryd.digest")

DIGESTS_DIRNAME = "digests"
CONSOLIDATION_DIRNAME = "_working/consolidation"
SKILLS_DIRNAME = "skills"
SKILLS_STAGING_DIRNAME = "_working/skills"

#: Marker proving a SKILL.md was generated here (and so may be regenerated).
GENERATED_BY = "vault-memory"

#: Fallback theme when a lesson carries neither `theme` nor `project`.
DEFAULT_THEME = "general"

LLMCallable = Callable[[str, Optional[str]], Awaitable[str]]


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

def _as_utc(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def period_bounds(kind: str, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    """``[start, end)`` for ``daily`` / ``weekly`` / ``monthly``."""
    end = _as_utc(now)
    if kind == "daily":
        start = end - timedelta(days=1)
    elif kind == "weekly":
        start = end - timedelta(days=7)
    elif kind == "monthly":
        start = end - timedelta(days=30)
    else:
        raise ValueError(f"unknown digest period: {kind}")
    return start, end


def iso_week_stamp(moment: datetime) -> str:
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def digest_filename(kind: str, now: datetime) -> str:
    if kind == "daily":
        return f"{now.strftime('%Y-%m-%d')}.md"
    if kind == "weekly":
        return f"{iso_week_stamp(now)}.md"
    if kind == "monthly":
        return f"{now.strftime('%Y-%m')}.md"
    raise ValueError(f"unknown digest period: {kind}")


def digest_enabled() -> bool:
    """Heartbeat gate — ``DIGESTS=on``."""
    return os.getenv("DIGESTS", "off").strip().lower() == "on"


def due_digest(now: Optional[datetime] = None) -> Optional[str]:
    """Which digest the heartbeat should run right now, if any.

    The daily digest supersedes the others: on the 1st of a month, or a Sunday,
    the daily still runs first and the larger digest is written on the same
    cycle. Keeping the trigger deterministic keeps the heartbeat idempotent.
    """
    now = _as_utc(now)
    if now.hour != 0:
        return None
    if now.day == 1:
        return "monthly"
    if now.weekday() == 6:  # Sunday
        return "weekly"
    return "daily"


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------

@dataclass
class DigestData:
    kind: str
    start: datetime
    end: datetime
    pages_changed: List[Dict[str, Any]] = field(default_factory=list)
    sessions: List[Dict[str, Any]] = field(default_factory=list)
    mined_sessions: int = 0
    drafts_mined: int = 0
    action_counts: Dict[str, int] = field(default_factory=dict)
    emerging_entities: List[Dict[str, Any]] = field(default_factory=list)
    promoted_lessons: List[Dict[str, Any]] = field(default_factory=list)
    rejected_lessons: List[Dict[str, Any]] = field(default_factory=list)
    pending_drafts: List[Dict[str, Any]] = field(default_factory=list)
    ingested_sources: List[Dict[str, Any]] = field(default_factory=list)
    inbox_pending: List[str] = field(default_factory=list)
    lint: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    @property
    def projects(self) -> List[str]:
        seen = []
        for session in self.sessions:
            project = session.get("project")
            if project and project not in seen:
                seen.append(project)
        for page in self.pages_changed:
            project = _project_of_page(page.get("file_path") or "")
            if project and project not in seen:
                seen.append(project)
        return seen


def _project_of_page(file_path: str) -> Optional[str]:
    """``05 Dev Projects/<project>/...`` → ``<project>``.

    The folder is conventionally numbered (``05 Dev Projects``), so match on the
    suffix rather than the exact name — hardcoding the number silently filed
    every project page under ``(unfiled)``.
    """
    parts = Path(file_path).parts
    for index, part in enumerate(parts):
        if part == "Dev Projects" or part.endswith(" Dev Projects"):
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


def query_rows(pg: Any, sql: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Best-effort query returning dict rows. Never raises."""
    try:
        with pg.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
    except Exception as e:  # noqa: BLE001 - a broken query is an empty section
        logger.debug("digest query skipped: %s", e)
        return []
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append({"value": row})
    return out


def _count_rows(pg: Any, sql: str, params: Tuple = ()) -> int:
    rows = query_rows(pg, sql, params)
    if not rows:
        return 0
    first = rows[0]
    for value in first.values():
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _in_window(value: Any, start: datetime, end: datetime) -> bool:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return start <= moment < end
    if isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        return start <= moment < end
    return False


def _lesson_entries(vault_root: Path, review: str) -> List[Dict[str, Any]]:
    from daemon import lessons as lessons_module

    out = []
    for draft in lessons_module.list_drafts(vault_root, review=review):
        out.append(
            {
                "slug": draft.slug,
                "title": draft.title,
                "project": draft.project,
                "theme": draft.frontmatter.get("theme") or draft.project or DEFAULT_THEME,
                "path": draft.rel_path,
                "corroboration": draft.corroboration,
                "reviewed_at": draft.frontmatter.get("reviewed_at") or draft.frontmatter.get("rejected_at"),
                "rejection_reason": draft.frontmatter.get("rejection_reason"),
            }
        )
    return out


async def gather(deps: Any, vault_root: Path, kind: str, *, now: Optional[datetime] = None) -> DigestData:
    """Everything the digest renderers need, gathered best-effort."""
    vault_root = Path(vault_root)
    start, end = period_bounds(kind, now)
    data = DigestData(kind=kind, start=start, end=end)
    pg = getattr(deps, "postgres", None)

    if pg is not None:
        data.pages_changed = query_rows(
            pg,
            """
            SELECT file_path, maturity, last_synced_at
            FROM sync_state
            WHERE last_synced_at >= %s AND last_synced_at < %s
              AND file_path != '__init__'
            ORDER BY last_synced_at DESC
            LIMIT 200
            """,
            (start, end),
        )
        data.sessions = query_rows(
            pg,
            """
            SELECT id, agent_name, project, task, closed_at, mined_at, status
            FROM agent_sessions
            WHERE COALESCE(closed_at, last_ping_at) >= %s
              AND COALESCE(closed_at, last_ping_at) < %s
            ORDER BY COALESCE(closed_at, last_ping_at) DESC
            LIMIT 200
            """,
            (start, end),
        )
        data.mined_sessions = _count_rows(
            pg,
            "SELECT COUNT(*) FROM agent_sessions WHERE mined_at >= %s AND mined_at < %s",
            (start, end),
        )
        data.action_counts = {
            str(row.get("action")): row.get("count") or 0
            for row in query_rows(
                pg,
                """
                SELECT action, COUNT(*) AS count
                FROM sync_log
                WHERE created_at >= %s AND created_at < %s
                GROUP BY action
                """,
                (start, end),
            )
            if row.get("action")
        }
        data.emerging_entities = query_rows(
            pg,
            """
            SELECT source_name AS entity, COUNT(*) AS new_edges
            FROM relationships
            WHERE created_at >= %s AND created_at < %s
            GROUP BY source_name
            ORDER BY new_edges DESC, entity ASC
            LIMIT 10
            """,
            (start, end),
        )

    # ── vault side ───────────────────────────────────────────────────────────
    from daemon import lessons as lessons_module

    data.pending_drafts = [
        entry
        for entry in _lesson_entries(vault_root, "pending")
    ]
    for entry in _lesson_entries(vault_root, "approved"):
        if _in_window(entry.get("reviewed_at"), start, end):
            data.promoted_lessons.append(entry)
    for entry in _lesson_entries(vault_root, "rejected"):
        if _in_window(entry.get("reviewed_at"), start, end):
            data.rejected_lessons.append(entry)

    # Corroboration events: lessons whose last corroboration landed in window.
    for draft in lessons_module.list_drafts(vault_root, review=None):
        stamped = draft.frontmatter.get("last_corroborated_at")
        if draft.review == "approved" and _in_window(stamped, start, end):
            slug = draft.slug
            if slug not in {p["slug"] for p in data.promoted_lessons}:
                data.promoted_lessons.append(
                    {
                        "slug": slug,
                        "title": draft.title,
                        "project": draft.project,
                        "theme": draft.frontmatter.get("theme") or draft.project or DEFAULT_THEME,
                        "path": draft.rel_path,
                        "corroboration": draft.corroboration,
                        "reviewed_at": stamped,
                    }
                )
    data.drafts_mined = len(data.pending_drafts)

    # Ingestion: manifest entries archived or compiled inside the window.
    try:
        from daemon import ingest as ingest_module

        manifest = ingest_module.read_manifest(vault_root)
        for ref, entry in (manifest.get("sources") or {}).items():
            stamps = [entry.get("archived_at"), entry.get("compiled_at")]
            if any(_in_window(stamp, start, end) for stamp in stamps):
                data.ingested_sources.append({"ref": ref, **entry})
        data.inbox_pending = [
            str(p.relative_to(vault_root)) for p in ingest_module.inbox_sources(vault_root)
        ]
    except Exception as e:  # noqa: BLE001
        data.errors.append(f"ingest: {e}")

    if pg is not None:
        try:
            # Awaited rather than wrapped in asyncio.run: gather() is called
            # from a running loop, where asyncio.run raises.
            from daemon.lint import run_lint

            report = await run_lint(pg, vault_root)
            data.lint = {
                "summary": report.summary,
                "contradictions": report.contradictions,
                "lesson_conflicts": report.lesson_conflicts,
                "speculative_pages": report.speculative_pages,
                "unlinked_pages": report.unlinked_pages,
                "stale_nodes": report.stale_nodes,
            }
        except Exception as e:  # noqa: BLE001
            data.errors.append(f"lint: {e}")

    return data


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _bullets(items: List[str], empty: str = "_(nothing)_") -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


def _wikilink(slug: str) -> str:
    return "[[" + slug + "]]"


def _page_link(file_path: str) -> str:
    return _wikilink(Path(file_path).stem)


def render_daily(data: DigestData) -> str:
    """Counts, titles, links. The structure is the deliverable; prose is optional."""
    day = data.end.strftime("%Y-%m-%d")
    lines = [
        "---",
        f"title: Daily digest — {day}",
        "type: digest",
        "period: daily",
        f"window-start: {data.start.isoformat()}",
        f"window-end: {data.end.isoformat()}",
        "maturity: seed",
        "trust: medium",
        "decay-profile: log",
        f"date_created: {data.end.isoformat()}",
        "---",
        "",
        f"# Daily digest — {day}",
        "",
        "## Changed pages (24h)",
        "",
        _bullets(
            [
                f"{_page_link(p.get('file_path') or '')} — `{p.get('file_path')}`"
                for p in data.pages_changed[:50]
            ]
        ),
        "",
        "## Sessions & lessons",
        "",
        f"- Sessions closed: {len(data.sessions)}",
        f"- Sessions mined: {data.mined_sessions}",
        f"- Lesson drafts awaiting review: {len(data.pending_drafts)}",
        f"- Lessons promoted: {len(data.promoted_lessons)}",
    ]
    for session in data.sessions[:20]:
        lines.append(
            f"- `{str(session.get('id'))[:8]}` {session.get('agent_name') or '?'} "
            f"({session.get('project') or 'no project'}) — {session.get('task') or 'no task'}"
        )
    for draft in data.pending_drafts[:20]:
        lines.append(f"- pending: {_wikilink(draft['slug'])} — {draft['title']}")

    lines += ["", "## Needs attention", ""]
    attention: List[str] = []
    summary = (data.lint or {}).get("summary") or {}
    for key, label in (
        ("contradictions", "contradictions"),
        ("lesson_conflicts", "mined lesson conflicts"),
        ("speculative_pages", "pages drifting into speculation"),
        ("stale_nodes", "stale nodes"),
    ):
        count = summary.get(key, 0)
        if count:
            attention.append(f"{count} {label}")
    if data.inbox_pending:
        attention.append(f"{len(data.inbox_pending)} file(s) waiting in the inbox")
    lines.append(_bullets(attention))

    lines += [
        "",
        "## Activity",
        "",
        _bullets(
            [f"{action}: {count}" for action, count in sorted(data.action_counts.items())]
        ),
        "",
    ]
    if data.errors:
        lines += ["## Collection warnings", "", _bullets(data.errors), ""]
    return "\n".join(lines)


def render_weekly(data: DigestData) -> str:
    stamp = iso_week_stamp(data.end)
    lines = [
        "---",
        f"title: Weekly digest — {stamp}",
        "type: digest",
        "period: weekly",
        f"window-start: {data.start.isoformat()}",
        f"window-end: {data.end.isoformat()}",
        "maturity: sapling",
        "trust: medium",
        "decay-profile: log",
        f"date_created: {data.end.isoformat()}",
        "---",
        "",
        f"# Weekly digest — {stamp}",
        "",
        "## Page velocity by project",
        "",
    ]
    per_project: Dict[str, int] = {}
    for page in data.pages_changed:
        project = _project_of_page(page.get("file_path") or "") or "(unfiled)"
        per_project[project] = per_project.get(project, 0) + 1
    lines.append(
        _bullets(
            [f"{project}: {count} pages" for project, count in sorted(per_project.items(), key=lambda kv: -kv[1])]
        )
    )

    lines += [
        "",
        "## Sessions mined",
        "",
        f"- Sessions closed: {len(data.sessions)}",
        f"- Sessions mined: {data.mined_sessions}",
        f"- Rejection reasons fed back into mining: {len(data.rejected_lessons)}",
        "",
        "## Lessons",
        "",
        f"- Promoted: {len(data.promoted_lessons)}",
    ]
    for lesson in data.promoted_lessons:
        lines.append(
            f"- {_wikilink(lesson['slug'])} — {lesson['title']} "
            f"(corroboration={lesson['corroboration']}, theme={lesson['theme']})"
        )
    lines += [f"- Pending review: {len(data.pending_drafts)}"]
    for lesson in data.rejected_lessons[:10]:
        reason = lesson.get("rejection_reason") or "(no reason recorded)"
        lines.append(f"- rejected {_wikilink(lesson['slug'])}: {reason}")

    lines += ["", "## Emerging entities", ""]
    lines.append(
        _bullets(
            [
                f"{_wikilink(str(e.get('entity')))} — {e.get('new_edges')} new edges"
                for e in data.emerging_entities
                if e.get("entity")
            ]
        )
    )

    lines += ["", "## Contradictions flagged by lint", ""]
    contradictions = (data.lint or {}).get("contradictions") or []
    lines.append(
        _bullets(
            [
                f"{_wikilink(str(c.get('source_name')))} — {c.get('relationship_type')} "
                f"points at {', '.join(str(t) for t in (c.get('conflicting_targets') or []))}"
                for c in contradictions[:20]
            ]
        )
    )

    lines += ["", "## Sources ingested", ""]
    lines.append(
        _bullets(
            [
                f"`{s.get('ref')}` → {', '.join(_wikilink(Path(p).stem) for p in (s.get('pages') or [])[:5]) or '(no pages)'}"
                for s in data.ingested_sources[:20]
            ]
        )
    )

    lines += ["", "## Inbox queue", ""]
    lines.append(_bullets(data.inbox_pending))

    lines += ["", "## Projects touched", ""]
    lines.append(_bullets(data.projects))
    lines.append("")
    if data.errors:
        lines += ["## Collection warnings", "", _bullets(data.errors), ""]
    return "\n".join(lines)


def render_monthly(data: DigestData, clusters: Dict[str, List[Dict[str, Any]]], proposals: List[Dict[str, Any]]) -> str:
    month = data.end.strftime("%Y-%m")
    lines = [
        "---",
        f"title: Monthly review — {month}",
        "type: digest",
        "period: monthly",
        f"window-start: {data.start.isoformat()}",
        f"window-end: {data.end.isoformat()}",
        "maturity: sapling",
        "trust: medium",
        "decay-profile: log",
        f"date_created: {data.end.isoformat()}",
        "---",
        "",
        f"# Monthly review — {month}",
        "",
        "## The month's sessions",
        "",
        f"- Sessions closed: {len(data.sessions)}",
        f"- Sessions mined: {data.mined_sessions}",
        f"- Pages changed: {len(data.pages_changed)}",
        f"- Sources ingested: {len(data.ingested_sources)}",
        "",
        "## Lessons: promoted / rejected",
        "",
        f"- Promoted: {len(data.promoted_lessons)}",
        f"- Awaiting review: {len(data.pending_drafts)}",
        f"- Rejected: {len(data.rejected_lessons)}",
        "",
    ]
    if data.rejected_lessons:
        lines += ["### Rejection themes (fed back into mining prompts)", ""]
        themes: Dict[str, int] = {}
        for lesson in data.rejected_lessons:
            reason = (lesson.get("rejection_reason") or "unstated").strip()
            key = reason.split("—")[0].split(":")[0].strip()[:80] or "unstated"
            themes[key] = themes.get(key, 0) + 1
        lines.append(_bullets([f"{theme}: {count}" for theme, count in sorted(themes.items(), key=lambda kv: -kv[1])]))
        lines.append("")

    lines += ["## Theme clusters", ""]
    if clusters:
        for theme, members in sorted(clusters.items()):
            lines.append(f"### {theme} ({len(members)} lessons)")
            lines.append("")
            lines.append(
                _bullets(
                    [
                        f"{_wikilink(m['slug'])} — {m['title']} (corroboration={m['corroboration']})"
                        for m in members
                    ]
                )
            )
            lines.append("")
    else:
        lines += ["_(no corroborated lessons this month)_", ""]

    lines += ["## Proposed skill bundles", ""]
    if proposals:
        lines.append(
            _bullets([f"`{p['path']}` — theme `{p['theme']}` from {len(p['lessons'])} lesson(s)" for p in proposals])
        )
        lines.append("")
        lines.append("These are proposals in `_working/consolidation/`. Promote them like any other draft.")
    else:
        lines.append("_(none proposed)_")

    lines += ["", "## Project trajectory", ""]
    lines.append(_bullets(data.projects))

    lines += ["", "## Open contradictions", ""]
    lines.append(
        _bullets(
            [
                f"{_wikilink(str(c.get('source_name')))} — {c.get('relationship_type')}"
                for c in ((data.lint or {}).get("contradictions") or [])[:20]
            ]
        )
    )
    lines.append("")
    if data.errors:
        lines += ["## Collection warnings", "", _bullets(data.errors), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Theme clustering + skill bundles
# ---------------------------------------------------------------------------

def lesson_theme(entry: Dict[str, Any]) -> str:
    """``theme`` → ``project`` → ``general``. Lower-kebab so it is a valid slug."""
    raw = entry.get("theme") or entry.get("project") or DEFAULT_THEME
    slug = re.sub(r"[^a-z0-9]+", "-", str(raw).lower()).strip("-")
    return slug or DEFAULT_THEME


def cluster_by_theme(
    lesson_entries: List[Dict[str, Any]],
    *,
    min_corroboration: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """Group lessons by theme, keeping only the ones worth consolidating.

    ``min_corroboration`` is what makes the monthly review the *compounding*
    step rather than an echo of every one-off: one session's conclusion is not
    yet a way of working.
    """
    clusters: Dict[str, List[Dict[str, Any]]] = {}
    for entry in lesson_entries:
        if int(entry.get("corroboration") or 0) < min_corroboration:
            continue
        clusters.setdefault(lesson_theme(entry), []).append(entry)
    for members in clusters.values():
        members.sort(key=lambda m: (-int(m.get("corroboration") or 0), m.get("slug") or ""))
    return clusters


def render_skill_md(theme: str, members: List[Dict[str, Any]], *, project: Optional[str] = None) -> str:
    """An agentskills.io-compatible ``SKILL.md`` built from theme lessons."""
    name = theme
    lessons = ", ".join(m.get("title") or m["slug"] for m in members[:4]) or "vault lessons"
    description = (
        f"How this vault works in the {theme} area, distilled from "
        f"{len(members)} corroborated session lesson(s): {lessons}."
    )
    lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "metadata:",
        f"  generated-by: {GENERATED_BY}",
        f"  category: {theme}",
    ]
    if project:
        lines.append(f"  project: {project}")
    lines.append(f"  lessons: {len(members)}")
    lines.append(f"  corroboration: {max(int(m.get('corroboration') or 0) for m in members)}")
    lines += [
        "---",
        "",
        f"# {theme}",
        "",
        "## When to use",
        "",
        "Load this skill before starting work that touches "
        + (", ".join(theme.split("-")) or theme)
        + ". It records what previous sessions in this vault learned the hard way,",
        "so the same ground does not get re-broken.",
        "",
        "## Lessons",
        "",
    ]
    for member in members:
        lines.append(f"### {member.get('title') or member['slug']}")
        lines.append("")
        lines.append(f"Source: {_wikilink(member['slug'])} (corroboration={member.get('corroboration')})")
        lines.append("")
        content = (member.get("content") or "").strip()
        if not content:
            content = f"See {_wikilink(member['slug'])}."
        lines.append(content)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def skill_dir(vault_root: Path, theme: str, *, staging: bool = False) -> Path:
    base = SKILLS_STAGING_DIRNAME if staging else SKILLS_DIRNAME
    return Path(vault_root) / base / theme


def _existing_skill_is_generated(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:1000]
    except OSError:
        return False
    return f"generated-by: {GENERATED_BY}" in head


def export_skills(
    vault_root: Path,
    *,
    lesson_entries: Optional[List[Dict[str, Any]]] = None,
    project: Optional[str] = None,
    min_corroboration: int = 1,
) -> Dict[str, Any]:
    """Publish corroborated lessons as SKILL.md bundles.

    A ``SKILL.md`` that exists without this generator's marker was hand-written
    (or came from elsewhere): it is never overwritten. The export for that theme
    goes to ``_working/skills/`` instead, so a human can merge deliberately.
    """
    from daemon import lessons as lessons_module

    root = Path(vault_root)
    if lesson_entries is None:
        lesson_entries = [
            {
                "slug": d.slug,
                "title": d.title,
                "project": d.project,
                "theme": d.frontmatter.get("theme") or d.project or DEFAULT_THEME,
                "path": d.rel_path,
                "corroboration": d.corroboration,
                "content": (d.body or "").strip(),
            }
            for d in lessons_module.list_drafts(root, review="approved", project=project)
        ]

    clusters = cluster_by_theme(lesson_entries, min_corroboration=min_corroboration)
    written: List[Dict[str, Any]] = []
    staged: List[Dict[str, Any]] = []
    missing_pages: List[str] = []

    for theme, members in sorted(clusters.items()):
        for member in members:
            source_path = root / (member.get("path") or "")
            if not source_path.is_file():
                # The bundle must only reference pages that exist.
                missing_pages.append(member.get("path") or member["slug"])

        content = render_skill_md(theme, members, project=members[0].get("project"))
        target_dir = skill_dir(root, theme)
        target = target_dir / "SKILL.md"

        if target.exists() and not _existing_skill_is_generated(target):
            staged_path = skill_dir(root, theme, staging=True) / "SKILL.md"
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_text(content, encoding="utf-8")
            staged.append(
                {
                    "theme": theme,
                    "path": str(staged_path.relative_to(root)),
                    "reason": "existing SKILL.md was not generated here",
                    "lessons": [m["slug"] for m in members],
                }
            )
            continue

        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(
            {
                "theme": theme,
                "name": theme,
                "path": str(target.relative_to(root)),
                "lessons": [m["slug"] for m in members],
                "description": f"{len(members)} corroborated lesson(s) for {theme}",
            }
        )

    return {
        "themes": len(clusters),
        "written": written,
        "staged": staged,
        "missing_pages": missing_pages,
    }


def write_consolidation_proposals(
    vault_root: Path,
    clusters: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Stage monthly skill proposals in ``_working/consolidation/``.

    Deliberately *not* ``skills/``: consolidation output is a proposal, and the
    review gate (S31-4) is what lets it become durable.
    """
    root = Path(vault_root)
    proposals: List[Dict[str, Any]] = []
    for theme, members in sorted(clusters.items()):
        target = root / CONSOLIDATION_DIRNAME / theme / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_skill_md(theme, members, project=members[0].get("project")),
            encoding="utf-8",
        )
        proposals.append(
            {
                "theme": theme,
                "path": str(target.relative_to(root)),
                "lessons": [m["slug"] for m in members],
            }
        )
    return proposals


# ---------------------------------------------------------------------------
# Summaries (the only LLM call)
# ---------------------------------------------------------------------------

DAILY_SUMMARY_PROMPT = """Write at most three sentences summarising this day in a knowledge vault.

Be concrete and plain. No preamble, no bullet points, no headings — just the
sentences. Do not invent anything that is not in the data below.

## Data
{data}
"""


def _data_digest_for_llm(data: DigestData) -> str:
    return json.dumps(
        {
            "window": [data.start.isoformat(), data.end.isoformat()],
            "pages_changed": [p.get("file_path") for p in data.pages_changed[:40]],
            "sessions_closed": len(data.sessions),
            "sessions_mined": data.mined_sessions,
            "lessons_promoted": [l["title"] for l in data.promoted_lessons],
            "lessons_pending": [l["title"] for l in data.pending_drafts],
            "lessons_rejected": [
                {"title": l["title"], "reason": l.get("rejection_reason")} for l in data.rejected_lessons
            ],
            "lint_summary": (data.lint or {}).get("summary") or {},
            "sources_ingested": [s.get("ref") for s in data.ingested_sources],
            "inbox_pending": data.inbox_pending,
        },
        indent=2,
        default=str,
    )


async def summarize(data: DigestData, *, llm: Optional[LLMCallable]) -> Optional[str]:
    """Three sentences, or ``None``. A model failure never fails the digest."""
    if llm is None:
        return None
    try:
        text = await llm(DAILY_SUMMARY_PROMPT.format(data=_data_digest_for_llm(data)), None)
    except Exception as e:  # noqa: BLE001
        logger.warning("digest summary failed, writing the digest without it: %s", e)
        return None
    text = (text or "").strip()
    return text or None


def insert_summary(markdown: str, summary: Optional[str]) -> str:
    """Put the LLM's summary directly under the H1, before the first section."""
    if not summary:
        return markdown
    block = "## Summary\n\n" + summary.strip() + "\n\n"
    match = re.search(r"\n## ", markdown)
    if match is None:
        return markdown.rstrip() + "\n\n" + block.rstrip() + "\n"
    return markdown[: match.start()] + "\n\n" + block + markdown[match.start() + 1:]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _write_digest(vault_root: Path, filename: str, content: str) -> str:
    directory = Path(vault_root) / DIGESTS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return str(path.relative_to(vault_root))


async def run_digest(
    deps: Any,
    vault_root: Path,
    kind: str,
    *,
    now: Optional[datetime] = None,
    llm: Optional[LLMCallable] = None,
) -> Dict[str, Any]:
    """Build and write one digest. Never raises."""
    root = Path(vault_root)
    moment = _as_utc(now)
    try:
        data = await gather(deps, root, kind, now=moment)
    except Exception as e:  # noqa: BLE001
        logger.warning("digest gather failed for %s: %s", kind, e)
        return {"kind": kind, "status": "failed", "error": str(e)[:500]}

    if kind == "daily":
        body = render_daily(data)
    elif kind == "weekly":
        body = render_weekly(data)
    else:
        body = None  # monthly needs the clusters, built below

    proposals: List[Dict[str, Any]] = []
    clusters: Dict[str, List[Dict[str, Any]]] = {}
    if kind == "monthly":
        # Only corroborated lessons are consolidated — one session's take is not
        # yet a way of working.
        from daemon import lessons as lessons_module

        entries = [
            {
                "slug": d.slug,
                "title": d.title,
                "project": d.project,
                "theme": d.frontmatter.get("theme") or d.project or DEFAULT_THEME,
                "path": d.rel_path,
                "corroboration": d.corroboration,
                "content": (d.body or "").strip(),
            }
            for d in lessons_module.list_drafts(root, review="approved")
        ]
        clusters = cluster_by_theme(entries, min_corroboration=2)
        proposals = write_consolidation_proposals(root, clusters)
        body = render_monthly(data, clusters, proposals)

    summary = await summarize(data, llm=llm) if kind != "monthly" else None
    body = insert_summary(body, summary)

    filename = digest_filename(kind, moment)
    rel_path = _write_digest(root, filename, body)

    result = {
        "kind": kind,
        "status": "written",
        "path": rel_path,
        "window": [data.start.isoformat(), data.end.isoformat()],
        "pages_changed": len(data.pages_changed),
        "sessions": len(data.sessions),
        "lessons_promoted": len(data.promoted_lessons),
        "pending_drafts": len(data.pending_drafts),
        "ingested_sources": len(data.ingested_sources),
        "summarised": summary is not None,
        "errors": data.errors,
    }
    if kind == "monthly":
        result["themes"] = sorted(clusters)
        result["proposals"] = proposals

    await _index(deps, root, rel_path)
    return result


async def _index(deps: Any, vault_root: Path, rel_path: str) -> None:
    watcher = getattr(deps, "watcher", None)
    engine = getattr(watcher, "engine", None) if watcher else None
    if engine is None:
        return
    try:
        await engine.sync_file(Path(vault_root) / rel_path, caller="agent")
    except Exception as e:  # noqa: BLE001
        logger.warning("could not index digest %s: %s", rel_path, e)


async def run_due_digests(
    deps: Any,
    vault_root: Path,
    *,
    now: Optional[datetime] = None,
    llm: Optional[LLMCallable] = None,
) -> List[Dict[str, Any]]:
    """Heartbeat entry point: run whatever is due, in dependency order."""
    if not digest_enabled():
        return []
    moment = _as_utc(now)
    results = []
    if moment.day == 1:
        results.append(await run_digest(deps, vault_root, "monthly", now=moment, llm=llm))
    if moment.weekday() == 6:
        results.append(await run_digest(deps, vault_root, "weekly", now=moment, llm=llm))
    results.append(await run_digest(deps, vault_root, "daily", now=moment, llm=llm))
    return results
