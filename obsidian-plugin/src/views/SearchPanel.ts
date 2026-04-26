import { Notice } from 'obsidian';
import type { WorkspaceLeaf } from 'obsidian';
import { App, View } from 'obsidian';
import type { DaemonClient, SearchResult, SearchMode } from '../components/DaemonClient';

interface ModeConfig {
  id: SearchMode;
  label: string;
  icon: string;
  description: string;
}

const SEARCH_MODES: ModeConfig[] = [
  { id: 'vector', label: 'Semantic', icon: '🔍', description: 'Neural embedding + keyword fusion' },
  { id: 'keyword', label: 'Text', icon: '📝', description: 'Full-text keyword search' },
  { id: 'graph', label: 'Graph', icon: '🕸️', description: 'Entity relationship traversal' },
  { id: 'temporal', label: 'Timeline', icon: '📅', description: 'Date-range history query' },
];

export class SearchPanel extends View {
  client: DaemonClient;
  results: SearchResult[] = [];
  currentMode: SearchMode = 'vector';
  isLoading: boolean = false;
  selectedResult: SearchResult | null = null;
  private readonly getDefaultMode: () => SearchMode;

  constructor(app: App, leaf: WorkspaceLeaf, client: DaemonClient, getDefaultMode: () => SearchMode = () => 'vector') {
    super(leaf);
    this.client = client;
    this.getDefaultMode = getDefaultMode;
  }

  getViewType(): string { return 'vault-portal-search'; }
  getDisplayText(): string { return 'VaultPortal Search'; }

  async onOpen() {
    // Always get fresh default mode from provider
    this.currentMode = this.getDefaultMode();
    this.containerEl.empty();
    this.renderHeader();
    this.renderModeSelector();
    this.renderSearchInput();
    this.renderActionButtons();
    this.renderResults();
    this.renderStatusBar();
  }

  private renderHeader() {
    const header = this.containerEl.createDiv('vp-search-header');
    header.createEl('h2', { text: 'Vault Search' });
    header.createEl('span', { 
      text: 'VaultPortal', 
      cls: 'vp-header-badge' 
    });
  }

  private renderModeSelector() {
    const selector = this.containerEl.createDiv('vp-mode-selector');
    
    SEARCH_MODES.forEach((mode) => {
      const btn = selector.createEl('button', {
        text: `${mode.icon} ${mode.label}`,
        cls: 'vp-mode-btn',
        attr: { 
          'data-mode': mode.id,
          'aria-label': mode.description,
          'title': mode.description
        }
      });
      
      btn.addEventListener('click', () => {
        this.setMode(mode.id);
      });
    });
    
    this.updateModeButtons();
  }

  private setMode(mode: SearchMode) {
    this.currentMode = mode;
    this.updateModeButtons();
    this.updateStatus(`Mode: ${mode}`);
    
    const input = this.containerEl.querySelector('.vp-search-input') as HTMLInputElement;
    if (input && input.value.trim()) {
      this.performSearch(input.value);
    }
  }

  private updateModeButtons() {
    const buttons = this.containerEl.querySelectorAll('.vp-mode-btn');
    buttons.forEach((btn) => {
      const el = btn as HTMLElement;
      const isActive = el.dataset.mode === this.currentMode;
      el.classList.toggle('vp-mode-active', isActive);
      el.setAttribute('aria-pressed', String(isActive));
    });
  }

