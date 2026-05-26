# daemon/retrieval.py
"""
UnifiedSearch: Four-strategy retrieval pipeline with RRF + temporal decay + cross-encoder reranking.

Strategies run in parallel (asyncio.gather).
Fusion uses Reciprocal Rank Fusion (k=60).
Temporal decay applied post-fusion.
Reranking uses cross-encoder on top-20 candidates only.
"""

import asyncio
import json
import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .weaviate_client import WeaviateClient
from .pg_client import PostgresClient
from .embedder import EmbedderService
from .context_assembler import assemble_context, DEFAULT_TOKEN_BUDGET

logger = logging.getLogger("vault-memoryd.retrieval")

RRF_K = 60

STRATEGY_WEIGHTS = {
    "dense": 1.0,
    "sparse": 1.0,
    "graph": 0.8,
    "temporal": 0.7,
}

# Typed relationship edge weights
EDGE_WEIGHTS: Dict[str, float] = {
    "frontmatter": 1.0,
    "body": 0.6,
    "implicit-folder": 0.3,
}
_DEFAULT_EDGE_WEIGHT = 0.5  # fallback for unknown edge_source values

# Temporal decay configuration per profile
DECAY_PROFILES: Dict[str, Optional[int]] = {
    "active": 30,
    "reference": 90,
    "identity": None,
}

DECAY_WEIGHT_SEMANTIC = 0.6
DECAY_WEIGHT_RECENCY = 0.3
DECAY_WEIGHT_IMPORTANCE = 0.1

# GARS (Graph-Augmented Relevance Score) weights.
GARS_WEIGHTS = {
    "W_sim": 0.70,  # RRF similarity score
    "W_cent": 0.20,  # Degree centrality in knowledge graph
    "W_act": 0.10,  # Neighbor co-occurrence activation
}


@dataclass
class VaultResult:
    vault_path: str
    content: str
    score: float
    source: str
    sources: List[str] = field(default_factory=list)
    project: Optional[str] = None
    tags: Optional[List[str]] = None
    date_modified: Optional[str] = None
    chunk_index: Optional[int] = None
    chunk_total: Optional[int] = None
    importance: float = 1.0
    decay_profile: str = "active"
    trust: str = "high"
    agent_written: bool = False

    def to_clip(self) -> Dict[str, Any]:
        return {
            "path": self.vault_path,
            "score": round(self.score, 3),
            "snippet": self.content[:100],
            "source": self.source,
            "sources": self.sources,
            "tags": self.tags or [],
            "modified": self.date_modified,
            "trust": self.trust,
            "agent_written": self.agent_written,
        }


class QueryIntent(str, Enum):
    SIMPLE = "simple"
    ENTITY = "entity"
    TEMPORAL = "temporal"
    CAUSAL = "causal"
    HYBRID = "hybrid"


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
_ENTITY_EXTRACT_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_-]{3,}\b")
_ENTITY_STOPWORDS = {
    "about",
    "their",
    "which",
    "where",
    "there",
    "these",
    "those",
    "could",
    "would",
    "should",
    "have",
    "been",
    "that",
    "this",
    "with",
    "from",
    "into",
    "when",
    "what",
    "will",
    "also",
}


def classify_query(query: str) -> QueryIntent:
    has_temporal = bool(_TEMPORAL_RE.search(query))
    has_entity = bool(_ENTITY_RE.search(query))
    has_causal = bool(_CAUSAL_RE.search(query))
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
    entities: list[str] = []
    seen: set[str] = set()
    for word in _ENTITY_EXTRACT_RE.findall(query):
        normalized = word.lower()
        if normalized in _ENTITY_STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        entities.append(normalized)
    return entities


