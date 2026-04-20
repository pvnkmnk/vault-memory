import { DaemonClient } from '../components/DaemonClient';

export class StatusBar {
  private client: DaemonClient;
  private el: HTMLElement | null = null;

  constructor(client: DaemonClient) { this.client = client; }

  async render(el: HTMLElement) {
    this.el = el;
    el.addClass('vp-status-bar');
    await this.update();
    setInterval(() => this.update(), 30000);
  }

  async update() {
    if (!this.el) return;
    const status = this.client.getStatus();
    if (status === 'checking') {
      this.el.setText('Daemon: checking...');
      await this.client.checkHealth();
      await this.update();
    } else if (status === 'connected') {
      this.el.setText('Daemon: connected');
      this.el.addClass('vp-status-online'); this.el.removeClass('vp-status-offline');
    } else {
      this.el.setText('Daemon: offline');
      this.el.addClass('vp-status-offline'); this.el.removeClass('vp-status-online');
    }
  }
}