# daemon/retrieval.py
"""
UnifiedSearch: Four-strategy retrieval pipeline with RRF + temporal decay + cross-encoder reranking.

Strategies run in parallel (asyncio.gather).
Fusion uses Reciprocal Rank Fusion (k=60).
Temporal decay applied post-fusion (v0.2.0).
Reranking uses cross-encoder on top-20 candidates only.
"""

import asyncio
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .weaviate_client import WeaviateClient
from .pg_client import PostgresClient
from .embedder import EmbedderService

logger = logging.getLogger("vault-memoryd.retrieval")

RRF_K = 60

STRATEGY_WEIGHTS = {
    "dense":    1.0,
    "sparse":   1.0,
    "graph":    0.8,
    "temporal": 0.7,
}

# Temporal decay configuration per profile
DECAY_PROFILES: Dict[str, Optional[int]] = {
    "active":    30,    # project notes — decay over 30 days
    "reference": 90,    # books, articles — decay over 90 days
    "identity":  None,  # boot.md, pvnkmnk.md — never decays
}

# Final score weights: semantic + recency + importance
DECAY_WEIGHT_SEMANTIC   = 0.6
DECAY_WEIGHT_RECENCY    = 0.3
DECAY_WEIGHT_IMPORTANCE = 0.1


@dataclass
class VaultResult:
    vault_path:    str
    content:       str
    score:         float
    source:        str
    sources:       List[str] = field(default_factory=list)
    project:       Optional[str] = None
    tags:          Optional[List[str]] = None
    date_modified: Optional[str] = None
    chunk_index:   Optional[int] = None
    chunk_total:   Optional[int] = None
    importance:    float = 1.0
    decay_profile: str = "active"
    trust:         str = "high"
    agent_written: bool = False

    def to_clip(self) -> Dict[str, Any]:
        return {
            "path":          self.vault_path,
            "score":         round(self.score, 3),
            "snippet":       self.content[:100],
            "source":        self.source,
            "sources":       self.sources,
            "tags":          self.tags or [],
            "modified":      self.date_modified,
            "trust":         self.trust,
            "agent_written": self.agent_written,
        }


class QueryIntent(str, Enum):
    SIMPLE   = "simple"
    ENTITY   = "entity"
    TEMPORAL = "temporal"
    CAUSAL   = "causal"
    HYBRID   = "hybrid"


_TEMPORAL_RE = re.compile(
    r"\b(yesterday|last\s+\w+|since|before|after|when|"
    r"in\s+\d{4}|\d{4}-\d{2}|this\s+(week|month|year)|"
    r"recently|changes?|evolv|history|timeline)\b",
    re.IGNORECASE,
)
_ENTITY_RE = re.compile(
    r"\b(related\s+to|connected\s+to|links?\s+to|impact|affect|"
    r"caused?\s+by|depends?\s+on|part\s+of|belong\s+to)\b",
    re.IGNORECASE,
)
_CAUSAL_RE = re.compile(
    r"\b(why|how\s+did|what\s+caused|result\s+of|led\s+to|"
    r"because|therefore|consequently|trigger)\b",
    re.IGNORECASE,
)


def classify_query(query: str) -> QueryIntent:
    has_temporal = bool(_TEMPORAL_RE.search(query))
    has_entity   = bool(_ENTITY_RE.search(query))
    has_causal   = bool(_CAUSAL_RE.search(query))
    if has_causal:
        return QueryIntent.CAUSAL
    if has_temporal and has_entity:
        return QueryIntent.HYBRID
    if has_temporal:
        return QueryIntent.TEMPORAL
    if has_entity:
        return QueryIntent.ENTITY
    return QueryIntent.SIMPLE


