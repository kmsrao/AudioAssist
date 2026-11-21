import { NgTemplateOutlet } from '@angular/common';
import { Component, OnInit, computed, inject, input, output, signal } from '@angular/core';

import { CaptureStatus, DeviceInfo, DeviceTestResult, DevicesResponse, Settings, SettingsResponse } from '../models';
import { ApiService, errorMessage } from '../services/api.service';

interface FieldDef { key: keyof Settings & string; label: string; hint?: string; step?: number; min?: number; max?: number;
  options?: (string | number)[] }

const ANALYSIS_FIELDS: FieldDef[] = [
  { key: 'fft_size', label: 'FFT size', options: [2048, 4096, 8192], hint: 'Larger = finer frequency resolution, slower response' },
  { key: 'analysis_rate_hz', label: 'Analysis rate (Hz)', step: 1, min: 2, max: 30 },
  { key: 'spectral_smoothing_tc_s', label: 'Spectral smoothing (s)', step: 0.1, min: 0.1, max: 10, hint: 'Time constant of the long-term spectrum' },
  { key: 'display_smoothing_tc_s', label: 'Display smoothing (s)', step: 0.05, min: 0, max: 5 },
  { key: 'ui_refresh_hz', label: 'UI refresh (Hz)', step: 1, min: 1, max: 30 },
  { key: 'activity_gate_dbfs', label: 'Activity gate (dBFS)', step: 1, min: -100, max: -20 },
  { key: 'mains_frequency', label: 'Mains frequency', options: ['auto', '50', '60'] },
];
const DIAG_FIELDS: FieldDef[] = [
  { key: 'min_confidence', label: 'Minimum confidence', step: 0.05, min: 0, max: 1 },
  { key: 'diagnostic_persistence_s', label: 'Diagnostic persistence (s)', step: 0.5, min: 0.5, max: 60 },
  { key: 'clear_time_s', label: 'Clear time (s)', step: 0.5, min: 0.5, max: 120 },
  { key: 'recommendation_cooldown_s', label: 'Cooldown after dismiss (s)', step: 10, min: 0, max: 3600 },
  { key: 'verification_window_s', label: 'Verification window (s)', step: 1, min: 2, max: 120 },
  { key: 'new_to_pending_s', label: 'New → pending after (s)', step: 1, min: 0, max: 600 },
];
const LIMIT_FIELDS: FieldDef[] = [
  { key: 'max_eq_boost_db', label: 'Max EQ boost (dB)', step: 0.5, min: 0, max: 12 },
  { key: 'max_eq_cut_db', label: 'Max EQ cut (dB)', step: 0.5, min: 0.5, max: 18 },
  { key: 'hpf_min_hz', label: 'HPF min (Hz)', step: 5, min: 10, max: 500 },
  { key: 'hpf_max_hz', label: 'HPF max (Hz)', step: 5, min: 20, max: 1000 },
  { key: 'max_gain_change_db', label: 'Max gain change (dB)', step: 1, min: 1, max: 30 },
];

