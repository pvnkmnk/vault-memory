import { App, requestUrl } from 'obsidian';

const DEFAULT_DAEMON_URL = 'http://localhost:5051';

export interface SearchResult {
  file_path: string;
  content: string;
  score: number;
  path?: string;
  snippet?: string;
  trust?: string;
  maturity?: string;
  modified?: string;
  tags?: string[];
  sources?: string[];
  agent_written?: boolean;
  vault_path?: string;
  title?: string;
}

interface GraphNode { id: string; label: string; connections: number; }
interface GraphEdge { source: string; target: string; }
interface CognifyResult { triples: Array<[string, string, string]>; }

export type SearchMode = 'vector' | 'keyword' | 'graph' | 'temporal';

export class DaemonClient {
  private status: 'connected' | 'offline' | 'checking' = 'checking';
  private daemonUrl: string = DEFAULT_DAEMON_URL;
  private apiKey: string = '';
  private app: App;

  constructor(app: App) {
    this.app = app;
  }

  setDaemonUrl(url: string) { this.daemonUrl = url || DEFAULT_DAEMON_URL; }
  getDaemonUrl() { return this.daemonUrl; }
  setApiKey(key: string) { this.apiKey = key; }
  getStatus() { return this.status; }

  private getHeaders(): Record<string, string> {
    const headers: Record<string, string> = {};
    if (this.apiKey) {
      headers['x-api-key'] = this.apiKey;
    }
    return headers;
  }

  private getUrl(path: string) { return `${this.daemonUrl}${path}`; }

  async checkHealth() {
    this.status = 'checking';
    try {
      const r = await requestUrl({ url: this.getUrl('/health'), method: 'GET', headers: this.getHeaders() });
      this.status = r.status === 200 ? 'connected' : 'offline';
    } catch { this.status = 'offline'; }
    return this.status === 'connected';
  }

  private async ensureConnected(): Promise<void> {
    if (this.status !== 'connected') {
      await this.checkHealth();
      if (this.status !== 'connected') {
        throw new Error('Daemon offline');
      }
    }
  }

  async search(query: string, mode: SearchMode = 'vector', topK = 10): Promise<SearchResult[]> {
    await this.ensureConnected();
    
    // Map mode to strategies
    const strategies = this.getStrategiesForMode(mode);
    
    const r = await requestUrl({ 
      url: this.getUrl('/search'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ 
        query, 
        strategies, 
        limit: topK,
        include_graph: mode === 'graph',
        include_temporal: mode === 'temporal'
      }) 
    });
    
