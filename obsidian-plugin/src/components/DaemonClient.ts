import { requestUrl } from 'obsidian';

const DAEMON_URL = 'http://localhost:5051';

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

export class DaemonClient {
  private status: 'connected' | 'offline' | 'checking' = 'checking';

  getStatus() { return this.status; }

  async checkHealth() {
    this.status = 'checking';
    try {
      const r = await requestUrl({ url: `${DAEMON_URL}/health`, method: 'GET' });
      this.status = r.status === 200 ? 'connected' : 'offline';
    } catch { this.status = 'offline'; }
    return this.status === 'connected';
  }

  async search(query: string, strategies = ['vector']) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({ url: `${DAEMON_URL}/search`, method: 'POST', body: JSON.stringify({ query, top_k: 10, include_graph: strategies.includes('graph'), include_temporal: strategies.includes('temporal') }) });
    if (r.status !== 200) throw new Error(`Search failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async getGraph(entity: string, relationship?: string): Promise<{ paths?: Array<{ target: string; relationship: string; edge_source: string }>; nodes?: GraphNode[]; edges?: GraphEdge[] }> {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const params = new URLSearchParams({ entity });
    if (relationship) params.set('relationship', relationship);
    const r = await requestUrl({ url: `${DAEMON_URL}/graph?${params.toString()}`, method: 'GET' });
    if (r.status !== 200) throw new Error(`Graph failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async temporal(entity: string, startDate: string, endDate: string) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const params = new URLSearchParams({ entity, start: startDate, end: endDate });
    const r = await requestUrl({ url: `${DAEMON_URL}/temporal?${params.toString()}`, method: 'GET' });
    if (r.status !== 200) throw new Error(`Temporal failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async cognify(content: string): Promise<CognifyResult> {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({ url: `${DAEMON_URL}/cognify`, method: 'POST', body: JSON.stringify({ content }) });
    if (r.status !== 200) throw new Error(`Cognify failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async promote(payload: PromotePayload) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({
      url: `${DAEMON_URL}/promote`,
      method: 'POST',
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
