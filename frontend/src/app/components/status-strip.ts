import { Component, computed, input } from '@angular/core';

import { Status } from '../models';

/** Overall status: what is being analysed, what needs attention, and whether the system is healthy. */
@Component({
  selector: 'app-status-strip',
  template: `
    <div class="strip">
      <div class="kpi"><div class="v num">{{ status()?.channels_analyzed ?? 0 }}<span class="of">/{{ status()?.channels_total ?? 0 }}</span></div><div class="l">Channels analysed</div></div>
      <div class="kpi" [class.alert]="(status()?.channels_attention ?? 0) > 0">
        <div class="v num">{{ status()?.channels_attention ?? 0 }}</div><div class="l">Need attention</div></div>
      <div class="kpi" [class.alert]="(status()?.active_recommendations ?? 0) > 0">
        <div class="v num">{{ status()?.active_recommendations ?? 0 }}</div><div class="l">Active recommendations</div></div>
      <div class="kpi health">
        <div class="row"><span class="led act" [class.on]="captureOk()"></span><b>Capture</b>
          <span class="dim num">{{ captureDetail() }}</span></div>
        <div class="row"><span class="led act" [class.on]="procOk()"></span><b>Processing</b>
          <span class="dim num">{{ procDetail() }}</span></div>
      </div>
    </div>
  `,
  styles: [`
    .strip{display:grid;grid-template-columns:repeat(3,minmax(110px,1fr)) minmax(260px,2.2fr);gap:8px}
    .kpi{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:8px 12px}
    .kpi .v{font-size:24px;font-weight:800;line-height:1.1}
    .kpi .of{font-size:14px;color:var(--text-faint);font-weight:600}
    .kpi .l{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.06em}
    .kpi.alert{border-color:#6b4316}
    .kpi.alert .v{color:var(--high)}
    .health{display:flex;flex-direction:column;justify-content:center;gap:4px;font-size:12px}
    .health .led:not(.on){background:#3a1520;border-color:#7a2335}
    @media (max-width:900px){.strip{grid-template-columns:repeat(3,1fr)} .health{grid-column:1/-1}}
  `],
})
export class StatusStrip {
  readonly status = input<Status | null>(null);

  readonly captureOk = computed(() => !!this.status()?.health?.capture?.ok);
  readonly procOk = computed(() => !!this.status()?.health?.processing?.ok);

  readonly captureDetail = computed(() => {
    const c = this.status()?.health?.capture;
    if (!c) return '—';
    if (c.error) return c.error;
    const st = c.stats;
    if (!st) return c.state;
    return `${c.state} · ${st.callbacks_per_s}/s callbacks · ${st.input_overflows} overflows`;
  });

  readonly procDetail = computed(() => {
    const p = this.status()?.health?.processing;
    if (!p) return '—';
    return `${p.analysis_rate_hz} Hz · FFT ${p.fft_size} · p95 ${p.tick_ms_p95}/${p.budget_ms} ms · ${p.overruns} overruns`;
  });
}
