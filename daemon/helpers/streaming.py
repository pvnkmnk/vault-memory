# daemon/helpers/streaming.py
"""Streaming response helpers for bulk export."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


async def _export_stream_generator(
    vault_root: Path,
    req,
    parser,
    from_date: Optional[datetime],
    to_date: Optional[datetime],
    entity_paths: Optional[set],
):
    """NDJSON streaming generator for bulk export (S26-3)."""
    count = 0
    for path in vault_root.rglob("*.md"):
        if ".obsidian" in path.parts or ".trash" in path.parts:
            continue

        try:
            rel_path = str(path.relative_to(vault_root))
            if req.project and not rel_path.startswith(req.project):
                continue
            if entity_paths is not None and rel_path not in entity_paths:
                continue

            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if from_date and mtime < from_date:
                continue
            if to_date and mtime > to_date:
                continue

            parsed = await parser.parse(path, caller="user")
            if req.tags:
                tags = set(parsed.get("tags") or [])
                if not tags.intersection(set(req.tags)):
                    continue

            note = {
                "id": rel_path,
                "title": path.stem,
                "content": parsed.get("body", ""),
                "project": parsed.get("project"),
                "tags": parsed.get("tags") or [],
                "metadata": {
                    "status": parsed.get("status"),
                    "trust": parsed.get("trust"),
                    "maturity": parsed.get("maturity"),
                    "importance": parsed.get("importance"),
                },
                "created_at": parsed.get("date_created"),
                "modified_at": parsed.get("date_modified"),
            }
            yield json.dumps(note) + "\n"
            count += 1
            if count >= req.limit:
                break
        except Exception:
            continue
