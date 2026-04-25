import { App } from 'obsidian';
import { DaemonClient } from '../components/DaemonClient';

export class StatusBar {
  private client: DaemonClient;
  private el: HTMLElement | null = null;

  constructor(app: App, client: DaemonClient) {
    this.client = client;
  }

  async render(el: HTMLElement) {
    this.el = el;
    el.addClass('vp-status-bar');
    await this.update();
  }

  async update() {
    if (!this.el) return;
    const status = this.client.getStatus();
    if (status === 'checking') {
      this.el.setText('Daemon: checking...');
      await this.client.checkHealth();
      return;
    }

    const isConnected = this.client.getStatus() === 'connected';
    this.el.setText(isConnected ? 'Daemon: connected' : 'Daemon: offline');
    this.el.toggleClass('vp-status-online', isConnected);
    this.el.toggleClass('vp-status-offline', !isConnected);
  }
}
