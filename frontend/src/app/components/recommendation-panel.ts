import { Component, computed, inject, input, output, signal } from '@angular/core';

import { categoryLabel, fmtAgo, fmtDb, fmtHz, fmtPct, pad2 } from '../format';
import { Recommendation } from '../models';
import { ApiService, errorMessage } from '../services/api.service';

/** Ranked, actionable recommendations. Buttons only record operator decisions - nothing touches the audio. */
@Component({
  selector: 'app-recommendation-panel',
  template: `
    <section class="panel">
      <div class="panel-head">
        <span class="panel-title">Recommendations</span>
        <span class="faint num">{{ visible().length }}</span>
        <span class="grow"></span>
        <button class="btn sm ghost" [class.active]="onlyFocus()" (click)="onlyFocus.set(!onlyFocus())"
                [disabled]="!focus()" title="Show only the focused channel">CH{{ focus() ? pad(focus()!) : '--' }} only</button>
      </div>
      @if (error()) { <div class="notice bad" style="margin:8px">{{ error() }}</div> }
      <div class="list">
        @for (r of visible(); track r.id) {
          <article class="card" [class]="'card s-' + r.severity" [class.applied]="r.status === 'applied'">
            <header class="row">
              <span class="sev" [class]="'sev ' + r.severity">{{ r.severity }}</span>
              <button class="chlink num" (click)="focusChange.emit(r.channel)" title="Show this channel">CH{{ pad(r.channel) }}</button>
              <span class="cname ellipsis grow">{{ r.channel_name }}</span>
              <span class="status" [class]="'status st-' + r.status">{{ statusLabel(r) }}</span>
            </header>
            <div class="issue">{{ r.issue }}</div>

            <div class="action">
              <div class="ctl">{{ r.control }}</div>
              <div class="params">
                @if (r.filter_type === 'high_pass') {
                  <div class="p"><span>HPF</span><b class="num">{{ hz(r.hpf_hz) }}</b></div>
                  <div class="p"><span>Slope</span><b>{{ r.hpf_slope }}</b></div>
                } @else if (r.filter_type === 'gain') {
                  <div class="p"><span>Gain</span><b class="num">{{ signed(r.gain_change_db) }} dB</b></div>
                } @else {
                  @if (r.target_hz) { <div class="p"><span>Freq</span><b class="num">{{ hz(r.target_hz) }}</b></div> }
                  @if (r.gain_db !== null) { <div class="p"><span>Adjust</span><b class="num">{{ signed(r.gain_db) }} dB</b></div> }
                  @if (r.q) { <div class="p"><span>Q</span><b class="num">{{ r.q }}</b></div> }
                }
                <div class="p conf" [title]="'Confidence ' + pct(r.confidence) + ' (evidence, persistence, context - see details)'">
                  <span>Confidence</span><b>{{ r.confidence_label }} <small class="num">{{ pct(r.confidence) }}</small></b>
                  <div class="cbar"><div [style.width.%]="r.confidence * 100"></div></div>
                </div>
              </div>
            </div>

            <p class="why">{{ r.explanation }}</p>
            <p class="exp"><span class="dim">Expected:</span> {{ r.expected_improvement }}</p>

            @if (r.verification && r.status === 'applied') {
              <div class="verify" [class]="'verify v-' + r.verification.state">
                @switch (r.verification.state) {
                  @case ('pending') { Verifying the adjustment… }
                  @case ('persists') { Issue still detected after the adjustment. }
                  @case ('improved') { Improved ({{ signed(r.verification.delta ?? null) }}) but still detected. }
                  @case ('resolved') { Resolved. }
                }
              </div>
            }

            <footer class="row wrap">
              @if (r.status !== 'applied') {
                <button class="btn ok sm" (click)="apply(r)" title="I made this adjustment on the console">Mark applied</button>
              } @else {
                <button class="btn sm" (click)="apply(r)" title="I made a further adjustment">Applied again</button>
              }
              <button class="btn sm" (click)="dismiss(r)">Dismiss</button>
              <button class="btn sm" (click)="reanalyze(r)" title="Restart evidence collection and compare before/after">Re-analyse</button>
              <span class="grow"></span>
              <span class="faint small">{{ ago(r.created_at) }}</span>
              <button class="btn sm ghost" (click)="details.emit(r)">Details</button>
            </footer>
          </article>
        } @empty {
          <div class="empty">
            <div class="ok-big">✓</div>
            No active recommendations{{ onlyFocus() ? ' for this channel' : '' }}.
            <div class="faint small">Diagnostics require persistent evidence before anything is suggested.</div>
          </div>
        }

        @if (recent().length) {
          <div class="recent">
            <div class="panel-title" style="margin:8px 4px">Recently closed</div>
            @for (r of recent(); track r.id) {
              <div class="rrow" (click)="details.emit(r)">
                <span class="status" [class]="'status st-' + r.status">{{ r.status }}</span>
                <span class="num dim">CH{{ pad(r.channel) }}</span>
                <span class="ellipsis grow">{{ cat(r.category) }}</span>
                <span class="faint small">{{ ago(r.closed_at) }}</span>
              </div>
            }
          </div>
        }
      </div>
    </section>
  `,
  styles: [`
    :host{display:block;min-height:0} section{height:100%}
    .list{overflow:auto;padding:8px;display:flex;flex-direction:column;gap:8px;min-height:0;flex:1}
    .card{background:var(--panel-2);border:1px solid var(--border);border-left:4px solid var(--sev-low);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:6px}
    .card.s-medium{border-left-color:var(--sev-medium)} .card.s-high{border-left-color:var(--sev-high)} .card.s-critical{border-left-color:var(--sev-critical)}
    .card.applied{opacity:.85;border-style:dashed;border-left-style:solid}
    .chlink{background:none;border:none;color:var(--accent);font-weight:800;cursor:pointer;padding:0}
    .cname{font-weight:700}
    .issue{font-size:17px;font-weight:800}
    .action{background:#0e141d;border:1px solid var(--border);border-radius:6px;padding:8px}
    .ctl{font-size:12px;color:var(--text-dim);margin-bottom:6px}
    .params{display:flex;gap:14px;flex-wrap:wrap}
    .p{display:flex;flex-direction:column}
    .p span{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint)}
    .p b{font-size:20px;line-height:1.1}
    .p.conf b{font-size:14px;text-transform:capitalize}
    .cbar{height:4px;background:#1b2433;border-radius:2px;margin-top:3px;width:90px}
    .cbar div{height:100%;background:var(--accent);border-radius:2px}
    .why{margin:0;font-size:13px;color:#c8d2e0}
    .exp{margin:0;font-size:13px}
    .verify{font-size:12px;padding:5px 8px;border-radius:5px;background:#10233a;border:1px solid #1f456f}
    .v-persists{background:#2b2210;border-color:#6b5316} .v-resolved{background:#0f2b20;border-color:#1c5b45}
    .status{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;padding:2px 6px;border-radius:4px;background:#1d2635;color:var(--text-dim)}
    .st-new{background:#1d4f86;color:#fff} .st-applied{background:#0f4a37;color:#bff5dc} .st-resolved{background:#0f4a37;color:#bff5dc}
    .st-dismissed,.st-expired{background:#262c36;color:#8995a7}
    .small{font-size:11px}
    .ok-big{font-size:28px;color:var(--ok)}
    .recent{border-top:1px solid var(--border);margin-top:4px}
    .rrow{display:flex;gap:8px;align-items:center;padding:4px;border-radius:5px;cursor:pointer;font-size:12px}
    .rrow:hover{background:#151d29}
  `],
})
export class RecommendationPanel {
  private api = inject(ApiService);

