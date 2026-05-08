import { Notice } from 'obsidian';
import type { WorkspaceLeaf } from 'obsidian';
import { App, View } from 'obsidian';
import * as d3 from 'd3';
import { DaemonClient, GraphNode, GraphEdge, GraphData } from '../components/DaemonClient';

interface NodeDetail {
  node: GraphNode;
  backlinks: Array<{ path: string; title: string }>;
  outlinks: Array<{ path: string; title: string }>;
}

export class GraphCanvas extends View {
  client: DaemonClient;
  nodes: GraphNode[] = [];
  edges: GraphEdge[] = [];
  depth: number = 2;
  simulation: d3.Simulation<GraphNode, GraphEdge> | null = null;
  selectedNode: GraphNode | null = null;
  filterType: string = 'all';
  private svgSelection: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null;

  constructor(app: App, leaf: WorkspaceLeaf, client: DaemonClient) {
    super(leaf);
    this.client = client;
  }

  getViewType(): string { return 'vault-portal-graph'; }
  getDisplayText(): string { return 'Knowledge Graph'; }

  async onOpen() {
    this.containerEl.empty();
    this.renderHeader();
    this.renderControls();
    this.renderLayout();
    await this.loadGraph();
  }

  private renderHeader() {
    const header = this.containerEl.createDiv('vp-graph-header');
    const h2 = header.createEl('h2');
    h2.setText('Knowledge Graph');
    const badge = header.createEl('span', { cls: 'vp-header-badge' });
    badge.setText('VaultPortal');
  }

  private renderControls() {
    const controls = this.containerEl.createDiv('vp-graph-controls');
    
    const refreshBtn = controls.createEl('button', { 
      text: '🔄 Refresh', 
      attr: { 'aria-label': 'Refresh graph' } 
    });
    refreshBtn.addEventListener('click', () => this.loadGraph());

    const depthWrapper = controls.createDiv('vp-depth-selector');
    const depthLabel = depthWrapper.createEl('label');
    depthLabel.setText('Depth:');
    const depthSelect = depthWrapper.createEl('select', { cls: 'vp-depth-select' });
    [1, 2, 3, 4, 5].forEach(n => {
      const opt = depthSelect.createEl('option');
      opt.setAttr('value', String(n));
      opt.setText(String(n));
    });
    depthSelect.value = String(this.depth);
    depthSelect.addEventListener('change', (e) => {
      this.depth = parseInt((e.target as HTMLSelectElement).value, 10);
      this.loadGraph();
    });

    // Relationship filter
    const filterWrapper = controls.createDiv('vp-relation-filter');
    const filterLabel = filterWrapper.createEl('label');
    filterLabel.setText('Relation:');
    const filterSelect = filterWrapper.createEl('select', { cls: 'vp-filter-select' });
    ['all', 'outgoing', 'incoming'].forEach(f => {
      const opt = filterSelect.createEl('option');
      opt.setAttr('value', f);
      opt.setText(f.charAt(0).toUpperCase() + f.slice(1));
    });
    filterSelect.addEventListener('change', (e) => {
      this.filterType = (e.target as HTMLSelectElement).value;
      this.updateGraphVisibility();
    });

    // Export buttons
    const exportWrapper = controls.createDiv('vp-export-controls');
    const pngBtn = exportWrapper.createEl('button', { text: '📷 PNG', attr: { 'aria-label': 'Export as PNG' } });
    pngBtn.addEventListener('click', () => this.exportPNG());
    
    const svgBtn = exportWrapper.createEl('button', { text: 'SVG', attr: { 'aria-label': 'Export as SVG' } });
    svgBtn.addEventListener('click', () => this.exportSVG());

    const canvasBtn = exportWrapper.createEl('button', {
      text: 'Canvas JSON',
      attr: { 'aria-label': 'Export as Obsidian Canvas JSON' },
    });
    canvasBtn.addEventListener('click', () => this.exportCanvasJson());

    const zoomWrapper = controls.createDiv('vp-zoom-controls');
    const zoomIn = zoomWrapper.createEl('button', { text: '+', attr: { 'aria-label': 'Zoom in' } });
    const zoomOut = zoomWrapper.createEl('button', { text: '−', attr: { 'aria-label': 'Zoom out' } });
    const zoomFit = zoomWrapper.createEl('button', { text: '⊡', attr: { 'aria-label': 'Fit to view' } });
    
    zoomIn.addEventListener('click', () => this.zoomIn());
    zoomOut.addEventListener('click', () => this.zoomOut());
    zoomFit.addEventListener('click', () => this.zoomFit());
  }

