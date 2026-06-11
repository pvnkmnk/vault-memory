# daemon/sync_watcher.py
# S20-C: Parallel file processing with async worker pool
# S20-E: State file write batching

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from asyncio import Queue, Semaphore
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import frontmatter as fm
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from .health import mark_indexing, mark_ready, record_index_complete
from .canvas_graph_pipeline import CanvasGraphPipeline
from .helpers.security import _sanitize_for_context, security_logger
try:
    from psycopg2.extras import execute_values
except Exception:  # pragma: no cover - psycopg2 may be unavailable in lite-only installs
    execute_values = None

logger = logging.getLogger(__name__)


CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_PCT = 0.15
MIN_CHUNK_TOKENS = 64
WORDS_PER_TOKEN = 0.75
DEBOUNCE_SECONDS = 2.0
RECONCILE_INTERVAL = 3600

# Default sync concurrency (can be overridden via config)
DEFAULT_SYNC_CONCURRENCY = 10
# Batch size for memory-efficient file processing
FILE_BATCH_SIZE = 20

# S20-E: State write batching defaults
DEFAULT_STATE_WRITE_BATCH = 10
DEFAULT_STATE_WRITE_TIMEOUT_S = 30

# Folders writable only by heartbeat caller
SEMANTIC_LAYER_PREFIXES = (
    '08 Meta/agent-context',
    '08 Meta/heartbeat',
    '08 Meta/skills',
)

# Working buffer - agent session output goes here
WORKING_BUFFER_PREFIX = '_working'

# Canvas file extension
CANVAS_FILE_EXTENSION = '.canvas'

