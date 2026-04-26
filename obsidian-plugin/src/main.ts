import { App, Notice, Plugin, PluginSettingTab, Setting } from 'obsidian';
import { SearchPanel } from './views/SearchPanel';
import { GraphCanvas } from './views/GraphCanvas';
import { StatusBar } from './views/StatusBar';
import { DaemonClient } from './components/DaemonClient';

const VIEW_TYPE_SEARCH = 'vault-portal-search';
const VIEW_TYPE_GRAPH = 'vault-portal-graph';
type SearchMode = 'vector' | 'hybrid' | 'topology';

interface VaultPortalSettings {
  daemonUrl: string;
  apiKey: string;
  defaultSearchMode: SearchMode;
  autoSync: boolean;
}

const DEFAULT_SETTINGS: VaultPortalSettings = {
  daemonUrl: 'http://localhost:5051',
  apiKey: '',
  defaultSearchMode: 'vector',
  autoSync: false,
};

export default class VaultPortal extends Plugin {
  settings!: VaultPortalSettings;
  daemonClient!: DaemonClient;
  statusBar!: StatusBar;

  async onload() {
    await this.loadSettings();
    this.daemonClient = new DaemonClient({
      daemonUrl: this.settings.daemonUrl,
      apiKey: this.settings.apiKey,
    });
    this.statusBar = new StatusBar(this.daemonClient);
    this.addSettingTab(new VaultPortalSettingTab(this.app, this));

    const statusEl = this.addStatusBarItem();
    void this.statusBar.render(statusEl);
    this.registerInterval(window.setInterval(() => { void this.statusBar.update(); }, 30000));
    this.registerView(
      VIEW_TYPE_SEARCH,
      (leaf) => new SearchPanel(leaf, this.daemonClient, this.settings.defaultSearchMode),
    );
    this.registerView(VIEW_TYPE_GRAPH, (leaf) => new GraphCanvas(leaf, this.daemonClient));

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
          const vaultPath = this.getVaultPath();
          if (!vaultPath) { new Notice('Could not determine vault path'); return; }
          await this.daemonClient.promote({
            text: editor.getValue(),
            title: file.basename,
            pageType: 'analysis',
            references: [],
            vaultPath,
          });
          new Notice('Promoted to wiki');
        } catch (e) { new Notice(`Error: ${e}`); }
      },
    });

    void this.daemonClient.checkHealth();
  }

  onunload() {}

  async openSearch() {
    const leaf = this.app.workspace.getLeaf('sidebar');
    await leaf.setViewState({ type: VIEW_TYPE_SEARCH });
  }

  async openGraph() {
    const leaf = this.app.workspace.getLeaf('modal');
    await leaf.setViewState({ type: VIEW_TYPE_GRAPH });
  }

  private getVaultPath(): string | null {
    const adapter = this.app.vault.adapter as { getBasePath?: () => string; basePath?: string };
    if (typeof adapter.getBasePath === 'function') return adapter.getBasePath();
    return adapter.basePath || null;
  }

  async loadSettings() {
    const persisted = await this.loadData() as Partial<VaultPortalSettings> | null;
    this.settings = Object.assign({}, DEFAULT_SETTINGS, persisted || {});
    this.settings.daemonUrl = this.normalizeDaemonUrl(this.settings.daemonUrl);
    this.settings.apiKey = this.settings.apiKey.trim();
    if (!['vector', 'hybrid', 'topology'].includes(this.settings.defaultSearchMode)) {
      this.settings.defaultSearchMode = DEFAULT_SETTINGS.defaultSearchMode;
    }
  }

  async saveSettings() {
    this.settings.daemonUrl = this.normalizeDaemonUrl(this.settings.daemonUrl);
    this.settings.apiKey = this.settings.apiKey.trim();
    await this.saveData(this.settings);
  }

  async onSettingsUpdated() {
    this.daemonClient.updateConfig({
      daemonUrl: this.settings.daemonUrl,
      apiKey: this.settings.apiKey,
    });
    await this.daemonClient.checkHealth();
    await this.statusBar.update();
  }

  private normalizeDaemonUrl(value: string): string {
    const trimmed = value.trim();
    const fallback = DEFAULT_SETTINGS.daemonUrl;
    const normalized = trimmed.length > 0 ? trimmed : fallback;
    return normalized.endsWith('/') ? normalized.slice(0, -1) : normalized;
  }
}

class VaultPortalSettingTab extends PluginSettingTab {
  plugin: VaultPortal;

  constructor(app: App, plugin: VaultPortal) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl('h2', { text: 'VaultPortal Settings' });

    new Setting(containerEl)
      .setName('Daemon URL')
      .setDesc('Base URL for vault-memory daemon.')
      .addText((text) =>
        text
          .setPlaceholder('http://localhost:5051')
          .setValue(this.plugin.settings.daemonUrl)
          .onChange(async (value) => {
            this.plugin.settings.daemonUrl = value;
            await this.plugin.saveSettings();
            await this.plugin.onSettingsUpdated();
          }),
      );

    new Setting(containerEl)
      .setName('API Key')
      .setDesc('Optional key sent as x-api-key header.')
      .addText((text) =>
        text
          .setPlaceholder('Optional')
          .setValue(this.plugin.settings.apiKey)
          .onChange(async (value) => {
            this.plugin.settings.apiKey = value;
            await this.plugin.saveSettings();
            await this.plugin.onSettingsUpdated();
          }),
      );

    new Setting(containerEl)
      .setName('Default Search Mode')
      .setDesc('Mode used by the search command by default.')
      .addDropdown((dropdown) =>
        dropdown
          .addOption('vector', 'Vector')
          .addOption('hybrid', 'Hybrid')
          .addOption('topology', 'Topology')
          .setValue(this.plugin.settings.defaultSearchMode)
          .onChange(async (value: SearchMode) => {
            this.plugin.settings.defaultSearchMode = value;
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName('Enable Auto Sync')
      .setDesc('Reserved toggle for upcoming auto-sync engine.')
      .addToggle((toggle) =>
        toggle
          .setValue(this.plugin.settings.autoSync)
          .onChange(async (value) => {
            this.plugin.settings.autoSync = value;
            await this.plugin.saveSettings();
          }),
      );
  }
}
