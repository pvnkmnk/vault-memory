import { App, Plugin, PluginSettingTab, Setting } from 'obsidian';
import { DaemonClient } from './components/DaemonClient';

import type { SearchMode } from './components/DaemonClient';

export interface VaultPortalSettings {
  daemonUrl: string;
  apiKey: string;
  defaultSearchMode: SearchMode;
  syncEnabled: boolean;
  syncDebounceMs: number;
  syncExcludePatterns: string;
}

export const DEFAULT_SETTINGS: VaultPortalSettings = {
  daemonUrl: 'http://localhost:5051',
  apiKey: '',
  defaultSearchMode: 'vector',
  syncEnabled: true,
  syncDebounceMs: 2000,
  syncExcludePatterns: '.git,_working,.obsidian',
};

export class VaultPortalSettingsTab extends PluginSettingTab {
  client: DaemonClient;
  settings: VaultPortalSettings;
  private debounceTimers: Map<string, number> = new Map();

  constructor(app: App, plugin: Plugin, client: DaemonClient, settings: VaultPortalSettings) {
    super(app, plugin);
    this.client = client;
    this.settings = settings;
  }

  private debounce(key: string, fn: () => void, delayMs = 500): void {
    const existing = this.debounceTimers.get(key);
    if (existing) clearTimeout(existing);
    const timer = window.setTimeout(() => {
      fn();
      this.debounceTimers.delete(key);
    }, delayMs);
    this.debounceTimers.set(key, timer);
  }

  hide(): void {
    // Clear any pending debounced saves when tab is closed
    this.debounceTimers.forEach(timer => clearTimeout(timer));
    this.debounceTimers.clear();
    super.hide();
  }

  display(): void {
    this.containerEl.empty();
    this.containerEl.createEl('h2', { text: 'VaultPortal settings' });

    new Setting(this.containerEl)
      .setName('Daemon URL')
      .setDesc('URL of the vault-memory daemon (default: http://localhost:5051)')
      .addText((text) =>
        text
          .setPlaceholder('http://localhost:5051')
          .setValue(this.settings.daemonUrl)
          .onChange((value) => {
            // Debounce save and connection check
            this.debounce('daemonUrl', async () => {
              this.settings.daemonUrl = value || DEFAULT_SETTINGS.daemonUrl;
              try {
                await this.plugin.saveData(this.settings);
                this.client.setDaemonUrl(this.settings.daemonUrl);
                this.client.checkHealth();
              } catch (e) {
                console.error('Failed to save settings:', e);
              }
            });
          })
      );

    new Setting(this.containerEl)
      .setName('API Key')
      .setDesc('Optional API key for daemon authentication')
      .addText((text) =>
        text
          .setPlaceholder('Enter API key')
          .setValue(this.settings.apiKey)
          .onChange((value) => {
            this.debounce('apiKey', async () => {
              this.settings.apiKey = value;
              try {
                await this.plugin.saveData(this.settings);
                this.client.setApiKey(value);
              } catch (e) {
                console.error('Failed to save API key:', e);
              }
            });
          })
      );

    new Setting(this.containerEl)
      .setName('Default search mode')
      .setDesc('Default search strategy for the search panel')
      .addDropdown((dropdown) =>
        dropdown
          .addOption('vector', 'Vector (Semantic)')
          .addOption('keyword', 'Keyword (Text)')
          .addOption('graph', 'Graph')
          .addOption('temporal', 'Timeline')
          .setValue(this.settings.defaultSearchMode)
          .onChange((value) => {
            this.debounce('searchMode', async () => {
              this.settings.defaultSearchMode = value as SearchMode;
              try {
                await this.plugin.saveData(this.settings);
              } catch (e) {
                console.error('Failed to save search mode:', e);
              }
            });
          })
      );

    new Setting(this.containerEl)
      .setName('Connection status')
      .setDesc('Test the connection to the daemon')
      .addButton((btn) => {
        btn.setText('Test connection').onClick(async () => {
          const connected = await this.client.checkHealth();
          btn.buttonEl.setText(connected ? 'Connected' : 'Offline');
          btn.buttonEl.classList.toggle('mod-success', connected);
          btn.buttonEl.classList.toggle('mod-warning', !connected);
        });
      });

    this.containerEl.createEl('h3', { text: 'Auto-Sync', cls: 'vp-settings-section' });

    new Setting(this.containerEl)
      .setName('Enable auto-sync')
      .setDesc('Automatically sync files when they change')
      .addToggle((toggle) =>
        toggle
          .setValue(this.settings.syncEnabled)
          .onChange((value) => {
            this.debounce('syncEnabled', async () => {
              this.settings.syncEnabled = value;
              await this.plugin.saveData(this.settings);
              (this.plugin as any).updateSyncEngine?.();
            });
          })
      );

    new Setting(this.containerEl)
      .setName('Sync debounce (ms)')
      .setDesc('Wait time before syncing after changes')
      .addText((text) =>
        text
          .setPlaceholder('2000')
          .setValue(String(this.settings.syncDebounceMs))
          .onChange((value) => {
            this.debounce('syncDebounce', async () => {
              const ms = parseInt(value, 10);
              if (!isNaN(ms) && ms >= 500) {
                this.settings.syncDebounceMs = ms;
                await this.plugin.saveData(this.settings);
                (this.plugin as any).updateSyncEngine?.();
              }
            });
          })
      );

    new Setting(this.containerEl)
      .setName('Exclude patterns')
      .setDesc('Comma-separated paths to exclude from sync')
      .addText((text) =>
        text
          .setPlaceholder('.git,_working,.obsidian')
          .setValue(this.settings.syncExcludePatterns)
          .onChange((value) => {
            this.debounce('excludePatterns', async () => {
              this.settings.syncExcludePatterns = value || DEFAULT_SETTINGS.syncExcludePatterns;
              await this.plugin.saveData(this.settings);
              (this.plugin as any).updateSyncEngine?.();
            });
          })
      );

    this.containerEl.createEl('h3', { text: 'About', cls: 'vp-settings-section' });
    new Setting(this.containerEl)
      .setName('VaultPortal')
      .setDesc('v0.7.0 — Semantic memory for Obsidian');
  }
}