def extract_time_range(query: str, context: Dict) -> Optional[Dict[str, str]]:
    if context.get("time_range"):
        return context["time_range"]
    now = datetime.now(timezone.utc)
    if re.search(r"\blast\s+week\b", query, re.IGNORECASE):
        from datetime import timedelta
        start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        return {"start": start, "end": now.strftime("%Y-%m-%d")}
    if re.search(r"\blast\s+month\b", query, re.IGNORECASE):
        from datetime import timedelta
        start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        return {"start": start, "end": now.strftime("%Y-%m-%d")}
    if re.search(r"\bthis\s+year\b", query, re.IGNORECASE):
        return {"start": f"{now.year}-01-01", "end": now.strftime("%Y-%m-%d")}
    m = re.search(r"\bin\s+(\d{4})\b", query, re.IGNORECASE)
    if m:
        year = m.group(1)
        return {"start": f"{year}-01-01", "end": f"{year}-12-31"}
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*\.\.\s*(\d{4}-\d{2}-\d{2})", query)
    if m:
        return {"start": m.group(1), "end": m.group(2)}
    return None


def extract_entities(query: str) -> List[str]:
    STOPWORDS = {
        "about", "their", "which", "where", "there", "these", "those",
        "could", "would", "should", "have", "been", "that", "this",
        "with", "from", "into", "when", "what", "will", "also",
    }
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{3,}\b", query)
    return [w.lower() for w in words if w.lower() not in STOPWORDS]


def apply_temporal_decay(
    results: List[VaultResult],
) -> List[VaultResult]:
    """
    Re-score results using exponential temporal decay.
    Score = semantic*0.6 + recency*0.3 + importance*0.1
    Decay window is controlled by each result's decay_profile.
    identity profile (boot.md, pvnkmnk.md) is never decayed.
    """
    now = datetime.now(timezone.utc)
    for r in results:
        decay_days = DECAY_PROFILES.get(r.decay_profile, 30)
        if r.date_modified and decay_days is not None:
            try:
                dt = datetime.fromisoformat(
                    r.date_modified.replace("Z", "+00:00")
                )
                age_days = max(0, (now - dt).days)
                recency = math.exp(-age_days / decay_days)
                r.score = (
                    r.score         * DECAY_WEIGHT_SEMANTIC
                    + recency       * DECAY_WEIGHT_RECENCY
                    + r.importance  * DECAY_WEIGHT_IMPORTANCE
                )
            except Exception as e:
                logger.debug("Decay skipped for %s: %s", r.vault_path, e)
        # identity profile: score untouched
    return sorted(results, key=lambda x: x.score, reverse=True)


def reciprocal_rank_fusion(
    strategy_results: Dict[str, List[VaultResult]],
    k: int = RRF_K,
) -> List[VaultResult]:
    scores: Dict[str, Dict] = {}
    for strategy, results in strategy_results.items():
        weight = STRATEGY_WEIGHTS.get(strategy, 1.0)
        for rank, result in enumerate(results, start=1):
            key = result.vault_path
            rrf = weight / (k + rank)
            if key not in scores:
                scores[key] = {"score": 0.0, "result": result, "sources": []}
            scores[key]["score"]   += rrf
            scores[key]["sources"].append(strategy)
    fused = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    output = []
    for item in fused:
        r = item["result"]
        r.score   = item["score"]
        r.sources = item["sources"]
        r.source  = item["sources"][0]
        output.append(r)
    return output


def build_weaviate_filter(context: Dict):
    from weaviate.classes.query import Filter
    filters = []
    if context.get("project"):
        filters.append(Filter.by_property("project").equal(context["project"]))
    if context.get("folder"):
        filters.append(Filter.by_property("folder").equal(context["folder"]))
    if context.get("status"):
        filters.append(Filter.by_property("status").equal(context["status"]))
    if context.get("tags"):
        for tag in context["tags"]:
            filters.append(Filter.by_property("tags").contains_any([tag]))
    if context.get("time_range"):
        tr = context["time_range"]
        if tr.get("start"):
            filters.append(Filter.by_property("date_modified").greater_or_equal(tr["start"]))
        if tr.get("end"):
            filters.append(Filter.by_property("date_modified").less_or_equal(tr["end"]))
    if not filters:
        return None
    if len(filters) == 1:
        return filters[0]
    combined = filters[0]
    for f in filters[1:]:
        combined = combined & f
    return combined