def apply_temporal_decay(results: List[VaultResult]) -> List[VaultResult]:
    now = datetime.now(timezone.utc)
    for r in results:
        decay_days = DECAY_PROFILES.get(r.decay_profile, 30)
        if r.date_modified and decay_days is not None:
            try:
                dt = datetime.fromisoformat(r.date_modified.replace("Z", "+00:00"))
                age_days = max(0, (now - dt).days)
                recency = math.exp(-age_days / decay_days)
                r.score = (
                    r.score * DECAY_WEIGHT_SEMANTIC
                    + recency * DECAY_WEIGHT_RECENCY
                    + r.importance * DECAY_WEIGHT_IMPORTANCE
                )
            except Exception as e:
                logger.debug("Decay skipped for %s: %s", r.vault_path, e)
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
            scores[key]["score"] += rrf
            scores[key]["sources"].append(strategy)
    fused = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
    output = []
    for item in fused:
        r = item["result"]
        r.score = item["score"]
        r.sources = item["sources"]
        r.source = item["sources"][0]
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


def _normalize_bm25_score(raw_score: float) -> float:
    """
    BM25 sigmoid calibration.
    Weaviate BM25 scores can vary widely depending on index size and term frequency.
    This sigmoid maps raw scores to a consistent 0-1 range.
    Scale of 2.5 means a raw score of ~5 maps to ~0.88, ~10 to ~0.98.
    """
    return 1.0 / (1.0 + math.exp(-raw_score / 2.5))


async def _strategy_dense(query, embedding, weaviate, meta_filter, limit=50):
    try:
        return await asyncio.to_thread(
            _strategy_dense_sync, query, embedding, weaviate, meta_filter, limit
        )
    except Exception as e:
        logger.error("Dense strategy error: %s", e)
        return []


def _strategy_dense_sync(query, embedding, weaviate, meta_filter, limit=50):
    try:
        from weaviate.classes.query import MetadataQuery

        collection = weaviate.client.collections.get("VaultNote")
        kwargs = dict(
            near_vector=embedding, limit=limit, return_metadata=MetadataQuery(distance=True)
        )
        if meta_filter is not None:
            kwargs["filters"] = meta_filter
        response = collection.query.near_vector(**kwargs)
        results = []
        for obj in response.objects:
            distance = obj.metadata.distance or 1.0
            props = obj.properties
            results.append(
                VaultResult(
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
                )
            )
        return results
    except Exception as e:
        raise RuntimeError(str(e)) from e


async def _strategy_sparse(query, weaviate, meta_filter, limit=50):
    try:
        return await asyncio.to_thread(_strategy_sparse_sync, query, weaviate, meta_filter, limit)
    except Exception as e:
        logger.error("Sparse strategy error: %s", e)
        return []


def _strategy_sparse_sync(query, weaviate, meta_filter, limit=50):
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
            results.append(
                VaultResult(
                    vault_path=props["vault_path"],
                    content=props.get("content", "")[:200],
                    score=_normalize_bm25_score(obj.metadata.score or 0.0),
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
                )
            )
        return results
    except Exception as e:
        raise RuntimeError(str(e)) from e


async def _strategy_graph(query, entities, postgres, context, max_hops=3, limit=30):
    """
    Graph traversal with typed edge weights.
    The relationships table edge_source column is used to apply EDGE_WEIGHTS
    so that frontmatter links (1.0) outweigh body links (0.6) and folder
    implicit links (0.3) in the activation contribution.
    """
    if not entities:
        return []
    try:
        return await asyncio.to_thread(
            _strategy_graph_sync, query, entities, postgres, context, max_hops, limit
        )
    except Exception as e:
        logger.error("Graph strategy error: %s", e)
        return []


