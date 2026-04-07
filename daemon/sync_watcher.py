# daemon/sync_watcher.py
"""
VaultSyncWatcher: Three-mode vault sync.
  Mode 1: Full sync (startup / first run)
  Mode 2: Incremental file watcher (watchdog, real-time)
  Mode 3: Scheduled reconciliation (hourly)
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

    def parse(self, path: Path) -> Dict[str, Any]:
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
        return {
            "body":          body,
            "tags":          tags,
            "status":        status,
            "project":       project,
            "folder":        path.parent.name,
            "date_created":  datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            "date_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "frontmatter":   frontmatter,
        }

    def _parse_yaml_simple(self, yaml_text: str) -> Dict[str, Any]:
        result = {}
        for line in yaml_text.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                result[key.strip()] = val.strip()
        return result

    def _first_match(self, pattern, text) -> Optional[str]:
        m = pattern.search(text)
        return m.group(1) if m else None


class MarkdownChunker:
    def __init__(self, chunk_size=CHUNK_SIZE_TOKENS, overlap_pct=CHUNK_OVERLAP_PCT, min_tokens=MIN_CHUNK_TOKENS):
        self.chunk_size = chunk_size
        self.overlap    = int(chunk_size * overlap_pct)
        self.min_tokens = min_tokens

    def _approx_tokens(self, text: str) -> int:
        return int(len(text.split()) / WORDS_PER_TOKEN)

    def _split_at_boundaries(self, text: str) -> List[str]:
        h1h2_sections = re.split(r"\n(?=#{1,2}\s)", text)
        units = []
        for section in h1h2_sections:
            if self._approx_tokens(section) <= self.chunk_size:
                units.append(section)
                continue
            sub_sections = re.split(r"\n(?=#{3,6}\s)", section)
            for sub in sub_sections:
                if self._approx_tokens(sub) <= self.chunk_size:
                    units.append(sub)
                    continue
                paragraphs = re.split(r"\n{2,}", sub)
                for para in paragraphs:
                    if self._approx_tokens(para) <= self.chunk_size:
                        units.append(para)
                        continue
                    sentences = re.split(r"(?<=[.!?])\s+", para)
                    units.extend(sentences)
        return [u.strip() for u in units if u.strip()]

    def chunk(self, text: str, vault_path: str) -> List[str]:
        units = self._split_at_boundaries(text)
        chunks, current_units, current_tokens = [], [], 0
        for unit in units:
            unit_tokens = self._approx_tokens(unit)
            if current_tokens + unit_tokens > self.chunk_size and current_units:
                chunk_text = "\n\n".join(current_units)
                if self._approx_tokens(chunk_text) >= self.min_tokens:
                    chunks.append(chunk_text)
                overlap_units, overlap_tokens = [], 0
                for u in reversed(current_units):
                    t = self._approx_tokens(u)
                    if overlap_tokens + t <= self.overlap:
                        overlap_units.insert(0, u)
                        overlap_tokens += t
                    else:
                        break
                current_units  = overlap_units + [unit]
                current_tokens = overlap_tokens + unit_tokens
            else:
                current_units.append(unit)
                current_tokens += unit_tokens
        if current_units:
            chunk_text = "\n\n".join(current_units)
            if self._approx_tokens(chunk_text) >= self.min_tokens:
                chunks.append(chunk_text)
        return chunks


class SyncEngine:
    def __init__(self, vault_path, weaviate, postgres, embedder):
        self.vault_path = Path(vault_path)
        self.weaviate   = weaviate
        self.postgres   = postgres
        self.embedder   = embedder
        self.parser     = MarkdownParser()
        self.chunker    = MarkdownChunker()
        self.state_file = self.vault_path / ".vault-memory-sync-state.json"
        self.state      = self._load_state()

    def _load_state(self) -> SyncState:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                return SyncState(**data)
            except Exception:
                pass
        return SyncState()

    def _save_state(self):
        self.state_file.write_text(json.dumps(asdict(self.state), indent=2))

    def _file_hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _chunk_uuid(self, vault_path: str, chunk_index: int) -> str:
        key = f"{vault_path}::{chunk_index}"
        return hashlib.sha1(key.encode()).hexdigest()[:32]

    async def sync_file(self, abs_path: Path) -> int:
        rel_path = str(abs_path.relative_to(self.vault_path))
        if abs_path.suffix.lower() != ".md" or abs_path.name.startswith("."):
            return 0
        current_hash = self._file_hash(abs_path)
        if self.state.file_hashes.get(rel_path) == current_hash:
            logger.debug("Skipping unchanged: %s", rel_path)
            return 0
        try:
            meta       = self.parser.parse(abs_path)
            raw_chunks = self.chunker.chunk(meta["body"], rel_path)
            total      = len(raw_chunks)
            if total == 0:
                return 0
            embeddings = self.embedder.embed_batch(raw_chunks)
            note_chunks = [
                NoteChunk(
                    uuid          = self._chunk_uuid(rel_path, i),
                    content       = text,
                    vault_path    = rel_path,
                    project       = meta["project"],
                    folder        = meta["folder"],
                    tags          = meta["tags"],
                    date_created  = meta["date_created"],
                    date_modified = meta["date_modified"],
                    status        = meta["status"],
                    chunk_index   = i,
                    chunk_total   = total,
                    content_hash  = hashlib.sha256(text.encode()).hexdigest(),
                    embedding     = emb,
                )
                for i, (text, emb) in enumerate(zip(raw_chunks, embeddings))
            ]
            await self.weaviate.delete_by_path(rel_path)
            await self.weaviate.batch_upsert(note_chunks)
            await self._upsert_postgres(rel_path, meta, note_chunks)
            self.state.file_hashes[rel_path] = current_hash
            self._save_state()
            logger.info("Synced %s -> %d chunks", rel_path, total)
            return total
        except Exception as e:
            logger.error("Failed to sync %s: %s", rel_path, e)
            return 0

    async def delete_file(self, abs_path: Path):
        rel_path = str(abs_path.relative_to(self.vault_path))
        await self.weaviate.delete_by_path(rel_path)
        await self._delete_postgres(rel_path)
        self.state.file_hashes.pop(rel_path, None)
        self._save_state()

    async def _upsert_postgres(self, rel_path, meta, chunks):
        cursor = self.postgres.conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO temporal_entities (entity_name, valid_from, properties, change_summary)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (entity_name) DO UPDATE
                SET valid_from = EXCLUDED.valid_from,
                    properties = EXCLUDED.properties,
                    change_summary = EXCLUDED.change_summary
            """, (
                rel_path, meta["date_modified"],
                json.dumps({"vault_path": rel_path, "project": meta["project"],
                            "tags": meta["tags"], "status": meta["status"],
                            "chunk_count": len(chunks)}),
                f"Synced {len(chunks)} chunks from {rel_path}",
            ))
            for chunk in chunks:
                cursor.execute("""
                    INSERT INTO vault_entity_links (entity_id, vault_path, chunk_uuid)
                    VALUES (gen_random_uuid(), %s, %s)
                    ON CONFLICT (vault_path, chunk_uuid) DO NOTHING
                """, (rel_path, chunk.uuid))
            self.postgres.conn.commit()
        except Exception as e:
            self.postgres.conn.rollback()
            logger.error("PostgreSQL upsert failed for %s: %s", rel_path, e)
        finally:
            cursor.close()

    async def _delete_postgres(self, rel_path):
        cursor = self.postgres.conn.cursor()
        try:
            cursor.execute("DELETE FROM vault_entity_links WHERE vault_path = %s", (rel_path,))
            cursor.execute("DELETE FROM temporal_entities WHERE entity_name = %s", (rel_path,))
            self.postgres.conn.commit()
        finally:
            cursor.close()


