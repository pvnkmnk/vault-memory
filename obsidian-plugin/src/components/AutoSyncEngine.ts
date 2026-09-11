import { App, debounce, Notice, Plugin, TAbstractFile, TFile } from 'obsidian';
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
    const debouncedSync = debounce(async (file: TAbstractFile) => {
      if (!(file instanceof TFile)) return;
      if (!this.shouldSync(file)) return;
      await this.queueSync(file.path);
    }, this.settings.debounceMs, true);

    this.plugin.registerEvent(this.app.vault.on('modify', debouncedSync));

    // Handle new files
    const debouncedCreate = debounce(async (file: TAbstractFile) => {
      if (!(file instanceof TFile)) return;
      if (!this.shouldSync(file)) return;
      await this.queueSync(file.path);
    }, this.settings.debounceMs, true);

    this.plugin.registerEvent(this.app.vault.on('create', debouncedCreate));

    // Handle deletions
    this.plugin.registerEvent(this.app.vault.on('delete', (file: TAbstractFile) => {
      if (file instanceof TFile && this.shouldSync(file)) {
        this.notifySync('deleted', file.path);
      }
    }));

    // Handle renames
    this.plugin.registerEvent(this.app.vault.on('rename', (file: TAbstractFile, oldPath: string) => {
      if (file instanceof TFile && this.shouldSync(file)) {
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

    // Snapshot WITHOUT clearing: files stay queued until sync succeeds so a
    // failed sync never loses them (S24-B7 / VAU-10).
    const filesToSync = Array.from(this.pendingFiles);

    // Retry with exponential backoff (3 attempts: 1s, 2s, 4s). /sync/file is
    // idempotent (content-hash sync state), so re-attempting a partial batch
    // is safe.
    const maxAttempts = 3;
    let result: { synced: number; failed: number } | null = null;
    let lastError: unknown = null;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        result = await this.client.syncFiles(filesToSync);
        if (result.failed === 0) break;
        lastError = new Error(`${result.failed} of ${filesToSync.length} files failed`);
      } catch (e) {
        lastError = e;
        result = null;
      }
      if (attempt < maxAttempts) {
        await new Promise((resolve) => setTimeout(resolve, 1000 * Math.pow(2, attempt - 1)));
      }
    }

    try {
      if (result && result.failed === 0) {
        // Success: remove the synced batch from the queue.
        for (const f of filesToSync) {
          this.pendingFiles.delete(f);
        }
        new Notice(`Synced ${filesToSync.length} files`, 2000);
        this.lastSyncTime = Date.now();
        this.updateStatus('synced', 0, result.synced);
      } else if (result && result.failed > 0) {
        // Partial failure after retries: keep everything queued (re-sync is
        // idempotent) and surface the problem.
        new Notice(`Sync: ${result.synced} synced, ${result.failed} failed — will retry`, 4000);
        this.updateStatus('error', this.pendingFiles.size, 0, String(lastError));
      } else {
        // Total failure after retries: files remain queued for the next trigger.
        new Notice(`Sync error: ${lastError} — ${filesToSync.length} files will retry`, 4000);
        this.updateStatus('error', this.pendingFiles.size, 0, String(lastError));
      }
    } finally {
      this.syncInProgress = false;

      // Check if more files queued while syncing
      if (this.pendingFiles.size > 0) {
        await this.processPending();
      }
    }
  }

  private updateStatus(status: SyncStatus['status'], pending?: number, synced?: number, error?: string) {
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