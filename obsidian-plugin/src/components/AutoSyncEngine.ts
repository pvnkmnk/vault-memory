import { App, debounce, Notice, Plugin, TAbstractFile, TFile } from 'obsidian';
import { DaemonClient } from './DaemonClient';
import { SyncConflictModal } from '../views/SyncConflictModal';

export interface SyncSettings {
  enabled: boolean;
  debounceMs: number;
  excludePatterns: string[];
}

export class AutoSyncEngine {
  private static readonly QUEUE_STORAGE_KEY = 'vault-portal.sync.pending-files.v1';
  private static readonly MAX_RETRIES = 5;
  private static readonly RETRY_BASE_MS = 5000;
  private app: App;
  private plugin: Plugin;
  private client: DaemonClient;
  private settings: SyncSettings;
  private pendingFiles: Set<string> = new Set();
  private pendingLwwTimestamps: Map<string, number> = new Map();
  private retryCounts: Map<string, number> = new Map();
  private syncInProgress: boolean = false;
  private lastSyncTime: number = 0;
  private retryTimer: number | null = null;
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
    this.loadPendingQueue();

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

    this.scheduleRetry(1000);
    window.addEventListener('online', this.onOnline);
    this.client.connectSyncEvents((event) => {
      if (event.event === 'sync.batch.completed') {
        this.lastSyncTime = Date.now();
        this.updateStatus('synced', this.pendingFiles.size, Number(event.synced || 0));
      }
    });
  }

  private onOnline = () => {
    if (this.pendingFiles.size > 0) {
      this.scheduleRetry(500);
    }
  };

  private loadPendingQueue() {
    try {
      const raw = localStorage.getItem(AutoSyncEngine.QUEUE_STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as {
        paths?: string[];
        retries?: Record<string, number>;
        lwwTimestamps?: Record<string, number>;
      };
      for (const path of parsed.paths || []) {
        this.pendingFiles.add(path);
      }
      for (const [path, retries] of Object.entries(parsed.retries || {})) {
        this.retryCounts.set(path, retries);
      }
      for (const [path, ts] of Object.entries(parsed.lwwTimestamps || {})) {
        this.pendingLwwTimestamps.set(path, ts);
      }
      if (this.pendingFiles.size > 0) {
        this.updateStatus('pending', this.pendingFiles.size);
      }
    } catch {
      // Ignore local storage corruption and continue with empty queue.
    }
  }

  private persistPendingQueue() {
    const retries: Record<string, number> = {};
    const lwwTimestamps: Record<string, number> = {};
    for (const [path, count] of this.retryCounts.entries()) {
      retries[path] = count;
    }
    for (const [path, ts] of this.pendingLwwTimestamps.entries()) {
      lwwTimestamps[path] = ts;
    }
    localStorage.setItem(
      AutoSyncEngine.QUEUE_STORAGE_KEY,
      JSON.stringify({
        paths: Array.from(this.pendingFiles),
        retries,
        lwwTimestamps,
      }),
    );
  }

  private scheduleRetry(delayMs: number) {
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
    }
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null;
      void this.processPending();
    }, delayMs);
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
    // LWW CRDT-style merge for local edits: latest event wins per file key.
    this.pendingLwwTimestamps.set(filePath, Date.now());
    this.pendingFiles.add(filePath);
    this.retryCounts.set(filePath, 0);
    this.persistPendingQueue();
    this.updateStatus('pending', this.pendingFiles.size);

    // Process pending files
    await this.processPending();
  }

  private async processPending() {
    if (this.syncInProgress || this.pendingFiles.size === 0) return;

    this.syncInProgress = true;
    this.updateStatus('syncing', this.pendingFiles.size);

    const filesToSync = Array.from(this.pendingFiles).sort((a, b) => {
      return (this.pendingLwwTimestamps.get(a) || 0) - (this.pendingLwwTimestamps.get(b) || 0);
    });
    this.pendingFiles.clear();
    this.persistPendingQueue();

    try {
      const result = await this.client.syncFiles(filesToSync);

      if (result.failed > 0) {
        new Notice(`Sync: ${filesToSync.length} files synced, ${result.failed} failed`, 3000);
        this.requeueFailed(filesToSync, result.errors || []);
      } else {
        new Notice(`Synced ${filesToSync.length} files`, 2000);
        for (const path of filesToSync) {
          this.retryCounts.delete(path);
          this.pendingLwwTimestamps.delete(path);
        }
      }
      
      this.lastSyncTime = Date.now();
      this.persistPendingQueue();
      this.updateStatus('synced', 0, result.synced);
    } catch (e) {
      for (const path of filesToSync) {
        this.pendingFiles.add(path);
      }
      this.bumpRetries(filesToSync, String(e));
      this.persistPendingQueue();
      const retryDelay = this.nextRetryDelay(filesToSync);
      new Notice(`Sync error: ${e}. Retrying in ${Math.ceil(retryDelay / 1000)}s`, 3500);
      this.updateStatus('error', this.pendingFiles.size, 0, String(e));
      this.scheduleRetry(retryDelay);
    } finally {
      this.syncInProgress = false;
      
      // Check if more files queued while syncing
      if (this.pendingFiles.size > 0) {
        const retryDelay = this.nextRetryDelay(Array.from(this.pendingFiles));
        this.scheduleRetry(retryDelay);
      }
    }
  }

  private requeueFailed(filesToSync: string[], errors: string[]) {
    const failedPaths = new Set<string>();
    for (const err of errors) {
      const delim = err.indexOf(':');
      if (delim > 0) {
        failedPaths.add(err.slice(0, delim).trim());
      }
    }

    // If daemon did not provide per-file paths, conservatively retry all.
    if (failedPaths.size === 0 && errors.length > 0) {
      filesToSync.forEach((path) => failedPaths.add(path));
    }

    const failedList = Array.from(failedPaths);
    if (failedList.length === 0) return;
    failedList.forEach((path) => this.pendingFiles.add(path));
    this.bumpRetries(failedList, errors[0] || 'sync failed');
    this.persistPendingQueue();
    this.scheduleRetry(this.nextRetryDelay(failedList));
  }

  private bumpRetries(paths: string[], error: string) {
    for (const path of paths) {
      const next = (this.retryCounts.get(path) || 0) + 1;
      this.retryCounts.set(path, next);
      if (next >= AutoSyncEngine.MAX_RETRIES) {
        this.showConflictModal(path, error);
      }
    }
  }

  private showConflictModal(path: string, error: string) {
    new SyncConflictModal(this.app, {
      filePath: path,
      reason: error,
      onRetryNow: () => {
        this.retryCounts.set(path, Math.max(0, (this.retryCounts.get(path) || 1) - 1));
        this.pendingFiles.add(path);
        this.pendingLwwTimestamps.set(path, Date.now());
        this.persistPendingQueue();
        this.scheduleRetry(500);
      },
      onDropLocal: () => {
        this.pendingFiles.delete(path);
        this.pendingLwwTimestamps.delete(path);
        this.retryCounts.delete(path);
        this.persistPendingQueue();
        this.updateStatus('idle', this.pendingFiles.size);
      },
    }).open();
  }

  private nextRetryDelay(paths: string[]): number {
    const maxRetry = Math.max(
      ...paths.map((p) => this.retryCounts.get(p) || 0),
      0,
    );
    const boundedRetry = Math.min(maxRetry, AutoSyncEngine.MAX_RETRIES);
    return AutoSyncEngine.RETRY_BASE_MS * Math.pow(2, Math.max(0, boundedRetry - 1));
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
    if (this.retryTimer !== null) {
      window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    window.removeEventListener('online', this.onOnline);
    this.client.disconnectSyncEvents();
    this.persistPendingQueue();
    this.pendingFiles.clear();
    this.pendingLwwTimestamps.clear();
    this.updateStatus('disabled');
  }
}

export interface SyncStatus {
  status: 'idle' | 'pending' | 'syncing' | 'synced' | 'error' | 'disabled';
  pendingFiles: number;
  lastSyncTime: number;
  error?: string;
}
