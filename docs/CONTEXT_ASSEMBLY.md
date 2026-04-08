# Context Assembly — Accordion Tier Algorithm

> **Module**: `daemon/context_assembler.py`  
> **Sprint**: P4  
> **Status**: ✅ Implemented

---

## Overview

The accordion context assembler converts a ranked list of `VaultResult` objects
(output of `UnifiedSearch`) into a single token-budgeted context string ready
for injection into an agent's context window.

Rather than applying hard score cut-offs, the algorithm uses **relative
thresholds**: each file's score is compared to the top result's score, so the
tier boundaries adapt automatically to the score distribution of each query.

---

## Tier Definitions

| Tier | Threshold (relative to top score) | Content included | Constraints |
|------|-----------------------------------|------------------|-------------|
| **Primary** | ≥ 90% | Full file content | 10% soft budget cap per file |
| **Supporting** | ≥ 70% | 500-char snippet around query terms | — |
| **Structural** | ≥ 35% | ATX headers only (TOC view) | Max 10 files |
| **Filtered** | < 35% | Dropped entirely | Prevents hallucination-by-bloat |

### Threshold calculation

```
relative_score = result.score / top_result.score
```

Example — top score = 0.82:

| File | Score | Relative | Tier |
|------|-------|----------|------|
| notes/djinn.md | 0.82 | 1.00 | Primary |
| notes/architecture.md | 0.76 | 0.927 | Primary |
| notes/planning.md | 0.61 | 0.744 | Supporting |
| notes/diary.md | 0.35 | 0.427 | Structural |
| notes/random.md | 0.22 | 0.268 | Filtered |

---

## Token Budget

- Default budget: **4 000 tokens** (configurable via `token_budget` parameter).
- Token estimate formula: `len(text) // 4`  (1 token ≈ 4 chars).
- **Per-file cap** (primary tier): `token_budget × 0.10` tokens.  
  At 4 000-token budget → 400 tokens per file.
- When a file exceeds the per-file cap it is truncated and a `[truncated: per-file cap]` marker is appended.
- When the global budget is exhausted, remaining files are dropped and `budget_exhausted: true` is set in the result.

### Budget walkthrough (4 000-token budget)

```
[PRIMARY]    djinn.md           → 380 tokens  (cap = 400)   remaining: 3620
[PRIMARY]    architecture.md    → 400 tokens  (truncated)   remaining: 3220
[SUPPORTING] planning.md        →  78 tokens  (snippet)     remaining: 3142
[STRUCTURAL] diary.md           →  12 tokens  (headers)     remaining: 3130
[FILTERED]   random.md          →   dropped
──────────────────────────────────────────────
Total: 870 tokens / 4000 budget
```

---

## API

```python
from daemon.context_assembler import assemble_context, DEFAULT_TOKEN_BUDGET

assembly = assemble_context(
    results=search_results,     # List[VaultResult]
    query="djinn architecture",
    vault_root="/home/user/vault",
    token_budget=4000,
)

print(assembly.to_text())        # Markdown string for context injection
print(assembly.to_dict())        # Structured metadata dict for MCP return
print(assembly.total_tokens)     # int
print(assembly.budget_exhausted) # bool
```

### `AssemblyResult` fields

| Field | Type | Description |
|-------|------|-------------|
| `entries` | `List[AssembledEntry]` | Ordered tier entries |
| `total_tokens` | `int` | Total token cost of assembled context |
| `budget` | `int` | Token budget used |
| `budget_exhausted` | `bool` | True if budget was reached |
| `dropped_count` | `int` | Files dropped (filtered or budget) |
| `truncated_count` | `int` | Files truncated (per-file cap or budget) |

### `AssembledEntry` fields

| Field | Type | Description |
|-------|------|-------------|
| `vault_path` | `str` | Vault-relative path |
| `tier` | `str` | `"primary"` \| `"supporting"` \| `"structural"` |
| `content` | `str` | Tier-appropriate content |
| `tokens` | `int` | Token cost of this entry |
| `score` | `float` | Absolute score from retrieval |
| `relative` | `float` | Score relative to top result |

---

## Integration Points

### `memory/project_state` MCP tool

The `_memory_project_state()` function in `cli/mcp_adapter.py` calls
`assemble_context()` on the semantic search results and injects the assembled
context string into the `semantic_context` field of the session bundle.

### Search result formatting

Any tool that returns search results to an agent (e.g., `search`, `search_siblings`)
can optionally call `assemble_context()` to convert ranked results into a
pre-assembled context string rather than returning raw snippets.

---

## Constants (tunable)

```python
DEFAULT_TOKEN_BUDGET      = 4000
PRIMARY_FILE_CAP_FRACTION = 0.10   # 10% of budget per primary file
TIER_PRIMARY              = 0.90   # >= 90% of top score
TIER_SUPPORTING           = 0.70   # >= 70%
TIER_STRUCTURAL           = 0.35   # >= 35%
STRUCTURAL_MAX_FILES      = 10
SNIPPET_CHARS             = 500
```

All constants are defined at module level and can be overridden at import time
or extended in a future `AssemblyConfig` dataclass if per-project tuning is needed.

---

## Design Notes

- **Relative thresholds** are preferred over absolute ones because score
  distributions shift depending on query type, vault size, and embedding model.
  A file that scores 0.40 may be highly relevant if the top score is 0.42,
  or completely irrelevant if the top score is 0.95.
- **The 35% structural floor** prevents "hallucination by bloat" — including
  low-signal files as full content inflates the context window and can cause
  the agent to invent connections between unrelated content.  Structural tier
  (headers only) preserves discoverability without adding noise.
- **Per-file cap (primary tier)** prevents a single large file from consuming
  the entire budget, ensuring at least ~10 primary-tier files can fit.
