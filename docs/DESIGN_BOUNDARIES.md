# Design Boundaries — What vault-memory Deliberately Does Not Build

> **Status:** Accepted — 2026-09-10
> **Companion:** [PRD.md](PRD.md)
> **Format credit:** [Astro-Han/karpathy-llm-wiki](https://github.com/Astro-Han/karpathy-llm-wiki) pioneered this section; their reasoning is cited where we agree, and our divergences are explained where we don't.

A boundary list is only useful if items are *evidence-based refusals*, not roadmap parking. Each item below states the temptation, the reasoning, and what would change our mind.

---

## 1. No cloud service, no telemetry, no account

The daemon binds loopback. Vault content never leaves the machine except to the LLM providers the user configures (local Ollama/llama.cpp are first-class — the `llm` compose profile and CI both run fully local). There is no hosted tier to upsell and no analytics pipeline. This is a load-bearing product decision: the vault is the user's second brain, and second brains don't phone home.

*Revisit only if:* a user explicitly asks for multi-device sync — and even then the answer is git, not a service.

## 2. No silent auto-merge of contradictions

sage-wiki's bi-temporal model (contradiction invalidates the old edge; `as_of` queries answer "what did we believe in January?") is the right *shape*, but our v0.9.0 position is more conservative: contradictions between mined lessons and high-trust pages are **flagged for human review, never resolved automatically** — by lint (S31-4), not by the miner.

*Revisit if:* corroboration data accumulates enough to calibrate automatic downgrading, and #80's benchmark gives us a recall measure to tune against.

## 3. No numeric confidence scores on prose knowledge

karpathy-llm-wiki's argument is accepted: uncalibrated 0–1 numbers are false precision that agents and humans alike over-trust. Evidence strength lives in *prose and provenance* — which session asserted it, how many corroborated it, what source it came from. The one exception is the existing retrieval stack (GARS/decay), where scores are *ranking signals*, not claims about truth; that distinction is now documented rather than left implicit.

*Revisit if:* we ever collect calibration data (predicted-confidence vs human-review outcomes) — until then, corroboration counts, not decimals.

## 4. No vector/graph search for lesson lookup below the measured crossover

karpathy-llm-wiki refuses vector search entirely: "at 50K–100K tokens of curated wiki, grep and read are more reliable." We partially dissent — but the burden of proof is on us. For **lesson serving** (S31-5), the top-K ranking uses recency × corroboration × trust over a *small, curated set* — deliberately not embeddings. The full hybrid stack (Weaviate + BM25 + GARS) exists for the *whole-vault* search problem, where corpora genuinely exceed grep's comfort zone.

**Action tied to this boundary:** #80 (benchmark) will measure where the crossover actually is for whole-vault search. If plain-agent grep matches vault-memory under N notes, the README will say so. A tool that's only honest about when it helps is more trustworthy than one that claims to always help.

## 5. No OKF conformance yet

The Open Knowledge Format is a v0.1 draft with minimal tooling. Our page conventions (entity/concept/comparison/analysis + maturity levels) predate it and are load-bearing in lint and heartbeat. Same conclusion karpathy-llm-wiki reached independently.

*Revisit when:* OKF ships a migration tool or two major adopters.

## 6. No agent-session recording beyond opt-in structured records

The miner (S31) consumes what the session *chooses* to report: the structured close record, freeform notes, and file-touch events from `sync_log`. We will not build transcript capture, screen recording, or automatic full-history ingestion of agent chat logs — the privacy surface and storage cost are wrong for a loopback tool, and Karpathy's rule holds: the LLM maintains the wiki, the human (and the agent's own judgment) choose what's worth keeping.

*Revisit if:* a major harness exposes a first-class, user-approved session-export format we can consume locally.

## 7. No per-page review timers

Nobody can predict at compile time how fast a domain moves. Maintenance is driven by whole-vault lint and the digest cadence (daily/weekly/monthly), not per-page countdowns. (Also rejected by karpathy-llm-wiki, for the same reason.)

## 8. No access-count-based decay for factual knowledge

"frequently asked is not the same as true" — accepted for facts. The scoped exception is *lessons* (process knowledge): for "how work gets done on this system," recency genuinely is evidence, because systems change underfoot. That's why lesson pages use the `log` decay profile unless corroborated, and factual pages don't decay at all. The distinction — not the mechanism — is the design decision.

## 9. No retract/bad-source machinery (yet)

Until a real bad-source event occurs, handling is manual. Premature machinery for a failure mode with zero observed instances is complexity without a customer. (Direct adoption of karpathy-llm-wiki's reasoning; their production logs convinced us.)

## 10. No MCP SDK migration until the contract test exists

Phase 5.1 of the modernization plan (official `mcp` SDK) is *recommended*, not scheduled, until #85's skills layer and the MCP surface test land — because swapping transport under a tool surface with no contract test is how working systems regress. Order of operations, not a refusal.

## 11. No multi-tenant/team features in v0.9.0

The learning loop is single-vault, single-operator by design. sage-wiki's team/federation tier is a real market, but it's a different product with different trust problems (whose lesson wins? whose review gate?). Nothing in S31/S32 requires multi-user, and adding it would contaminate the trust model in §6 of the PRD.

*Revisit if:* the single-vault loop proves itself and a concrete second-vault use case appears (the answer is probably git remotes, not a server).

## 12. Not building: ontology editor, graph UI beyond the plugin, dashboard web app

The Obsidian plugin + CLI + MCP is the whole interface story. Competitors with editors and dashboards (OpenKnowledge, BrainDB's frontend) are validated by their traction — but our differentiator is the loop, not surface area. Every week spent on UI polish is a week the loop isn't compounding.

---

## The one-sentence version

**vault-memory compiles what humans feed it and what agents learn into reviewed, provenance-stamped knowledge, serves it back at the moment of need, and refuses to do silently what a human should decide.**
