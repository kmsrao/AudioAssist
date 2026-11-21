import { Component, computed, inject, input, output, signal } from '@angular/core';

import { evidenceValue, fmtDb, fmtHz, fmtPct, fmtTime, humanKey, pad2 } from '../format';
import { Recommendation } from '../models';
import { ApiService, errorMessage } from '../services/api.service';

@Component({
  selector: 'app-rec-detail-dialog',
  template: `
    <div class="overlay" (click)="close.emit()">
      <div class="dialog" (click)="$event.stopPropagation()" role="dialog" aria-modal="true">
        <div class="dialog-head">
          <span class="sev" [class]="'sev ' + rec().severity">{{ rec().severity }}</span>
          <h2>CH{{ pad(rec().channel) }} {{ rec().channel_name }} — {{ rec().issue }}</h2>
          <span class="grow"></span>
          <span class="tag">{{ rec().status }}</span>
          <button class="btn ghost" (click)="close.emit()" aria-label="Close">✕</button>
        </div>
        <div class="dialog-body">
          @if (error()) { <div class="notice bad">{{ error() }}</div> }
          <div class="two">
            <div>
              <h3>Suggested action</h3>
              <div class="box">
                <div class="dim">{{ rec().control }}</div>
                <div class="big">{{ rec().summary }}</div>
                <ol>@for (s of rec().actions; track $index) { <li>{{ s }}</li> }</ol>
                @if (rec().fallback; as fb) { <div class="notice" style="margin-top:6px">Fallback: {{ fb['note'] }}</div> }
                @for (c of rec().caveats; track $index) { <div class="notice warn" style="margin-top:6px">{{ c }}</div> }
              </div>
              <h3>Why</h3>
              <p>{{ rec().explanation }}</p>
              <p><span class="dim">Expected improvement:</span> {{ rec().expected_improvement }}</p>

              <h3>Confidence — {{ rec().confidence_label }} ({{ pct(rec().confidence) }})</h3>
              <table class="tbl">
                <tbody>
                  @for (k of confKeys(); track k) {
                    <tr><td class="dim">{{ human(k) }}</td><td class="num">{{ val(conf()[k]) }}</td></tr>
                  }
                </tbody>
              </table>
            </div>
            <div>
              <h3>Supporting analysis</h3>
              <table class="tbl">
                <tbody>
                  <tr><td class="dim">Measured</td><td class="num">{{ val(rec().supporting_analysis.measured.value) }} {{ rec().supporting_analysis.measured.unit }}</td></tr>
                  @for (k of evKeys(); track k) {
                    <tr><td class="dim">{{ human(k) }}</td><td class="num">{{ val(ev()[k]) }}</td></tr>
                  }
                </tbody>
              </table>

              <h3>Before / after</h3>
              @if (rec().before || rec().after) {
                <table class="tbl">
                  <thead><tr><th></th><th>Before</th><th>After</th><th>Δ</th></tr></thead>
                  <tbody>
                    <tr><td class="dim">Measured metric</td><td class="num">{{ val(rec().before?.metric) }}</td><td class="num">{{ val(rec().after?.metric) }}</td>
                      <td class="num">{{ delta(rec().before?.metric, rec().after?.metric) }}</td></tr>
                    <tr><td class="dim">Condition detected</td><td>{{ yesno(rec().before?.diagnostic_active) }}</td><td>{{ yesno(rec().after?.diagnostic_active) }}</td><td></td></tr>
                    <tr><td class="dim">Signal (RMS p90)</td><td class="num">{{ db(rec().before?.signal_dbfs) }}</td><td class="num">{{ db(rec().after?.signal_dbfs) }}</td>
                      <td class="num">{{ delta(rec().before?.signal_dbfs, rec().after?.signal_dbfs) }}</td></tr>
                    @for (b of bandRows(); track b.label) {
                      <tr><td class="dim">Band {{ b.label }}</td><td class="num">{{ db(b.before) }}</td><td class="num">{{ db(b.after) }}</td><td class="num">{{ delta(b.before, b.after) }}</td></tr>
                    }
                  </tbody>
                </table>
                @if (rec().verification?.note) { <div class="notice" style="margin-top:6px">{{ rec().verification?.note }}</div> }
              } @else {
                <div class="faint">Mark the recommendation as applied (or press Re-analyse) to capture a before/after comparison.</div>
              }

              <h3>History</h3>
              <table class="tbl"><tbody>
                @for (h of rec().history; track $index) {
                  <tr><td class="num dim">{{ time(h.at) }}</td><td>{{ h.from ? h.from + ' → ' : '' }}<b>{{ h.to }}</b></td><td class="dim">{{ h.note ?? '' }}</td></tr>
                }
              </tbody></table>
            </div>
          </div>
        </div>
        <div class="dialog-foot">
          @if (active()) {
            <button class="btn" (click)="act('reanalyze')">Re-analyse</button>
            <button class="btn" (click)="act('dismiss')">Dismiss</button>
            <button class="btn ok" (click)="act('apply')">Mark applied</button>
          }
          <button class="btn primary" (click)="close.emit()">Close</button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .two{display:grid;grid-template-columns:1fr 1fr;gap:20px}
    h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);margin:14px 0 6px}
    h3:first-child{margin-top:0}
    .box{background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:10px}
    .big{font-size:17px;font-weight:800;margin:4px 0}
    ol{margin:6px 0 0;padding-left:18px} li{margin:3px 0}
    p{margin:4px 0}
    @media (max-width:800px){.two{grid-template-columns:1fr}}
  `],
})
export class RecDetailDialog {
  private api = inject(ApiService);
  readonly rec = input.required<Recommendation>();
  readonly close = output<void>();
  readonly error = signal<string | null>(null);

  readonly active = computed(() => ['new', 'pending', 'applied'].includes(this.rec().status));
  readonly ev = computed(() => this.rec().supporting_analysis?.evidence ?? {});
  readonly evKeys = computed(() => Object.keys(this.ev()).filter((k) => !['candidates', 'other_candidates'].includes(k)));
  readonly conf = computed(() => this.rec().supporting_analysis?.confidence ?? {});
  readonly confKeys = computed(() => Object.keys(this.conf()));
  readonly bandRows = computed(() => {
    const b = this.rec().before?.bands ?? [];
    const a = this.rec().after?.bands ?? [];
    const labels = (b.length ? b : a).map((x) => x.label);
    return labels.map((label) => ({
      label,
      before: b.find((x) => x.label === label)?.level_db ?? null,
      after: a.find((x) => x.label === label)?.level_db ?? null,
    }));
  });

  pad = pad2;
  pct = fmtPct;
  hz = fmtHz;
  time = fmtTime;
  human = humanKey;
  val = evidenceValue;
  db = (v: number | null | undefined) => fmtDb(v, 1);
  yesno = (v: boolean | undefined) => (v === undefined ? '—' : v ? 'yes' : 'no');
  delta(a: number | null | undefined, b: number | null | undefined): string {
    if (a === null || a === undefined || b === null || b === undefined) return '';
    return fmtDb(b - a, 1, true);
  }

  async act(kind: 'apply' | 'dismiss' | 'reanalyze'): Promise<void> {
    this.error.set(null);
    try {
      const id = this.rec().id;
      if (kind === 'apply') await this.api.markApplied(id);
      else if (kind === 'dismiss') await this.api.dismiss(id);
      else await this.api.reanalyze(id);
      this.close.emit();
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }
}
