import { Component, OnInit, computed, inject, output, signal } from '@angular/core';

import { categoryLabel, fmtPct, fmtTime, pad2 } from '../format';
import { Recommendation } from '../models';
import { ApiService, errorMessage } from '../services/api.service';

@Component({
  selector: 'app-history-dialog',
  template: `
    <div class="overlay" (click)="close.emit()">
      <div class="dialog" (click)="$event.stopPropagation()" role="dialog" aria-modal="true">
        <div class="dialog-head">
          <h2>History</h2>
          <span class="grow"></span>
          <div class="tabs" style="margin:0;border:none">
            <button [class.on]="tab() === 'recs'" (click)="tab.set('recs')">Recommendations</button>
            <button [class.on]="tab() === 'actions'" (click)="tab.set('actions')">Operator actions</button>
          </div>
          <button class="btn ghost" (click)="close.emit()" aria-label="Close">✕</button>
        </div>
        <div class="dialog-body">
          @if (error()) { <div class="notice bad">{{ error() }}</div> }
          @if (tab() === 'recs') {
            <div class="row" style="margin-bottom:8px">
              <select class="select sm" [value]="statusFilter()" (change)="statusFilter.set($any($event.target).value)">
                <option value="">All statuses</option>
                @for (s of statuses; track s) { <option [value]="s">{{ s }}</option> }
              </select>
              <span class="faint">{{ filtered().length }} entries</span>
            </div>
            <table class="tbl">
              <thead><tr><th>Time</th><th>Ch</th><th>Issue</th><th>Suggested</th><th>Conf.</th><th>Status</th><th>Verification</th></tr></thead>
              <tbody>
                @for (r of filtered(); track r.id) {
                  <tr (click)="open.emit(r)" style="cursor:pointer">
                    <td class="num dim">{{ time(r.created_at) }}</td>
                    <td class="num">CH{{ pad(r.channel) }} <span class="dim">{{ r.channel_name }}</span></td>
                    <td><span class="sev" [class]="'sev ' + r.severity">{{ r.severity }}</span> {{ cat(r.category) }}</td>
                    <td>{{ r.summary }}</td>
                    <td class="num">{{ pct(r.confidence) }}</td>
                    <td><span class="tag">{{ r.status }}</span></td>
                    <td class="dim">{{ r.verification?.state ?? '' }}</td>
                  </tr>
                } @empty { <tr><td colspan="7" class="empty">No recommendations yet.</td></tr> }
              </tbody>
            </table>
          } @else {
            <table class="tbl">
              <thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Details</th></tr></thead>
              <tbody>
                @for (a of actions(); track $index) {
                  <tr><td class="num dim">{{ time(a.at) }}</td><td>{{ a.action }}</td><td>{{ a.target ?? '' }}</td>
                    <td class="dim small">{{ json(a.payload) }}</td></tr>
                } @empty { <tr><td colspan="4" class="empty">No operator actions recorded.</td></tr> }
              </tbody>
            </table>
          }
        </div>
      </div>
    </div>
  `,
  styles: [`.small{font-size:11px;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}`],
})
export class HistoryDialog implements OnInit {
  private api = inject(ApiService);
  readonly close = output<void>();
  readonly open = output<Recommendation>();
  readonly tab = signal<'recs' | 'actions'>('recs');
  readonly history = signal<Recommendation[]>([]);
  readonly actions = signal<{ at: number; action: string; target: string | null; payload: Record<string, unknown> }[]>([]);
  readonly statusFilter = signal('');
  readonly error = signal<string | null>(null);
  readonly statuses = ['new', 'pending', 'applied', 'dismissed', 'resolved', 'expired'];
  readonly filtered = computed(() => this.history().filter((r) => !this.statusFilter() || r.status === this.statusFilter()));

  pad = pad2;
  time = fmtTime;
  pct = fmtPct;
  cat = categoryLabel;
  json = (o: unknown) => JSON.stringify(o);

  async ngOnInit(): Promise<void> {
    try {
      const [h, a] = await Promise.all([this.api.recommendationHistory(500), this.api.operatorActions(300)]);
      this.history.set(h.history);
      this.actions.set(a.actions);
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }
}
