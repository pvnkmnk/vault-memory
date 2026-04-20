import { requestUrl } from 'obsidian';

const DAEMON_URL = 'http://localhost:5051';

interface SearchResult { file_path: string; content: string; score: number; }
interface GraphNode { id: string; label: string; connections: number; }
interface GraphEdge { source: string; target: string; }
interface CognifyResult { triples: Array<[string, string, string]>; }

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
    const r = await requestUrl({ url: `${DAEMON_URL}/search`, method: 'POST', body: JSON.stringify({ query, strategies, limit: 10 }) });
    if (r.status !== 200) throw new Error(`Search failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async getGraph(depth = 2) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({ url: `${DAEMON_URL}/graph`, method: 'POST', body: JSON.stringify({ depth, limit: 50 }) });
    if (r.status !== 200) throw new Error(`Graph failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async temporal(startDate: string, endDate: string) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({ url: `${DAEMON_URL}/temporal`, method: 'POST', body: JSON.stringify({ start_date: startDate, end_date: endDate }) });
    if (r.status !== 200) throw new Error(`Temporal failed: ${r.status}`);
    return JSON.parse(r.text).results || [];
  }

  async cognify(content: string): Promise<CognifyResult> {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({ url: `${DAEMON_URL}/cognify`, method: 'POST', body: JSON.stringify({ content }) });
    if (r.status !== 200) throw new Error(`Cognify failed: ${r.status}`);
    return JSON.parse(r.text);
  }

  async promote(filePath: string) {
    if (this.status !== 'connected') { await this.checkHealth(); if (this.status !== 'connected') throw new Error('Daemon offline'); }
    const r = await requestUrl({ url: `${DAEMON_URL}/memory/promote`, method: 'POST', body: JSON.stringify({ file_path: filePath }) });
    if (r.status !== 200) throw new Error(`Promote failed: ${r.status}`);
  }
}