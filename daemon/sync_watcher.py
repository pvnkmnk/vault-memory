# daemon/sync_watcher.py
"""
VaultSyncWatcher: Three-mode vault sync.
 Mode 1: Full sync (startup / first run)
 Mode 2: Incremental file watcher (watchdog, real-time)
 Mode 3: Scheduled reconciliation (hourly)
Write Layer Gate (v0.2.0):
 - caller="user" → vault layer (read/write anywhere in vault)
 - caller="agent" → working layer only (_working/ buffer)
 - caller="heartbeat" → semantic layer (08 Meta/agent-context/, project notes)
 Semantic-layer writes from non-heartbeat callers are REJECTED.
v0.4.0 changes:
 - MarkdownParser now uses python-frontmatter library (replaces _parse_yaml_simple)
 - AGENT_FRONTMATTER_DEFAULTS now includes maturity: seed and status: working
 - NoteChunk carries maturity field
 - SyncEngine.sync_file() passes maturity through to upsert
v0.5.0-p3 changes:
 - P3-B: CanvasParser + .canvas extension watch, node/edge upsert
 - P3-C: _sanitize_for_context() injection stripping + security.log
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
SEMANTIC_LAYER_PREFIXES = (
    "08 Meta/agent-context",
    "08 Meta/heartbeat",
    "08 Meta/skills",
)
WORKING_BUFFER_PREFIX = "_working"
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
class MarkdownParser:
    TAG_RE = re.compile(r"(?:^|\\s)#([\\w/]+)", re.MULTILINE)
    STATUS_RE = re.compile(r"status:\\s*(\\S+)", re.IGNORECASE)
    def parse(self, path: Path, caller: str = "user") -> Dict[str, Any]:
        raw = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        try:
            post = fm.loads(raw)
            frontmatter_data = dict(post.metadata)
            body = post.content
        except Exception as e:
            logger.warning("frontmatter parse failed for %s: %s — falling back to body-only", path, e)
            frontmatter_data = {}
            body = raw
        fm_tags = frontmatter_data.get("tags", [])
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        inline_tags = self.TAG_RE.findall(body)
        tags = list(set(fm_tags + inline_tags))
        status = frontmatter_data.get("status") or self._first_match(self.STATUS_RE, body) or "active"
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
def _is_semantic_path(vault_relative: str) -> bool:
    return any(vault_relative.startswith(p) for p in SEMANTIC_LAYER_PREFIXES)
def _is_working_path(vault_relative: str) -> bool:
    return vault_relative.startswith(WORKING_BUFFER_PREFIX)
    embedding: Optional[List[float]] = field(default=None, repr=False)
@dataclass
class SyncState:
    last_full_sync: Optional[str] = None
    file_hashes: Dict[str, str] = field(default_factory=dict)
    last_reconcile: Optional[str] = None