def _strategy_graph_sync(query, entities, postgres, context, max_hops=3, limit=30):
    try:
        with postgres.cursor() as cursor:
            edge_sources = list(EDGE_WEIGHTS.keys())
            sql = """
            WITH RECURSIVE entity_graph AS (
                SELECT
                    e.id,
                    e.entity_name,
                    e.properties,
                    1                    AS depth,
                    ARRAY[e.entity_name] AS path_taken,
                    1.0                  AS activation
                FROM temporal_entities e
                WHERE e.entity_name = ANY(%s)

                UNION ALL

                    SELECT
                        te.id,
                        te.entity_name,
                        te.properties,
                        eg.depth + 1,
                        eg.path_taken || te.entity_name,
                        eg.activation * COALESCE(
                            CASE r.edge_source
                                WHEN 'frontmatter'     THEN 1.0
                                WHEN 'body'            THEN 0.6
                                WHEN 'implicit-folder' THEN 0.3
                                ELSE 0.5
                            END,
                            0.5
                        ) AS activation
                    FROM temporal_entities te
                    JOIN relationships r
                        ON  te.entity_name = r.target_name
                        AND r.source_name  = eg.entity_name
                    JOIN entity_graph eg
                        ON r.source_name = eg.entity_name
                    WHERE eg.depth < %s
                      AND NOT te.entity_name = ANY(eg.path_taken)
                )
                SELECT DISTINCT ON (vel.vault_path)
                    eg.entity_name,
                    eg.properties,
                    eg.depth,
                    eg.activation,
                    vel.vault_path
                FROM entity_graph eg
                JOIN vault_entity_links vel
                    ON eg.entity_name = vel.vault_path
                    OR eg.id::text    = vel.entity_id::text
                ORDER BY vel.vault_path, eg.activation DESC
                LIMIT %s
            """
            cursor.execute(sql, (entities, max_hops, limit))
            rows = cursor.fetchall()
            results = []
            for row in rows:
                entity_name = row["entity_name"]
                props = row["properties"] or {}
                depth = row["depth"]
                activation = float(row["activation"])
                vault_path = row["vault_path"]
                # Base score: activation drives the contribution, depth still penalised mildly
                score = max(0.05, activation * max(0.5, 1.0 - (depth * 0.05)))
                results.append(
                    VaultResult(
                        vault_path=vault_path,
                        content=str(props.get("content", f"Entity: {entity_name}"))[:200],
                        score=score,
                        source="graph",
                        project=props.get("project"),
                        tags=[entity_name],
                    )
                )
            return results
    except Exception as e:
        raise RuntimeError(str(e)) from e


async def _strategy_temporal(query, time_range, entities, postgres, limit=20):
    try:
        return await asyncio.to_thread(
            _strategy_temporal_sync, query, time_range, entities, postgres, limit
        )
    except Exception as e:
        logger.error("Temporal strategy error: %s", e)
        return []


def _strategy_temporal_sync(query, time_range, entities, postgres, limit=20):
    try:
        start = time_range.get("start", "2000-01-01")
        end = time_range.get("end", "2099-12-31")
        with postgres.cursor() as cursor:
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

        results = []
        for row in rows:
            ts_str = row["valid_from"].strftime("%Y-%m-%d") if row["valid_from"] else "unknown"
            results.append(
                VaultResult(
                    vault_path=row["vault_path"],
                    content=f"[{ts_str}] {row['change_summary'] or ''}"[:200],
                    score=0.75,
                    source="temporal",
                    date_modified=ts_str,
                )
            )
        return results
    except Exception as e:
        raise RuntimeError(str(e)) from e


# ripgrep fast-path.


