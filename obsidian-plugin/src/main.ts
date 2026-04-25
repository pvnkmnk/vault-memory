import { Plugin, Notice } from 'obsidian';
import { SearchPanel } from './views/SearchPanel';
import { GraphCanvas } from './views/GraphCanvas';
import { DailyNotesView } from './views/DailyNotesView';
import { IngestModal } from './views/IngestModal';
import { StatusBar } from './views/StatusBar';
import { DaemonClient } from './components/DaemonClient';
import { AutoSyncEngine, SyncSettings, SyncStatus } from './components/AutoSyncEngine';
import { VaultPortalSettingsTab, VaultPortalSettings, DEFAULT_SETTINGS } from './SettingsTab';

const VIEW_TYPE_SEARCH = 'vault-portal-search';
const VIEW_TYPE_GRAPH = 'vault-portal-graph';
const VIEW_TYPE_DAILY = 'vault-portal-daily';

export default class VaultPortal extends Plugin {
  daemonClient!: DaemonClient;
  statusBar!: StatusBar;
  settings!: VaultPortalSettings;
  settingsTab!: VaultPortalSettingsTab;
  autoSyncEngine?: AutoSyncEngine;
  syncStatusEl?: HTMLElement;

  async onload() {
    await this.loadSettings();
  }

  getSyncSettings(): SyncSettings {
    return {
      enabled: this.settings.syncEnabled,
      debounceMs: this.settings.syncDebounceMs,
      excludePatterns: this.settings.syncExcludePatterns.split(',').map(p => p.trim()).filter(Boolean)
    };
  }

  updateSyncEngine() {
    if (this.autoSyncEngine) {
      this.autoSyncEngine.stop();
    }
    
    const syncSettings = this.getSyncSettings();
    this.autoSyncEngine = new AutoSyncEngine(this.app, this, this.daemonClient, syncSettings);
    this.autoSyncEngine.setStatusCallback((status) => this.onSyncStatusChange(status));
    this.autoSyncEngine.start();
  }

  private onSyncStatusChange(status: SyncStatus) {
    if (!this.syncStatusEl) return;
    
    this.syncStatusEl.empty();
    const dot = this.syncStatusEl.createSpan({ cls: `vp-sync-dot vp-sync-${status.status}` });
    const text = this.syncStatusEl.createSpan({ cls: 'vp-sync-text' });
    
    switch (status.status) {
      case 'idle': text.setText('Synced'); break;
      case 'pending': text.setText(`Pending (${status.pendingFiles})`); break;
      case 'syncing': text.setText('Syncing...'); break;
      case 'synced': text.setText(`Synced ${status.pendingFiles} files`); break;
      case 'error': text.setText('Sync error'); break;
      case 'disabled': text.setText('Sync disabled'); break;
    }
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    this.daemonClient = new DaemonClient(this.app);
    this.daemonClient.setDaemonUrl(this.settings.daemonUrl);
    
    this.statusBar = new StatusBar(this.app, this.daemonClient);
    this.settingsTab = new VaultPortalSettingsTab(this.app, this, this.daemonClient, this.settings);
    this.addSettingTab(this.settingsTab);

    const statusEl = this.addStatusBarItem();
    this.statusBar.render(statusEl);

    // Add sync status indicator
    this.syncStatusEl = statusEl.createEl('div', { cls: 'vp-sync-indicator' });
    this.syncStatusEl.createSpan({ cls: 'vp-sync-dot vp-sync-idle' });
    this.syncStatusEl.createSpan({ cls: 'vp-sync-text', text: 'Idle' });

    // Register views
    this.registerView(VIEW_TYPE_SEARCH, (leaf) => new SearchPanel(this.app, this.daemonClient));
    this.registerView(VIEW_TYPE_GRAPH, (leaf) => new GraphCanvas(this.app, this.daemonClient));
    this.registerView(VIEW_TYPE_DAILY, (leaf) => new DailyNotesView(this.app, this.daemonClient));

    // Commands
    this.addCommand({
      id: 'search',
      name: 'Search vault',
      callback: () => this.openSearch(),
    });

    this.addCommand({
      id: 'graph',
      name: 'View knowledge graph',
      callback: () => this.openGraph(),
    });

    this.addCommand({
      id: 'daily-notes',
      name: 'Open daily notes view',
      callback: () => this.openDailyNotes(),
    });

    this.addCommand({
      id: 'cognify',
      name: 'Extract triples',
      editorCallback: async (editor, file) => {
        if (!file) { new Notice('No active file'); return; }
        const content = editor.getValue();
        try {
          const result = await this.daemonClient.cognify(content);
          new Notice(`Extracted ${result.triples?.length || 0} triples`);
        } catch (e) { new Notice(`Error: ${e}`); }
      },
    });

    this.addCommand({
      id: 'promote',
      name: 'Promote to wiki',
      editorCallback: async (editor, file) => {
        if (!file) { new Notice('No active file'); return; }
        try {
          await this.daemonClient.promote(file.path);
          new Notice('Promoted to wiki');
        } catch (e) { new Notice(`Error: ${e}`); }
      },
    });

    // Ingest commands
    this.addCommand({
      id: 'ingest',
      name: 'Quick ingest to _working/',
      callback: () => {
        new IngestModal(this.app, this.daemonClient, 'write_working').open();
      },
    });

    this.addCommand({
      id: 'ingest-promote',
      name: 'Promote content to wiki',
      callback: () => {
        new IngestModal(this.app, this.daemonClient, 'promote').open();
      },
    });

    this.addCommand({
      id: 'list-blocks',
      name: 'List attached memory blocks',
      callback: async () => {
        try {
          const result = await this.daemonClient.listBlocks();
          const blockNames = result.attached_blocks.map(b => b.name).join(', ');
          new Notice(`Blocks: ${blockNames || 'none'} (${result.total_tokens} tokens)`, 4000);
        } catch (e) { new Notice(`Error: ${e}`); }
      },
    });

    // Sync commands
    this.addCommand({
      id: 'sync-now',
      name: 'Sync now',
      callback: () => {
        if (this.autoSyncEngine) {
          this.autoSyncEngine.forceSyncNow();
          new Notice('Syncing...', 2000);
        }
      },
    });

    this.addCommand({
      id: 'sync-status',
      name: 'Sync status',
      callback: () => {
        if (this.autoSyncEngine) {
          const status = this.autoSyncEngine.getStatus();
          new Notice(`Sync: ${status.status}, ${status.pendingFiles} pending`, 3000);
        }
      },
    });

    // Initialize auto-sync engine
    this.updateSyncEngine();
    this.daemonClient.checkHealth();
  }

  onunload() {
    this.autoSyncEngine?.stop();
  }

  openSearch() {
    this.app.workspace.getLeaf('sidebar').setViewState({ type: VIEW_TYPE_SEARCH });
  }

  openGraph() {
    this.app.workspace.getLeaf('modal').setViewState({ type: VIEW_TYPE_GRAPH });
  }

  openDailyNotes() {
    this.app.workspace.getLeaf('sidebar').setViewState({ type: VIEW_TYPE_DAILY });
  }
}