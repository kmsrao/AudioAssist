import { Component, computed, input, output } from '@angular/core';

import { Status } from '../models';

@Component({
  selector: 'app-top-bar',
  template: `
    <header class="bar">
      <div class="brand">
        <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
          <rect x="2" y="9" width="3" height="8" rx="1.5" fill="#4ea8ff"/><rect x="8" y="4" width="3" height="18" rx="1.5" fill="#4ea8ff"/>
          <rect x="14" y="7" width="3" height="12" rx="1.5" fill="#34d399"/><rect x="20" y="11" width="3" height="4" rx="1.5" fill="#34d399"/>
        </svg>
        <div>
          <div class="name">Live Audio Assist</div>
          <div class="sub">Analysis &amp; advice only · never changes audio</div>
        </div>
      </div>

      <div class="items">
        <span class="pill" [class.ok]="connected()" [class.bad]="!connected()" title="Dashboard ↔ backend connection">
          <span class="dot"></span>{{ connected() ? 'Connected' : 'Disconnected' }}
        </span>
        <span class="pill" [class]="'pill ' + captureClass()" [title]="status()?.capture?.error ?? ''">
          <span class="dot"></span>Capture: {{ captureLabel() }}
        </span>
        <span class="pill device" [title]="deviceTitle()">
          <span class="ellipsis">{{ deviceName() }}</span>
        </span>
        <span class="pill num" title="Sample rate / block size">
          {{ (status()?.sample_rate ?? 0) / 1000 }} kHz · {{ status()?.device?.block_size ?? '—' }}
        </span>
        <span class="pill num" [class]="'pill num ' + sysClass()" title="Analysis load (p95 tick time vs. budget) and process CPU">
          <span class="dot"></span>System {{ status()?.health?.processing?.tick_ms_p95 ?? '—' }} ms · CPU {{ status()?.health?.cpu_percent ?? '—' }}%
        </span>
      </div>

      <div class="actions">
        <button class="btn" (click)="openHistory.emit()">History</button>
        <button class="btn" (click)="openChannels.emit()">Channels</button>
        <button class="btn primary" (click)="openSettings.emit()">Audio &amp; Settings</button>
      </div>
    </header>
  `,
  styles: [`
    .bar{display:flex;align-items:center;gap:16px;padding:10px 16px;border-bottom:1px solid var(--border);background:#0d1118;flex-wrap:wrap}
    .brand{display:flex;align-items:center;gap:10px;min-width:220px}
    .name{font-weight:800;font-size:16px;letter-spacing:.02em}
    .sub{font-size:11px;color:var(--text-faint)}
    .items{display:flex;gap:8px;flex-wrap:wrap;flex:1;align-items:center}
    .device{max-width:340px}
    .actions{display:flex;gap:8px}
  `],
})
export class TopBar {
  readonly status = input<Status | null>(null);
  readonly connected = input(false);
  readonly openSettings = output<void>();
  readonly openHistory = output<void>();
  readonly openChannels = output<void>();

  readonly captureLabel = computed(() => {
    const s = this.status()?.capture?.state;
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : '—';
  });

  readonly captureClass = computed(() => {
    const s = this.status()?.capture?.state;
    return s === 'running' ? 'ok' : s === 'stalled' || s === 'starting' ? 'warn' : 'bad';
  });

  readonly deviceName = computed(() => {
    const d = this.status()?.device;
    if (!d) return 'No audio source';
    return d.device_name ?? 'Audio device';
  });

  readonly deviceTitle = computed(() => {
    const d = this.status()?.device;
    return d ? `${d.device_name} · ${d.hostapi} · ${d.channels} ch` : '';
  });

  readonly sysClass = computed(() => {
    const h = this.status()?.health;
    if (!h) return '';
    if (!h.processing.ok) return 'warn';
    return 'ok';
  });
}