async def _strategy_dense(query, embedding, weaviate, meta_filter, limit=50):
    try:
        from weaviate.classes.query import MetadataQuery
        collection = weaviate.client.collections.get("VaultNote")
        kwargs = dict(near_vector=embedding, limit=limit, return_metadata=MetadataQuery(distance=True))
        if meta_filter is not None:
            kwargs["filters"] = meta_filter
        response = collection.query.near_vector(**kwargs)
        results = []
        for obj in response.objects:
            distance = obj.metadata.distance or 1.0
            props = obj.properties
            results.append(VaultResult(
                vault_path=props["vault_path"],
                content=props.get("content", "")[:200],
                score=max(0.0, 1.0 - distance),
                source="dense",
                project=props.get("project"),
                tags=props.get("tags"),
                date_modified=props.get("date_modified"),
                chunk_index=props.get("chunk_index"),
                chunk_total=props.get("chunk_total"),
                importance=float(props.get("importance", 1.0)),
                decay_profile=props.get("decay_profile", "active"),
                trust=props.get("trust", "high"),
                agent_written=bool(props.get("agent_written", False)),
            ))
        return results
    except Exception as e:
        logger.error("Dense strategy error: %s", e)
        return []


async def _strategy_sparse(query, weaviate, meta_filter, limit=50):
    try:
        from weaviate.classes.query import MetadataQuery
        collection = weaviate.client.collections.get("VaultNote")
        kwargs = dict(query=query, limit=limit, return_metadata=MetadataQuery(score=True))
        if meta_filter is not None:
            kwargs["filters"] = meta_filter
        response = collection.query.bm25(**kwargs)
        results = []
        for obj in response.objects:
            props = obj.properties
            results.append(VaultResult(
                vault_path=props["vault_path"],
                content=props.get("content", "")[:200],
                score=obj.metadata.score or 0.0,
                source="sparse",
                project=props.get("project"),
                tags=props.get("tags"),
                date_modified=props.get("date_modified"),
                chunk_index=props.get("chunk_index"),
                chunk_total=props.get("chunk_total"),
                importance=float(props.get("importance", 1.0)),
                decay_profile=props.get("decay_profile", "active"),
                trust=props.get("trust", "high"),
                agent_written=bool(props.get("agent_written", False)),
            ))
        return results
    except Exception as e:
        logger.error("Sparse strategy error: %s", e)
        return []


async def _strategy_graph(query, entities, postgres, context, max_hops=3, limit=30):
    if not entities:
        return []
    cursor = postgres.conn.cursor()
    try:
        sql = """
        WITH RECURSIVE entity_graph AS (
            SELECT e.id, e.entity_name, e.properties, 1 AS depth, ARRAY[e.entity_name] AS path_taken
            FROM temporal_entities e WHERE e.entity_name = ANY(%s)
            UNION ALL
            SELECT te.id, te.entity_name, te.properties, eg.depth + 1, eg.path_taken || te.entity_name
            FROM temporal_entities te
            JOIN relationships r ON te.entity_name = r.target_name AND r.source_name = eg.entity_name
            JOIN entity_graph eg ON r.source_name = eg.entity_name
            WHERE eg.depth < %s AND NOT te.entity_name = ANY(eg.path_taken)
        )
        SELECT DISTINCT eg.entity_name, eg.properties, eg.depth, vel.vault_path
        FROM entity_graph eg
        JOIN vault_entity_links vel ON eg.entity_name = vel.vault_path OR eg.id::text = vel.entity_id::text
        ORDER BY eg.depth ASC LIMIT %s
        """
        cursor.execute(sql, (entities, max_hops, limit))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            entity_name = row["entity_name"]
            props = row["properties"] or {}
            depth = row["depth"]
            vault_path = row["vault_path"]
            results.append(VaultResult(
                vault_path=vault_path,
                content=str(props.get("content", f"Entity: {entity_name}"))[:200],
                score=max(0.5, 1.0 - (depth * 0.1)),
                source="graph",
                project=props.get("project"),
                tags=[entity_name],
            ))
        cursor.close()
        return results
    except Exception as e:
        logger.error("Graph strategy error: %s", e)
        cursor.close()
        return []