@Component({
  selector: 'app-settings-dialog',
  template: `
    <div class="overlay" (click)="close.emit()">
      <div class="dialog" (click)="$event.stopPropagation()" role="dialog" aria-modal="true">
        <div class="dialog-head"><h2>Audio &amp; settings</h2><span class="grow"></span>
          <button class="btn ghost" (click)="close.emit()" aria-label="Close">✕</button></div>
        <div class="dialog-body">
          <div class="tabs">
            <button [class.on]="tab() === 'audio'" (click)="tab.set('audio')">Audio input</button>
            <button [class.on]="tab() === 'analysis'" (click)="tab.set('analysis')">Analysis &amp; limits</button>
          </div>
          @if (error()) { <div class="notice bad" style="margin-bottom:10px">{{ error() }}</div> }
          @if (info()) { <div class="notice" style="margin-bottom:10px">{{ info() }}</div> }

          @if (tab() === 'audio') {
            <div class="capture row wrap">
              <span class="pill" [class.ok]="capture()?.state === 'running'" [class.bad]="capture()?.state === 'error'">
                <span class="dot"></span>{{ capture()?.state ?? '—' }}</span>
              <span class="dim">{{ capture()?.source?.device_name ?? 'No source' }}
                @if (capture()?.source) { · {{ capture()?.source?.channels }} ch · {{ capture()?.source?.sample_rate }} Hz · block {{ capture()?.source?.block_size }} }</span>
              <span class="grow"></span>
              <button class="btn sm" (click)="capCmd('start')" [disabled]="busy()">Start</button>
              <button class="btn sm" (click)="capCmd('stop')" [disabled]="busy()">Stop</button>
              <button class="btn sm" (click)="capCmd('restart')" [disabled]="busy()">Restart</button>
            </div>

            <div class="row" style="margin:12px 0 8px">
              <b>Audio interface</b>
              <span class="grow"></span>
              <button class="btn sm" (click)="loadDevices(true)" [disabled]="busy()" title="Requires capture to be stopped">Rescan devices</button>
            </div>

            @if (devices() && !devices()!.available) { <div class="notice bad">PortAudio unavailable: {{ devices()!.error }}</div> }
            <table class="tbl devs">
              <thead><tr><th></th><th>Device</th><th>Host API</th><th>Inputs</th><th>Sample rates</th><th>Latency</th><th></th></tr></thead>
              <tbody>
                @for (d of devices()?.devices ?? []; track d.index) {
                  <tr [class.picked]="d.index === deviceIndex()" (click)="pick(d)">
                    <td><input type="radio" name="dev" [checked]="d.index === deviceIndex()" /></td>
                    <td>{{ d.name }} @if (d.is_default_input) { <span class="tag">default</span> }</td>
                    <td class="dim">{{ d.hostapi }}</td>
                    <td class="num">{{ d.max_input_channels }}</td>
                    <td class="num dim">{{ rates(d) }}</td>
                    <td class="num dim">{{ (d.default_low_input_latency * 1000).toFixed(1) }} ms</td>
                    <td><button class="btn sm" (click)="test(d); $event.stopPropagation()" [disabled]="busy()">Test</button></td>
                  </tr>
                } @empty { <tr><td colspan="7" class="empty">No input devices found.</td></tr> }
              </tbody>
            </table>
            @if (testResult(); as t) {
              <div class="notice" [class.bad]="!t.ok" style="margin-top:8px">
                @if (!t.ok) { Test failed: {{ t.error }} } @else {
                  Test ({{ t.mode }}):
                  @for (c of t.channels ?? []; track c.channel) {
                    <span class="tag" [class.accent]="c.signal_present">CH{{ c.channel }} {{ c.rms_dbfs }} / {{ c.peak_dbfs }} dBFS</span>
                  }
                }
              </div>
            }

            <div class="grid-form" style="margin-top:12px">
              <label class="field">Sample rate
                <select class="select" [value]="sampleRate()" (change)="sampleRate.set(+$any($event.target).value)">
                  @for (r of availableRates(); track r) { <option [value]="r" [selected]="r === sampleRate()">{{ r }} Hz</option> }
                </select></label>
              <label class="field">Buffer (block) size
                <select class="select" [value]="blockSize()" (change)="blockSize.set(+$any($event.target).value)">
                  @for (b of opts()?.block_sizes ?? [512]; track b) { <option [value]="b" [selected]="b === blockSize()">{{ b }} frames</option> }
                </select></label>
              <label class="field">Input channels
                <input class="input" type="number" min="1" [max]="maxChannels()" [value]="channelCount()" (input)="channelCount.set(+$any($event.target).value)" />
                <span class="hint">max {{ maxChannels() }}</span></label>
            </div>
            <div class="row" style="margin-top:14px">
              <span class="grow"></span>
              <button class="btn primary lg" (click)="applyDevice()" [disabled]="busy() || deviceIndex() === null">Use this input &amp; start capture</button>
            </div>
          } @else {
            @if (form(); as f) {
              <h3>Analysis</h3>
              <div class="grid-form">@for (fd of analysisFields; track fd.key) { <ng-container *ngTemplateOutlet="field; context: { $implicit: fd }" /> }</div>
              <h3>Diagnostics</h3>
              <div class="grid-form">@for (fd of diagFields; track fd.key) { <ng-container *ngTemplateOutlet="field; context: { $implicit: fd }" /> }</div>
              <h3>Recommendation limits</h3>
              <div class="grid-form">@for (fd of limitFields; track fd.key) { <ng-container *ngTemplateOutlet="field; context: { $implicit: fd }" /> }</div>
            }
            <ng-template #field let-fd>
              <label class="field">{{ fd.label }}
                @if (fd.options) {
                  <select class="select" (change)="set(fd, $any($event.target).value)">
                    @for (o of fd.options; track o) { <option [value]="o" [selected]="'' + o === '' + form()![fd.key]">{{ o }}</option> }
                  </select>
                } @else {
                  <input class="input" type="number" [step]="fd.step" [min]="fd.min" [max]="fd.max" [value]="form()![fd.key]" (input)="set(fd, $any($event.target).value)" />
                }
                @if (fd.hint) { <span class="hint">{{ fd.hint }}</span> }
                @if (isChanged(fd.key)) { <span class="hint" style="color:var(--warn)">default {{ defaults()?.[fd.key] }}</span> }
              </label>
            </ng-template>
          }
        </div>
        <div class="dialog-foot">
          @if (tab() === 'analysis') {
            <button class="btn danger" (click)="reset()" [disabled]="busy()">Reset to defaults</button>
            <span class="grow"></span>
            <button class="btn" (click)="close.emit()">Close</button>
            <button class="btn primary" (click)="saveSettings()" [disabled]="busy()">Save settings</button>
          } @else {
            <button class="btn" (click)="close.emit()">Close</button>
          }
        </div>
      </div>
    </div>
  `,
  styles: [`
    h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-dim);margin:16px 0 8px}
    .capture{background:var(--panel-2);border:1px solid var(--border);border-radius:8px;padding:8px 10px}
    .devs tr{cursor:pointer} .devs tr.picked td{background:#14223a}
  `],
  imports: [NgTemplateOutlet],
})
export class SettingsDialog implements OnInit {
  private api = inject(ApiService);
  readonly current = input<Settings | null>(null);
  readonly capture = input<CaptureStatus | null>(null);
  readonly close = output<void>();

