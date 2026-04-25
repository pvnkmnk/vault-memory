import { App, debounce, Notice, Plugin, TFile } from 'obsidian';
import { DaemonClient } from './DaemonClient';

export interface SyncSettings {
  enabled: boolean;
  debounceMs: number;
  excludePatterns: string[];
}

export class AutoSyncEngine {
  private app: App;
  private plugin: Plugin;
  private client: DaemonClient;
  private settings: SyncSettings;
  private pendingFiles: Set<string> = new Set();
  private syncInProgress: boolean = false;
  private lastSyncTime: number = 0;
  private statusCallback?: (status: SyncStatus) => void;

  constructor(app: App, plugin: Plugin, client: DaemonClient, settings: SyncSettings) {
    this.app = app;
    this.plugin = plugin;
    this.client = client;
    this.settings = settings;
  }

  setStatusCallback(callback: (status: SyncStatus) => void) {
    this.statusCallback = callback;
  }

  updateSettings(settings: SyncSettings) {
    this.settings = settings;
  }

  start() {
    if (!this.settings.enabled) return;

    // Handle file modifications
    const debouncedSync = debounce(async (file: TFile) => {
      if (!this.shouldSync(file)) return;
      await this.queueSync(file.path);
    }, this.settings.debounceMs, true);

    this.plugin.registerEvent(this.app.vault.on('modify', debouncedSync));

    // Handle new files
    const debouncedCreate = debounce(async (file: TFile) => {
      if (!this.shouldSync(file)) return;
      await this.queueSync(file.path);
    }, this.settings.debounceMs, true);

    this.plugin.registerEvent(this.app.vault.on('create', debouncedCreate));

    // Handle deletions
    this.plugin.registerEvent(this.app.vault.on('delete', (file: TFile) => {
      if (this.shouldSync(file)) {
        this.notifySync('deleted', file.path);
      }
    }));

    // Handle renames
    this.plugin.registerEvent(this.app.vault.on('rename', (file: TFile, oldPath: string) => {
      if (this.shouldSync(file)) {
        this.notifySync('renamed', file.path, oldPath);
      }
    }));
  }

  private shouldSync(file: TFile): boolean {
    if (!(file instanceof TFile)) return false;
    if (file.extension !== 'md') return false;
    if (file.path.startsWith('_working/')) return false;
    if (file.path.startsWith('.obsidian/')) return false;

    for (const pattern of this.settings.excludePatterns) {
      if (file.path.includes(pattern)) return false;
    }

    return true;
  }

  private async queueSync(filePath: string) {
    this.pendingFiles.add(filePath);
    this.updateStatus('pending', this.pendingFiles.size);

    // Process pending files
    await this.processPending();
  }

  private async processPending() {
    if (this.syncInProgress || this.pendingFiles.size === 0) return;

    this.syncInProgress = true;
    this.updateStatus('syncing', this.pendingFiles.size);

    const filesToSync = Array.from(this.pendingFiles);
    this.pendingFiles.clear();

    try {
      const result = await this.client.syncFiles(filesToSync);
      
      if (result.failed > 0) {
        new Notice(`Sync: ${result.synced} synced, ${result.failed} failed`, 3000);
      }
      
      this.lastSyncTime = Date.now();
      this.updateStatus('synced', 0, result.synced);
    } catch (e) {
      new Notice(`Sync error: ${e}`, 3000);
      this.updateStatus('error', 0, 0, String(e));
    } finally {
      this.syncInProgress = false;
      
      // Check if more files queued while syncing
      if (this.pendingFiles.size > 0) {
        await this.processPending();
      }
    }
  }

  private updateStatus(status: string, pending?: number, synced?: number, error?: string) {
    if (this.statusCallback) {
      this.statusCallback({
        status,
        pendingFiles: pending ?? this.pendingFiles.size,
        lastSyncTime: this.lastSyncTime,
        error
      });
    }
  }

  private notifySync(action: string, path: string, oldPath?: string) {
    const msg = action === 'renamed' 
      ? `${action}: ${oldPath} → ${path}`
      : `${action}: ${path}`;
    new Notice(msg, 2000);
  }

  getStatus(): SyncStatus {
    return {
      status: this.syncInProgress ? 'syncing' : (this.pendingFiles.size > 0 ? 'pending' : 'idle'),
      pendingFiles: this.pendingFiles.size,
      lastSyncTime: this.lastSyncTime
    };
  }

  forceSyncNow() {
    if (this.pendingFiles.size > 0) {
      this.processPending();
    }
  }

  stop() {
    this.pendingFiles.clear();
    this.updateStatus('disabled');
  }
}

export interface SyncStatus {
  status: 'idle' | 'pending' | 'syncing' | 'synced' | 'error' | 'disabled';
  pendingFiles: number;
  lastSyncTime: number;
  error?: string;
}