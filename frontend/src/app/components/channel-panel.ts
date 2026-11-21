import { Component, computed, inject, input, output, signal } from '@angular/core';

import { fmtDb, meterFraction, pad2, profileName } from '../format';
import { ChannelConfig, Level, Profile } from '../models';
import { ApiService, errorMessage } from '../services/api.service';

interface Row { cfg: ChannelConfig; level: Level | undefined }

/** Channel list: selection (individual / all / none / by group), search, profile filter, live activity. */
@Component({
  selector: 'app-channel-panel',
  template: `
    <section class="panel">
      <div class="panel-head">
        <span class="panel-title">Channels</span>
        <span class="faint num">{{ selectedCount() }}/{{ channels().length }} selected</span>
        <span class="grow"></span>
        <button class="btn sm" (click)="selectAll()">All</button>
        <button class="btn sm" (click)="selectNone()">None</button>
      </div>
      <div class="tools">
        <input class="input sm grow" placeholder="Search…" [value]="query()" (input)="query.set($any($event.target).value)" />
        <select class="select sm" [value]="profileFilter()" (change)="profileFilter.set($any($event.target).value)" aria-label="Filter by profile">
          <option value="">All profiles</option>
          @for (p of profiles(); track p.key) { <option [value]="p.key">{{ p.name }}</option> }
        </select>
      </div>
      <div class="groups">
        @for (g of groups(); track g) {
          <button class="btn sm" [title]="'Select only ' + g + ' (Shift: add to selection)'" (click)="selectGroup(g, $event.shiftKey)">{{ g }}</button>
        }
      </div>
      @if (error()) { <div class="notice bad" style="margin:0 10px 8px">{{ error() }}</div> }
      <div class="list">
        @for (r of rows(); track r.cfg.index) {
          <div class="ch" [class.sel]="r.cfg.selected" [class.focus]="r.cfg.number === focus()" [class.disabled]="!r.cfg.enabled"
               (click)="r.cfg.selected && focusChange.emit(r.cfg.number)">
            <input type="checkbox" [checked]="r.cfg.selected" [disabled]="!r.cfg.enabled"
                   (click)="$event.stopPropagation()" (change)="toggle(r.cfg, $any($event.target).checked)"
                   [attr.aria-label]="'Analyse ' + r.cfg.name" />
            <div class="num chn">{{ pad(r.cfg.number) }}</div>
            <div class="info">
              <div class="row"><span class="nm ellipsis">{{ r.cfg.name }}</span>
                @if ((r.level?.diagnostics ?? 0) > 0) { <span class="badge num" title="Active diagnostics">{{ r.level?.diagnostics }}</span> }
              </div>
              <div class="sub ellipsis">{{ profName(r.cfg.profile) }} · {{ r.cfg.group }}</div>
              <div class="row meterrow">
                <span class="led act" [class.on]="!!r.level?.active" title="Signal activity"></span>
                <div class="meter grow" [title]="'RMS ' + db(r.level?.rms) + ' / peak ' + db(r.level?.peak) + ' dBFS'">
                  <div class="fill" [style.width.%]="frac(r.level?.rms) * 100" [style.background-size]="meterW(r.level?.rms)"></div>
                  <div class="peak" [style.left.%]="frac(r.level?.peak_hold ?? r.level?.peak) * 100"></div>
                </div>
                <span class="led" [class.on]="!!r.level?.clip" title="Clip"></span>
              </div>
            </div>
            <div class="vals num">
              <div>{{ db(r.level?.rms) }}</div>
              <div class="faint">{{ db(r.level?.peak) }}</div>
            </div>
          </div>
        } @empty {
          <div class="empty">No channels match.</div>
        }
      </div>
    </section>
  `,
  styles: [`
    :host{display:block;min-height:0}
    section{height:100%}
    .tools{display:flex;gap:6px;padding:8px 10px 4px}
    .groups{display:flex;gap:4px;flex-wrap:wrap;padding:4px 10px 8px;border-bottom:1px solid var(--border)}
    .list{overflow:auto;flex:1;min-height:0;padding:4px}
    .ch{display:grid;grid-template-columns:22px 26px 1fr 48px;align-items:center;gap:8px;padding:8px;border-radius:8px;border:1px solid transparent;cursor:pointer;opacity:.55}
    .ch.sel{opacity:1;background:#121a26}
    .ch.focus{border-color:var(--accent);background:#14223a}
    .ch.disabled{opacity:.3;cursor:default}
    .ch:hover{background:#172131}
    .chn{font-size:15px;font-weight:800;color:var(--text-dim)}
    .ch.sel .chn{color:var(--accent)}
    .info{min-width:0;display:flex;flex-direction:column;gap:2px}
    .nm{font-weight:700}
    .sub{font-size:11px;color:var(--text-faint)}
    .meterrow{gap:6px;margin-top:3px}
    .badge{background:var(--high);color:#111;border-radius:999px;font-size:11px;font-weight:800;padding:0 6px;line-height:16px}
    .vals{text-align:right;font-size:11px}
  `],
})
export class ChannelPanel {
  private api = inject(ApiService);

  readonly channels = input<ChannelConfig[]>([]);
  readonly levels = input<Level[]>([]);
  readonly profiles = input<Profile[]>([]);
  readonly groups = input<string[]>([]);
  readonly focus = input<number | null>(null);
  readonly focusChange = output<number>();

  readonly query = signal('');
  readonly profileFilter = signal('');
  readonly error = signal<string | null>(null);

  readonly selectedCount = computed(() => this.channels().filter((c) => c.selected).length);

  readonly rows = computed<Row[]>(() => {
    const q = this.query().trim().toLowerCase();
    const pf = this.profileFilter();
    const lv = new Map(this.levels().map((l) => [l.channel, l]));
    return this.channels()
      .filter((c) => !pf || c.profile === pf)
      .filter((c) => !q || c.name.toLowerCase().includes(q) || `${c.number}`.includes(q) || c.group.toLowerCase().includes(q))
      .map((cfg) => ({ cfg, level: lv.get(cfg.number) }));
  });

  pad = pad2;
  db = (v: number | null | undefined) => fmtDb(v, 0);
  frac = (v: number | null | undefined) => meterFraction(v);
  /** Keep the gradient anchored to the full meter width while the fill grows. */
  meterW = (v: number | null | undefined) => `${(1 / Math.max(meterFraction(v), 0.01)) * 100}% 100%`;
  profName = (k: string) => profileName(k, this.profiles());

  private async run(p: Promise<unknown>): Promise<void> {
    this.error.set(null);
    try {
      await p;
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  toggle(c: ChannelConfig, on: boolean): void {
    const sel = this.channels().filter((x) => (x.number === c.number ? on : x.selected)).map((x) => x.number);
    void this.run(this.api.setSelection({ selected: sel }));
  }

  selectAll(): void { void this.run(this.api.setSelection({ select_all: true })); }
  selectNone(): void { void this.run(this.api.setSelection({ deselect_all: true })); }
  selectGroup(g: string, add: boolean): void {
    void this.run(this.api.setSelection(add ? { add_group: g } : { group: g }));
  }
}
