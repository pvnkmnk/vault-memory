import { Notice, TFile } from 'obsidian';
import type { WorkspaceLeaf } from 'obsidian';
import { App, View } from 'obsidian';
import moment from 'moment';
import { DaemonClient } from '../components/DaemonClient';

const VIEW_TYPE_DAILY = 'vault-portal-daily';

interface ContextEntry {
  title: string;
  path: string;
  snippet: string;
  date: string;
}

interface DailyNoteData {
  date: string;
  title: string;
  content: string;
  contextEntries: ContextEntry[];
}

export class DailyNotesView extends View {
  client: DaemonClient;
  currentDate: moment.Moment;
  mood: string = '';
  productivity: number = 3;
  notes: string = '';

  constructor(app: App, leaf: WorkspaceLeaf, client: DaemonClient) {
    super(leaf);
    this.client = client;
    this.currentDate = moment();
  }

  getViewType(): string { return VIEW_TYPE_DAILY; }
  getDisplayText(): string { return 'Daily Notes'; }

  async onOpen() {
    this.containerEl.empty();
    this.renderHeader();
    this.renderControls();
    await this.renderDailyNote();
  }

  private renderHeader() {
    const header = this.containerEl.createDiv('vp-daily-header');
    const h2 = header.createEl('h2');
    h2.setText('Daily Note');
    const badge = header.createEl('span', { cls: 'vp-header-badge' });
    badge.setText('VaultPortal');
  }

  private renderControls() {
    const controls = this.containerEl.createDiv('vp-daily-controls');
    
    // Date navigation
    const navWrapper = controls.createDiv('vp-daily-nav');
    const prevBtn = navWrapper.createEl('button', { text: '←', attr: { 'aria-label': 'Previous day' } });
    prevBtn.addEventListener('click', () => this.navigateDate(-1));
    
    const dateDisplay = navWrapper.createEl('span', { cls: 'vp-daily-date' });
    dateDisplay.setText(this.currentDate.format('dddd, MMMM D, YYYY'));
    
    const nextBtn = navWrapper.createEl('button', { text: '→', attr: { 'aria-label': 'Next day' } });
    nextBtn.addEventListener('click', () => this.navigateDate(1));
    
    const todayBtn = navWrapper.createEl('button', { text: 'Today', cls: 'vp-daily-today-btn' });
    todayBtn.addEventListener('click', () => this.goToToday());

    // Context button
    const contextBtn = controls.createEl('button', {
      text: '🔄 Fetch Context',
      cls: 'vp-daily-context-btn',
      attr: { 'aria-label': 'Fetch semantic context for today' }
    });
    contextBtn.addEventListener('click', () => this.fetchContext());
  }

  private navigateDate(days: number) {
    this.currentDate.add(days, 'days');
    this.onOpen();
  }

  private goToToday() {
    this.currentDate = moment();
    this.onOpen();
  }

  private async fetchContext() {
    try {
      const result = await this.client.triggerLookup(this.currentDate.format('YYYY-MM-DD'));
      
      if (result && result.blocks && result.blocks.length > 0) {
        const entries = result.blocks.map((block: any) => ({
          title: block.name || 'Untitled',
          path: block.path || '',
          snippet: block.content?.slice(0, 200) || '',
          date: this.currentDate.format('YYYY-MM-DD')
        }));
        
        this.renderContextPanel(entries);
        new Notice(`Loaded ${entries.length} context entries`, 2000);
      } else {
        new Notice('No context found for this date', 2000);
      }
    } catch (e) {
      new Notice(`Failed to fetch context: ${e}`, 3000);
    }
  }

  private renderContextPanel(entries: ContextEntry[]) {
    const existing = this.containerEl.querySelector('.vp-daily-context');
    if (existing) existing.remove();

    const contextPanel = this.containerEl.createDiv('vp-daily-context');
    const h3 = contextPanel.createEl('h3');
    h3.setText('📚 Related Context');

    if (entries.length === 0) {
      const emptyEl = contextPanel.createDiv('vp-daily-no-context');
      emptyEl.setText('No context entries found');
      return;
    }

    const list = contextPanel.createEl('ul', { cls: 'vp-daily-context-list' });
    entries.forEach((entry) => {
      const item = list.createEl('li', { cls: 'vp-daily-context-item' });
      const strong = item.createEl('strong');
      strong.setText(entry.title);
      const p = item.createEl('p');
      p.setText(entry.snippet);
      const pathEl = item.createEl('span', { cls: 'vp-context-path' });
      pathEl.setText(entry.path);
      
      item.addEventListener('click', () => this.openContextFile(entry.path));
    });
  }

