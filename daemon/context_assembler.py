# daemon/context_assembler.py
"""
Accordion context assembly: relative-threshold tier packing for LLM context window optimization.

Tiers are defined relative to the top result's score (not absolute thresholds)
so quality is consistent regardless of vault size or score distribution.

P4-A: Full accordion context assembly implementation (tier-based packing strategy)
P4-E: Integration with memory/project_state for token-budgeted session bundles
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
from .retrieval import VaultResult

logger = logging.getLogger("vault-memoryd.context_assembler")

# =============================================================================
# Accordion Tier Configuration (relative to top score)
# =============================================================================
# Tiers are defined relative to the top result's score.
# Primary (>=90%): Full file content (10% soft budget cap per file)
# Supporting (>=70%): 500-char snippets around query terms
# Structural (>=35%): Headers only (TOC view), max 10 files
# Filtered (<35%): Dropped — prevents hallucination-by-bloat

TIER_PRIMARY_THRESHOLD = 0.90
TIER_SUPPORTING_THRESHOLD = 0.70
TIER_STRUCTURAL_THRESHOLD = 0.35
TIER_PRIMARY_BUDGET_CAP = 0.10  # max 10% of total budget per primary file
TIER_SUPPORTING_SNIPPET_CHARS = 500
TIER_STRUCTURAL_MAX_FILES = 10

# Token budget estimates (rough, for planning)
CHARS_PER_TOKEN_ESTIMATE = 4  # conservative estimate for English

# Neighbor expansion trigger threshold
NEIGHBOR_EXPANSION_THRESHOLD = 0.40  # seed's absolute GARS must be >= this


@dataclass
class ContextTier:
    """A single tier in the accordion context assembly."""
    name: str
    threshold: float
    strategy: str  # "full", "snippet", "headers", "dropped"
    results: List[VaultResult] = field(default_factory=list)
    content: List[str] = field(default_factory=list)
    token_count: int = 0


@dataclass
class AssembledContext:
    """Result of accordion context assembly."""
    primary_content: List[str] = field(default_factory=list)
    supporting_snippets: List[str] = field(default_factory=list)
    structural_headers: List[str] = field(default_factory=list)
    total_tokens: int = 0
    token_budget_used: float = 0.0  # 0.0 to 1.0
    primary_tokens: int = 0
    supporting_tokens: int = 0
    structural_tokens: int = 0
    files_included: List[str] = field(default_factory=list)
    dropped_count: int = 0
    neighbor_expanded: bool = False
    warnings: List[str] = field(default_factory=list)

    def to_clip(self) -> Dict[str, Any]:
        """Compact representation for agent consumption."""
        return {
            "primary": self.primary_content[:3],  # top 3 file paths
            "primary_tokens": self.primary_tokens,
            "supporting_snippets": self.supporting_snippets[:5],
            "structural_count": len(self.structural_headers),
            "total_tokens": self.total_tokens,
            "files_included": self.files_included,
            "dropped_count": self.dropped_count,
        }


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def read_file_content(vault_root: str, vault_path: str, max_chars: Optional[int] = None) -> str:
    """Read file content from vault, optionally truncated."""
    full_path = os.path.join(vault_root, vault_path)
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if max_chars:
            return content[:max_chars]
        return content
    except Exception as e:
        logger.warning("Failed to read %s: %s", vault_path, e)
        return ""


def extract_headers(content: str) -> str:
    """Extract only markdown headers (## #) from content."""
    lines = content.split("\n")
    headers = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            headers.append(stripped)
    return "\n".join(headers)


def extract_snippet(content: str, query: str, max_chars: int = 500) -> str:
    """Extract a snippet from content around the query term."""
    query_lower = query.lower()
    content_lower = content.lower()
    start = content_lower.find(query_lower)
    if start == -1:
        return content[:max_chars]
    snippet_start = max(0, start - 100)
    snippet_end = min(len(content), start + max_chars - 100)
    prefix = "..." if snippet_start > 0 else ""
    suffix = "..." if snippet_end < len(content) else ""
    return prefix + content[snippet_start:snippet_end] + suffix


def determine_tier(score: float, top_score: float) -> Optional[str]:
    """Determine which tier a result belongs to, relative to top score."""
    if top_score <= 0:
        return "filtered"
    ratio = score / top_score
    if ratio >= TIER_PRIMARY_THRESHOLD:
        return "primary"
    elif ratio >= TIER_SUPPORTING_THRESHOLD:
        return "supporting"
    elif ratio >= TIER_STRUCTURAL_THRESHOLD:
        return "structural"
    else:
        return "filtered"


def assemble_context(
    results: List[VaultResult],
    vault_root: Optional[str] = None,
    max_tokens: int = 8000,
    query: str = "",
) -> AssembledContext:
    """
    Accordion context assembly: pack the LLM context window using relative-threshold tiers.

    Args:
        results: GARS-ranked and temporally-decayed search results
        vault_root: Path to vault root directory (for file content loading)
        max_tokens: Maximum token budget for the context window
        query: Original search query (for snippet extraction)

    Returns:
        AssembledContext with tiered content and token accounting
    """
    ctx = AssembledContext()
    max_chars = max_tokens * CHARS_PER_TOKEN_ESTIMATE

    if not results:
        ctx.warnings.append("No results to assemble")
        return ctx

    top_score = results[0].score if results else 0.0
    remaining_budget = max_chars
    neighbor_expanded = False

    logger.info("Accordion assembly: %d results, top_score=%.3f, max_tokens=%d", len(results), top_score, max_tokens)

    # Check neighbor expansion trigger
    if top_score >= NEIGHBOR_EXPANSION_THRESHOLD:
        neighbor_expanded = True

    # Categorize results into tiers
    tiers: Dict[str, List[VaultResult]] = {"primary": [], "supporting": [], "structural": [], "filtered": []}
    for r in results:
        tier = determine_tier(r.score, top_score)
        if tier:
            tiers[tier].append(r)

    ctx.dropped_count = len(tiers["filtered"])

    # ---------------------------------------------------------
    # PRIMARY TIER: Full file content (10% soft budget cap each)
    # ---------------------------------------------------------
    for r in tiers["primary"]:
        if remaining_budget <= 0:
            break

        # Load full content
        content = read_file_content(vault_root or "", r.vault_path) if vault_root else r.content
        content_tokens = estimate_tokens(content)

        # Apply per-file budget cap (10% of total)
        file_budget = int(max_chars * TIER_PRIMARY_BUDGET_CAP)
        if content_tokens > file_budget:
            content = content[: file_budget * CHARS_PER_TOKEN_ESTIMATE]
            content_tokens = file_budget
            ctx.warnings.append(f"Truncated {r.vault_path} to {file_budget} tokens (primary cap)")

        # Check if we can fit it
        if content_tokens <= remaining_budget // CHARS_PER_TOKEN_ESTIMATE:
            ctx.primary_content.append(f"=== {r.vault_path} ===\n{content}")
            ctx.files_included.append(r.vault_path)
            ctx.primary_tokens += content_tokens
            remaining_budget -= content_tokens * CHARS_PER_TOKEN_ESTIMATE

    # ---------------------------------------------------------
    # SUPPORTING TIER: 500-char snippets around query terms
    # ---------------------------------------------------------
    for r in tiers["supporting"]:
        if remaining_budget <= 0:
            break

        content = read_file_content(vault_root or "", r.vault_path) if vault_root else r.content
        snippet = extract_snippet(content, query, TIER_SUPPORTING_SNIPPET_CHARS)
        snippet_tokens = estimate_tokens(snippet)

        if snippet_tokens <= remaining_budget // CHARS_PER_TOKEN_ESTIMATE:
            ctx.supporting_snippets.append(f"[{r.vault_path}] {snippet}")
            ctx.files_included.append(r.vault_path)
            ctx.supporting_tokens += snippet_tokens
            remaining_budget -= snippet_tokens * CHARS_PER_TOKEN_ESTIMATE

    # ---------------------------------------------------------
    # STRUCTURAL TIER: Headers only (TOC view), max 10 files
    # ---------------------------------------------------------
    structural_count = 0
    for r in tiers["structural"]:
        if structural_count >= TIER_STRUCTURAL_MAX_FILES or remaining_budget <= 0:
            break

        content = read_file_content(vault_root or "", r.vault_path) if vault_root else r.content
        headers = extract_headers(content)
        if headers:
            header_tokens = estimate_tokens(headers)
            if header_tokens <= remaining_budget // CHARS_PER_TOKEN_ESTIMATE:
                ctx.structural_headers.append(f"=== TOC: {r.vault_path} ===\n{headers}")
                ctx.files_included.append(r.vault_path)
                ctx.structural_tokens += header_tokens
                remaining_budget -= header_tokens * CHARS_PER_TOKEN_ESTIMATE
                structural_count += 1

    # Calculate totals
    ctx.total_tokens = ctx.primary_tokens + ctx.supporting_tokens + ctx.structural_tokens
    ctx.token_budget_used = ctx.total_tokens / max_tokens if max_tokens > 0 else 0.0
    ctx.neighbor_expanded = neighbor_expanded

    # Budget warnings
    if ctx.total_tokens > max_tokens:
        ctx.warnings.append(f"Context exceeded budget: {ctx.total_tokens} > {max_tokens} tokens")
    elif ctx.token_budget_used < 0.5 and len(results) > 0:
        ctx.warnings.append(f"Under-utilized context window: {ctx.token_budget_used:.1%} of {max_tokens} tokens")

    logger.info(
        "Accordion assembly complete: primary=%d, supporting=%d, structural=%d, dropped=%d, total_tokens=%d, budget_used=%.1f%%",
        len(tiers["primary"]),
        len(tiers["supporting"]),
        len(tiers["structural"]),
        ctx.dropped_count,
        ctx.total_tokens,
        ctx.token_budget_used * 100,
    )

    return ctx


def format_for_llm(ctx: AssembledContext) -> str:
    """Format assembled context as a single string for LLM consumption."""
    parts = []

    if ctx.primary_content:
        parts.append("# PRIMARY CONTEXT\n")
        parts.extend(ctx.primary_content)
        parts.append("")

    if ctx.supporting_snippets:
        parts.append("# SUPPORTING SNIPPETS\n")
        parts.extend(ctx.supporting_snippets)
        parts.append("")

    if ctx.structural_headers:
        parts.append("# STRUCTURAL (TOC)\n")
        parts.extend(ctx.structural_headers)
        parts.append("")

    if ctx.warnings:
        parts.append("# WARNINGS\n")
        parts.extend(f"- {w}" for w in ctx.warnings)

    parts.append(f"\n# STATS: {ctx.total_tokens} tokens, {ctx.token_budget_used:.1%} of budget used")

    return "\n".join(parts)
