import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  CaptureStatus, ChannelConfig, DeviceTestResult, DevicesResponse, Diagnostic, Recommendation, RecPayload,
  Settings, SettingsResponse, Status,
} from '../models';

/** Thin typed wrapper over the REST API (relative URLs: same origin, or proxied by `ng serve`). */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = '/api';

  private get<T>(path: string): Promise<T> {
    return firstValueFrom(this.http.get<T>(this.base + path));
  }

  private post<T>(path: string, body: unknown = {}): Promise<T> {
    return firstValueFrom(this.http.post<T>(this.base + path, body));
  }

  private put<T>(path: string, body: unknown): Promise<T> {
    return firstValueFrom(this.http.put<T>(this.base + path, body));
  }

  status(): Promise<Status> { return this.get('/status'); }

  devices(refresh = false): Promise<DevicesResponse> { return this.get(`/devices${refresh ? '?refresh=true' : ''}`); }
  testDevice(index: number, channels?: number, sampleRate?: number): Promise<DeviceTestResult> {
    return this.post(`/devices/${index}/test`, { channels, sample_rate: sampleRate, duration_s: 1.0 });
  }

  startCapture(): Promise<CaptureStatus> { return this.post('/capture/start'); }
  stopCapture(): Promise<CaptureStatus> { return this.post('/capture/stop'); }
  restartCapture(): Promise<CaptureStatus> { return this.post('/capture/restart'); }
  changeDevice(body: {
    device_index: number; sample_rate?: number; block_size?: number; channel_count?: number; start?: boolean;
  }): Promise<CaptureStatus> { return this.post('/capture/device', body); }

  channels(): Promise<{ channels: ChannelConfig[]; groups: string[] }> { return this.get('/channels'); }
  saveChannels(channels: ChannelConfig[]): Promise<{ channels: ChannelConfig[] }> {
    return this.put('/channels', { channels: channels.map(({ number: _n, ...c }) => c) });
  }
  setSelection(body: { selected?: number[]; select_all?: boolean; deselect_all?: boolean; group?: string; add_group?: string }):
    Promise<{ selected: number[] }> { return this.put('/channels/selected', body); }
  saveGroups(groups: string[]): Promise<{ groups: string[] }> { return this.put('/groups', { groups }); }
  reanalyzeChannel(n: number): Promise<unknown> { return this.post(`/channels/${n}/reanalyze`); }

  diagnostics(channel?: number): Promise<{ active: Diagnostic[] }> {
    return this.get(`/diagnostics${channel ? `?channel=${channel}` : ''}`);
  }
  recommendations(): Promise<RecPayload> { return this.get('/recommendations'); }
  recommendationHistory(limit = 200): Promise<{ history: Recommendation[] }> {
    return this.get(`/recommendations/history?limit=${limit}`);
  }
  recommendation(id: string): Promise<Recommendation> { return this.get(`/recommendations/${id}`); }
  markApplied(id: string, note?: string): Promise<Recommendation> { return this.post(`/recommendations/${id}/applied`, { note }); }
  dismiss(id: string, note?: string): Promise<Recommendation> { return this.post(`/recommendations/${id}/dismissed`, { note }); }
  reanalyze(id: string): Promise<Recommendation> { return this.post(`/recommendations/${id}/reanalyze`); }
  operatorActions(limit = 100): Promise<{ actions: { at: number; action: string; target: string | null; payload: Record<string, unknown> }[] }> {
    return this.get(`/operator-actions?limit=${limit}`);
  }

  settings(): Promise<SettingsResponse> { return this.get('/settings'); }
  updateSettings(values: Partial<Settings>): Promise<{ settings: Settings }> { return this.post('/settings', { values }); }
  resetSettings(): Promise<{ settings: Settings }> { return this.post('/settings/reset'); }
}

/** Extract a readable message from an HttpErrorResponse-like error. */
export function errorMessage(e: unknown): string {
  const err = e as { error?: { detail?: unknown }; message?: string; status?: number };
  const detail = err?.error?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d as { msg?: string }).msg ?? JSON.stringify(d)).join('; ');
  if (err?.status === 0) return 'Backend not reachable.';
  return err?.message ?? String(e);
}
