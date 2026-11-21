import { Component, computed, inject, input, signal } from '@angular/core';

import { BandChart } from '../charts/band-chart';
import { SpectrumChart, SpectrumMarker } from '../charts/spectrum-chart';
import { Spectrogram } from '../charts/spectrogram';
import { categoryLabel, fmtDb, fmtHz, fmtPct, meterFraction, pad2, profileName } from '../format';
import { Analysis, Diagnostic, Grid, Profile, Recommendation } from '../models';
import { ApiService } from '../services/api.service';

const SEV_COLOR: Record<string, string> = { low: '#60a5fa', medium: '#fbbf24', high: '#fb923c', critical: '#f43f5e' };

@Component({
  selector: 'app-analysis-panel',
  imports: [SpectrumChart, Spectrogram, BandChart],
  template: `
    <section class="panel">
      <div class="panel-head">
        <span class="panel-title">Analysis</span>
        @if (a(); as an) {
          <span class="chname"><span class="num">CH{{ pad(an.channel) }}</span> {{ an.name }}</span>
          <span class="tag">{{ profName(an.profile) }}</span>
          <span class="grow"></span>
          <button class="btn sm" (click)="reanalyze(an.channel)" title="Restart evidence collection for this channel">Re-analyse</button>
        } @else {
          <span class="grow"></span>
        }
      </div>

      @if (!focus()) {
        <div class="empty">Select a channel to view its analysis.</div>
      } @else if (!a()) {
        <div class="empty">Waiting for analysis data for CH{{ pad(focus()!) }}…</div>
      } @else {
        <div class="body">
          <div class="meters">
            <div class="m big"><div class="l">RMS</div><div class="v num">{{ db(a()!.rms_dbfs) }}<small>dBFS</small></div>
              <div class="meter"><div class="fill" [style.width.%]="frac(a()!.rms_dbfs) * 100"></div></div></div>
            <div class="m big"><div class="l">Peak (hold)</div><div class="v num">{{ db(a()!.peak_hold_dbfs) }}<small>dBFS</small></div>
              <div class="meter"><div class="fill" [style.width.%]="frac(a()!.peak_hold_dbfs) * 100"></div></div></div>
            <div class="m"><div class="l">Clipping</div>
              <div class="v row"><span class="led" [class.on]="a()!.clipping"></span>
                <span [class.clip]="a()!.clipping">{{ a()!.clipping ? a()!.clip_events_5s + ' events / 5 s' : 'None' }}</span></div></div>
            <div class="m"><div class="l">Crest</div><div class="v num">{{ db(a()!.crest_db) }} dB</div></div>
            <div class="m"><div class="l">Noise floor (est.)</div><div class="v num">{{ db(a()!.noise_floor_dbfs) }}</div></div>
            <div class="m"><div class="l">SNR (est.)</div><div class="v num" title="Only shown when quiet and active periods are distinguishable">{{ db(a()!.snr_estimate_db) }}</div></div>
            <div class="m"><div class="l">Centroid</div><div class="v num">{{ hz(a()!.stats.centroid_hz) }}</div></div>
            <div class="m"><div class="l">Activity</div><div class="v num">{{ pct(a()!.activity_ratio) }}</div></div>
          </div>

          <div class="charts">
            <div class="chartbox spectrum">
              <div class="cap">Spectrum <span class="faint">· smoothed (blue), instantaneous max (grey), persistent peaks (yellow), recommendation targets (dashed)</span></div>
              <app-spectrum-chart class="fill" [freqs]="grid()?.freqs ?? []" [db]="a()!.spectrum.db" [maxhold]="a()!.spectrum.maxhold"
                                  [peaks]="a()!.peaks" [markers]="markers()" />
            </div>
            <div class="split">
              <div class="chartbox">
                <div class="cap">Spectrogram <span class="faint">· last {{ historyS() }} s</span></div>
                <app-spectrogram class="fill" [columns]="spectro()" [version]="spectroVersion()" [history]="history()"
                                 [freqs]="grid()?.spectrogram_freqs ?? []" />
              </div>
              <div class="chartbox">
                <div class="cap row">Band energy
                  <span class="grow"></span>
                  <button class="btn sm ghost" [class.active]="bandMode() === 'fast'" (click)="bandMode.set('fast')">Live</button>
                  <button class="btn sm ghost" [class.active]="bandMode() === 'long'" (click)="bandMode.set('long')" title="Long-term average of active signal (what diagnostics use)">Long-term</button>
                </div>
                <app-band-chart class="fill" [bands]="bandMode() === 'fast' ? a()!.bands : a()!.bands_long" [highlight]="targetFreqs()" />
              </div>
            </div>
          </div>

          <div class="lower">
            <div>
              <div class="cap">Persistent narrow peaks</div>
              @if (a()!.peaks.length) {
                <table class="tbl"><thead><tr><th>Freq</th><th>Level</th><th>Prom.</th><th>Q</th><th>Persist.</th><th></th></tr></thead>
                  <tbody>@for (p of a()!.peaks; track p.id) {
                    <tr><td class="num">{{ hz(p.freq_hz) }}</td><td class="num">{{ db(p.level_db) }}</td><td class="num">{{ db(p.prominence_db) }} dB</td>
                      <td class="num">{{ p.q }}</td><td class="num">{{ pct(p.persistence) }} · {{ p.observed_s }}s</td>
                      <td>@if (p.harmonic) {<span class="tag">harmonic</span>}</td></tr>
                  }</tbody></table>
              } @else { <div class="faint small">None - no stable narrowband peaks.</div> }
            </div>
            <div>
              <div class="cap">Diagnostics on this channel</div>
              @for (d of channelDiags(); track d.id) {
                <div class="diag" [class.supp]="!!d.suppressed_by">
                  <span class="sev" [class]="'sev ' + d.severity">{{ d.severity }}</span>
                  <span class="grow">{{ d.title }} @if (d.target_hz) {<span class="dim num">· {{ hz(d.target_hz) }}</span>}</span>
                  <span class="num dim">{{ pct(d.confidence) }}</span>
                  @if (d.suppressed_by) { <span class="tag" [title]="'Not recommended while ' + cat(d.suppressed_by) + ' is active'">after {{ cat(d.suppressed_by) }}</span> }
                </div>
              } @empty { <div class="faint small">No active diagnostics.</div> }
            </div>
          </div>
        </div>
      }
    </section>
  `,
  styles: [`
    :host{display:block;min-height:0} section{height:100%}
    .chname{font-weight:800;font-size:15px} .chname .num{color:var(--accent)}
    .body{display:flex;flex-direction:column;gap:10px;padding:10px 12px;overflow:auto;min-height:0}
    .meters{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:8px}
    .m{background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:6px 10px}
    .m.big{grid-column:span 2}
    .m .l{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-faint)}
    .m .v{font-size:16px;font-weight:700;margin:2px 0}
    .m.big .v{font-size:22px}
    .m small{font-size:11px;color:var(--text-faint);margin-left:4px;font-weight:600}
    .clip{color:var(--critical);font-weight:800}
    .charts{display:flex;flex-direction:column;gap:8px}
    .chartbox{background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:6px 8px;display:flex;flex-direction:column}
    .chartbox .fill{flex:1;min-height:0}
    .spectrum{height:300px}
    .split{display:grid;grid-template-columns:1.3fr 1fr;gap:8px;height:200px}
    .cap{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);font-weight:700;margin-bottom:4px}
    .cap .faint{text-transform:none;letter-spacing:0;font-weight:400}
    .lower{display:grid;grid-template-columns:1fr 1fr;gap:12px}
    .diag{display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #1a2230;font-size:13px}
    .diag.supp{opacity:.55}
    .small{font-size:12px}
    @media (max-width:1100px){.split,.lower{grid-template-columns:1fr}.split{height:auto}.split .chartbox{height:190px}}
  `],
})
export class AnalysisPanel {
  private api = inject(ApiService);