  private renderLayout() {
    // Main container with graph and sidebar
    const mainContainer = this.containerEl.createDiv('vp-graph-main');
    mainContainer.createDiv('vp-graph-container');
    mainContainer.createDiv('vp-graph-sidebar');
  }

  private renderSidebar(detail: NodeDetail) {
    const sidebar = this.containerEl.querySelector('.vp-graph-sidebar') as HTMLElement;
    if (!sidebar) return;
    
    sidebar.empty();
    
    const panel = sidebar.createDiv('vp-node-detail');
    
    // Header with close button
    const header = panel.createDiv('vp-detail-header');
    const h3 = header.createEl('h3');
    h3.setText(detail.node.label);
    const closeBtn = header.createEl('button', { cls: 'vp-detail-close' });
    closeBtn.setText('✕');
    closeBtn.addEventListener('click', () => this.closeSidebar());
    
    // Metadata
    const meta = panel.createDiv('vp-detail-meta');
    const connEl = meta.createEl('span', { cls: 'vp-detail-connections' });
    connEl.setText(`Connections: ${detail.node.connections}`);
    if (detail.node.id) {
      const idEl = meta.createEl('span', { cls: 'vp-detail-id' });
      idEl.setText(`ID: ${detail.node.id}`);
    }

    // Open file button
    const openBtn = panel.createEl('button', { text: '📝 Open in Editor', cls: 'vp-detail-open-btn' });
    openBtn.addEventListener('click', () => this.openNodeFile(detail.node));

    // Backlinks section
    if (detail.backlinks.length > 0) {
      const backlinksSection = panel.createDiv('vp-detail-section');
      const blH4 = backlinksSection.createEl('h4');
      blH4.setText(`← Backlinks (${detail.backlinks.length})`);
      const backlinksList = backlinksSection.createEl('ul', { cls: 'vp-detail-list' });
      detail.backlinks.forEach(link => {
        const item = backlinksList.createEl('li');
        const linkEl = item.createEl('span', { cls: 'vp-detail-link' });
        linkEl.setText(link.title);
        linkEl.addEventListener('click', () => this.navigateToFile(link.path));
        const pathEl = item.createEl('span', { cls: 'vp-detail-path' });
        pathEl.setText(link.path);
      });
    }

    // Outlinks section
    if (detail.outlinks.length > 0) {
      const outlinksSection = panel.createDiv('vp-detail-section');
      const olH4 = outlinksSection.createEl('h4');
      olH4.setText(`→ Outlinks (${detail.outlinks.length})`);
      const outlinksList = outlinksSection.createEl('ul', { cls: 'vp-detail-list' });
      detail.outlinks.forEach(link => {
        const item = outlinksList.createEl('li');
        const linkEl = item.createEl('span', { cls: 'vp-detail-link' });
        linkEl.setText(link.title);
        linkEl.addEventListener('click', () => this.navigateToFile(link.path));
        const pathEl = item.createEl('span', { cls: 'vp-detail-path' });
        pathEl.setText(link.path);
      });
    }

    // No links message
    if (detail.backlinks.length === 0 && detail.outlinks.length === 0) {
      const msgEl = panel.createDiv('vp-detail-no-links');
      msgEl.setText('No linked files found');
    }
  }

  private closeSidebar() {
    const sidebar = this.containerEl.querySelector('.vp-graph-sidebar') as HTMLElement;
    if (sidebar) sidebar.empty();
    this.selectedNode = null;
    
    // Reset node highlighting
    d3.selectAll('.vp-d3-node circle')
      .attr('stroke', 'var(--background-primary)')
      .attr('stroke-width', 2);
  }