class VaultSyncWatcher:
    def __init__(self, vault_path, weaviate, postgres, embedder):
        self.vault_path    = Path(vault_path)
        self.engine        = SyncEngine(self.vault_path, weaviate, postgres, embedder)
        self._event_queue: Queue = Queue()
        self._observer: Optional[Observer] = None
        self._running      = False

    async def start(self):
        self._running = True
        if self.engine.state.last_full_sync is None:
            logger.info("No prior sync state — running full sync...")
            mark_indexing()
            await self.full_sync()
        self._start_watcher()
        await asyncio.gather(self._process_event_queue(), self._reconcile_loop())

    async def stop(self):
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()

    async def full_sync(self):
        md_files     = list(self.vault_path.rglob("*.md"))
        total_files  = len(md_files)
        total_chunks = 0
        start        = time.monotonic()
        logger.info("Full sync: %d files", total_files)
        for i, path in enumerate(md_files):
            total_chunks += await self.engine.sync_file(path)
            if (i + 1) % 50 == 0:
                logger.info("  Full sync progress: %d/%d", i + 1, total_files)
        self.engine.state.last_full_sync = datetime.now(timezone.utc).isoformat()
        self.engine._save_state()
        record_index_complete()
        mark_ready()
        logger.info("Full sync complete: %d files, %d chunks in %.1fs",
                    total_files, total_chunks, time.monotonic() - start)

    def _start_watcher(self):
        event_queue = self._event_queue

        class VaultEventHandler(FileSystemEventHandler):
            def on_created(self, event: FileSystemEvent):
                if not event.is_directory and event.src_path.endswith(".md"):
                    asyncio.get_event_loop().call_soon_threadsafe(
                        event_queue.put_nowait, ("upsert", event.src_path)
                    )
            def on_modified(self, event: FileSystemEvent):
                if not event.is_directory and event.src_path.endswith(".md"):
                    asyncio.get_event_loop().call_soon_threadsafe(
                        event_queue.put_nowait, ("upsert", event.src_path)
                    )
            def on_deleted(self, event: FileSystemEvent):
                if not event.is_directory and event.src_path.endswith(".md"):
                    asyncio.get_event_loop().call_soon_threadsafe(
                        event_queue.put_nowait, ("delete", event.src_path)
                    )
            def on_moved(self, event: FileSystemEvent):
                if not event.is_directory:
                    asyncio.get_event_loop().call_soon_threadsafe(
                        event_queue.put_nowait, ("delete", event.src_path)
                    )
                    if event.dest_path.endswith(".md"):
                        asyncio.get_event_loop().call_soon_threadsafe(
                            event_queue.put_nowait, ("upsert", event.dest_path)
                        )

        self._observer = Observer()
        self._observer.schedule(VaultEventHandler(), str(self.vault_path), recursive=True)
        self._observer.start()
        logger.info("File watcher started on: %s", self.vault_path)

    async def _process_event_queue(self):
        pending: Dict[str, tuple] = {}
        while self._running:
            try:
                while True:
                    action, path = self._event_queue.get_nowait()
                    pending[path] = (action, time.monotonic())
            except asyncio.QueueEmpty:
                pass
            now = time.monotonic()
            to_process = {p: a for p, (a, ts) in pending.items() if now - ts >= DEBOUNCE_SECONDS}
            for path, action in to_process.items():
                del pending[path]
                abs_path = Path(path)
                if action == "upsert":
                    await self.engine.sync_file(abs_path)
                elif action == "delete":
                    await self.engine.delete_file(abs_path)
            await asyncio.sleep(0.5)

    async def _reconcile_loop(self):
        while self._running:
            await asyncio.sleep(RECONCILE_INTERVAL)
            logger.info("Starting reconciliation pass...")
            drifted = 0
            for path in self.vault_path.rglob("*.md"):
                if path.name.startswith("."):
                    continue
                rel          = str(path.relative_to(self.vault_path))
                current_hash = self.engine._file_hash(path)
                if current_hash != self.engine.state.file_hashes.get(rel):
                    await self.engine.sync_file(path)
                    drifted += 1
            self.engine.state.last_reconcile = datetime.now(timezone.utc).isoformat()
            self.engine._save_state()
            record_index_complete()
            logger.info("Reconciliation complete: %d drifted files", drifted)