  private renderSearchInput() {
    const wrapper = this.containerEl.createDiv('vp-search-wrapper');
    
    const inputWrapper = wrapper.createDiv('vp-input-wrapper');
    const input = inputWrapper.createEl('input', { 
      type: 'text', 
      placeholder: 'Search vault... (Enter to search)',
      cls: 'vp-search-input',
      attr: { 
        'aria-label': 'Search query',
        'autocomplete': 'off',
        'spellcheck': 'false'
      }
    });
    
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        this.performSearch((e.target as HTMLInputElement).value);
      }
    });
    
    const clearBtn = wrapper.createEl('button', {
      text: '✕',
      cls: 'vp-clear-btn',
      attr: { 'aria-label': 'Clear search' }
    });
    clearBtn.addEventListener('click', () => {
      input.value = '';
      this.results = [];
      this.selectedResult = null;
      this.renderResults();
    });

    const topKWrapper = wrapper.createDiv('vp-topk-wrapper');
    topKWrapper.createEl('label', { text: 'Results:' });
    const topKSelect = topKWrapper.createEl('select', { cls: 'vp-topk-select' });
    ['5', '10', '15', '20', '30'].forEach(n => {
      topKSelect.createEl('option', { value: n, text: n });
    });
  }

  private renderActionButtons() {
    const actions = this.containerEl.createDiv('vp-action-buttons');
    
    // Bulk Import
    const importBtn = actions.createEl('button', {
      text: '📥 Import',
      cls: 'vp-action-btn',
      attr: { 'aria-label': 'Bulk import notes' }
    });
    importBtn.addEventListener('click', () => this.showBulkImportModal());

    // Bulk Export
    const exportBtn = actions.createEl('button', {
      text: '📤 Export',
      cls: 'vp-action-btn',
      attr: { 'aria-label': 'Bulk export notes' }
    });
    exportBtn.addEventListener('click', () => this.handleBulkExport());

    // Promote selected
    const promoteBtn = actions.createEl('button', {
      text: '📑 Promote',
      cls: 'vp-action-btn vp-action-promote',
      attr: { 'aria-label': 'Promote selected content to wiki' }
    });
    promoteBtn.addEventListener('click', () => this.handlePromote());

    // Cognify current file
    const cognifyBtn = actions.createEl('button', {
      text: '🧠 Cognify',
      cls: 'vp-action-btn',
      attr: { 'aria-label': 'Extract triples from current file' }
    });
    cognifyBtn.addEventListener('click', () => this.handleCognify());

    // Vault Lint
    const lintBtn = actions.createEl('button', {
      text: '🔍 Lint',
      cls: 'vp-action-btn',
      attr: { 'aria-label': 'Run vault health check' }
    });
    lintBtn.addEventListener('click', () => this.handleLint());
  }

  private async showBulkImportModal() {
    const activeFile = this.app.workspace.getActiveFile();
    if (!activeFile) {
      new Notice('Open a file to use as import source', 2000);
      return;
    }

    try {
      const content = await this.app.vault.read(activeFile);
      const title = activeFile.name.replace(/\\.md$/i, '');
      
      const result = await this.client.bulkImport([{ title, content }]);
      
      if (result.imported > 0) {
        new Notice(`Imported ${result.imported} note(s)`, 2000);
      } else if (result.skipped > 0) {
        new Notice(`Skipped ${result.skipped} duplicate(s)`, 2000);
      }
      
      if (result.errors.length > 0) {
        new Notice(`Errors: ${result.errors[0].error}`, 3000);
      }
    } catch (e) {
      new Notice(`Import failed: ${e}`, 3000);
    }
  }

  private async handleBulkExport() {
    try {
      new Notice('Exporting notes...', 1500);
      const result = await this.client.bulkExport({ limit: 50 });
      
      if (result.notes.length === 0) {
        new Notice('No notes to export', 2000);
        return;
      }

      // Create markdown content from export
      const exportContent = result.notes.map(n => 
        `# ${n.title}\n\n${n.content}\n\n---\n`
      ).join('\n');

      // Create a file in vault
      const filename = `export-${Date.now()}.md`;
      await this.app.vault.create(filename, exportContent);
      
      new Notice(`Exported ${result.count} notes`, 2000);
    } catch (e) {
      new Notice(`Export failed: ${e}`, 3000);
    }
  }

  private async handlePromote() {
    if (!this.selectedResult) {
      new Notice('Select a search result first', 2000);
      return;
    }

    const path = this.selectedResult.file_path || this.selectedResult.path;
    if (!path) {
      new Notice('No file path available', 2000);
      return;
    }

    try {
      await this.client.promote(path);
      new Notice('Promoted to wiki', 2000);
    } catch (e) {
      new Notice(`Promote failed: ${e}`, 3000);
    }
  }

  private async handleCognify() {
    const activeFile = this.app.workspace.getActiveFile();
    if (!activeFile) {
      new Notice('Open a file to cognify', 2000);
      return;
    }

    try {
      const content = await this.app.vault.read(activeFile);
      const result = await this.client.cognify(content);
      
      const count = result.triples?.length || 0;
      new Notice(`Extracted ${count} triples`, 2500);
    } catch (e) {
      new Notice(`Cognify failed: ${e}`, 3000);
    }
  }

  private async handleLint() {
    try {
      new Notice('Running vault lint...', 1500);
      const result = await this.client.runLint(30);
      
      const issues = [
        result.orphans > 0 ? `${result.orphans} orphans` : null,
        result.stale_nodes > 0 ? `${result.stale_nodes} stale` : null,
        result.missing_pages > 0 ? `${result.missing_pages} missing` : null,
      ].filter(Boolean).join(', ');

      if (issues) {
        new Notice(`Lint: ${issues}`, 4000);
      } else {
        new Notice('Vault is healthy ✓', 2000);
      }
    } catch (e) {
      new Notice(`Lint failed: ${e}`, 3000);
    }
  }

  async performSearch(query: string) {
    if (!query.trim()) {
      new Notice('Please enter a search query', 2000);
      return;
    }

    this.isLoading = true;
    this.selectedResult = null;
    this.showLoading();

    try {
      const topKSelect = this.containerEl.querySelector('.vp-topk-select') as HTMLSelectElement;
      const topK = parseInt(topKSelect?.value || '10', 10);
      
      this.results = await this.client.search(query, this.currentMode, topK);
      this.renderResults();
      this.updateStatus(`${this.results.length} results for \"${query}\" (${this.currentMode} mode)`);
    } catch (e) {
      this.showError(`Search failed: ${e}`);
    } finally {
      this.isLoading = false;
    }
  }

  private showLoading() {
    const resultsEl = this.containerEl.querySelector('.vp-results');
    if (resultsEl) resultsEl.remove();
    
    const loading = this.containerEl.createDiv('vp-results vp-loading');
    const loadingText = loading.createEl('div', { cls: 'vp-loading-text' });
    loadingText.setText('🔄 Searching...');
  }

  private renderResults() {
    const existing = this.containerEl.querySelector('.vp-results');
    if (existing) existing.remove();

    const results = this.containerEl.createDiv('vp-results');
    
    if (this.results.length === 0) {
      const noResults = results.createDiv('vp-no-results');
      noResults.setText('No results found. Try a different query or mode.');
      return;
    }

    this.results.forEach((r, index) => {
      const item = results.createDiv('vp-result-item');
      
      // Selection indicator
      if (this.selectedResult === r) {
        item.addClass('vp-result-selected');
      }
      
      const header = item.createDiv('vp-result-header');
      const titleEl = header.createEl('div', { cls: 'vp-result-title' });
      titleEl.setText(r.title || r.file_path?.split('/').pop() || 'Untitled');
      
      const badges = header.createDiv('vp-result-badges');
      
      badges.createEl('span', { 
        text: `${(r.score * 100).toFixed(0)}%`, 
        cls: 'vp-badge vp-badge-score' 
      });
      
      const trustClass = r.trust === 'high' ? 'vp-badge-trust-high' : 
                         r.trust === 'medium' ? 'vp-badge-trust-med' : 'vp-badge-trust-low';
      badges.createEl('span', { 
        text: r.trust || 'unknown', 
        cls: `vp-badge vp-badge-trust ${trustClass}` 
      });
      
      badges.createEl('span', { 
        text: r.maturity || 'unknown', 
        cls: 'vp-badge vp-badge-maturity' 
      });
      
      if (r.agent_written) {
        badges.createEl('span', { text: 'agent', cls: 'vp-badge vp-badge-agent' });
      }

      item.createEl('div', { 
        text: r.file_path || r.path || '', 
        cls: 'vp-result-path' 
      });

      const snippet = item.createDiv('vp-result-content');
      snippet.createEl('p', { text: r.content?.slice(0, 300) || r.snippet?.slice(0, 300) || 'No preview available' });
      
      const footer = item.createDiv('vp-result-footer');
      
      if (r.tags && r.tags.length > 0) {
        const tagsEl = footer.createDiv('vp-result-tags');
        r.tags.slice(0, 5).forEach(tag => {
          tagsEl.createEl('span', { text: `#${tag}`, cls: 'vp-tag' });
        });
      }
      
      if (r.modified) {
        const date = new Date(r.modified).toLocaleDateString();
        footer.createEl('span', { text: `Modified: ${date}`, cls: 'vp-result-date' });
      }

      item.addEventListener('click', (e) => {
        // Toggle selection with Ctrl/Cmd click
        if (e.ctrlKey || e.metaKey) {
          this.selectedResult = this.selectedResult === r ? null : r;
          this.renderResults();
        } else {
          this.selectedResult = r;
          this.renderResults();
          this.openNote(r);
        }
      });
      item.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') this.openNote(r);
      });
      item.setAttribute('tabindex', '0');
      item.setAttribute('role', 'button');
    });

    // Update promote button state
    const promoteBtn = this.containerEl.querySelector('.vp-action-promote') as HTMLButtonElement;
    if (promoteBtn) {
      promoteBtn.disabled = !this.selectedResult;
    }
  }

  private async openNote(result: SearchResult) {
    const path = result.file_path || result.vault_path || result.path;
    if (!path) return;
    
    try {
      // Normalize path separators
      const normalizedPath = path.replace(/\\/g, '/');
      const file = this.app.metadataCache.getFirstLinkpathDest(normalizedPath, '');
      
      if (file) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
      } else {
        new Notice(`File not found: ${path}`, 3000);
      }
    } catch (e) {
      new Notice(`Could not open: ${path}`, 3000);
    }
  }

  private showError(msg: string) {
    const existing = this.containerEl.querySelector('.vp-error');
    if (existing) existing.remove();
    
    const error = this.containerEl.createDiv('vp-error');
    error.setText(msg);
  }

  private renderStatusBar() {
    const statusBar = this.containerEl.createDiv('vp-status-bar');
    const status = this.client.getStatus();
    statusBar.createEl('span', { 
      text: status === 'connected' ? '🟢 Online' : status === 'offline' ? '🔴 Offline' : '🟡 Checking...',
      cls: `vp-status-text ${status === 'connected' ? 'vp-status-online' : 'vp-status-offline'}`
    });
    statusBar.createEl('span', { 
      text: `Mode: ${this.currentMode}`,
      cls: 'vp-status-current-mode'
    });
    
    // Selected result indicator
    if (this.selectedResult) {
      const selectedTitle = this.selectedResult.title || this.selectedResult.file_path?.split('/').pop() || 'Selected';
      statusBar.createEl('span', {
        text: `Selected: ${selectedTitle}`,
        cls: 'vp-status-selected'
      });
    }
  }

  private updateStatus(msg: string) {
    const statusEl = this.containerEl.querySelector('.vp-status-bar .vp-status-text');
    if (statusEl) statusEl.textContent = msg;
  }

  protected async onClose(): Promise<void> {
    this.results = [];
    this.selectedResult = null;
  }
}