  readonly recs = input<Recommendation[]>([]);
  readonly recentClosed = input<Recommendation[]>([]);
  readonly focus = input<number | null>(null);
  readonly focusChange = output<number>();
  readonly details = output<Recommendation>();

  readonly onlyFocus = signal(false);
  readonly error = signal<string | null>(null);

  readonly visible = computed(() => {
    const f = this.focus();
    return this.onlyFocus() && f ? this.recs().filter((r) => r.channel === f) : this.recs();
  });
  readonly recent = computed(() => this.recentClosed().slice(0, 6));

  pad = pad2;
  hz = fmtHz;
  pct = fmtPct;
  cat = categoryLabel;
  ago = (t: number | null) => fmtAgo(t);
  signed = (v: number | null) => fmtDb(v, 1, true);

  statusLabel(r: Recommendation): string {
    return r.status === 'pending' ? 'pending action' : r.status;
  }

  private async run(p: Promise<unknown>): Promise<void> {
    this.error.set(null);
    try {
      await p;
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  apply(r: Recommendation): void { void this.run(this.api.markApplied(r.id)); }
  dismiss(r: Recommendation): void { void this.run(this.api.dismiss(r.id)); }
  reanalyze(r: Recommendation): void { void this.run(this.api.reanalyze(r.id)); }
}
