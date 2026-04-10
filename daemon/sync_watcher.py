# daemon/sync_watcher.py
"""
Vault sync engine with:
- full sync, watcher-based incremental sync, and reconcile loop
- write-layer gates for user/agent/heartbeat callers
- markdown and canvas parsing + indexing
"""

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from asyncio import Queue
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import frontmatter as fm
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from .health import mark_indexing, mark_ready, record_index_complete

logger = logging.getLogger("vault-memoryd.sync")
security_logger = logging.getLogger("vault-memoryd.security")

CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_PCT = 0.15
MIN_CHUNK_TOKENS = 64
WORDS_PER_TOKEN = 0.75
DEBOUNCE_SECONDS = 2.0
RECONCILE_INTERVAL = 3600

# Folders writable only by heartbeat caller
SEMANTIC_LAYER_PREFIXES = (
    "08 Meta/agent-context",
    "08 Meta/heartbeat",
    "08 Meta/skills",
)

# Working buffer - agent session output goes here
WORKING_BUFFER_PREFIX = "_working"

# Canvas file extension
CANVAS_FILE_EXTENSION = ".canvas"

# Agent frontmatter defaults
AGENT_FRONTMATTER_DEFAULTS = {
    "agent-written": True,
    "agent-confidence": "medium",
    "agent-source-episodes": [],
    "trust": "low",
    "importance": 0.5,
    "decay-profile": "active",
    "maturity": "seed",
    "status": "working",
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
    maturity: str = "seed"
    agent_written: bool = False
    agent_confidence: Optional[str] = None
    embedding: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class CanvasNode:
    uuid: str
    content: str
    vault_path: str
    project: str
    folder: str
    tags: List[str]
    date_created: str
    date_modified: str
    status: str = "active"
    chunk_index: int = 0
    chunk_total: int = 1
    content_hash: str = ""
    trust: str = "high"
    importance: float = 1.0
    decay_profile: str = "active"
    maturity: str = "seed"
    agent_written: bool = False
    agent_confidence: Optional[str] = None
    embedding: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class CanvasEdge:
    uuid: str
    content: str
    vault_path: str
    project: str
    folder: str
    tags: List[str]
    date_created: str
    date_modified: str
    status: str = "active"
    chunk_index: int = 0
    chunk_total: int = 1
    content_hash: str = ""
    trust: str = "high"
    importance: float = 1.0
    decay_profile: str = "active"
    maturity: str = "seed"
    agent_written: bool = False
    agent_confidence: Optional[str] = None
    embedding: Optional[List[float]] = field(default=None, repr=False)


@dataclass
class SyncState:
    last_full_sync: Optional[str] = None
    file_hashes: Dict[str, str] = field(default_factory=dict)
    last_reconcile: Optional[str] = None


class MarkdownParser:
    TAG_RE = re.compile(r"(?:^|\s)#([\w/]+)", re.MULTILINE)
    STATUS_RE = re.compile(r"status:\s*(\S+)", re.IGNORECASE)

    def parse(self, path: Path, caller: str = "user") -> Dict[str, Any]:
        raw = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        try:
            post = fm.loads(raw)
            frontmatter_data = dict(post.metadata)
            body = post.content
        except Exception as e:
            logger.warning(
                "frontmatter parse failed for %s: %s - falling back to body-only", path, e
            )
            frontmatter_data = {}
            body = raw

        fm_tags = frontmatter_data.get("tags", [])
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        inline_tags = self.TAG_RE.findall(body)
        tags = list(set(fm_tags + inline_tags))
        status = (
            frontmatter_data.get("status") or self._first_match(self.STATUS_RE, body) or "active"
        )

        parts = path.parts
        project = parts[1] if len(parts) > 2 else parts[0]

        if caller == "agent":
            for k, v in AGENT_FRONTMATTER_DEFAULTS.items():
                frontmatter_data.setdefault(k, v)

        default_maturity = "seed" if caller == "agent" else "sapling"
        maturity = frontmatter_data.get("maturity", default_maturity)

        return {
            "body": body,
            "tags": tags,
            "status": status,
            "project": project,
            "folder": path.parent.name,
            "date_created": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            "date_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "trust": frontmatter_data.get("trust", "high" if caller == "user" else "low"),
            "importance": float(frontmatter_data.get("importance", 1.0)),
            "decay_profile": frontmatter_data.get("decay-profile", "active"),
            "maturity": maturity,
            "agent_written": bool(frontmatter_data.get("agent-written", caller == "agent")),
            "agent_confidence": frontmatter_data.get("agent-confidence"),
        }

    @staticmethod
    def _first_match(pattern, text):
        m = pattern.search(text)
        return m.group(1) if m else None


def _token_estimate(text: str) -> int:
    return max(1, int(len(text.split()) / WORDS_PER_TOKEN))


def _chunk_text(text: str) -> List[str]:
    words = text.split()
    chunk_w = max(int(CHUNK_SIZE_TOKENS * WORDS_PER_TOKEN), 10)
    overlap_w = max(int(chunk_w * CHUNK_OVERLAP_PCT), 1)
    min_w = int(MIN_CHUNK_TOKENS * WORDS_PER_TOKEN)

    chunks, i = [], 0
    while i < len(words):
        end = min(i + chunk_w, len(words))
        chunk = " ".join(words[i:end])
        if len(chunk.split()) >= min_w or not chunks:
            chunks.append(chunk)
        i += chunk_w - overlap_w
    return chunks if chunks else [text]


def _sanitize_for_context(text: str) -> str:
    patterns = [
        r"(?i)ignore\s+previous\s+instructions",
        r"(?i)disregard\s+(?:the\s+)?(?:above|prior|previous)\s+(?:instructions|content)",
        r"(?i)you\s+(?:are\s+)?(?:now|will)\s+(?:be|become|a)\s+",
        r"(?i)system\s*:\s*(?:instruction|prompt|command|directive)",
        r"(?i)<\|endofprompt\|>",
        r"(?i)<\|startofprompt\|>",
        r"(?i)<\|assistant\|>",
        r"(?i)<\|user\|>",
        r"(?i)<\|system\|>",
        r"(?i)<\|im\|>start",
        r"(?i)<\|im\|>end",
        r"(?i)\[INST\]",
        r"(?i)\[/INST\]",
        r"(?i)\[SYS\]",
        r"(?i)\[/SYS\]",
        r"(?i)<\|beginof\w+\|>",
        r"(?i)<\|endof\w+\|>",
    ]
    sanitized = text
    injection_count = 0
    for pattern in patterns:
        matches = re.findall(pattern, sanitized)
        if matches:
            injection_count += len(matches)
            sanitized = re.sub(pattern, "[SANITIZED]", sanitized)
    if injection_count > 0:
        security_logger.warning(
            "Injection pattern detected and stripped: %d pattern(s) in context", injection_count
        )
    return sanitized


def _is_semantic_path(vault_relative: str) -> bool:
    return any(vault_relative.startswith(p) for p in SEMANTIC_LAYER_PREFIXES)


def _is_working_path(vault_relative: str) -> bool:
    return vault_relative.startswith(WORKING_BUFFER_PREFIX)


class CanvasParser:
    """Parses Obsidian Canvas JSON format: {nodes: [{type, id, text, file, ...}], edges: [{fromNode, toNode, id, ...}]}"""

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root

    def parse(self, path: Path, caller: str = "user") -> Tuple[List[CanvasNode], List[CanvasEdge]]:
        raw = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("Canvas JSON parse failed for %s: %s", path, e)
            return [], []

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        parts = path.parts
        project = parts[1] if len(parts) > 2 else parts[0]
        date_created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
        date_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        rel_path = (
            str(path.relative_to(self.vault_root))
            if path.is_relative_to(self.vault_root)
            else str(path)
        )
        folder = path.parent.name

        parsed_nodes: List[CanvasNode] = []
        for node in nodes:
            node_id = node.get("id", "")
            text = node.get("text", "")
            file_path = node.get("file", "")
            content = f"{text}\n\n[file: {file_path}]" if file_path else text
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            parsed_nodes.append(
                CanvasNode(
                    uuid=f"{rel_path}::node::{node_id}",
                    content=content,
                    vault_path=rel_path,
                    project=project,
                    folder=folder,
                    tags=[],
                    date_created=date_created,
                    date_modified=date_modified,
                    content_hash=content_hash,
                    agent_written=(caller == "agent"),
                    agent_confidence=None,
                )
            )

        parsed_edges: List[CanvasEdge] = []
        for edge in edges:
            edge_id = edge.get("id", "")
            from_node = edge.get("fromNode", "")
            to_node = edge.get("toNode", "")
            content = f"Connection: {from_node} -> {to_node}"
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            parsed_edges.append(
                CanvasEdge(
                    uuid=f"{rel_path}::edge::{edge_id}",
                    content=content,
                    vault_path=rel_path,
                    project=project,
                    folder=folder,
                    tags=[],
                    date_created=date_created,
                    date_modified=date_modified,
                    content_hash=content_hash,
                    agent_written=(caller == "agent"),
                    agent_confidence=None,
                )
            )

        return parsed_nodes, parsed_edges


class SyncEngine:
    """Syncs vault files to Weaviate + PostgreSQL. Canvas files upsert to entity_links and relationships."""

    def __init__(self, vault_root, weaviate_client, pg_client, embedder):
        self.vault_root = Path(vault_root)
        self.weaviate = weaviate_client
        self.pg = pg_client
        self.embedder = embedder
        self.parser = MarkdownParser()
        self.canvas_parser = CanvasParser(self.vault_root)
        self._state = SyncState()
        self._state_path = self.vault_root / ".vault-memory-state.json"
        self._load_state()

    @property
    def state(self) -> SyncState:
        return self._state

    def _enforce_write_layer(self, abs_path: Path, caller: str):
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
                "Agent writing outside _working/: %s - "
                "redirecting is recommended; proceeding with trust:low",
                rel,
            )

    def _upsert_entity_link(self, vault_path: str, chunk_uuid: str):
        """Upsert canvas node to vault_entity_links table."""
        try:
            with self.pg.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO vault_entity_links (entity_id, vault_path, chunk_uuid)
                    VALUES (gen_random_uuid(), %s, %s)
                    ON CONFLICT (vault_path, chunk_uuid) DO NOTHING
                """,
                    (vault_path, chunk_uuid),
                )
        except Exception as e:
            logger.error("Failed to upsert entity_link for %s: %s", vault_path, e)

    def _upsert_relationship(self, from_name: str, to_name: str):
        """Upsert canvas edge to relationships table."""
        try:
            with self.pg.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO relationships (source_name, target_name, relationship_type, edge_source)
                    VALUES (%s, %s, 'references', 'body')
                """,
                    (from_name, to_name),
                )
        except Exception as e:
            logger.error("Failed to upsert relationship %s->%s: %s", from_name, to_name, e)

    async def sync_file(
        self,
        abs_path: Path,
        caller: str = "user",
        agent_confidence: Optional[str] = None,
    ) -> int:
        self._enforce_write_layer(abs_path, caller)
        if abs_path.suffix == CANVAS_FILE_EXTENSION:
            return await self._sync_canvas(abs_path, caller)

        meta = self.parser.parse(abs_path, caller=caller)
        chunks = _chunk_text(meta["body"])
        total = len(chunks)
        upserted = 0

        if agent_confidence:
            meta["agent_confidence"] = agent_confidence

        raw_importance = meta["importance"]
        maturity = meta.get("maturity", "seed")
        if maturity == "seed":
            importance = min(raw_importance, 0.4)
        elif maturity == "tree":
            importance = max(raw_importance, 0.8)
        else:
            importance = raw_importance

        for idx, chunk_text in enumerate(chunks):
            content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]
            try:
                rel_path = str(abs_path.relative_to(self.vault_root))
            except ValueError:
                rel_path = str(abs_path)

            embedding = await self.embedder.embed_one(chunk_text)
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
                importance=importance,
                decay_profile=meta["decay_profile"],
                maturity=maturity,
                agent_written=meta["agent_written"],
                agent_confidence=meta["agent_confidence"],
                embedding=embedding,
            )
            await self.weaviate.upsert_chunk(chunk)
            upserted += 1

        file_hash = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
        try:
            rel = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel = str(abs_path)
        self._state.file_hashes[rel] = file_hash
        self._save_state()
        return upserted

    async def _sync_canvas(self, abs_path: Path, caller: str = "user") -> int:
        """Sync canvas file: nodes -> Weaviate + entity_links, edges -> Weaviate + relationships."""
        nodes, edges = self.canvas_parser.parse(abs_path, caller=caller)
        upserted = 0

        try:
            rel_path = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel_path = str(abs_path)

        # Upsert nodes to Weaviate and Postgres entity_links
        for node in nodes:
            node.embedding = await self.embedder.embed_one(node.content)
            await self.weaviate.upsert_chunk(node)
            # Upsert to vault_entity_links (file nodes -> entity_links)
            self._upsert_entity_link(node.vault_path, node.uuid)
            upserted += 1

        # Upsert edges to Weaviate and Postgres relationships
        for edge in edges:
            edge.embedding = await self.embedder.embed_one(edge.content)
            await self.weaviate.upsert_chunk(edge)
            # Extract fromNode and toNode from edge content for relationships
            # Edge content format: "Connection: {from_node} -> {to_node}"
            match = re.search(r"Connection: (.+?) -> (.+)", edge.content)
            if match:
                from_node, to_node = match.groups()
                self._upsert_relationship(from_node, to_node)
            upserted += 1

        file_hash = hashlib.sha256(abs_path.read_bytes()).hexdigest()[:16]
        self._state.file_hashes[rel_path] = file_hash
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
        canvas_files = list(self.vault_root.rglob(f"*{CANVAS_FILE_EXTENSION}"))
        all_files = md_files + canvas_files

        for file_path in all_files:
            rel = (
                str(file_path.relative_to(self.vault_root))
                if file_path.is_relative_to(self.vault_root)
                else str(file_path)
            )
            if _is_working_path(rel) and caller != "heartbeat":
                stats["skipped"] += 1
                continue
            try:
                file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]
                if self._state.file_hashes.get(rel) == file_hash:
                    stats["skipped"] += 1
                    continue
                n = await self.sync_file(file_path, caller=caller)
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

    def _load_state(self):
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self._state = SyncState(**data)
            except Exception:
                pass

    def _save_state(self):
        self._state_path.write_text(json.dumps(asdict(self._state), indent=2))


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, queue: Queue, loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = loop
        self._pending: Dict[str, float] = {}

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            if event.src_path.endswith(".md") or event.src_path.endswith(CANVAS_FILE_EXTENSION):
                self._pending[event.src_path] = time.time()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            if event.src_path.endswith(".md") or event.src_path.endswith(CANVAS_FILE_EXTENSION):
                self._pending[event.src_path] = time.time()

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            if event.src_path.endswith(".md") or event.src_path.endswith(CANVAS_FILE_EXTENSION):
                self._pending.pop(event.src_path, None)
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ("delete", event.src_path))

    async def flush_debounced(self):
        now = time.time()
        ready = [p for p, t in list(self._pending.items()) if now - t >= DEBOUNCE_SECONDS]
        for p in ready:
            del self._pending[p]
            await self._queue.put(("upsert", p))


class VaultSyncWatcher:
    def __init__(self, engine: SyncEngine):
        self.engine = engine
        self._queue = Queue()
        self._handler = None
        self._observer = Observer()

    async def start(self):
        vault = str(self.engine.vault_root)
        loop = asyncio.get_running_loop()
        self._handler = _VaultEventHandler(self._queue, loop)
        self._observer.schedule(self._handler, vault, recursive=True)
        self._observer.start()
        logger.info("Watcher started: %s", vault)
        await asyncio.gather(
            self._process_queue(),
            self._debounce_loop(),
            self._reconcile_loop(),
        )

    async def stop(self):
        if self._observer.is_alive():
            self._observer.stop()
            await asyncio.to_thread(self._observer.join, timeout=5.0)
            if self._observer.is_alive():
                logger.warning("Observer thread did not terminate within 5s")

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
