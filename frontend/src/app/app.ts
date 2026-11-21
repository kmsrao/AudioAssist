import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';

import { AnalysisPanel } from './components/analysis-panel';
import { ChannelEditorDialog } from './components/channel-editor-dialog';
import { ChannelPanel } from './components/channel-panel';
import { HistoryDialog } from './components/history-dialog';
import { RecDetailDialog } from './components/rec-detail-dialog';
import { RecommendationPanel } from './components/recommendation-panel';
import { SettingsDialog } from './components/settings-dialog';
import { StatusStrip } from './components/status-strip';
import { TopBar } from './components/top-bar';
import { Recommendation } from './models';
import { LiveService } from './services/live.service';

@Component({
  selector: 'app-root',
  imports: [TopBar, StatusStrip, ChannelPanel, AnalysisPanel, RecommendationPanel, RecDetailDialog, HistoryDialog,
    SettingsDialog, ChannelEditorDialog],
  template: `
    <app-top-bar [status]="live.status()" [connected]="live.connected()"
                 (openSettings)="showSettings.set(true)" (openHistory)="showHistory.set(true)" (openChannels)="showChannels.set(true)" />

    @if (!live.connected()) {
      <div class="notice bad banner">Connection to the analysis backend lost — reconnecting… (is the terminal running <code>start</code>?)</div>
    } @else if (live.status()?.capture?.state === 'error') {
      <div class="notice bad banner">Audio capture error: {{ live.status()?.capture?.error }} — open <b>Audio &amp; Settings</b> to restart or pick another input.</div>
    }

    <main class="layout">
      <app-channel-panel class="left" [channels]="live.channels()" [levels]="live.levels()" [profiles]="live.profiles()"
                         [groups]="live.groups()" [focus]="live.focus()" (focusChange)="live.setFocus($event)" />
      <div class="center">
        <app-status-strip [status]="live.status()" />
        <app-analysis-panel class="analysis" [analysis]="live.analysis()" [focus]="live.focus()" [grid]="live.grid()"
                            [profiles]="live.profiles()" [recs]="live.recs().active" [diagnostics]="live.diagnostics()"
                            [spectro]="live.spectrogram" [spectroVersion]="live.spectroVersion()"
                            [history]="live.settings()?.spectrogram_history ?? 120" [analysisRate]="live.settings()?.analysis_rate_hz ?? 10" />
      </div>
      <app-recommendation-panel class="right" [recs]="live.recs().active" [recentClosed]="live.recs().recent" [focus]="live.focus()"
                                (focusChange)="live.setFocus($event)" (details)="openDetail($event)" />
    </main>

    @if (detail(); as r) { <app-rec-detail-dialog [rec]="r" (close)="detailId.set(null); detailSnapshot.set(null)" /> }
    @if (showHistory()) { <app-history-dialog (close)="showHistory.set(false)" (open)="openDetail($event)" /> }
    @if (showSettings()) { <app-settings-dialog [current]="live.settings()" [capture]="live.status()?.capture ?? null" (close)="showSettings.set(false)" /> }
    @if (showChannels()) {
      <app-channel-editor-dialog [channels]="live.channels()" [profiles]="live.profiles()" [groups]="live.groups()" (close)="showChannels.set(false)" />
    }
  `,
  styles: [`
    :host{display:flex;flex-direction:column;height:100vh;overflow:hidden}
    .banner{margin:8px 12px 0;border-radius:8px}
    .layout{flex:1;min-height:0;display:grid;grid-template-columns:320px minmax(0,1fr) 400px;gap:10px;padding:10px 12px 12px}
    .left,.right{min-height:0}
    .center{display:flex;flex-direction:column;gap:10px;min-height:0;min-width:0}
    .analysis{flex:1;min-height:0}
    @media (max-width:1400px){.layout{grid-template-columns:280px minmax(0,1fr) 360px}}
    @media (max-width:1100px){
      :host{height:auto;overflow:visible}
      .layout{grid-template-columns:1fr;grid-auto-rows:auto}
      .left{height:420px}.right{height:auto;max-height:none}
    }
  `],
})
export class App implements OnInit, OnDestroy {
  readonly live = inject(LiveService);

  readonly showSettings = signal(false);
  readonly showHistory = signal(false);
  readonly showChannels = signal(false);
  readonly detailId = signal<string | null>(null);
  readonly detailSnapshot = signal<Recommendation | null>(null);

  /** Details dialog follows the live version of the recommendation while it is active. */
  readonly detail = computed(() => {
    const id = this.detailId();
    if (!id) return null;
    const p = this.live.recs();
    return p.active.find((r) => r.id === id) ?? p.recent.find((r) => r.id === id) ?? this.detailSnapshot();
  });

  ngOnInit(): void {
    this.live.connect();
  }

  ngOnDestroy(): void {
    this.live.disconnect();
  }

  openDetail(r: Recommendation): void {
    this.detailSnapshot.set(r);
    this.detailId.set(r.id);
  }
}
