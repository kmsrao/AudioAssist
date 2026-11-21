import { Injectable, computed, signal } from '@angular/core';

import { Analysis, Diagnostic, Hello, Level, RecPayload, Status } from '../models';

type ServerMessage =
  | Hello
  | ({ type: 'status' } & Status)
  | { type: 'levels'; tick: number; levels: Level[] }
  | Analysis & { type: 'analysis' }
  | ({ type: 'recommendations' } & RecPayload)
  | { type: 'diagnostics'; active: Diagnostic[] }
  | { type: 'pong'; t: number }
  | { type: 'error'; message: string };

/**
 * Real-time feed from the backend WebSocket. Holds the latest state as signals;
 * reconnects automatically with backoff. All analysis is done server-side -
 * this service only stores what it receives.
 */
@Injectable({ providedIn: 'root' })
export class LiveService {
  readonly connected = signal(false);
  readonly hello = signal<Hello | null>(null);
  readonly status = signal<Status | null>(null);
  readonly levels = signal<Level[]>([]);
  readonly analysis = signal<Analysis | null>(null);
  readonly recs = signal<RecPayload>({ version: -1, active: [], recent: [] });
  readonly diagnostics = signal<Diagnostic[]>([]);
  readonly focus = signal<number | null>(null);
  readonly lastMessageAt = signal(0);

  /** Spectrogram columns for the focused channel (uint8 0..255 -> floor..0 dB). Mutated in place; `spectroVersion` signals changes. */
  spectrogram: number[][] = [];
  readonly spectroVersion = signal(0);
  spectroFullRedraw = true;

  readonly channels = computed(() => this.hello()?.channels ?? []);
  readonly profiles = computed(() => this.hello()?.profiles ?? []);
  readonly groups = computed(() => this.hello()?.groups ?? []);
  readonly grid = computed(() => this.hello()?.grid ?? null);
  readonly settings = computed(() => this.hello()?.settings ?? null);

  private ws: WebSocket | null = null;
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private stopped = false;

  connect(url = LiveService.defaultUrl()): void {
    this.stopped = false;
    this.open(url);
  }

  disconnect(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
  }

  static defaultUrl(): string {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws`;
  }

  setFocus(channel: number | null): void {
    if (channel === this.focus()) return;
    this.focus.set(channel);
    this.spectrogram = [];
    this.spectroFullRedraw = true;
    this.spectroVersion.update((v) => v + 1);
    this.analysis.set(null);
    this.send({ type: 'focus', channel });
  }

  send(msg: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private open(url: string): void {
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect(url);
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.connected.set(true);
      this.retry = 0;
      if (this.focus() !== null) this.send({ type: 'focus', channel: this.focus() });
    };
    ws.onclose = () => {
      this.connected.set(false);
      if (!this.stopped) this.scheduleReconnect(url);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => {
      try {
        this.handle(JSON.parse(ev.data as string) as ServerMessage);
      } catch (e) {
        console.error('bad message', e);
      }
    };
  }

  private scheduleReconnect(url: string): void {
    const delay = Math.min(5000, 500 * 2 ** this.retry++);
    this.timer = setTimeout(() => this.open(url), delay);
  }

  /** Exposed for unit tests. */
  handle(msg: ServerMessage): void {
    this.lastMessageAt.set(Date.now());
    switch (msg.type) {
      case 'hello': {
        this.hello.set(msg);
        const sel = msg.channels.filter((c) => c.selected);
        const f = this.focus();
        if (f === null || !msg.channels.some((c) => c.number === f && c.selected)) {
          this.setFocus(sel.length ? sel[0].number : null);
        } else {
          this.send({ type: 'focus', channel: f });
          this.spectroFullRedraw = true;
        }
        break;
      }
      case 'status': {
        const { type: _t, ...rest } = msg;
        this.status.set(rest as Status);
        break;
      }
      case 'levels':
        this.levels.set(msg.levels);
        break;
      case 'analysis': {
        if (msg.channel !== this.focus()) return;
        this.analysis.set(msg);
        const max = this.hello()?.settings.spectrogram_history ?? 120;
        if (msg.full && msg.spectrogram) {
          this.spectrogram = msg.spectrogram.slice(-max);
          this.spectroFullRedraw = true;
        } else if (msg.spectrogram_column) {
          this.spectrogram.push(msg.spectrogram_column);
          if (this.spectrogram.length > max) this.spectrogram.splice(0, this.spectrogram.length - max);
        }
        this.spectroVersion.update((v) => v + 1);
        break;
      }
      case 'recommendations': {
        const { type: _t, ...rest } = msg;
        this.recs.set(rest as RecPayload);
        break;
      }
      case 'diagnostics':
        this.diagnostics.set(msg.active);
        break;
      default:
        break;
    }
  }
}
