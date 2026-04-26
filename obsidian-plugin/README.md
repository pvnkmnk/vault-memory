# VaultPortal

A semantic memory layer for Obsidian — integrates vault-memory daemon for neural search, knowledge graphs, and context management.

## Features

### 🔍 Multi-Strategy Search
- **Semantic**: Neural embedding + keyword fusion for natural language queries
- **Text**: Full-text keyword search
- **Graph**: Entity relationship traversal
- **Timeline**: Date-range history queries

### 🕸️ Knowledge Graph
- Interactive D3 force-directed visualization
- Zoom, pan, and drag nodes
- Click to open linked files
- Configurable traversal depth (1-5)

### 📝 Quick Ingest
- Write directly to `_working/` buffer
- Promote wiki-quality content to permanent pages
- Support for entity, concept, comparison, and analysis page types

### 🔄 Auto-Sync
- Background file change detection
- Debounced batching for performance
- Visual status indicator in status bar
- Configurable exclude patterns

### ⚙️ Settings
- Daemon URL configuration
- Sync enable/disable with debounce control
- Pattern-based file exclusion

## Installation

### Requirements
- [Obsidian](https://obsidian.md/) v0.15.0+
- [vault-memory daemon](https://github.com/pvnkmnk/vault-memory) running on `http://localhost:5051`

### Manual Installation
1. Download the latest release
2. Copy `main.js`, `manifest.json`, and `styles.css` to your vault's `.obsidian/plugins/vault-portal/` folder
3. Enable the plugin in Obsidian Settings → Community Plugins

**Note**: If building from source, run `npm run build` to generate `main.js`.

### Development
```bash
cd obsidian-plugin
npm install
npm run build
```

## Usage

### Commands (Command Palette)
- `VaultPortal: Search vault` — Open search panel
- `VaultPortal: View knowledge graph` — Open graph visualization
- `VaultPortal: Extract triples` — Extract knowledge graph from current file
- `VaultPortal: Promote to wiki` — Promote current file to wiki
- `VaultPortal: Quick ingest to _working/` — Open ingest modal
- `VaultPortal: Promote content to wiki` — Open promote modal
- `VaultPortal: List attached memory blocks` — Show current context blocks
- `VaultPortal: Sync now` — Force immediate sync
- `VaultPortal: Sync status` — Show sync status

### Search Modal
1. Open the search panel via command palette
2. Select a search mode (Semantic, Text, Graph, Timeline)
3. Enter your query and press Enter
4. Click results to open files

### Knowledge Graph
1. Open the graph panel via command palette
2. Select traversal depth (1-5)
3. Drag nodes to reposition
4. Use zoom controls (+/−⊡) to navigate
5. Click nodes to open linked files

## Configuration

### Daemon URL
Default: `http://localhost:5051`

Set this to your vault-memory daemon address if running on a different port or machine.

### Auto-Sync Settings
- **Enable auto-sync**: Toggle background sync on/off
- **Sync debounce**: Delay before syncing after changes (default: 2000ms)
- **Exclude patterns**: Comma-separated paths to exclude (default: `.git,_working,.obsidian`)

## Architecture

```
Obsidian Plugin (VaultPortal)
    ├── SearchPanel — Multi-strategy search UI
    ├── GraphCanvas — D3 force-directed knowledge graph
    ├── IngestModal — Content ingestion workflow
    ├── AutoSyncEngine — Background file change detection
    └── DaemonClient — HTTP client for vault-memory daemon
    
vault-memory Daemon (port 5051)
    ├── Semantic Search (Weaviate)
    ├── PostgreSQL Storage
    └── Knowledge Graph
```

## License

MIT