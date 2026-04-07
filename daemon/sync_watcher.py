# daemon/sync_watcher.py
"""
VaultSyncWatcher: Three-mode vault sync.
  Mode 1: Full sync (startup / first run)
  Mode 2: Incremental file watcher (watchdog, real-time)
  Mode 3: Scheduled reconciliation (hourly)

Write Layer Gate (v0.2.0):
  - caller="user"      → vault layer (read/write anywhere in vault)
  - caller="agent"     → working layer only (_working/ buffer)
  - caller="heartbeat" → semantic layer (08 Meta/agent-context/, project notes)
  Semantic-layer writes from non-heartbeat callers are REJECTED.
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from asyncio import Queue
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from .health import mark_indexing, mark_ready, record_index_complete

logger = logging.getLogger("vault-memoryd.sync")

CHUNK_SIZE_TOKENS  = 512
CHUNK_OVERLAP_PCT  = 0.15
MIN_CHUNK_TOKENS   = 64
WORDS_PER_TOKEN    = 0.75
DEBOUNCE_SECONDS   = 2.0
RECONCILE_INTERVAL = 3600

# Folders writable only by heartbeat caller
SEMANTIC_LAYER_PREFIXES = (
    "08 Meta/agent-context",
    "08 Meta/heartbeat",
    "08 Meta/skills",
)

# Working buffer — agent session output goes here
WORKING_BUFFER_PREFIX = "_working"

# Frontmatter injected on all agent writes
AGENT_FRONTMATTER_DEFAULTS = {
    "agent-written": True,
    "agent-confidence": "medium",
    "agent-source-episodes": [],
    "trust": "low",
    "importance": 0.5,
    "decay-profile": "active",
}


@dataclass
class NoteChunk:
    uuid: str
    content: str
    vault_path: str
    project: str
    folder: str
    tags: List[str]
    date_created: str
    date_modified: str
    status: str
    chunk_index: int
    chunk_total: int
    content_hash: str
    trust: str = "high"
    importance: float = 1.0
    decay_profile: str = "active"
    agent_written: bool = False
    agent_confidence: Optional[str] = None
    embedding: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class SyncState:
    last_full_sync: Optional[str] = None
    file_hashes: Dict[str, str] = field(default_factory=dict)
    last_reconcile: Optional[str] = None


class MarkdownParser:
    FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
    TAG_RE         = re.compile(r"(?:^|\s)#([\w/]+)", re.MULTILINE)
    STATUS_RE      = re.compile(r"status:\s*(\S+)", re.IGNORECASE)

    def parse(self, path: Path, caller: str = "user") -> Dict[str, Any]:
        raw  = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        frontmatter = {}
        body = raw
        fm_match = self.FRONTMATTER_RE.match(raw)
        if fm_match:
            body = raw[fm_match.end():]
            frontmatter = self._parse_yaml_simple(fm_match.group(1))
        fm_tags     = frontmatter.get("tags", [])
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        inline_tags = self.TAG_RE.findall(body)
        tags = list(set(fm_tags + inline_tags))
        status  = frontmatter.get("status") or self._first_match(self.STATUS_RE, body) or "active"
        parts   = path.parts
        project = parts[1] if len(parts) > 2 else parts[0]

        # Inject agent metadata if written by agent
        if caller == "agent":
            for k, v in AGENT_FRONTMATTER_DEFAULTS.items():
                frontmatter.setdefault(k, v)

        return {
            "body":             body,
            "tags":             tags,
            "status":           status,
            "project":          project,
            "folder":           path.parent.name,
            "date_created":     datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            "date_modified":    datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "trust":            frontmatter.get("trust", "high" if caller == "user" else "low"),
            "importance":       float(frontmatter.get("importance", 1.0)),
            "decay_profile":    frontmatter.get("decay-profile", "active"),
            "agent_written":    bool(frontmatter.get("agent-written", caller == "agent")),
            "agent_confidence": frontmatter.get("agent-confidence"),
        }

    def _parse_yaml_simple(self, yaml_str: str) -> Dict[str, Any]:
        result = {}
        for line in yaml_str.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if v.startswith("["):
                    v = [i.strip().strip('"') for i in v.strip("[]").split(",") if i.strip()]
                elif v.lower() == "true":
                    v = True
                elif v.lower() == "false":
                    v = False
                elif v.replace(".", "", 1).lstrip("-").isdigit():
                    v = float(v) if "." in v else int(v)
                result[k] = v
        return result

    @staticmethod
    def _first_match(pattern, text):
        m = pattern.search(text)
        return m.group(1) if m else None


def _token_estimate(text: str) -> int:
    return max(1, int(len(text.split()) / WORDS_PER_TOKEN))


def _chunk_text(text: str) -> List[str]:
    words      = text.split()
    chunk_w    = max(int(CHUNK_SIZE_TOKENS * WORDS_PER_TOKEN), 10)
    overlap_w  = max(int(chunk_w * CHUNK_OVERLAP_PCT), 1)
    min_w      = int(MIN_CHUNK_TOKENS * WORDS_PER_TOKEN)
    chunks, i  = [], 0
    while i < len(words):
        end = min(i + chunk_w, len(words))
        chunk = " ".join(words[i:end])
        if len(chunk.split()) >= min_w or not chunks:
            chunks.append(chunk)
        i += chunk_w - overlap_w
    return chunks if chunks else [text]


def _is_semantic_path(vault_relative: str) -> bool:
    """Returns True if this path belongs to the semantic write layer."""
    return any(vault_relative.startswith(p) for p in SEMANTIC_LAYER_PREFIXES)


def _is_working_path(vault_relative: str) -> bool:
    """Returns True if this path is in the _working/ buffer."""
    return vault_relative.startswith(WORKING_BUFFER_PREFIX)


class SyncEngine:
    def __init__(self, vault_root, weaviate_client, pg_client, embedder):
        self.vault_root = Path(vault_root)
        self.weaviate   = weaviate_client
        self.pg         = pg_client
        self.embedder   = embedder
        self.parser     = MarkdownParser()
        self._state     = SyncState()
        self._state_path = self.vault_root / ".vault-memory-state.json"
        self._load_state()

    # ── write layer enforcement ───────────────────────────────────────────────

    def _enforce_write_layer(self, abs_path: Path, caller: str):
        """
        Enforce write layer discipline.
        Raises PermissionError if caller tries to write to semantic layer
        without heartbeat authorization.
        """
        try:
            rel = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel = str(abs_path)

        if _is_semantic_path(rel) and caller not in ("heartbeat", "user"):
            raise PermissionError(
                f"Semantic layer write rejected: '{rel}' requires heartbeat authorization. "
                f"Caller='{caller}'. Stage output in _working/ instead."
            )

        if caller == "agent" and not _is_working_path(rel):
            logger.warning(
                "Agent writing outside _working/: %s — "
                "redirecting is recommended; proceeding with trust:low",
                rel,
            )

    # ── core sync ────────────────────────────────────────────────────────────

    async def sync_file(
        self,
        abs_path: Path,
        caller: str = "user",
        agent_confidence: Optional[str] = None,
    ) -> int:
        """
        Parse, chunk, embed, and upsert a single .md file.
        caller: 'user' | 'agent' | 'heartbeat'
        Returns number of chunks upserted.
        """
        self._enforce_write_layer(abs_path, caller)

        meta   = self.parser.parse(abs_path, caller=caller)
        chunks = _chunk_text(meta["body"])
        total  = len(chunks)
        upserted = 0

        if agent_confidence:
            meta["agent_confidence"] = agent_confidence

        for idx, chunk_text in enumerate(chunks):
            content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]
            try:
                rel_path = str(abs_path.relative_to(self.vault_root))
            except ValueError:
                rel_path = str(abs_path)

            embedding = self.embedder.embed_one(chunk_text)

            chunk = NoteChunk(
                uuid=f"{rel_path}::{idx}",
                content=chunk_text,
                vault_path=rel_path,
                project=meta["project"],
                folder=meta["folder"],
                tags=meta["tags"],
                date_created=meta["date_created"],
                date_modified=meta["date_modified"],
                status=meta["status"],
                chunk_index=idx,
                chunk_total=total,
                content_hash=content_hash,
                trust=meta["trust"],
                importance=meta["importance"],
                decay_profile=meta["decay_profile"],
                agent_written=meta["agent_written"],
                agent_confidence=meta["agent_confidence"],
                embedding=embedding,
            )
            await self.weaviate.upsert_chunk(chunk)
            upserted += 1

        file_hash = hashlib.sha256(
            abs_path.read_bytes()
        ).hexdigest()[:16]
        try:
            rel = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel = str(abs_path)
        self._state.file_hashes[rel] = file_hash
        self._save_state()
        return upserted

    async def delete_file(self, abs_path: Path):
        try:
            rel = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel = str(abs_path)
        await self.weaviate.delete_by_path(rel)
        self._state.file_hashes.pop(rel, None)
        self._save_state()

    async def full_sync(self, caller: str = "user") -> Dict[str, int]:
        mark_indexing()
        stats = {"synced": 0, "skipped": 0, "errors": 0}
        md_files = list(self.vault_root.rglob("*.md"))
        for md_path in md_files:
            rel = str(md_path.relative_to(self.vault_root))
            # Skip _working/ during full sync unless caller is heartbeat
            if _is_working_path(rel) and caller != "heartbeat":
                stats["skipped"] += 1
                continue
            try:
                file_hash = hashlib.sha256(md_path.read_bytes()).hexdigest()[:16]
                if self._state.file_hashes.get(rel) == file_hash:
                    stats["skipped"] += 1
                    continue
                n = await self.sync_file(md_path, caller=caller)
                stats["synced"] += n
            except PermissionError as e:
                logger.warning("Write gate blocked: %s", e)
                stats["skipped"] += 1
            except Exception as e:
                logger.error("Error syncing %s: %s", rel, e)
                stats["errors"] += 1
        self._state.last_full_sync = datetime.now(timezone.utc).isoformat()
        self._save_state()
        record_index_complete(stats["synced"])
        mark_ready()
        return stats

    # ── state persistence ─────────────────────────────────────────────────────

    def _load_state(self):
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self._state = SyncState(**data)
            except Exception:
                pass

    def _save_state(self):
        self._state_path.write_text(
            json.dumps(asdict(self._state), indent=2)
        )


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, queue: Queue):
        self._queue   = queue
        self._pending: Dict[str, float] = {}

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._pending[event.src_path] = time.time()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory and event.src_path.endswith(".md"):
            self._pending[event.src_path] = time.time()

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory and event.src_path.endswith(".md"):
            asyncio.get_event_loop().call_soon_threadsafe(
                self._queue.put_nowait, ("delete", event.src_path)
            )

    async def flush_debounced(self):
        now = time.time()
        ready = [
            p for p, t in list(self._pending.items())
            if now - t >= DEBOUNCE_SECONDS
        ]
        for p in ready:
            del self._pending[p]
            await self._queue.put(("upsert", p))


class VaultSyncWatcher:
    def __init__(self, engine: SyncEngine):
        self.engine   = engine
        self._queue   = Queue()
        self._handler = _VaultEventHandler(self._queue)
        self._observer = Observer()

    async def start(self):
        vault = str(self.engine.vault_root)
        self._observer.schedule(self._handler, vault, recursive=True)
        self._observer.start()
        logger.info("Watcher started: %s", vault)
        await asyncio.gather(
            self._process_queue(),
            self._debounce_loop(),
            self._reconcile_loop(),
        )

    def stop(self):
        self._observer.stop()
        self._observer.join()

    async def _process_queue(self):
        while True:
            event_type, path = await self._queue.get()
            abs_path = Path(path)
            try:
                if event_type == "upsert" and abs_path.exists():
                    await self.engine.sync_file(abs_path, caller="user")
                elif event_type == "delete":
                    await self.engine.delete_file(abs_path)
            except Exception as e:
                logger.error("Queue processor error [%s] %s: %s", event_type, path, e)

    async def _debounce_loop(self):
        while True:
            await asyncio.sleep(0.5)
            await self._handler.flush_debounced()

    async def _reconcile_loop(self):
        while True:
            await asyncio.sleep(RECONCILE_INTERVAL)
            logger.info("Scheduled reconciliation starting...")
            try:
                stats = await self.engine.full_sync(caller="user")
                logger.info("Reconciliation complete: %s", stats)
            except Exception as e:
                logger.error("Reconciliation error: %s", e)
