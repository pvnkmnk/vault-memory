import { App, View, Modal } from 'obsidian';
import { DaemonClient } from '../components/DaemonClient';

interface GraphNode {
  id: string;
  label: string;
  connections: number;
}

interface GraphEdge {
  source: string;
  target: string;
}

export class GraphCanvas extends View {
  client: DaemonClient;
  nodes: GraphNode[] = [];
  edges: GraphEdge[] = [];

  constructor(app: App, client: DaemonClient) {
    super(app, client);
    this.client = client;
  }

  get displayText(): string {
    return 'Knowledge Graph';
  }

  async onOpen() {
    this.containerEl.empty();

    const header = this.containerEl.createDiv('vp-graph-header');
    header.createEl('h2', { text: 'Knowledge Graph' });

    const controls = this.containerEl.createDiv('vp-graph-controls');
    
    const refreshBtn = controls.createEl('button', {
      text: 'Refresh',
      attr: {
        'aria-label': 'Refresh graph',
        'data-tooltip-position': 'top',
      }
    });
    refreshBtn.addEventListener('click', () => this.loadGraph());

    const container = this.containerEl.createDiv('vp-graph-container');

    await this.loadGraph();
  }

  async loadGraph() {
    try {
      const data = await this.client.getGraph(2);
      this.nodes = data.nodes;
      this.edges = data.edges;
      this.renderGraph();
    } catch (e) {
      this.showError(String(e));
    }
  }

  renderGraph() {
    const container = this.containerEl.querySelector('.vp-graph-container');
    if (!container) return;

    container.empty();

    if (this.nodes.length === 0) {
      container.createDiv('vp-no-graph', { text: 'No graph data' });
      return;
    }

    const svg = container.createEl('svg', {
      attr: { width: '100%', height: '400' }
    });

    const nodeMap = new Map(this.nodes.map((n, i) => [n.id, i]));

    this.edges.forEach((edge) => {
      const sourceIdx = nodeMap.get(edge.source);
      const targetIdx = nodeMap.get(edge.target);
      if (sourceIdx === undefined || targetIdx === undefined) return;

      const source = this.nodes[sourceIdx];
      const target = this.nodes[targetIdx];

      svg.createEl('line', {
        attr: {
          x1: String(50 + sourceIdx * 30),
          y1: String(50 + sourceIdx * 20),
          x2: String(50 + targetIdx * 30),
          y2: String(50 + targetIdx * 20),
          stroke: 'var(--text-muted)',
        }
      });
    });

    this.nodes.forEach((node, idx) => {
      const circle = svg.createEl('circle', {
        attr: {
          cx: String(50 + idx * 30),
          cy: String(50 + idx * 20),
          r: String(10 + Math.min(node.connections * 2, 20)),
          fill: 'var(--interactive-accent)',
        }
      });

      const label = svg.createEl('text', {
        attr: {
          x: String(50 + idx * 30),
          y: String(50 + idx * 20 + 25),
          'text-anchor': 'middle',
          fill: 'var(--text-normal)',
        }
      });
      label.setText(node.label);
    });
  }

  showError(message: string) {
    const container = this.containerEl.querySelector('.vp-graph-container');
    if (!container) return;

    const errorEl = container.querySelector('.vp-error');
    if (errorEl) errorEl.remove();

    container.createDiv('vp-error', { text: message });
  }
}