async def _strategy_temporal(query, time_range, entities, postgres, limit=20):
    cursor = postgres.conn.cursor()
    try:
        start = time_range.get("start", "2000-01-01")
        end   = time_range.get("end",   "2099-12-31")
        if entities:
            sql = """
            SELECT vault_path, content, change_summary, valid_from, valid_to
            FROM workflow_history
            WHERE valid_from >= %s AND (valid_to IS NULL OR valid_to <= %s)
              AND (vault_path = ANY(%s) OR change_summary ILIKE ANY(%s))
            ORDER BY valid_from DESC LIMIT %s
            """
            like_patterns = [f"%{e}%" for e in entities]
            cursor.execute(sql, (start, end, entities, like_patterns, limit))
        else:
            sql = """
            SELECT vault_path, content, change_summary, valid_from, valid_to
            FROM workflow_history
            WHERE valid_from >= %s AND (valid_to IS NULL OR valid_to <= %s)
            ORDER BY valid_from DESC LIMIT %s
            """
            cursor.execute(sql, (start, end, limit))
        rows = cursor.fetchall()
        cursor.close()
        results = []
        for row in rows:
            ts_str = row["valid_from"].strftime("%Y-%m-%d") if row["valid_from"] else "unknown"
            results.append(VaultResult(
                vault_path=row["vault_path"],
                content=f"[{ts_str}] {row['change_summary'] or ''}"[:200],
                score=0.75,
                source="temporal",
                date_modified=ts_str,
            ))
        return results
    except Exception as e:
        logger.error("Temporal strategy error: %s", e)
        cursor.close()
        return []


class UnifiedSearch:
    def __init__(self, weaviate: WeaviateClient, postgres: PostgresClient, embedder: EmbedderService):
        self.weaviate  = weaviate
        self.postgres  = postgres
        self.embedder  = embedder

    async def search(
        self,
        query: str,
        project=None, folder=None, status=None, tags=None,
        time_range=None, top_k=5, include_graph=False, include_temporal=False,
        apply_decay: bool = True,
    ) -> List[VaultResult]:
        intent   = classify_query(query)
        entities = extract_entities(query)
        t_range  = extract_time_range(query, {"time_range": time_range}) or time_range

        use_graph    = include_graph    or intent in (QueryIntent.ENTITY, QueryIntent.CAUSAL, QueryIntent.HYBRID)
        use_temporal = include_temporal or intent in (QueryIntent.TEMPORAL, QueryIntent.CAUSAL, QueryIntent.HYBRID)

        logger.info("Search: intent=%s entities=%s time=%s graph=%s temporal=%s decay=%s",
                    intent.value, entities, t_range, use_graph, use_temporal, apply_decay)

        embedding   = self.embedder.embed_one(query)
        context     = {"project": project, "folder": folder, "status": status, "tags": tags, "time_range": t_range}
        meta_filter = build_weaviate_filter(context)

        strategy_coros = {
            "dense":  _strategy_dense(query, embedding, self.weaviate, meta_filter),
            "sparse": _strategy_sparse(query, self.weaviate, meta_filter),
        }
        if use_graph:
            strategy_coros["graph"] = _strategy_graph(query, entities, self.postgres, context)
        if use_temporal and t_range:
            strategy_coros["temporal"] = _strategy_temporal(query, t_range, entities, self.postgres)

        keys    = list(strategy_coros.keys())
        results = await asyncio.gather(*strategy_coros.values(), return_exceptions=True)

        strategy_results: Dict[str, List[VaultResult]] = {}
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                logger.error("Strategy '%s' raised: %s", key, result)
                strategy_results[key] = []
            else:
                strategy_results[key] = result

        fused = reciprocal_rank_fusion(strategy_results)
        if not fused:
            return []

        # Apply temporal decay before reranking
        if apply_decay:
            fused = apply_temporal_decay(fused)

        candidates = fused[:20]
        return self._rerank(query, candidates)[:top_k]

    def _rerank(self, query: str, candidates: List[VaultResult]) -> List[VaultResult]:
        if len(candidates) <= 1:
            return candidates
        texts = [c.content for c in candidates]
        try:
            scores = self.embedder.rerank(query, texts)
            reranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            output = []
            for result, score in reranked:
                result.score = float(score)
                output.append(result)
            return output
        except Exception as e:
            logger.error("Reranking failed: %s", e)
            return candidates