  private async navigateToFile(path: string) {
    try {
      const normalizedPath = path.replace(/\\/g, '/');
      const file = this.app.metadataCache.getFirstLinkpathDest(normalizedPath, '');
      
      if (file) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(file);
      } else {
        new Notice(`File not found: ${path}`, 2000);
      }
    } catch {
      new Notice(`Could not open: ${path}`, 2000);
    }
  }

  async loadGraph() {
    const container = this.containerEl.querySelector('.vp-graph-container');
    if (!container) return;
    
    container.empty();
    const loading = container.createDiv('vp-loading');
    const loadingText = loading.createDiv();
    loadingText.setText('Loading graph...');

    try {
      const data: GraphData = await this.client.getGraph(this.depth);
      this.nodes = (data.nodes || []).map(n => ({ ...n }));
      this.edges = (data.edges || []).map(e => ({ ...e }));
      container.empty();
      
      // Close sidebar when reloading
      this.closeSidebar();
      
      if (this.nodes.length === 0) {
        const msg = container.createDiv('vp-no-graph');
        msg.setText('No graph data. Try increasing the depth.');
        return;
      }

      this.renderD3Graph(container as HTMLElement);
    } catch (e) {
      this.showError('Failed to load graph: ' + e);
    }
  }

  private renderD3Graph(container: HTMLElement) {
    const width = container.clientWidth || 600;
    const height = 500;

    const svg = d3.select(container as Element)
      .append('svg')
      .attr('width', '100%')
      .attr('height', height)
      .attr('viewBox', '0 0 ' + width + ' ' + height)
      .attr('class', 'vp-d3-graph');

    this.svgSelection = svg as d3.Selection<SVGSVGElement, unknown, null, undefined>;

    const g = svg.append('g');
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.1, 4])
      .on('zoom', (event) => {
        g.attr('transform', event.transform);
      });
    svg.call(zoom as any);

    (this as any)._zoomBehavior = zoom;
    (this as any)._zoomSelection = svg;

    svg.append('defs').append('marker')
      .attr('id', 'arrowhead')
      .attr('viewBox', '-0 -5 10 10')
      .attr('refX', 20)
      .attr('refY', 0)
      .attr('orient', 'auto')
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .append('path')
      .attr('d', 'M 0,-5 L 10,0 L 0,5')
      .attr('fill', 'var(--text-muted)');

    this.simulation = d3.forceSimulation<GraphNode>(this.nodes)
      .force('link', d3.forceLink<GraphNode, GraphEdge>(this.edges).id(d => d.id).distance(100))
      .force('charge', d3.forceManyBody().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius(40));

    const link = g.append('g').attr('class', 'vp-d3-links')
      .selectAll('line')
      .data(this.edges)
      .enter()
      .append('line')
      .attr('stroke', 'var(--background-modifier-border)')
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#arrowhead)');

    const node = g.append('g').attr('class', 'vp-d3-nodes')
      .selectAll('g')
      .data(this.nodes)
      .enter()
      .append('g')
      .attr('class', 'vp-d3-node')
      .call(d3.drag<SVGGElement, GraphNode>()
        .on('start', (event, d) => this.dragStarted(event, d))
        .on('drag', (event, d) => this.dragged(event, d))
        .on('end', (event, d) => this.dragEnded(event, d)));

    node.append('circle')
      .attr('r', d => 10 + Math.min(d.connections * 2, 20))
      .attr('fill', 'var(--interactive-accent)')
      .attr('stroke', 'var(--background-primary)')
      .attr('stroke-width', 2)
      .style('cursor', 'pointer');

    node.append('text')
      .text(d => d.label)
      .attr('dy', d => 25 + Math.min(d.connections * 2, 20))
      .attr('text-anchor', 'middle')
      .attr('fill', 'var(--text-normal)')
      .attr('font-size', '12px')
      .style('pointer-events', 'none');

    node.append('text')
      .text(d => String(d.connections))
      .attr('dy', 4)
      .attr('text-anchor', 'middle')
      .attr('fill', 'white')
      .attr('font-size', '10px')
      .attr('font-weight', 'bold')
      .style('pointer-events', 'none');

    // Click handler for node detail
    node.on('click', (event, d) => this.showNodeDetail(d));

    node.on('mouseenter', (event: MouseEvent) => {
      d3.select(event.currentTarget as SVGGElement).select('circle')
        .attr('stroke', 'var(--interactive-accent)')
        .attr('stroke-width', 3);
    }).on('mouseleave', (event: MouseEvent) => {
      // Don't reset if this is the selected node
      const selectedId = (window as any)._vpSelectedNodeId;
      const nodeData = d3.select(event.currentTarget as SVGGElement).datum() as GraphNode;
      if (nodeData.id !== selectedId) {
        d3.select(event.currentTarget as SVGGElement).select('circle')
          .attr('stroke', 'var(--background-primary)')
          .attr('stroke-width', 2);
      }
    });

    this.simulation.on('tick', () => {
      link
        .attr('x1', d => (d.source as GraphNode).x || 0)
        .attr('y1', d => (d.source as GraphNode).y || 0)
        .attr('x2', d => (d.target as GraphNode).x || 0)
        .attr('y2', d => (d.target as GraphNode).y || 0);
      node.attr('transform', d => 'translate(' + (d.x || 0) + ',' + (d.y || 0) + ')');
    });
  }

  private async showNodeDetail(node: GraphNode) {
    this.selectedNode = node;
    (window as any)._vpSelectedNodeId = node.id;
    
    // Highlight selected node
    d3.selectAll('.vp-d3-node circle')
      .attr('stroke', (d: any) => d.id === node.id ? 'var(--interactive-accent)' : 'var(--background-primary)')
      .attr('stroke-width', (d: any) => d.id === node.id ? 4 : 2);

    // Find linked nodes from edges
    const backlinks: Array<{ path: string; title: string }> = [];
    const outlinks: Array<{ path: string; title: string }> = [];

    this.edges.forEach(edge => {
      const sourceId = typeof edge.source === 'string' ? edge.source : (edge.source as GraphNode).id;
      const targetId = typeof edge.target === 'string' ? edge.target : (edge.target as GraphNode).id;
      
      if (targetId === node.id) {
        const sourceNode = this.nodes.find(n => n.id === sourceId);
        if (sourceNode) {
          const path = this.findFilePath(sourceNode.label);
          backlinks.push({ path, title: sourceNode.label });
        }
      }
      
      if (sourceId === node.id) {
        const targetNode = this.nodes.find(n => n.id === targetId);
        if (targetNode) {
          const path = this.findFilePath(targetNode.label);
          outlinks.push({ path, title: targetNode.label });
        }
      }
    });

    const detail: NodeDetail = { node, backlinks, outlinks };
    this.renderSidebar(detail);
  }

  private findFilePath(label: string): string {
    const normalizedLabel = label.replace(/[-_\/]/g, '/').toLowerCase();
    const files = this.app.vault.getFiles();
    const match = files.find(f => 
      f.path.toLowerCase().includes(normalizedLabel) ||
      f.name.toLowerCase().includes(label.toLowerCase())
    );
    return match ? match.path : label;
  }

  private updateGraphVisibility() {
    if (this.filterType === 'all') {
      d3.selectAll('.vp-d3-node').style('opacity', 1);
      d3.selectAll('.vp-d3-links line').style('opacity', 0.6);
    } else if (this.selectedNode) {
      const selectedId = this.selectedNode.id;
    d3.selectAll<SVGGElement, GraphNode>('.vp-d3-node').style('opacity', (d: GraphNode): string | number => {
      if (d.id === selectedId) return 1;
      // Check if connected
      const connected = this.edges.some(e => {
        const sourceId = typeof e.source === 'string' ? e.source : (e.source as GraphNode).id;
        const targetId = typeof e.target === 'string' ? e.target : (e.target as GraphNode).id;
        return (sourceId === selectedId && targetId === d.id) ||
               (targetId === selectedId && sourceId === d.id);
      });
      return connected ? 1 : 0.2;
    });
    
    d3.selectAll<SVGLineElement, GraphEdge>('.vp-d3-links line').style('opacity', (d: GraphEdge): string | number => {
      const sourceId = typeof d.source === 'string' ? d.source : (d.source as GraphNode).id;
      const targetId = typeof d.target === 'string' ? d.target : (d.target as GraphNode).id;
      if (this.filterType === 'outgoing') {
        return sourceId === selectedId ? 0.8 : 0.1;
      } else {
        return targetId === selectedId ? 0.8 : 0.1;
      }
    });
    }
  }

  private exportPNG() {
    const svgEl = this.containerEl.querySelector('.vp-d3-graph') as SVGElement;
    if (!svgEl) {
      new Notice('No graph to export', 2000);
      return;
    }

    try {
      const svgData = new XMLSerializer().serializeToString(svgEl);
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const img = new Image();

      canvas.width = svgEl.clientWidth || 800;
      canvas.height = svgEl.clientHeight || 600;

      img.onload = () => {
        if (ctx) {
          ctx.fillStyle = getComputedStyle(document.body).getPropertyValue('--background-secondary') || '#1e1e1e';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0);
          
          const link = document.createElement('a');
          link.download = 'knowledge-graph.png';
          link.href = canvas.toDataURL('image/png');
          link.click();
          new Notice('Exported as PNG', 2000);
        }
      };

      img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgData)));
    } catch (e) {
      new Notice('Export failed: ' + e, 3000);
    }
  }

  private exportSVG() {
    const svgEl = this.containerEl.querySelector('.vp-d3-graph') as SVGElement;
    if (!svgEl) {
      new Notice('No graph to export', 2000);
      return;
    }

    try {
      const svgData = new XMLSerializer().serializeToString(svgEl);
      const blob = new Blob([svgData], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      
      const link = document.createElement('a');
      link.download = 'knowledge-graph.svg';
      link.href = url;
      link.click();
      
      URL.revokeObjectURL(url);
      new Notice('Exported as SVG', 2000);
    } catch (e) {
      new Notice('Export failed: ' + e, 3000);
    }
  }

  private async exportCanvasJson() {
    try {
      const exportData = await this.client.getGraphCanvasExport('canvas');
      const blob = new Blob([JSON.stringify({
        nodes: exportData.nodes,
        edges: exportData.edges,
      }, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.download = `knowledge-graph-${Date.now()}.canvas`;
      link.href = url;
      link.click();
      URL.revokeObjectURL(url);
      new Notice(`Exported ${exportData.count} edges as canvas`, 2500);
    } catch (e) {
      new Notice(`Canvas export failed: ${e}`, 3000);
    }
  }

  private dragStarted(event: d3.D3DragEvent<SVGGElement, GraphNode, GraphNode>, d: GraphNode) {
    if (!event.active) this.simulation?.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
  }

  private dragged(event: d3.D3DragEvent<SVGGElement, GraphNode, GraphNode>, d: GraphNode) {
    d.fx = event.x;
    d.fy = event.y;
  }

  private dragEnded(event: d3.D3DragEvent<SVGGElement, GraphNode, GraphNode>, d: GraphNode) {
    if (!event.active) this.simulation?.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }

  private async openNodeFile(node: GraphNode) {
    try {
      const normalizedLabel = node.label.replace(/[-_\/]/g, '/').toLowerCase();
      const files = this.app.vault.getFiles();
      const matchingFile = files.find(f => 
        f.path.toLowerCase().includes(normalizedLabel) ||
        f.name.toLowerCase().includes(node.label.toLowerCase())
      );

      if (matchingFile) {
        const leaf = this.app.workspace.getLeaf(false);
        await leaf.openFile(matchingFile);
        new Notice('Opened: ' + matchingFile.name, 2000);
      } else {
        new Notice('File not found for: ' + node.label, 2000);
      }
    } catch {
      new Notice('Could not open: ' + node.label, 2000);
    }
  }

  private zoomIn() {
    const svg = (this as any)._zoomSelection;
    const zoom = (this as any)._zoomBehavior;
    if (svg && zoom && this.svgSelection) {
      this.svgSelection.transition().duration(300).call(zoom.scaleBy as any, 1.3);
    }
  }

  private zoomOut() {
    const svg = (this as any)._zoomSelection;
    const zoom = (this as any)._zoomBehavior;
    if (svg && zoom && this.svgSelection) {
      this.svgSelection.transition().duration(300).call(zoom.scaleBy as any, 0.7);
    }
  }

  private zoomFit() {
    const svg = (this as any)._zoomSelection;
    const zoom = (this as any)._zoomBehavior;
    const g = svg?.select('g');
    const bounds = (g?.node() as SVGGElement)?.getBBox();
    if (bounds && svg && zoom && this.svgSelection) {
      const w = this.svgSelection.node()?.clientWidth || 600;
      const h = 500;
      const scale = Math.min(0.9 * w / bounds.width, 0.9 * h / bounds.height, 2);
      const tx = w / 2 - scale * (bounds.x + bounds.width / 2);
      const ty = h / 2 - scale * (bounds.y + bounds.height / 2);
      this.svgSelection.transition().duration(500).call(zoom.transform as any, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }
  }

  private showError(msg: string) {
    const container = this.containerEl.querySelector('.vp-graph-container');
    if (!container) return;
    container.empty();
    const errEl = container.createDiv('vp-error');
    errEl.setText(msg);
  }

  protected async onClose(): Promise<void> {
    this.simulation?.stop();
    this.simulation = null;
    this.svgSelection = null;
    delete (window as any)._vpSelectedNodeId;
  }
}
