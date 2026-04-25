import { Plugin, Notice } from 'obsidian';
import { SearchPanel } from './views/SearchPanel';
import { GraphCanvas } from './views/GraphCanvas';
import { StatusBar } from './views/StatusBar';
import { DaemonClient } from './components/DaemonClient';

const VIEW_TYPE_SEARCH = 'vault-portal-search';
const VIEW_TYPE_GRAPH = 'vault-portal-graph';

export default class VaultPortal extends Plugin {
  daemonClient!: DaemonClient;
  statusBar!: StatusBar;

  async onload() {
    this.daemonClient = new DaemonClient();
    this.statusBar = new StatusBar(this.daemonClient);

    const statusEl = this.addStatusBarItem();
    void this.statusBar.render(statusEl);
    this.registerInterval(window.setInterval(() => { void this.statusBar.update(); }, 30000));
    this.registerView(VIEW_TYPE_SEARCH, (leaf) => new SearchPanel(leaf, this.daemonClient));
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
}
