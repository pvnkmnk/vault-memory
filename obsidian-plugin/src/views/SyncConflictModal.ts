import { App, Modal } from 'obsidian';

interface SyncConflictModalOptions {
  filePath: string;
  reason: string;
  onRetryNow: () => void;
  onDropLocal: () => void;
}

export class SyncConflictModal extends Modal {
  private options: SyncConflictModalOptions;

  constructor(app: App, options: SyncConflictModalOptions) {
    super(app);
    this.options = options;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl('h3', { text: 'Sync Conflict Detected' });
    contentEl.createEl('p', {
      text: `File: ${this.options.filePath}`,
    });
    contentEl.createEl('p', {
      text: `Reason: ${this.options.reason}`,
    });

    const actions = contentEl.createDiv({ cls: 'vp-ingest-actions' });
    const retryBtn = actions.createEl('button', {
      text: 'Retry Now',
      cls: 'mod-cta',
    });
    retryBtn.addEventListener('click', () => {
      this.options.onRetryNow();
      this.close();
    });

    const dropBtn = actions.createEl('button', {
      text: 'Drop Local Change',
      cls: 'mod-warning',
    });
    dropBtn.addEventListener('click', () => {
      this.options.onDropLocal();
      this.close();
    });
  }
}