  readonly tab = signal<'audio' | 'analysis'>('audio');
  readonly error = signal<string | null>(null);
  readonly info = signal<string | null>(null);
  readonly busy = signal(false);

  readonly devices = signal<DevicesResponse | null>(null);
  readonly opts = signal<SettingsResponse['options'] | null>(null);
  readonly defaults = signal<Settings | null>(null);
  readonly form = signal<Settings | null>(null);
  readonly testResult = signal<DeviceTestResult | null>(null);

  readonly deviceIndex = signal<number | null>(null);
  readonly sampleRate = signal(48000);
  readonly blockSize = signal(512);
  readonly channelCount = signal(8);

  readonly analysisFields = ANALYSIS_FIELDS;
  readonly diagFields = DIAG_FIELDS;
  readonly limitFields = LIMIT_FIELDS;

  readonly selectedDevice = computed(() => this.devices()?.devices.find((d) => d.index === this.deviceIndex()) ?? null);
  readonly availableRates = computed(() => {
    const d = this.selectedDevice();
    const all = this.opts()?.sample_rates ?? [44100, 48000, 96000];
    return d?.supported_sample_rates.length ? d.supported_sample_rates : all;
  });
  readonly maxChannels = computed(() => this.selectedDevice()?.max_input_channels ?? 64);