  readonly analysis = input<Analysis | null>(null);
  readonly focus = input<number | null>(null);
  readonly grid = input<Grid | null>(null);
  readonly profiles = input<Profile[]>([]);
  readonly recs = input<Recommendation[]>([]);
  readonly diagnostics = input<Diagnostic[]>([]);
  readonly spectro = input<number[][]>([]);
  readonly spectroVersion = input(0);
  readonly history = input(120);
  readonly analysisRate = input(10);

  readonly bandMode = signal<'fast' | 'long'>('fast');

  readonly a = computed(() => {
    const an = this.analysis();
    return an && an.channel === this.focus() ? an : null;
  });

  readonly channelRecs = computed(() => this.recs().filter((r) => r.channel === this.focus()));
  readonly channelDiags = computed(() => this.diagnostics().filter((d) => d.channel === this.focus()));
  readonly targetFreqs = computed(() => this.channelRecs().map((r) => r.target_hz ?? r.hpf_hz).filter((f): f is number => !!f));
  readonly historyS = computed(() => Math.round(this.history() / this.analysisRate()));

  readonly markers = computed<SpectrumMarker[]>(() =>
    this.channelRecs()
      .filter((r) => r.target_hz || r.hpf_hz)
      .map((r) => ({
        freq: (r.target_hz ?? r.hpf_hz) as number,
        label: r.filter_type === 'high_pass' ? `HPF ${fmtHz(r.hpf_hz)}` : `${categoryLabel(r.category)} ${fmtHz(r.target_hz)}`,
        color: SEV_COLOR[r.severity] ?? '#fb923c',
      })),
  );

  pad = pad2;
  db = (v: number | null | undefined) => fmtDb(v, 1);
  hz = fmtHz;
  pct = fmtPct;
  frac = (v: number | null | undefined) => meterFraction(v);
  cat = categoryLabel;
  profName = (k: string) => profileName(k, this.profiles());

  reanalyze(ch: number): void {
    void this.api.reanalyzeChannel(ch);
  }
}