# Agent frontmatter defaults
AGENT_FRONTMATTER_DEFAULTS = {
    'agent-written': True,
    'agent-confidence': 'medium',
    'agent-source-episodes': [],
    'trust': 'low',
    'importance': 0.5,
    'decay-profile': 'active',
    'maturity': 'seed',
    'status': 'working',
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
    trust: str = 'high'
    importance: float = 1.0
    decay_profile: str = 'active'
    maturity: str = 'seed'
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
    status: str = 'active'
    chunk_index: int = 0
    chunk_total: int = 1
    content_hash: str = ''
    trust: str = 'high'
    importance: float = 1.0
    decay_profile: str = 'active'
    maturity: str = 'seed'
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
    status: str = 'active'
    chunk_index: int = 0
    chunk_total: int = 1
    content_hash: str = ''
    trust: str = 'high'
    importance: float = 1.0
    decay_profile: str = 'active'
    maturity: str = 'seed'
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

    async def parse(self, path: Path, caller: str = 'user') -> Dict[str, Any]:
        # S20-C Fix: Use asyncio.to_thread to avoid blocking event loop
        raw = await asyncio.to_thread(path.read_text, encoding='utf-8', errors='replace')
        stat = await asyncio.to_thread(path.stat)
        return self._parse_content(raw, stat, path, caller)

    def _parse_content(self, raw: str, stat, path: Path, caller: str = 'user') -> Dict[str, Any]:
        try:
            post = fm.loads(raw)
            frontmatter_data = dict(post.metadata)
            body = post.content
        except Exception as e:
            logger.warning(
                'frontmatter parse failed for %s: %s - falling back to body-only', path, e
            )
            frontmatter_data = {}
            body = raw

        fm_tags = frontmatter_data.get('tags', [])
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        inline_tags = self.TAG_RE.findall(body)
        tags = list(set(fm_tags + inline_tags))
        
        # Ensure status is always a string
        status_val = frontmatter_data.get('status') or self._first_match(self.STATUS_RE, body) or 'active'
        if isinstance(status_val, list):
            status = status_val[0] if status_val else 'active'
        else:
            status = str(status_val) if status_val else 'active'

        parts = path.parts
        project = parts[1] if len(parts) > 2 else parts[0]

        if caller == 'agent':
            for k, v in AGENT_FRONTMATTER_DEFAULTS.items():
                frontmatter_data.setdefault(k, v)

        default_maturity = 'seed' if caller == 'agent' else 'sapling'
        maturity = frontmatter_data.get('maturity', default_maturity)

        return {
            'body': body,
            'tags': tags,
            'status': status,
            'project': project,
            'folder': path.parent.name,
            'date_created': datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            'date_modified': datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            'trust': frontmatter_data.get('trust', 'high' if caller == 'user' else 'low'),
            'importance': float(frontmatter_data.get('importance', 1.0)),
            'decay_profile': frontmatter_data.get('decay-profile', 'active'),
            'maturity': maturity,
            'agent_written': bool(frontmatter_data.get('agent-written', caller == 'agent')),
            'agent_confidence': frontmatter_data.get('agent-confidence'),
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
        chunk = ' '.join(words[i:end])
        if len(chunk.split()) >= min_w or not chunks:
            chunks.append(chunk)
        i += chunk_w - overlap_w
    return chunks if chunks else [text]




def _is_semantic_path(vault_relative: str) -> bool:
    return any(vault_relative.startswith(p) for p in SEMANTIC_LAYER_PREFIXES)


def _is_working_path(vault_relative: str) -> bool:
    return vault_relative.startswith(WORKING_BUFFER_PREFIX)


class CanvasParser:
    '''Parses Obsidian Canvas JSON format: {nodes: [{type, id, text, file, ...}], edges: [{fromNode, toNode, id, ...}]}'''

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root

    async def parse(self, path: Path, caller: str = 'user') -> Tuple[List[CanvasNode], List[CanvasEdge]]:
        # S20-C Fix: Use asyncio.to_thread to avoid blocking event loop
        raw = await asyncio.to_thread(path.read_text, encoding='utf-8', errors='replace')
        stat = await asyncio.to_thread(path.stat)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning('Canvas JSON parse failed for %s: %s', path, e)
            return [], []

        nodes = data.get('nodes', [])
        edges = data.get('edges', [])

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
            node_id = node.get('id', '')
            text = node.get('text', '')
            file_path = node.get('file', '')
            content = f'{text}\n\n[file: {file_path}]' if file_path else text
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            parsed_nodes.append(
                CanvasNode(
                    uuid=f'{rel_path}::node::{node_id}',
                    content=content,
                    vault_path=rel_path,
                    project=project,
                    folder=folder,
                    tags=[],
                    date_created=date_created,
                    date_modified=date_modified,
                    status='active',
                    chunk_index=0,
                    chunk_total=1,
                    content_hash=content_hash,
                )
            )

        parsed_edges: List[CanvasEdge] = []
        for edge in edges:
            edge_id = edge.get('id', '')
            from_node = edge.get('fromNode', '')
            to_node = edge.get('toNode', '')
            content = f'Connection: {from_node} -> {to_node}'
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            parsed_edges.append(
                CanvasEdge(
                    uuid=f'{rel_path}::edge::{edge_id}',
                    content=content,
                    vault_path=rel_path,
                    project=project,
                    folder=folder,
                    tags=[],
                    date_created=date_created,
                    date_modified=date_modified,
                    status='active',
                    chunk_index=0,
                    chunk_total=1,
                    content_hash=content_hash,
                )
            )

        return parsed_nodes, parsed_edges


class SyncEngine:
    # S20-C: Parallel file processing with async worker pool

    def __init__(
        self,
        vault_root: str | Path,
        weaviate_client,
        pg_client,
        embedder,
        sync_concurrency: int = DEFAULT_SYNC_CONCURRENCY,
        state_write_batch: int = DEFAULT_STATE_WRITE_BATCH,
        state_write_timeout_s: int = DEFAULT_STATE_WRITE_TIMEOUT_S,
    ):
        self.vault_root = Path(vault_root)
        self.weaviate = weaviate_client
        self.pg = pg_client
        self.embedder = embedder
        self.markdown_parser = MarkdownParser()
        self.canvas_parser = CanvasParser(self.vault_root)
        self.canvas_graph = CanvasGraphPipeline()

        # S20-C: Sync concurrency configuration
        self._sync_concurrency = max(1, min(sync_concurrency, 50))  # Clamp 1-50
        self._semaphore: Optional[asyncio.Semaphore] = None  # Created per full_sync

        # S20-E: State write batching configuration
        self._state_write_batch = max(1, state_write_batch)
        self._state_write_timeout_s = max(1, state_write_timeout_s)
        self._pending_state_writes: Dict[str, Optional[str]] = {}  # path -> new_hash or None for delete
        self._pending_full_sync_time: Optional[str] = None  # Deferred last_full_sync update
        self._last_state_write_time: float = time.time()
        # S20-C Fix: Lock to prevent race condition in check-and-spawn of flush tasks
        self._flush_lock = asyncio.Lock()

        self._state_path = self.vault_root / '.vault-memory-state.json'
        self._state = SyncState()
        self._load_state()

    @property
    def sync_concurrency(self) -> int:
        '''Current sync concurrency setting.'''
        return self._sync_concurrency

    @sync_concurrency.setter
    def sync_concurrency(self, value: int):
        '''Set sync concurrency with bounds checking.'''
        self._sync_concurrency = max(1, min(value, 50))

    async def sync_file(self, abs_path: Path, caller: str = 'user') -> int:
        '''Sync a single file (markdown or canvas) and return chunk count.'''
        # Calculate rel_path once and pass to helper methods (DRY)
        try:
            rel_path = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel_path = str(abs_path)

        if abs_path.suffix.lower() == CANVAS_FILE_EXTENSION:
            return await self._sync_canvas(abs_path, rel_path, caller)
        else:
            return await self._sync_markdown(abs_path, rel_path, caller)

    async def _sync_markdown(self, abs_path: Path, rel_path: str, caller: str = 'user') -> int:
        '''Sync markdown file: parse -> chunk -> embed -> upsert.'''
        parsed = await self.markdown_parser.parse(abs_path, caller=caller)
        body = parsed.get('body', '')

        if not body.strip():
            return 0

        chunks = _chunk_text(body)
        total = len(chunks)

        # Batch embed all chunks at once
        embeddings = await self.embedder.embed_batch(chunks) if chunks else []
        if embeddings and len(embeddings) != len(chunks):
            raise ValueError(
                f'embed_batch returned {len(embeddings)} embeddings for {total} chunks'
            )

        note_chunks: List[NoteChunk] = []
        for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings or [])):
            content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]
            chunk = NoteChunk(
                uuid=f'{rel_path}::{i}',
                content=chunk_text,
                vault_path=rel_path,
                project=parsed['project'],
                folder=parsed['folder'],
                tags=parsed['tags'],
                date_created=parsed['date_created'],
                date_modified=parsed['date_modified'],
                status=parsed['status'],
                chunk_index=i,
                chunk_total=total,
                content_hash=content_hash,
                trust=parsed['trust'],
                importance=parsed['importance'],
                decay_profile=parsed['decay_profile'],
                maturity=parsed['maturity'],
                agent_written=parsed['agent_written'],
                agent_confidence=parsed['agent_confidence'],
                embedding=embedding,
            )
            note_chunks.append(chunk)

        upserted = 0
        if note_chunks:
            # Batch upsert in smaller chunks for memory efficiency
            chunk_batch: List[NoteChunk] = []
            for chunk in note_chunks:
                chunk_batch.append(chunk)
                if len(chunk_batch) >= 100:
                    await self.weaviate.batch_upsert(chunk_batch)
                    upserted += len(chunk_batch)
                    chunk_batch = []
            if chunk_batch:
                await self.weaviate.batch_upsert(chunk_batch)
                upserted += len(chunk_batch)

        # S20-C Fix: Use asyncio.to_thread for blocking file read
        file_hash_bytes = await asyncio.to_thread(abs_path.read_bytes)
        file_hash = hashlib.sha256(file_hash_bytes).hexdigest()[:16]
        self._queue_state_write(rel_path, file_hash)
        return upserted

    async def _sync_canvas(self, abs_path: Path, rel_path: str, caller: str = 'user') -> int:
        '''Sync canvas file: nodes -> Weaviate + entity_links, edges -> Weaviate + relationships.
        S27-1: Also extracts canvas_entities and creates canvas-sourced relationships.'''
        nodes, edges = await self.canvas_parser.parse(abs_path, caller=caller)
        upserted = 0

        # Upsert nodes to Weaviate and Postgres entity_links
        if nodes:
            node_embeddings = await self.embedder.embed_batch([node.content for node in nodes])
            if len(node_embeddings) != len(nodes):
                raise ValueError(
                    f'embed_batch returned {len(node_embeddings)} embeddings for {len(nodes)} nodes'
                )

            entity_links = []
            for i, (node, embedding) in enumerate(zip(nodes, node_embeddings)):
                node.embedding = embedding
                node.chunk_index = i
                entity_links.append((node.vault_path, node.uuid))

            await asyncio.gather(
                self.weaviate.batch_upsert(nodes),
                self._batch_upsert_entity_links(entity_links),
            )
            upserted += len(nodes)

        # Upsert edges to Weaviate and Postgres relationships
        if edges:
            edge_embeddings = await self.embedder.embed_batch([edge.content for edge in edges])
            if len(edge_embeddings) != len(edges):
                raise ValueError(
                    f'embed_batch returned {len(edge_embeddings)} embeddings for {len(edges)} edges'
                )

            edge_index_offset = len(nodes)
            relationships = []
            for i, (edge, embedding) in enumerate(zip(edges, edge_embeddings)):
                edge.embedding = embedding
                edge.chunk_index = edge_index_offset + i

                match = re.search(r'Connection: (.+?) -> (.+)', edge.content)
                if match:
                    from_node, to_node = match.groups()
                    relationships.append((from_node, to_node))

            await asyncio.gather(
                self.weaviate.batch_upsert(edges),
                self._batch_upsert_relationships(relationships),
            )
            upserted += len(edges)

        # S27-1: Extract canvas entities and create canvas-sourced relationships
        await self._sync_canvas_graph(rel_path, abs_path)

        # S20-C Fix: Use asyncio.to_thread for blocking file read
        file_hash_bytes = await asyncio.to_thread(abs_path.read_bytes)
        file_hash = hashlib.sha256(file_hash_bytes).hexdigest()[:16]
        self._queue_state_write(rel_path, file_hash)
        return upserted

    async def _batch_upsert_entity_links(self, links: List[Tuple[str, str]]):
        """Bolt: Batch insert or update vault_entity_links records."""
        if not links or not hasattr(self.pg, 'cursor') or not callable(self.pg.cursor):
            return
        if execute_values is None:
            logger.warning('batch_upsert_entity_links skipped: psycopg2.extras.execute_values unavailable')
            return
        try:
            def _do_batch_insert():
                with self.pg.cursor() as cur:
                    execute_values(
                        cur,
                        '''
                        INSERT INTO vault_entity_links (vault_path, chunk_uuid, created_at)
                        VALUES %s
                        ON CONFLICT (vault_path, chunk_uuid) DO NOTHING
                        ''',
                        links,
                        template="(%s, %s, NOW())"
                    )
            await asyncio.to_thread(_do_batch_insert)
        except Exception as e:
            logger.warning('batch_upsert_entity_links failed for %d rows: %s', len(links), e)

    async def _batch_upsert_relationships(self, relations: List[Tuple[str, str]]):
        """Bolt: Batch insert or update relationship records."""
        if not relations or not hasattr(self.pg, 'cursor') or not callable(self.pg.cursor):
            return
        if execute_values is None:
            logger.warning('batch_upsert_relationships skipped: psycopg2.extras.execute_values unavailable')
            return
        try:
            def _do_batch_insert():
                with self.pg.cursor() as cur:
                    execute_values(
                        cur,
                        '''
                        INSERT INTO relationships (source_name, target_name, relationship_type, created_at)
                        VALUES %s
                        ON CONFLICT (source_name, target_name) DO NOTHING
                        ''',
                        relations,
                        template="(%s, %s, 'connected', NOW())"
                    )
            await asyncio.to_thread(_do_batch_insert)
        except Exception as e:
            logger.warning('batch_upsert_relationships failed for %d rows: %s', len(relations), e)

    async def _sync_canvas_graph(self, rel_path: str, abs_path: Path):
        """S27-1: Extract canvas entities and create canvas-sourced relationships."""
        if not hasattr(self.pg, 'cursor') or not callable(self.pg.cursor):
            return
        try:
            raw = await asyncio.to_thread(abs_path.read_text, encoding='utf-8', errors='replace')
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as e:
            logger.debug('canvas_graph parse skipped for %s: %s', rel_path, e)
            return

        result = self.canvas_graph.parse(rel_path, data)
        if not result.entities and not result.edges:
            return

        logger.info(
            'Canvas graph: %s → %d entities, %d edges',
            rel_path, len(result.entities), len(result.edges),
        )

        # Store canvas_entities
        for entity in result.entities:
            await self._upsert_canvas_entity(entity)

        # Create canvas-sourced relationships
        for edge in result.edges:
            await self._upsert_canvas_relationship(edge)

    async def _upsert_canvas_entity(self, entity):
        """Insert or update a canvas_entities record."""
        if not hasattr(self.pg, 'cursor') or not callable(self.pg.cursor):
            return
        try:
            def _do_insert():
                with self.pg.cursor() as cur:
                    cur.execute(
                        '''
                        INSERT INTO canvas_entities (canvas_path, node_id, entity_name, entity_type, node_text, extracted_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (canvas_path, node_id) DO UPDATE
                            SET entity_name = EXCLUDED.entity_name,
                                entity_type = EXCLUDED.entity_type,
                                node_text = EXCLUDED.node_text,
                                extracted_at = NOW()
                        ''',
                        (entity.canvas_path, entity.node_id, entity.entity_name,
                         entity.entity_type, entity.node_text),
                    )
            await asyncio.to_thread(_do_insert)
        except Exception as e:
            logger.debug('upsert_canvas_entity skipped: %s', e)

    async def _upsert_canvas_relationship(self, edge):
        """Insert a canvas-sourced relationship with edge_source='canvas'."""
        if not hasattr(self.pg, 'cursor') or not callable(self.pg.cursor):
            return
        try:
            def _do_insert():
                with self.pg.cursor() as cur:
                    cur.execute(
                        '''
                        INSERT INTO relationships (source_name, target_name, relationship_type, edge_source, created_at)
                        VALUES (%s, %s, %s, 'canvas', NOW())
                        ON CONFLICT (source_name, target_name, relationship_type, edge_source) DO NOTHING
                        ''',
                        (edge.source_name, edge.target_name, edge.relationship_type),
                    )
            await asyncio.to_thread(_do_insert)
        except Exception as e:
            logger.debug('upsert_canvas_relationship skipped: %s', e)

    async def delete_file(self, abs_path: Path):
        try:
            rel = str(abs_path.relative_to(self.vault_root))
        except ValueError:
            rel = str(abs_path)
        await self.weaviate.delete_by_path(rel)
        self._queue_state_write(rel, None)  # None = delete from state

    # S20-C: Parallel file processing with async worker pool
    async def full_sync(self, caller: str = 'user') -> Dict[str, int]:
        '''Full sync with parallel file processing using asyncio.Semaphore.'''
        mark_indexing()
        stats = {'synced': 0, 'skipped': 0, 'errors': 0}

        md_files = list(self.vault_root.rglob('*.md'))
        canvas_files = list(self.vault_root.rglob(f'*{CANVAS_FILE_EXTENSION}'))
        all_files = md_files + canvas_files

        total_files = len(all_files)
        logger.info(
            'Starting full sync: %d files with concurrency=%d',
            total_files,
            self._sync_concurrency,
        )

        # Create semaphore for concurrency control
        self._semaphore = Semaphore(self._sync_concurrency)

        # Process files in batches for memory efficiency
        async def process_file(file_path: Path) -> Tuple[bool, int, Optional[str]]:
            '''Process a single file, returns (success, chunk_count, error_message).'''
            async with self._semaphore:
                rel = (
                    str(file_path.relative_to(self.vault_root))
                    if file_path.is_relative_to(self.vault_root)
                    else str(file_path)
                )
                if _is_working_path(rel) and caller != 'heartbeat':
                    return (True, 0, None)  # Skipped, not error

                try:
                    # S20-C Fix: Use asyncio.to_thread for blocking file read
                    file_bytes = await asyncio.to_thread(file_path.read_bytes)
                    file_hash = hashlib.sha256(file_bytes).hexdigest()[:16]
                    if self._state.file_hashes.get(rel) == file_hash:
                        return (True, 0, None)  # Unchanged, skipped

                    n = await self.sync_file(file_path, caller=caller)
                    return (True, n, None)  # Success
                except PermissionError as e:
                    logger.warning('Write gate blocked: %s', e)
                    return (True, 0, None)  # Skipped, not error
                except Exception as e:
                    logger.error('Error syncing %s: %s', rel, e)
                    return (False, 0, str(e))  # Error

        # Process in parallel batches (memory-efficient)
        results = []
        for batch_start in range(0, total_files, FILE_BATCH_SIZE):
            batch_end = min(batch_start + FILE_BATCH_SIZE, total_files)
            batch = all_files[batch_start:batch_end]
            logger.debug('Processing batch %d-%d of %d files', batch_start + 1, batch_end, total_files)
            batch_results = await asyncio.gather(
                *[process_file(f) for f in batch],
                return_exceptions=True,
            )
            results.extend(batch_results)

        # Aggregate results
        for result in results:
            if isinstance(result, Exception):
                stats['errors'] += 1
                logger.error('Unexpected error during parallel sync: %s', result)
            else:
                success, chunks, error = result
                if error:
                    stats['errors'] += 1
                elif chunks > 0:
                    stats['synced'] += chunks
                else:
                    stats['skipped'] += 1

        self._pending_full_sync_time = datetime.now(timezone.utc).isoformat()
        await self._flush_state_write(force=True)  # Always flush at end of full sync
        self._pending_full_sync_time = None  # Clear after flush
        record_index_complete(stats['synced'])
        mark_ready()

        logger.info(
            'Full sync complete: %d synced, %d skipped, %d errors',
            stats['synced'],
            stats['skipped'],
            stats['errors'],
        )
        return stats

    def _load_state(self):
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text())
                self._state = SyncState(**data)
            except Exception:
                pass

    # S20-E: Batched state write implementation
    def _queue_state_write(self, rel_path: str, file_hash: Optional[str]):
        '''
        Queue a state write operation. Flushes automatically when batch threshold
        or timeout is reached.
        '''
        self._pending_state_writes[rel_path] = file_hash

        # Check if we need to flush — hold the lock while checking and scheduling
        # to prevent the race window between `if not _flushing` and `create_task`
        now = time.time()
        time_since_flush = now - self._last_state_write_time

        if len(self._pending_state_writes) >= self._state_write_batch or time_since_flush >= self._state_write_timeout_s:
            # Atomically check-and-spawn: only one coroutine will win the lock
            # and successfully launch _flush_state_write at a time
            if self._flush_lock.locked():
                return  # Flush already in progress — skip
            asyncio.create_task(self._try_flush())

    async def _try_flush(self):
        '''
        Try to acquire the flush lock and perform a flush.
        Uses lock to prevent concurrent flush tasks from racing.
        '''
        if self._flush_lock.locked():
            return  # Already flushing
        async with self._flush_lock:
            await self._flush_state_write(force=False)

    async def _flush_state_write(self, force: bool = False):
        '''
        Flush all pending state writes to disk.
        S20-C Fix: Caller must hold _flush_lock to prevent concurrent flushes.
        S20-E: Reduces 1000 writes per full_sync to ~100 writes (10x improvement).

        Args:
            force: If True, always flush all pending writes immediately.
                   If False, only flush if batch threshold or timeout reached.
        '''
        if not self._pending_state_writes and self._pending_full_sync_time is None:
            return

        # Check conditions for non-forced flush
        if not force:
            if len(self._pending_state_writes) < self._state_write_batch:
                time_since_flush = time.time() - self._last_state_write_time
                if time_since_flush < self._state_write_timeout_s:
                    return  # Not yet time to flush

        # S20-C Fix: Swap dict BEFORE iteration to prevent data loss
        pending = self._pending_state_writes
        self._pending_state_writes = {}

        # Apply pending writes to state
        for rel_path, file_hash in pending.items():
            if file_hash is None:
                self._state.file_hashes.pop(rel_path, None)
            else:
                self._state.file_hashes[rel_path] = file_hash

        # Apply deferred full_sync time
        if self._pending_full_sync_time:
            self._state.last_full_sync = self._pending_full_sync_time
            self._pending_full_sync_time = None

        # Write state file once
        # S20-C Fix: Use asyncio.to_thread for blocking file write
        state_json = json.dumps(asdict(self._state), indent=2)
        await asyncio.to_thread(self._state_path.write_text, state_json)
        self._last_state_write_time = time.time()

        logger.debug('State flush: %d file hashes written', len(self._state.file_hashes))


    async def flush_pending_state(self):
        '''
        Public method to force flush pending state writes.
        Call this at strategic points (end of operations, before shutdown).
        '''
        await self._flush_state_write(force=True)

    async def clear_sync_state(self):
        '''
        Public method to clear sync state (file_hashes and last_full_sync).
        This is used by the --force flag in sync_command.py.
        '''
        self._state.file_hashes.clear()
        self._state.last_full_sync = None
        # Flush immediately to persist the cleared state
        await self.flush_pending_state()

    def _save_state(self):
        '''
        Synchronous wrapper for flush_pending_state.
        Used by sync_command.py which runs in asyncio.run() context.
        '''
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, this is called from async context
                # The caller should await flush_pending_state() directly
                raise RuntimeError("_save_state called from async context - use await flush_pending_state() instead")
            else:
                # If no loop or loop not running, run directly
                asyncio.run(self.flush_pending_state())
        except RuntimeError:
            # No event loop, create a new one
            asyncio.run(self.flush_pending_state())