    if (r.status !== 200) throw new Error(`Search failed: ${r.status}`);
    const data = JSON.parse(r.text);
    return (data.results || []).map((r: SearchResult) => this.normalizeResult(r));
  }

  private getStrategiesForMode(mode: SearchMode): string[] {
    switch (mode) {
      case 'vector': return ['vector', 'keyword'];
      case 'keyword': return ['keyword'];
      case 'graph': return ['vector', 'keyword', 'graph'];
      case 'temporal': return ['vector', 'keyword', 'temporal'];
      default: return ['vector'];
    }
  }

  private normalizeResult(r: SearchResult): SearchResult {
    return {
      file_path: r.file_path || r.path || '',
      content: r.content || r.snippet || '',
      score: r.score || 0,
      trust: r.trust || 'unknown',
      maturity: r.maturity || 'unknown',
      modified: r.modified,
      tags: r.tags || [],
      sources: r.sources || [],
      agent_written: r.agent_written || false,
      vault_path: r.vault_path || r.file_path,
      title: r.title || this.extractTitle(r.file_path || r.path || '')
    };
  }

  private extractTitle(path: string): string {
    const parts = path.split('/');
    const filename = parts[parts.length - 1] || 'Untitled';
    return filename.replace(/\.md$/i, '').replace(/[-_]/g, ' ');
  }

  async getGraph(depth = 2) {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/graph'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ depth, limit: 50 }) });
    if (r.status !== 200) throw new Error(`Graph failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async temporal(startDate: string, endDate: string) {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/temporal'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ start_date: startDate, end_date: endDate }) });
    if (r.status !== 200) throw new Error(`Temporal failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async cognify(content: string): Promise<CognifyResult> {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/cognify'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ content }) });
    if (r.status !== 200) throw new Error(`Cognify failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async promote(filePath: string) {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/memory/promote'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ file_path: filePath }) });
    if (r.status !== 200) throw new Error(`Promote failed: ${r.status}`);
  }

  async writeWorking(filename: string, content: string, confidence: 'high' | 'medium' | 'low' = 'medium', maturity: 'seed' | 'sapling' = 'seed'): Promise<{written: string; filename_used: string}> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/memory/write_working'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ filename, content, vault_path: vaultPath, confidence, maturity }) 
    });
    if (r.status !== 200) throw new Error(`Write failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async promoteText(text: string, title: string, pageType: 'entity' | 'concept' | 'comparison' | 'analysis', references: string[] = []): Promise<any> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/promote'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ text, title, page_type: pageType, references, vault_path: vaultPath }) 
    });
    if (r.status !== 200) throw new Error(`Promote failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async attachBlock(blockName: string): Promise<{attached: string; token_est: number}> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/memory/attach_block'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ block_name: blockName, vault_path: vaultPath }) 
    });
    if (r.status !== 200) throw new Error(`Attach failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async listBlocks(): Promise<{attached_blocks: Array<{name: string; token_est: number}>; total_tokens: number}> {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/memory/list_blocks'), method: 'GET', headers: this.getHeaders() });
    if (r.status !== 200) throw new Error(`List blocks failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async syncFiles(paths: string[]): Promise<{synced: number; failed: number; errors: string[]}> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/sync'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ paths, vault_path: vaultPath }) 
    });
    if (r.status !== 200) throw new Error(`Sync failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async triggerSync(filePath: string): Promise<{success: boolean; message: string}> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/sync/file'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ file_path: filePath, vault_path: vaultPath }) 
    });
    if (r.status !== 200) throw new Error(`Sync failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  // ── Vault Lint ──────────────────────────────────────────────────────────────

  async runLint(staleDays = 30): Promise<{
    run_at: string;
    orphans: number;
    contradictions: number;
    stale_nodes: number;
    missing_pages: number;
    unlinked_pages: number;
    summary: Record<string, number>;
    report_path?: string;
  }> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/lint'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ vault_path: vaultPath, stale_days: staleDays, file_report: true }) 
    });
    if (r.status !== 200) throw new Error(`Lint failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  // ── Bulk Operations ─────────────────────────────────────────────────────────

  async bulkImport(notes: Array<{title?: string; content: string; tags?: string[]; metadata?: Record<string, any>}>, project?: string, skipDuplicates = true): Promise<{
    imported: number;
    skipped: number;
    total: number;
    errors: Array<{index: number; error: string}>;
    paths: string[];
  }> {
    await this.ensureConnected();
    const r = await requestUrl({ 
      url: this.getUrl('/bulk/import'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ notes, project, skip_duplicates: skipDuplicates }) 
    });
    if (r.status !== 201) throw new Error(`Bulk import failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async bulkExport(filters: {
    project?: string;
    tags?: string[];
    entity?: string;
    date_from?: string;
    date_to?: string;
    limit?: number;
  } = {}): Promise<{
    notes: Array<{
      id: string;
      title: string;
      content: string;
      project?: string;
      tags: string[];
      metadata: Record<string, any>;
      created_at?: string;
      modified_at?: string;
    }>;
    count: number;
    filters: Record<string, any>;
  }> {
    await this.ensureConnected();
    const r = await requestUrl({ 
      url: this.getUrl('/bulk/export'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify(filters) 
    });
    if (r.status !== 200) throw new Error(`Bulk export failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async bulkDelete(paths: string[], confirm = true): Promise<{
    deleted: number;
    not_found: string[];
    errors: Array<{path: string; error: string}>;
    total_requested: number;
  }> {
    await this.ensureConnected();
    const r = await requestUrl({ 
      url: this.getUrl('/bulk/delete'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ paths, confirm }) 
    });
    if (r.status !== 200) throw new Error(`Bulk delete failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  // ── Session Registry ────────────────────────────────────────────────────────

  async registerSession(agentName: string, project: string, task: string, vaultPaths?: string[], planRef?: string): Promise<{
    session_id: string;
    agent_name: string;
    project: string;
    task: string;
    started_at: string;
    status: string;
  }> {
    await this.ensureConnected();
    const vaultPath = this.app.vault.getRoot().path;
    const r = await requestUrl({ 
      url: this.getUrl('/sessions'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ 
        agent_name: agentName, 
        project, 
        task, 
        vault_path: vaultPath,
        vault_paths: vaultPaths || [],
        plan_ref: planRef
      }) 
    });
    if (r.status !== 201) throw new Error(`Register session failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async getSessions(filters: {
    agentName?: string;
    project?: string;
    status?: string;
    limit?: number;
  } = {}): Promise<{
    sessions: Array<{
      session_id: string;
      agent_name: string;
      project: string;
      task: string;
      status: string;
      started_at?: string;
      closed_at?: string;
    }>;
    count: number;
  }> {
    await this.ensureConnected();
    const params = new URLSearchParams();
    if (filters.agentName) params.append('agent_name', filters.agentName);
    if (filters.project) params.append('project', filters.project);
    if (filters.status) params.append('status', filters.status);
    if (filters.limit) params.append('limit', String(filters.limit));
    const r = await requestUrl({ 
      url: this.getUrl('/sessions') + (params.toString() ? '?' + params.toString() : ''), 
      method: 'GET',
      headers: this.getHeaders()
    });
    if (r.status !== 200) throw new Error(`Get sessions failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async closeSession(sessionId: string): Promise<{
    session_id: string;
    status: string;
    started_at?: string;
    closed_at?: string;
    duration_s?: number;
  }> {
    await this.ensureConnected();
    const r = await requestUrl({ 
      url: this.getUrl(`/sessions/${sessionId}`), 
      method: 'PATCH',
      headers: this.getHeaders(),
      body: JSON.stringify({ status: 'closed' }) 
    });
    if (r.status !== 200) throw new Error(`Close session failed: ${r.status}`);
    return JSON.parse(r.text);
  }
}