  private async openContextFile(path: string) {
    try {
      const normalizedPath = path.replace(/\\/g, '/');
      const file = this.app.metadataCache.getFirstLinkpathDest(normalizedPath, '');
      
      if (file) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
      } else {
        new Notice(`File not found: ${path}`, 3000);
      }
    } catch (e) {
      new Notice(`Could not open: ${path}`, 3000);
    }
  }

  private async renderDailyNote() {
    const container = this.containerEl.createDiv('vp-daily-content');
    
    // Check for existing daily note
    const dailyNotesPath = this.findDailyNotePath();
    const existingFile = dailyNotesPath ? this.app.vault.getAbstractFileByPath(dailyNotesPath) as TFile : null;

    if (existingFile) {
      // Show existing note with context
      await this.renderExistingNote(container, existingFile);
    } else {
      // Show template for new daily note
      await this.renderNewNoteTemplate(container);
    }
  }

  private findDailyNotePath(): string | null {
    const dateStr = this.currentDate.format('YYYY-MM-DD');
    const files = this.app.vault.getFiles();
    
    // Try various common daily note naming patterns
    const patterns = [
      `${dateStr}.md`,
      `daily/${dateStr}.md`,
      `Daily/${dateStr}.md`,
      `notes/daily/${dateStr}.md`,
      `${this.currentDate.format('YYYY')}/${this.currentDate.format('MM')}/${dateStr}.md`
    ];

    for (const pattern of patterns) {
      const file = files.find(f => f.path === pattern);
      if (file) return file.path;
    }

    // Try matching by date in filename
    const dateRegex = new RegExp(`^.*${dateStr.replace(/-/g, '[-.]')}.*\\.md$`, 'i');
    const match = files.find(f => dateRegex.test(f.path));
    return match ? match.path : null;
  }

  private async renderExistingNote(container: HTMLElement, file: TFile) {
    const content = await this.app.vault.read(file);
    
    const noteHeader = container.createDiv('vp-daily-note-header');
    const h3 = noteHeader.createEl('h3');
    h3.setText(file.name);
    const modifiedSpan = noteHeader.createEl('span', { cls: 'vp-daily-modified' });
    modifiedSpan.setText(`Modified: ${moment(file.stat.mtime).format('MMM D, YYYY h:mm A')}`);

    const openBtn = container.createEl('button', {
      text: '📝 Open in Editor',
      cls: 'vp-daily-open-btn'
    });
    openBtn.addEventListener('click', async () => {
      const leaf = this.app.workspace.getLeaf(false);
      await leaf.openFile(file);
    });

    // Show content preview
    const preview = container.createDiv('vp-daily-preview');
    preview.createEl('h4', { text: 'Preview' });
    preview.createEl('pre', { text: content.slice(0, 1000) + (content.length > 1000 ? '...' : '') });
  }

  private async renderNewNoteTemplate(container: HTMLElement) {
    const template = container.createDiv('vp-daily-template');
    
    const heading = template.createEl('h3');
    heading.setText(`📅 ${this.currentDate.format('MMMM D, YYYY')}`);
    
    const form = template.createEl('div', { cls: 'vp-daily-form' });
    
    // Mood selector
    const moodWrapper = form.createDiv('vp-daily-field');
    moodWrapper.createEl('label', { text: 'Mood:', cls: 'vp-daily-label' });
    const moodSelect = moodWrapper.createEl('select', { cls: 'vp-daily-mood' });
    ['Great', 'Good', 'Okay', 'Tired', 'Stressed'].forEach(m => {
      moodSelect.createEl('option', { value: m.toLowerCase(), text: m });
    });
    moodSelect.addEventListener('change', (e) => {
      this.mood = (e.target as HTMLSelectElement).value;
    });
    
    // Productivity slider
    const prodWrapper = form.createDiv('vp-daily-field');
    prodWrapper.createEl('label', { text: 'Productivity:', cls: 'vp-daily-label' });
    const prodSlider = prodWrapper.createEl('input', {
      type: 'range',
      cls: 'vp-daily-productivity',
      attr: { min: '1', max: '5', value: '3' }
    });
    const prodValue = prodWrapper.createEl('span', { text: '3/5', cls: 'vp-daily-prod-value' });
    
    prodSlider.addEventListener('input', (e) => {
      this.productivity = parseInt((e.target as HTMLInputElement).value, 10);
      prodValue.textContent = `${this.productivity}/5`;
    });

    // Previous/Next day links
    const linksWrapper = form.createDiv('vp-daily-links');
    const yesterday = this.currentDate.clone().subtract(1, 'day');
    const tomorrow = this.currentDate.clone().add(1, 'day');
    
    const prevLink = linksWrapper.createEl('span', { text: `← Yesterday: ${yesterday.format('MMM D')}`, cls: 'vp-daily-day-link' });
    prevLink.addEventListener('click', () => {
      this.currentDate = yesterday;
      this.onOpen();
    });

    const nextLink = linksWrapper.createEl('span', { text: `Tomorrow: ${tomorrow.format('MMM D')} →`, cls: 'vp-daily-day-link' });
    nextLink.addEventListener('click', () => {
      this.currentDate = tomorrow;
      this.onOpen();
    });

    // Notes textarea
    const notesWrapper = form.createDiv('vp-daily-field vp-daily-notes');
    notesWrapper.createEl('label', { text: 'Notes:', cls: 'vp-daily-label' });
    const textarea = notesWrapper.createEl('textarea', {
      cls: 'vp-daily-textarea',
      attr: { placeholder: 'What happened today? What did you learn?' }
    });
    textarea.value = this.notes;
    textarea.addEventListener('input', (e) => {
      this.notes = (e.target as HTMLTextAreaElement).value;
    });

    // Actions
    const actions = template.createDiv('vp-daily-actions');
    const createBtn = actions.createEl('button', {
      text: 'Create Daily Note',
      cls: 'vp-daily-create-btn vp-primary'
    });
    createBtn.addEventListener('click', () => this.createDailyNote());

    const insertBtn = actions.createEl('button', {
      text: 'Insert into Current Note',
      cls: 'vp-daily-insert-btn'
    });
    insertBtn.addEventListener('click', () => this.insertIntoCurrentNote());
  }

  private async createDailyNote() {
    const dateStr = this.currentDate.format('YYYY-MM-DD');
    const fileName = `${dateStr}.md`;
    
    // Try to find the daily notes folder from plugin settings
    const dailyFolder = 'Daily'; // Default folder
    
    let folderPath = dailyFolder;
    const fullPath = folderPath ? `${folderPath}/${fileName}` : fileName;
    
    try {
      // Create folder if needed
      if (folderPath) {
        const folder = this.app.vault.getAbstractFileByPath(folderPath);
        if (!folder) {
          await this.app.vault.createFolder(folderPath);
        }
      }

      // Build content
      const content = this.buildDailyNoteContent();
      
      // Create file
      await this.app.vault.create(fullPath, content);
      new Notice(`Created ${fullPath}`, 2000);
      
      // Open the file
      const file = this.app.vault.getAbstractFileByPath(fullPath) as TFile;
      if (file) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
      }
    } catch (e) {
      new Notice(`Failed to create: ${e}`, 3000);
    }
  }

  private buildDailyNoteContent(): string {
    const date = this.currentDate.format('YYYY-MM-DD');
    const yesterday = this.currentDate.clone().subtract(1, 'day').format('YYYY-MM-DD');
    const tomorrow = this.currentDate.clone().add(1, 'day').format('YYYY-MM-DD');

    return `---
date: ${date}
mood: ${this.mood}
productivity: ${this.productivity}/5
---

# ${this.currentDate.format('MMMM D, YYYY')}

## Yesterday
<!-- Links to previous context -->

## Today
${this.notes || '<!-- Your notes here -->'}

## Tomorrow
<!-- Forward-looking notes -->

---
*Generated by VaultPortal*
`;
  }

  private async insertIntoCurrentNote() {
    const activeFile = this.app.workspace.getActiveFile();
    if (!activeFile) {
      new Notice('No active file. Open a note first.', 2000);
      return;
    }

    const content = await this.app.vault.read(activeFile);
    const insertion = `\n\n## ${this.currentDate.format('MMMM D, YYYY')}\n\n**Mood:** ${this.mood}\n**Productivity:** ${this.productivity}/5\n\n${this.notes || ''}\n`;

    // Find last header or end of file
    const lastHeaderIndex = content.lastIndexOf('\n## ');
    const insertPos = lastHeaderIndex !== -1 ? lastHeaderIndex : content.length;

    const newContent = content.slice(0, insertPos) + insertion + content.slice(insertPos);
    
    await this.app.vault.modify(activeFile, newContent);
    new Notice('Inserted daily note template', 2000);
  }

  async onClose(): Promise<void> {
    // Cleanup
  }
}