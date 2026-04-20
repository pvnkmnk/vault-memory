import { App, View } from 'obsidian';
import { DaemonClient } from '../components/DaemonClient';

export class SearchPanel extends View {
  client: DaemonClient;
  results: Array<{ file_path: string; content: string; score: number }> = [];

  constructor(app: App, client: DaemonClient) {
    super(app, client);
    this.client = client;
  }

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
    try { this.results = await this.client.search(query); this.renderResults(); } catch (e) { this.showError(String(e)); }
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
}