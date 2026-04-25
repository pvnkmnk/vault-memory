import { View, WorkspaceLeaf } from 'obsidian';
import { DaemonClient } from '../components/DaemonClient';

const VIEW_TYPE_GRAPH = 'vault-portal-graph';

export class GraphCanvas extends View {
  client: DaemonClient;
  nodes: Array<{ id: string; label: string; connections: number }> = [];
  edges: Array<{ source: string; target: string }> = [];

  constructor(leaf: WorkspaceLeaf, client: DaemonClient) { super(leaf); this.client = client; }
  getViewType() { return VIEW_TYPE_GRAPH; }
  get displayText() { return 'Knowledge Graph'; }

  async onOpen() {
    this.containerEl.empty();
    this.containerEl.createDiv('vp-graph-header').createEl('h2', { text: 'Knowledge Graph' });
    const controls = this.containerEl.createDiv('vp-graph-controls');
    const refreshBtn = controls.createEl('button', { text: 'Refresh', attr: { 'aria-label': 'Refresh graph', 'data-tooltip-position': 'top' } });
    refreshBtn.addEventListener('click', () => this.loadGraph());
    this.containerEl.createDiv('vp-graph-container');
    await this.loadGraph();
  }

  async loadGraph() {
    const entity = this.app.workspace.getActiveFile()?.basename;
    if (!entity) { this.showError('Open a note to graph its relationships'); return; }

    try {
      const d = await this.client.getGraph(entity);
      const paths = d.paths || [];
      const targets = Array.from(new Set(paths.map((p) => p.target)));
      this.nodes = [{ id: entity, label: entity, connections: paths.length }, ...targets.map((target) => ({ id: target, label: target, connections: 1 }))];
      this.edges = paths.map((p) => ({ source: entity, target: p.target }));
      this.renderGraph();
    } catch (e) { this.showError(String(e)); }
  }

  renderGraph() {
    const c = this.containerEl.querySelector('.vp-graph-container'); if (!c) return;
    c.empty();
    if (this.nodes.length === 0) { c.createDiv('vp-no-graph', { text: 'No graph data' }); return; }
    const svg = c.createEl('svg', { attr: { width: '100%', height: '400' } });
    const nodeMap = new Map(this.nodes.map((n, i) => [n.id, i]));
    this.edges.forEach((e) => {
      const si = nodeMap.get(e.source), ti = nodeMap.get(e.target);
      if (si === undefined || ti === undefined) return;
      svg.createEl('line', { attr: { x1: String(50 + si * 30), y1: String(50 + si * 20), x2: String(50 + ti * 30), y2: String(50 + ti * 20), stroke: 'var(--text-muted)' } });
    });
    this.nodes.forEach((n, i) => {
      const circle = svg.createEl('circle', { attr: { cx: String(50 + i * 30), cy: String(50 + i * 20), r: String(10 + Math.min(n.connections * 2, 20)), fill: 'var(--interactive-accent)' } });
      const label = svg.createEl('text', { attr: { x: String(50 + i * 30), y: String(50 + i * 20 + 25), 'text-anchor': 'middle', fill: 'var(--text-normal)' } });
      label.setText(n.label);
    });
  }

  showError(msg: string) { const c = this.containerEl.querySelector('.vp-graph-container'); if (!c) return; const e = c.querySelector('.vp-error'); if (e) e.remove(); c.createDiv('vp-error', { text: msg }); }
}
