"""
Accordion Context Assembly — P4 Sprint

Assembles a token-budgeted context window from VaultResult candidates
using relative-threshold tiers based on the top result's score.

Tier thresholds (relative to top score):
  Primary    >= 90%  : Full file content (10% soft budget cap per file)
  Supporting >= 70%  : 500-char snippet around query terms
  Structural >= 35%  : Headers only (TOC view), max 10 files
  Filtered    < 35%  : Dropped to prevent hallucination-by-bloat

Token budget: caller-supplied (default 4000 tokens).
All thresholds are relative to the top result's normalised score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default token budget for assembled context
DEFAULT_TOKEN_BUDGET = 4000

# Per-file soft cap as fraction of total budget (primary tier)
PRIMARY_FILE_CAP_FRACTION = 0.10

# Tier thresholds (relative to top score)
TIER_PRIMARY    = 0.90
TIER_SUPPORTING = 0.70
TIER_STRUCTURAL = 0.35

# Max files in structural tier
STRUCTURAL_MAX_FILES = 10

# Snippet window around query term match
SNIPPET_CHARS = 500


def _token_est(text: str) -> int:
    return max(1, len(text) // 4)


def _extract_headers(content: str) -> str:
    """Return only ATX-style Markdown headers from content."""
    lines = content.splitlines()
    headers = [l for l in lines if re.match(r"^#{1,6}\s", l)]
    return "\n".join(headers) if headers else "(no headers)"


def _snippet_around_query(content: str, query: str, window: int = SNIPPET_CHARS) -> str:
    """
    Find the first occurrence of any query term and return a window of
    `window` characters centred on it.  Falls back to the file head.
    """
    terms = [t.strip().lower() for t in re.split(r"\s+", query) if len(t.strip()) >= 3]
    lower = content.lower()
    best_pos = len(content)  # sentinel
    for term in terms:
        idx = lower.find(term)
        if idx != -1 and idx < best_pos:
            best_pos = idx
    if best_pos == len(content):
        # No term found — return head
        return content[:window]
    start = max(0, best_pos - window // 2)
    end   = min(len(content), start + window)
    snippet = content[start:end]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(content) else ""
    return prefix + snippet + suffix


@dataclass
class AssembledEntry:
    vault_path: str
    tier:       str   # "primary" | "supporting" | "structural"
    content:    str
    tokens:     int
    score:      float
    relative:   float  # score / top_score


@dataclass
class AssemblyResult:
    entries:          List[AssembledEntry] = field(default_factory=list)
    total_tokens:     int = 0
    budget:           int = DEFAULT_TOKEN_BUDGET
    budget_exhausted: bool = False
    dropped_count:    int = 0   # files below TIER_STRUCTURAL threshold
    truncated_count:  int = 0   # files that were token-capped

    def to_text(self) -> str:
        """Render the assembled context as a single Markdown string."""
        parts: List[str] = []
        for e in self.entries:
            tier_label = e.tier.upper()
            parts.append(f"### [{tier_label}] {e.vault_path}  (score={e.score:.3f})\n\n{e.content}")
        return "\n\n---\n\n".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [
                {
                    "vault_path": e.vault_path,
                    "tier":       e.tier,
                    "tokens":     e.tokens,
                    "score":      round(e.score, 4),
                    "relative":   round(e.relative, 4),
                }
                for e in self.entries
            ],
            "total_tokens":     self.total_tokens,
            "budget":           self.budget,
            "budget_exhausted": self.budget_exhausted,
            "dropped_count":    self.dropped_count,
            "truncated_count":  self.truncated_count,
        }


def assemble_context(
    results: List[Any],           # List[VaultResult] from retrieval pipeline
    query: str,
    vault_root: Optional[str] = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> AssemblyResult:
    """
    Build an accordion-tiered context from retrieval results.

    Args:
        results:      Ranked VaultResult list (highest score first).
        query:        Original query string (used for snippet extraction).
        vault_root:   Absolute path to vault root — used to read full file
                      content for primary-tier entries when result.content
                      contains only a truncated preview.
        token_budget: Maximum tokens for the assembled context.

    Returns:
        AssemblyResult with ranked, tiered, budget-capped entries.
    """
    assembly = AssemblyResult(budget=token_budget)

    if not results:
        return assembly

    top_score = results[0].score
    if top_score <= 0:
        top_score = 1e-9  # avoid division by zero

    per_file_cap_tokens = max(200, int(token_budget * PRIMARY_FILE_CAP_FRACTION))
    structural_count = 0
    remaining = token_budget

    for r in results:
        relative = r.score / top_score

        # ── Classify tier ──────────────────────────────────────────────────
        if relative >= TIER_PRIMARY:
            tier = "primary"
        elif relative >= TIER_SUPPORTING:
            tier = "supporting"
        elif relative >= TIER_STRUCTURAL:
            tier = "structural"
        else:
            assembly.dropped_count += 1
            continue  # filtered — below noise floor

        if tier == "structural":
            if structural_count >= STRUCTURAL_MAX_FILES:
                assembly.dropped_count += 1
                continue
            structural_count += 1

        if remaining <= 0:
            assembly.budget_exhausted = True
            assembly.dropped_count += 1
            continue

        # ── Fetch full content when needed ─────────────────────────────────
        full_content: Optional[str] = None
        if vault_root and tier in ("primary", "supporting"):
            vr = Path(vault_root)
            candidate = (vr / r.vault_path).resolve()
            try:
                candidate.relative_to(vr.resolve())
                if candidate.exists():
                    full_content = candidate.read_text(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
        if full_content is None:
            full_content = r.content  # fall back to truncated preview from Weaviate

        # ── Build tier content ──────────────────────────────────────────────
        if tier == "primary":
            raw = full_content
            raw_tokens = _token_est(raw)
            if raw_tokens > per_file_cap_tokens:
                raw = raw[: per_file_cap_tokens * 4] + "\n... [truncated: per-file cap]"
                assembly.truncated_count += 1
            content = raw

        elif tier == "supporting":
            content = _snippet_around_query(full_content, query, window=SNIPPET_CHARS)

        else:  # structural
            content = _extract_headers(full_content)

        # ── Token-budget gate ───────────────────────────────────────────────
        entry_tokens = _token_est(content)
        if entry_tokens > remaining:
            # Partially include what fits
            fit_chars = remaining * 4
            if fit_chars > 40:
                content = content[:fit_chars] + "\n... [truncated: budget]"
                entry_tokens = _token_est(content)
                assembly.truncated_count += 1
            else:
                assembly.budget_exhausted = True
                assembly.dropped_count += 1
                continue

        assembly.entries.append(AssembledEntry(
            vault_path=r.vault_path,
            tier=tier,
            content=content,
            tokens=entry_tokens,
            score=r.score,
            relative=relative,
        ))
        assembly.total_tokens += entry_tokens
        remaining             -= entry_tokens

    return assembly