  async ngOnInit(): Promise<void> {
    const cur = this.current();
    if (cur) {
      this.deviceIndex.set(cur.device_index);
      this.sampleRate.set(cur.sample_rate);
      this.blockSize.set(cur.block_size);
      this.channelCount.set(cur.channel_count);
    }
    try {
      const s = await this.api.settings();
      this.opts.set(s.options);
      this.defaults.set(s.defaults);
      this.form.set({ ...s.settings });
    } catch (e) {
      this.error.set(errorMessage(e));
    }
    await this.loadDevices(false);
  }

  async loadDevices(refresh: boolean): Promise<void> {
    this.error.set(null);
    try {
      this.devices.set(await this.api.devices(refresh));
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  rates(d: DeviceInfo): string {
    return d.supported_sample_rates.length ? d.supported_sample_rates.map((r) => r / 1000).join(' / ') + ' kHz' : `${d.default_samplerate / 1000} kHz`;
  }

  pick(d: DeviceInfo): void {
    this.deviceIndex.set(d.index);
    this.channelCount.set(Math.min(d.max_input_channels, Math.max(this.channelCount(), 1)));
    if (d.supported_sample_rates.length && !d.supported_sample_rates.includes(this.sampleRate())) {
      this.sampleRate.set(d.supported_sample_rates.includes(48000) ? 48000 : d.supported_sample_rates[0]);
    }
  }

  private async run<T>(p: Promise<T>, ok?: string): Promise<T | null> {
    this.busy.set(true);
    this.error.set(null);
    this.info.set(null);
    try {
      const r = await p;
      if (ok) this.info.set(ok);
      return r;
    } catch (e) {
      this.error.set(errorMessage(e));
      return null;
    } finally {
      this.busy.set(false);
    }
  }

  async test(d: DeviceInfo): Promise<void> {
    this.testResult.set(null);
    const r = await this.run(this.api.testDevice(d.index, Math.min(d.max_input_channels, 16)));
    if (r) this.testResult.set(r);
  }

  async capCmd(kind: 'start' | 'stop' | 'restart'): Promise<void> {
    const fn = kind === 'start' ? this.api.startCapture() : kind === 'stop' ? this.api.stopCapture() : this.api.restartCapture();
    await this.run(fn, `Capture ${kind} requested.`);
  }

  async applyDevice(): Promise<void> {
    const index = this.deviceIndex();
    if (index === null) return;
    const body = { device_index: index, sample_rate: this.sampleRate(), block_size: this.blockSize(),
      channel_count: this.channelCount(), start: true };
    await this.run(this.api.changeDevice(body), 'Input changed and capture started.');
  }

  set(fd: FieldDef, raw: string): void {
    const f = this.form();
    if (!f) return;
    const v = fd.options ? (typeof fd.options[0] === 'number' ? Number(raw) : raw) : Number(raw);
    this.form.set({ ...f, [fd.key]: v });
  }

  isChanged(k: string): boolean {
    const d = this.defaults();
    const f = this.form();
    return !!d && !!f && `${d[k]}` !== `${f[k]}`;
  }

  async saveSettings(): Promise<void> {
    const f = this.form();
    if (!f) return;
    const keys = [...ANALYSIS_FIELDS, ...DIAG_FIELDS, ...LIMIT_FIELDS].map((x) => x.key);
    const values = Object.fromEntries(keys.map((k) => [k, f[k]])) as Partial<Settings>;
    const r = await this.run(this.api.updateSettings(values), 'Settings saved.');
    if (r) this.form.set({ ...r.settings });
  }

  async reset(): Promise<void> {
    const r = await this.run(this.api.resetSettings(), 'Settings reset to defaults (audio input kept).');
    if (r) this.form.set({ ...r.settings });
  }
}
