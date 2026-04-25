# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-04-25

### Added
- **Settings Panel (DJI-239)**
  - GUI settings tab in Obsidian preferences
  - Daemon URL configuration with persistent storage
  - Default URL: `http://localhost:5051`

- **Auth + Daemon URL Wiring (DJI-240)**
  - MCP adapter `--daemon-url` and `--api-key` CLI options
  - Environment variable fallback: `VAULT_MEMORY_URL`, `VAULT_MEMORY_API_KEY`
  - Full chain: Plugin Settings → DaemonClient.setDaemonUrl() → MCP adapter → Daemon HTTP calls

- **Multi-Strategy Search Panel (DJI-241)**
  - Semantic mode with neural embedding + keyword fusion
  - Text mode for full-text keyword search
  - Graph mode for entity relationship traversal
  - Timeline mode for date-range history queries
  - Result metadata badges (score, trust, maturity, agent-written)
  - TopK selector for configurable results count

- **Ingest Command Flow (DJI-242)**
  - Quick write to `_working/` buffer with confidence/maturity metadata
  - Promote wiki-quality content with page type selection (entity, concept, comparison, analysis)
  - Command palette entries for all ingest operations

- **Knowledge Graph Visualization (DJI-243)**
  - D3 force-directed graph with interactive nodes
  - Drag to reposition, hover effects with stroke highlight
  - Zoom controls (in, out, fit to view)
  - Configurable traversal depth (1-5)
  - Click-to-open file linking with normalized path matching
  - Arrow markers for directional edges

- **Auto-Sync Engine (DJI-244)**
  - Background file change detection via Obsidian vault events
  - Debounced batching with configurable delay (500ms+)
  - File filtering: `.md` extension only, excludes `_working/` and `.obsidian/`
  - Visual status indicator in status bar (idle, pending, syncing, synced, error)
  - Commands: Sync now, Sync status

- **Command Palette Integration**
  - 9 commands: search, graph, cognify, promote, ingest, ingest-promote, list-blocks, sync-now, sync-status
  - Editor callbacks for cognify and promote operations

### Changed
- Plugin ID: `vault-portal`
- Min App Version: 0.15.0
- Build: esbuild with minification (31KB output)

### Security
- API key support via CLI and environment variables
- Network permissions declared in manifest

### Dependencies
- `d3` v7.8.5 — graph visualization
- `typescript` v5.0+ — type checking
- `esbuild` v0.28+ — bundling and minification