def _ripgrep_search(query: str, vault_root: str, limit: int = 10) -> Optional[List[VaultResult]]:
    """
    Shell out to `rg --json -i` for an exact-string fast-path.
    Returns None if rg is not found (graceful degradation).
    Returns a list of VaultResult (possibly empty) otherwise.
    Confidence is measured as: hits found / limit, capped at 1.0.
    The first result confidence is set to 1.0 if any matches exist.
    """
    if not shutil.which("rg"):
        logger.debug("ripgrep (rg) not found — fast-path unavailable")
        return None

    try:
        # Use -- to prevent argument injection from query.
        # Remove -l as it's incompatible with --json and suppresses match data.
        proc = subprocess.run(
            ["rg", "--json", "-F", "-i", "--max-count", "1", "--", query, vault_root],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode not in (0, 1):  # 0=match, 1=no match
            logger.debug("rg exited with code %d", proc.returncode)
            return None

        matched_paths: List[str] = []
        for line in proc.stdout.splitlines():
            try:
                obj = json.loads(line)
                if obj.get("type") == "match":
                    path = obj["data"]["path"]["text"]
                    if path not in matched_paths:
                        matched_paths.append(path)
                        if len(matched_paths) >= limit:
                            break
            except (json.JSONDecodeError, KeyError):
                continue

        if not matched_paths:
            return []

        results = []
        for i, path in enumerate(matched_paths):
            # Confidence decays slightly for later matches; first match = 1.0
            confidence = max(0.5, 1.0 - i * 0.05)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    content = f.read(200)
            except Exception:
                content = ""
            # Make path vault-relative if possible
            try:
                rel = str(Path(path).relative_to(vault_root))
            except ValueError:
                rel = path
            results.append(
                VaultResult(
                    vault_path=rel,
                    content=content,
                    score=confidence,
                    source="ripgrep",
                )
            )
        return results

    except subprocess.TimeoutExpired:
        logger.warning("ripgrep fast-path timed out")
        return None
    except Exception as e:
        logger.warning("ripgrep fast-path error: %s", e)
        return None


class UnifiedSearch:
    def __init__(
        self, weaviate: WeaviateClient, postgres: PostgresClient, embedder: EmbedderService
    ):
        self.weaviate = weaviate
        self.postgres = postgres
        self.embedder = embedder

    async def search(
        self,
        query: str,
        project=None,
        folder=None,
        status=None,
        tags=None,
        time_range=None,
        top_k=5,
        include_graph=False,
        include_temporal=False,
        apply_decay: bool = True,
        vault_root: Optional[str] = None,
        token_budget: Optional[int] = None,
    ) -> List[VaultResult]:
        # ripgrep fast-path — short queries, no graph/temporal flags.
        if vault_root and len(query.split()) < 5 and not include_graph and not include_temporal:
            # Use to_thread for blocking subprocess call in _ripgrep_search
            rg_results = await asyncio.to_thread(_ripgrep_search, query, vault_root)
            _is_path_query = "/" in query or query.endswith(".md") or len(query.split()) == 1
            if _is_path_query and rg_results and rg_results[0].score >= 0.85:
                logger.info(
                    "ripgrep fast-path hit for query=%r (%d results)", query, len(rg_results)
                )
                return rg_results[:top_k]
            # else fall through to full pipeline

        intent = classify_query(query)
        entities = extract_entities(query)
        t_range = extract_time_range(query, {"time_range": time_range}) or time_range

        use_graph = include_graph or intent in (
            QueryIntent.ENTITY,
            QueryIntent.CAUSAL,
            QueryIntent.HYBRID,
        )
        use_temporal = include_temporal or intent in (
            QueryIntent.TEMPORAL,
            QueryIntent.CAUSAL,
            QueryIntent.HYBRID,
        )

        logger.info(
            "Search: intent=%s entities=%s time=%s graph=%s temporal=%s decay=%s",
            intent.value,
            entities,
            t_range,
            use_graph,
            use_temporal,
            apply_decay,
        )

        context = {
            "project": project,
            "folder": folder,
            "status": status,
            "tags": tags,
            "time_range": t_range,
        }
        meta_filter = build_weaviate_filter(context)

        # Parallelize strategies that don't depend on embedding with the embedding calculation itself
        # This saves the latency of the embedding model call for sparse/graph/temporal paths
        async def _dense_with_embedding():
            emb = await self.embedder.embed_one(query)
            return await _strategy_dense(query, emb, self.weaviate, meta_filter)

        strategy_coros = {
            "dense": _dense_with_embedding(),
            "sparse": _strategy_sparse(query, self.weaviate, meta_filter),
        }
        if use_graph:
            strategy_coros["graph"] = _strategy_graph(query, entities, self.postgres, context)
        if use_temporal and t_range:
            strategy_coros["temporal"] = _strategy_temporal(query, t_range, entities, self.postgres)

        keys = list(strategy_coros.keys())
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

        if apply_decay:
            fused = apply_temporal_decay(fused)

        candidates = fused[:20]

        # Apply graph-augmented re-scoring.
        if self.postgres:
            candidates = await self._apply_gars(candidates, self.postgres)

        results = (await self._rerank(query, candidates))[:top_k]

        # Assemble context slices when a token budget is provided.
        if token_budget and vault_root:
            assembled = assemble_context(
                results,
                query,
                vault_root=vault_root,
                token_budget=token_budget,
            )
            # Reconstruct VaultResult list from assembled entries
            # Preserve original fields where possible
            assembled_results: List[VaultResult] = []
            for entry in assembled.entries:
                vr = VaultResult(
                    vault_path=entry.vault_path,
                    content=entry.content,
                    score=entry.score,
                    source=f"context:{entry.tier}",
                    sources=[f"tier:{entry.tier}"],
                )
                assembled_results.append(vr)
            return assembled_results

        return results

    async def _rerank(self, query: str, candidates: List[VaultResult]) -> List[VaultResult]:
        if len(candidates) <= 1:
            return candidates
        texts = [c.content for c in candidates]
        try:
            scores = await self.embedder.rerank(query, texts)
            reranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            output = []
            for result, score in reranked:
                result.score = float(score)
                output.append(result)
            return output
        except Exception as e:
            logger.error("Reranking failed: %s", e)
            return candidates

    async def _apply_gars(
        self,
        candidates: List[VaultResult],
        postgres,
    ) -> List[VaultResult]:
        """Apply GARS = (sim × W_sim) + (cent × W_cent) + (act × W_act)."""
        if not candidates:
            return candidates

        # Get candidate paths for activation scoring
        candidate_paths = {r.vault_path for r in candidates}
        if not candidate_paths:
            return candidates

        centrality_lookup: Dict[str, float] = {}
        activation_lookup: Dict[str, float] = {}
        try:
            # Bolt: Use asyncio.to_thread for blocking DB query
            def _fetch_gars_stats():
                with postgres.cursor() as cursor:
                    candidate_paths_list = list(candidate_paths)
                    stats_sql = """
                        WITH candidates AS (
                            SELECT UNNEST(%s::text[]) AS file_path
                        ),
                        rel_stats AS (
                            SELECT
                                r.source_name AS file_path,
                                COUNT(*) FILTER (WHERE r.target_name = ANY(%s)) AS neighbor_count,
                                COUNT(*) AS out_degree
                            FROM relationships r
                            WHERE r.source_name = ANY(%s)
                            GROUP BY r.source_name
                        )
                        SELECT
                            c.file_path,
                            COALESCE(ss.centrality_score, 0) AS centrality_score,
                            COALESCE(rs.neighbor_count, 0) AS neighbor_count,
                            COALESCE(rs.out_degree, 0) AS out_degree
                        FROM candidates c
                        LEFT JOIN sync_state ss ON ss.file_path = c.file_path
                        LEFT JOIN rel_stats rs ON rs.file_path = c.file_path
                    """
                    cursor.execute(
                        stats_sql,
                        [candidate_paths_list, candidate_paths_list, candidate_paths_list],
                    )
                    return cursor.fetchall()

            rows = await asyncio.to_thread(_fetch_gars_stats)

            for row in rows:
                path = row["file_path"]
                centrality_lookup[path] = float(row.get("centrality_score") or 0.0)
                neighbor_count = int(row.get("neighbor_count") or 0)
                out_deg = int(row.get("out_degree") or 0)
                activation_lookup[path] = min(1.0, neighbor_count / max(1, out_deg)) if out_deg else 0.0

        except Exception as e:
            logger.debug("GARS batch stats query failed: %s", e)
            for candidate in candidates:
                centrality_lookup[candidate.vault_path] = 0.0
                activation_lookup[candidate.vault_path] = 0.0

        # Apply GARS formula
        W_sim = GARS_WEIGHTS["W_sim"]
        W_cent = GARS_WEIGHTS["W_cent"]
        W_act = GARS_WEIGHTS["W_act"]

        for candidate in candidates:
            sim = candidate.score  # RRF similarity
            cent = centrality_lookup.get(candidate.vault_path, 0.0)
            act = activation_lookup.get(candidate.vault_path, 0.0)

            # GARS formula
            gars = (sim * W_sim) + (cent * W_cent) + (act * W_act)
            candidate.score = gars

            # Track source for debugging
            candidate.sources = candidate.sources + ["gars"]

        # Re-sort by GARS score
        return sorted(candidates, key=lambda x: x.score, reverse=True)
