import { App, Plugin, PluginSettingTab, Setting } from 'obsidian';
import { DaemonClient } from './components/DaemonClient';

export interface VaultPortalSettings {
  daemonUrl: string;
  syncEnabled: boolean;
  syncDebounceMs: number;
  syncExcludePatterns: string;
}

export const DEFAULT_SETTINGS: VaultPortalSettings = {
  daemonUrl: 'http://localhost:5051',
  syncEnabled: true,
  syncDebounceMs: 2000,
  syncExcludePatterns: '.git,_working,.obsidian',
};

export class VaultPortalSettingsTab extends PluginSettingTab {
  client: DaemonClient;
  settings: VaultPortalSettings;

  constructor(app: App, plugin: Plugin, client: DaemonClient, settings: VaultPortalSettings) {
    super(app, plugin);
    this.client = client;
    this.settings = settings;
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
          .onChange(async (value) => {
            this.settings.daemonUrl = value || DEFAULT_SETTINGS.daemonUrl;
            try {
              await this.plugin.saveData(this.settings);
              this.client.setDaemonUrl(this.settings.daemonUrl);
            } catch (e) {
              console.error('Failed to save settings:', e);
            }
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
          .onChange(async (value) => {
            this.settings.syncEnabled = value;
            await this.plugin.saveData(this.settings);
            (this.plugin as any).updateSyncEngine?.();
          })
      );

    new Setting(this.containerEl)
      .setName('Sync debounce (ms)')
      .setDesc('Wait time before syncing after changes')
      .addText((text) =>
        text
          .setPlaceholder('2000')
          .setValue(String(this.settings.syncDebounceMs))
          .onChange(async (value) => {
            const ms = parseInt(value, 10);
            if (!isNaN(ms) && ms >= 500) {
              this.settings.syncDebounceMs = ms;
              await this.plugin.saveData(this.settings);
              (this.plugin as any).updateSyncEngine?.();
            }
          })
      );

    new Setting(this.containerEl)
      .setName('Exclude patterns')
      .setDesc('Comma-separated paths to exclude from sync')
      .addText((text) =>
        text
          .setPlaceholder('.git,_working,.obsidian')
          .setValue(this.settings.syncExcludePatterns)
          .onChange(async (value) => {
            this.settings.syncExcludePatterns = value || DEFAULT_SETTINGS.syncExcludePatterns;
            await this.plugin.saveData(this.settings);
            (this.plugin as any).updateSyncEngine?.();
          })
      );

    this.containerEl.createEl('h3', { text: 'About', cls: 'vp-settings-section' });
    new Setting(this.containerEl)
      .setName('VaultPortal')
      .setDesc('v0.7.0 — Semantic memory for Obsidian');
  }
}