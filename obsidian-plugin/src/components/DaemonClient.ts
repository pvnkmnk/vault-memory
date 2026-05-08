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

interface CognifyResult { triples: Array<[string, string, string]>; }

// Graph types shared with GraphCanvas
export interface GraphNode {
  id: string;
  label: string;
  connections: number;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
  [key: string]: any;
}

export interface GraphEdge {
  source: string | GraphNode;
  target: string | GraphNode;
  type?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type SearchMode = 'vector' | 'keyword' | 'graph' | 'temporal';

export class DaemonClient {
  private status: 'connected' | 'offline' | 'checking' = 'checking';
  private daemonUrl: string = DEFAULT_DAEMON_URL;
  private apiKey: string = '';
  private app?: App;

  constructor(app?: App) {
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

  private describeHttpStatus(status: number): string {
    if (status === 401 || status === 403) return 'Authentication failed. Check your API key in plugin settings.';
    if (status === 404) return 'Daemon route not found. Verify daemon and plugin versions are compatible.';
    if (status === 429) return 'Rate limit exceeded. Wait and retry.';
    if (status >= 500) return 'Daemon internal error. Check daemon logs for details.';
    return `Unexpected daemon response (${status}).`;
  }

  private parseErrorMessage(text: string | undefined): string | null {
    if (!text) return null;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed?.error === 'string' && parsed.error.trim()) return parsed.error;
      if (typeof parsed?.detail === 'string' && parsed.detail.trim()) return parsed.detail;
      if (typeof parsed?.message === 'string' && parsed.message.trim()) return parsed.message;
      return null;
    } catch {
      return text.trim() || null;
    }
  }

  private assertStatus(
    operation: string,
    response: { status: number; text: string },
    expected: number | number[],
  ): void {
    const expectedList = Array.isArray(expected) ? expected : [expected];
    if (expectedList.includes(response.status)) return;

    const serverMessage = this.parseErrorMessage(response.text);
    const statusMessage = this.describeHttpStatus(response.status);
    const fullMessage = serverMessage ? `${statusMessage} (${serverMessage})` : statusMessage;
    throw new Error(`${operation} failed: ${fullMessage}`);
  }

  async checkHealth(): Promise<boolean> {
    this.status = 'checking';
    try {
      const r = await requestUrl({ url: this.getUrl('/health'), method: 'GET', headers: this.getHeaders() });
      const ok: boolean = r.status === 200;
      this.status = ok ? 'connected' : 'offline';
      return ok;
    } catch { this.status = 'offline'; return false; }
  }

  private async ensureConnected(): Promise<void> {
    if (await this.checkHealth()) return;
    throw new Error('Daemon offline');
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
    
    this.assertStatus('Search', r, 200);
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
    return filename.replace(/\b.md$/i, '').replace(/[-_]/g, ' ');
  }

  async getGraph(depth = 2) {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/graph'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ depth, limit: 50 }) });
    this.assertStatus('Graph query', r, 200);
    return JSON.parse(r.text);
  }

  async temporal(startDate: string, endDate: string) {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/temporal'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ start_date: startDate, end_date: endDate }) });
    this.assertStatus('Temporal query', r, 200);
    return JSON.parse(r.text).results || [];
  }

  async cognify(content: string): Promise<CognifyResult> {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/cognify'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ content }) });
    this.assertStatus('Cognify', r, 200);
    return JSON.parse(r.text);
  }

  async promote(filePath: string) {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/memory/promote'), method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ file_path: filePath }) });
    this.assertStatus('Promote', r, 200);
  }

  async writeWorking(filename: string, content: string, confidence: 'high' | 'medium' | 'low' = 'medium', maturity: 'seed' | 'sapling' = 'seed'): Promise<{written: string; filename_used: string}> {
    await this.ensureConnected();
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/memory/write_working'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ filename, content, vault_path: vaultPath, confidence, maturity }) 
    });
    this.assertStatus('Write working note', r, 200);
    return JSON.parse(r.text);
  }

  async promoteText(text: string, title: string, pageType: 'entity' | 'concept' | 'comparison' | 'analysis', references: string[] = []): Promise<any> {
    await this.ensureConnected();
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/promote'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ text, title, page_type: pageType, references, vault_path: vaultPath }) 
    });
    this.assertStatus('Promote text', r, 200);
    return JSON.parse(r.text);
  }

  async attachBlock(blockName: string): Promise<{attached: string; token_est: number}> {
    await this.ensureConnected();
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/memory/attach_block'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ block_name: blockName, vault_path: vaultPath }) 
    });
    this.assertStatus('Attach block', r, 200);
    return JSON.parse(r.text);
  }

  async listBlocks(): Promise<{attached_blocks: Array<{name: string; token_est: number}>; total_tokens: number}> {
    await this.ensureConnected();
    const r = await requestUrl({ url: this.getUrl('/memory/list_blocks'), method: 'GET', headers: this.getHeaders() });
    this.assertStatus('List blocks', r, 200);
    return JSON.parse(r.text);
  }

  async syncFiles(paths: string[]): Promise<{synced: number; failed: number; errors: string[]}> {
    await this.ensureConnected();
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/sync'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ paths, vault_path: vaultPath }) 
    });
    this.assertStatus('Sync files', r, 200);
    return JSON.parse(r.text);
  }

  async triggerSync(filePath: string): Promise<{success: boolean; message: string}> {
    await this.ensureConnected();
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/sync/file'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ file_path: filePath, vault_path: vaultPath }) 
    });
    this.assertStatus('Trigger sync', r, 200);
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
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/lint'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ vault_path: vaultPath, stale_days: staleDays, file_report: true }) 
    });
    this.assertStatus('Vault lint', r, 200);
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
    this.assertStatus('Bulk import', r, 201);
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
    this.assertStatus('Bulk export', r, 200);
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
    this.assertStatus('Bulk delete', r, 200);
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
    const vaultPath = this.app?.vault.getRoot().path || '';
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
    this.assertStatus('Register session', r, 201);
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
    this.assertStatus('List sessions', r, 200);
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
    this.assertStatus('Close session', r, 200);
    return JSON.parse(r.text);
  }

  // ── Context Lookup ──────────────────────────────────────────────────────────

  /**
   * Look up context blocks by keyword/date.
   * Uses the /search endpoint with keyword strategy to find relevant vault content.
   * Returns blocks in a shape compatible with DailyNotesView.
   */
  async triggerLookup(query: string): Promise<{blocks: Array<{name: string; path: string; content: string}>}> {
    await this.ensureConnected();
    const vaultPath = this.app?.vault.getRoot().path || '';
    const r = await requestUrl({ 
      url: this.getUrl('/search'), 
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ 
        query, 
        strategies: ['keyword'],
        limit: 20,
        vault_path: vaultPath
      }) 
    });
    this.assertStatus('Trigger lookup', r, 200);
    const data = JSON.parse(r.text);
    // Normalize to the blocks shape expected by DailyNotesView
    const blocks = (data.results || []).map((result: SearchResult) => ({
      name: result.title || this.extractTitle(result.file_path),
      path: result.file_path || '',
      content: result.content || result.snippet || ''
    }));
    return { blocks };
  }
}
