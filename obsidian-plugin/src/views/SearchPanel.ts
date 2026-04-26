import { View, WorkspaceLeaf } from 'obsidian';
import { DaemonClient } from '../components/DaemonClient';

const VIEW_TYPE_SEARCH = 'vault-portal-search';

export class SearchPanel extends View {
  client: DaemonClient;
  defaultMode: 'vector' | 'hybrid' | 'topology';
  results: Array<{ file_path: string; content: string; score: number }> = [];

  constructor(leaf: WorkspaceLeaf, client: DaemonClient, defaultMode: 'vector' | 'hybrid' | 'topology') {
    super(leaf);
    this.client = client;
    this.defaultMode = defaultMode;
    this.navigation = false;
  }

  getViewType() { return VIEW_TYPE_SEARCH; }
  get displayText() { return 'VaultPortal Search'; }

  async onOpen() {
    this.containerEl.empty();
    const header = this.containerEl.createDiv('vp-search-header');
    header.createEl('h2', { text: 'Search vault' });

    const inputWrapper = this.containerEl.createDiv('vp-input-wrapper');
    const input = inputWrapper.createEl('input', { type: 'text', placeholder: 'Search vault...', attr: { 'aria-label': 'Search vault' } });
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.performSearch(input.value); });
    this.renderResults();
  }

  async performSearch(query: string) {
    if (!query.trim()) return;
    try {
      const strategies = this.getStrategiesForMode(this.defaultMode);
      this.results = await this.client.search(query, strategies);
      this.renderResults();
    } catch (e) { this.showError(String(e)); }
  }

  renderResults() {
    const resultsEl = this.containerEl.querySelector('.vp-results'); if (resultsEl) resultsEl.remove();
    const results = this.containerEl.createDiv('vp-results');
    if (this.results.length === 0) { results.createDiv('vp-no-results', { text: 'No results found' }); return; }
    this.results.forEach((r) => {
      const item = results.createDiv('vp-result-item');
      item.createEl('div', { text: r.file_path, cls: 'vp-result-path' });
      item.createEl('div', { text: r.content.slice(0, 200), cls: 'vp-result-content' });
    });
  }

  showError(msg: string) { const e = this.containerEl.querySelector('.vp-error'); if (e) e.remove(); this.containerEl.createDiv('vp-error', { text: msg }); }

  private getStrategiesForMode(mode: 'vector' | 'hybrid' | 'topology'): string[] {
    if (mode === 'hybrid') return ['vector', 'graph'];
    if (mode === 'topology') return ['graph'];
    return ['vector'];
  }
}
