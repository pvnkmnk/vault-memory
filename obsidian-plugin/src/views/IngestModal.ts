import { App, Modal, Notice, Setting } from 'obsidian';
import { DaemonClient } from '../components/DaemonClient';

type PageType = 'entity' | 'concept' | 'comparison' | 'analysis';
type Confidence = 'high' | 'medium' | 'low';
type Maturity = 'seed' | 'sapling';

export class IngestModal extends Modal {
  client: DaemonClient;
  private content: string = '';
  private filename: string = '';
  private confidence: Confidence = 'medium';
  private maturity: Maturity = 'seed';
  private mode: 'write_working' | 'promote';
  private title: string = '';
  private pageType: PageType = 'concept';
  private references: string = '';

  constructor(app: App, client: DaemonClient, mode: 'write_working' | 'promote' = 'write_working') {
    super(app);
    this.client = client;
    this.mode = mode;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.empty();
    
    this.renderHeader();
    this.renderModeSelector();
    this.renderForm();
    this.renderActions();
  }

  private renderHeader() {
    const header = this.contentEl.createDiv('vp-ingest-header');
    header.createEl('h2', { text: 'Ingest Content' });
    header.createEl('span', { text: 'VaultPortal', cls: 'vp-header-badge' });
  }

  private renderModeSelector() {
    const selector = this.contentEl.createDiv('vp-ingest-mode-selector');
    
    const writeBtn = selector.createEl('button', { 
      text: '📝 Quick Write',
      cls: 'vp-ingest-mode-btn' 
    });
    writeBtn.addEventListener('click', () => {
      this.mode = 'write_working';
      this.onOpen();
    });

    const promoteBtn = selector.createEl('button', { 
      text: '⭐ Promote to Wiki',
      cls: 'vp-ingest-mode-btn' 
    });
    promoteBtn.addEventListener('click', () => {
      this.mode = 'promote';
      this.onOpen();
    });

    if (this.mode === 'write_working') writeBtn.classList.add('vp-mode-active');
    else promoteBtn.classList.add('vp-mode-active');
  }

  private renderForm() {
    const form = this.contentEl.createDiv('vp-ingest-form');

    if (this.mode === 'write_working') {
      // Filename
      new Setting(form)
        .setName('Filename')
        .setDesc('Name for the file in _working/')
        .addText(text => {
          text.setPlaceholder('my-insight-2026-05-15.md')
              .setValue(this.filename)
              .onChange(v => this.filename = v);
        });

      // Content
      const contentSetting = new Setting(form)
        .setName('Content')
        .setDesc('Markdown content to write');
      
      const contentArea = contentSetting.descEl.createEl('textarea', {
        cls: 'vp-ingest-textarea',
        attr: { placeholder: 'Enter your markdown content here...' }
      });
      contentArea.value = this.content;
      contentArea.addEventListener('input', (e) => {
        this.content = (e.target as HTMLTextAreaElement).value;
      });

      // Confidence
      new Setting(form)
        .setName('Confidence')
        .setDesc('Agent confidence level')
        .addDropdown(drop => {
          drop.addOption('high', 'High')
              .addOption('medium', 'Medium')
              .addOption('low', 'Low')
              .setValue(this.confidence)
              .onChange(v => this.confidence = v as Confidence);
        });

      // Maturity
      new Setting(form)
        .setName('Maturity')
        .setDesc('Content maturity level')
        .addDropdown(drop => {
          drop.addOption('seed', 'Seed (unreviewed)')
              .addOption('sapling', 'Sapling (reviewed)')
              .setValue(this.maturity)
              .onChange(v => this.maturity = v as Maturity);
        });
    } else {
      // Promote mode
      new Setting(form)
        .setName('Title')
        .setDesc('Page title for the promoted content')
        .addText(text => {
          text.setPlaceholder('My New Page')
              .setValue(this.title)
              .onChange(v => this.title = v);
        });

      new Setting(form)
        .setName('Page Type')
        .setDesc('Type of wiki page')
        .addDropdown(drop => {
          drop.addOption('entity', 'Entity')
              .addOption('concept', 'Concept')
              .addOption('comparison', 'Comparison')
              .addOption('analysis', 'Analysis')
              .setValue(this.pageType)
              .onChange(v => this.pageType = v as PageType);
        });

      // Content
      const contentSetting = new Setting(form)
        .setName('Content')
        .setDesc('Wiki-quality content to promote');
      
      const contentArea = contentSetting.descEl.createEl('textarea', {
        cls: 'vp-ingest-textarea',
        attr: { placeholder: 'Enter wiki-quality markdown content...' }
      });
      contentArea.value = this.content;
      contentArea.addEventListener('input', (e) => {
        this.content = (e.target as HTMLTextAreaElement).value;
      });

      new Setting(form)
        .setName('References')
        .setDesc('Entity names to link (comma-separated)')
        .addText(text => {
          text.setPlaceholder('project-x, method-y, concept-z')
              .setValue(this.references)
              .onChange(v => this.references = v);
        });
    }
  }

  private renderActions() {
    const actions = this.contentEl.createDiv('vp-ingest-actions');
    
    const cancelBtn = actions.createEl('button', { text: 'Cancel', cls: 'vp-ingest-cancel' });
    cancelBtn.addEventListener('click', () => this.close());

    const submitBtn = actions.createEl('button', { 
      text: this.mode === 'write_working' ? 'Write to _working/' : 'Promote to Wiki', 
      cls: 'vp-ingest-submit vp-primary' 
    });
    submitBtn.addEventListener('click', () => this.handleSubmit());
  }

  private async handleSubmit() {
    if (this.mode === 'write_working') {
      if (!this.filename.trim()) {
        new Notice('Please enter a filename', 2000);
        return;
      }
      if (!this.content.trim()) {
        new Notice('Please enter content', 2000);
        return;
      }

      try {
        const result = await this.client.writeWorking(this.filename, this.content, this.confidence, this.maturity);
        new Notice(`Written to _working/: ${result.filename_used}`, 3000);
        this.close();
      } catch (e) {
        new Notice(`Error: ${e}`, 4000);
      }
    } else {
      if (!this.title.trim()) {
        new Notice('Please enter a title', 2000);
        return;
      }
      if (!this.content.trim()) {
        new Notice('Please enter content', 2000);
        return;
      }

      try {
        const refs = this.references.split(',').map(r => r.trim()).filter(r => r);
        await this.client.promoteText(this.content, this.title, this.pageType, refs);
        new Notice(`Promoted: ${this.title}`, 3000);
        this.close();
      } catch (e) {
        new Notice(`Error: ${e}`, 4000);
      }
    }
  }

  onClose() {
    const { contentEl } = this;
    contentEl.empty();
  }
}