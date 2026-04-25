import { requestUrl } from 'obsidian';

interface SearchResult { file_path: string; content: string; score: number; }
interface GraphNode { id: string; label: string; connections: number; }
interface GraphEdge { source: string; target: string; }
interface CognifyResult { triples: Array<[string, string, string]>; }
interface PromotePayload {
  text: string;
  title: string;
  pageType: 'entity' | 'concept' | 'comparison' | 'analysis';
  references?: string[];
  vaultPath: string;
}

interface DaemonClientConfig {
  daemonUrl: string;
  apiKey: string;
}

export class DaemonClient {
  private status: 'connected' | 'offline' | 'checking' = 'checking';
  private daemonUrl: string;
  private apiKey: string;

  constructor(config: DaemonClientConfig) {
    this.daemonUrl = this.normalizeUrl(config.daemonUrl);
    this.apiKey = config.apiKey.trim();
  }

  getStatus() { return this.status; }

  updateConfig(config: Partial<DaemonClientConfig>) {
    if (typeof config.daemonUrl === 'string') {
      this.daemonUrl = this.normalizeUrl(config.daemonUrl);
    }
    if (typeof config.apiKey === 'string') {
      this.apiKey = config.apiKey.trim();
    }
    this.status = 'checking';
  }

  getDaemonUrl() {
    return this.daemonUrl;
  }

  getHasApiKey() {
    return this.apiKey.length > 0;
  }

  private normalizeUrl(value: string): string {
    const trimmed = value.trim();
    return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
  }

  private buildHeaders(includeJson: boolean): Record<string, string> {
    const headers: Record<string, string> = {};
    if (includeJson) {
      headers['Content-Type'] = 'application/json';
    }
    if (this.apiKey) {
      headers['x-api-key'] = this.apiKey;
    }
    return headers;
  }

  async checkHealth() {
    this.status = 'checking';
    try {
      const r = await requestUrl({
        url: `${this.daemonUrl}/health`,
        method: 'GET',
        headers: this.buildHeaders(false),
      });
      this.status = r.status === 200 ? 'connected' : 'offline';
    } catch { this.status = 'offline'; }
    return this.status === 'connected';
  }

  async search(query: string, strategies = ['vector']) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({
      url: `${this.daemonUrl}/search`,
      method: 'POST',
      headers: this.buildHeaders(true),
      body: JSON.stringify({
        query,
        top_k: 10,
        include_graph: strategies.includes('graph'),
        include_temporal: strategies.includes('temporal'),
      }),
    });
    if (r.status !== 200) throw new Error(`Search failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async getGraph(entity: string, relationship?: string): Promise<{ paths?: Array<{ target: string; relationship: string; edge_source: string }>; nodes?: GraphNode[]; edges?: GraphEdge[] }> {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const params = new URLSearchParams({ entity });
    if (relationship) params.set('relationship', relationship);
    const r = await requestUrl({
      url: `${this.daemonUrl}/graph?${params.toString()}`,
      method: 'GET',
      headers: this.buildHeaders(false),
    });
    if (r.status !== 200) throw new Error(`Graph failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async temporal(entity: string, startDate: string, endDate: string) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const params = new URLSearchParams({ entity, start: startDate, end: endDate });
    const r = await requestUrl({
      url: `${this.daemonUrl}/temporal?${params.toString()}`,
      method: 'GET',
      headers: this.buildHeaders(false),
    });
    if (r.status !== 200) throw new Error(`Temporal failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async cognify(content: string): Promise<CognifyResult> {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({
      url: `${this.daemonUrl}/cognify`,
      method: 'POST',
      headers: this.buildHeaders(true),
      body: JSON.stringify({ content }),
    });
    if (r.status !== 200) throw new Error(`Cognify failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async promote(payload: PromotePayload) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({
      url: `${this.daemonUrl}/promote`,
      method: 'POST',
      headers: this.buildHeaders(true),
      body: JSON.stringify({
        text: payload.text,
        title: payload.title,
        page_type: payload.pageType,
        references: payload.references || [],
        vault_path: payload.vaultPath,
      }),
    });
    if (r.status !== 200) throw new Error(`Promote failed: ${r.status}`);
    return JSON.parse(r.text);
  }
}