class _VaultEventHandler(FileSystemEventHandler):
    def __init__(self, queue: Queue, loop: asyncio.AbstractEventLoop):
        self._queue = queue
        self._loop = loop
        self._pending: Dict[str, float] = {}

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            if event.src_path.endswith('.md') or event.src_path.endswith(CANVAS_FILE_EXTENSION):
                self._pending[event.src_path] = time.time()

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            if event.src_path.endswith('.md') or event.src_path.endswith(CANVAS_FILE_EXTENSION):
                self._pending[event.src_path] = time.time()

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            if event.src_path.endswith('.md') or event.src_path.endswith(CANVAS_FILE_EXTENSION):
                self._pending.pop(event.src_path, None)
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ('delete', event.src_path))

    async def flush_debounced(self):
        now = time.time()
        ready = [p for p, t in list(self._pending.items()) if now - t >= DEBOUNCE_SECONDS]
        for p in ready:
            del self._pending[p]
            await self._queue.put(('upsert', p))


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
        logger.info('Watcher started: %s', vault)
        await asyncio.gather(
            self._process_queue(),
            self._debounce_loop(),
            self._reconcile_loop(),
        )

    async def stop(self):
        self._observer.stop()
        self._observer.join()

    async def _process_queue(self):
        while True:
            event_type, path = await self._queue.get()
            abs_path = Path(path)
            try:
                if event_type == 'upsert' and abs_path.exists():
                    await self.engine.sync_file(abs_path, caller='user')
                elif event_type == 'delete':
                    # S30-7: Only delete if file truly doesn't exist (prevents race with quick recreate)
                    if not abs_path.exists():
                        await self.engine.delete_file(abs_path)
                    else:
                        logger.debug('Skipping delete for %s - file still exists (recreate race)', path)
            except Exception as e:
                logger.error('Queue processor error [%s] %s: %s', event_type, path, e)

    async def _debounce_loop(self):
        while True:
            await asyncio.sleep(0.5)
            await self._handler.flush_debounced()

    async def _reconcile_loop(self):
        while True:
            await asyncio.sleep(RECONCILE_INTERVAL)
            logger.info('Scheduled reconciliation starting...')
            try:
                stats = await self.engine.full_sync(caller='user')
                logger.info('Reconciliation complete: %s', stats)
            except Exception as e:
                logger.error('Reconciliation error: %s', e)
