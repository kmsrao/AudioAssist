import { TestBed } from '@angular/core/testing';

import { Analysis, ChannelConfig, Hello } from '../models';
import { LiveService } from './live.service';

function channel(n: number, selected = true): ChannelConfig {
  return { index: n - 1, number: n, name: `CH${n}`, profile: 'general', group: 'Other', selected, enabled: true };
}

function hello(channels: ChannelConfig[], history = 4): Hello {
  return {
    type: 'hello',
    grid: { freqs: [20, 200, 2000], spectrogram_freqs: [20, 2000], spectrogram_floor_db: -120, fft_size: 4096, bin_hz: 11.7, sample_rate: 48000 },
    channels, groups: ['Other'], profiles: [],
    settings: { spectrogram_history: history } as unknown as Hello['settings'],
  };
}

function analysis(ch: number, extra: Partial<Analysis> = {}): Analysis & { type: 'analysis' } {
  return {
    type: 'analysis', channel: ch, name: '', profile: 'general', tick: 1, active: true, rms_dbfs: -20, peak_dbfs: -10,
    peak_hold_dbfs: -10, crest_db: 10, clipping: false, clip_events_5s: 0, noise_floor_dbfs: null, snr_estimate_db: null,
    activity_ratio: 1, spectrum: { db: [], maxhold: [] }, bands: [], bands_long: [], stats: { centroid_hz: 1, rolloff_hz: 1, flatness: 0 },
    peaks: [], ...extra,
  };
}

describe('LiveService', () => {
  let svc: LiveService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    svc = TestBed.inject(LiveService);
  });

  it('focuses the first selected channel on hello', () => {
    svc.handle(hello([channel(1, false), channel(2), channel(3)]));
    expect(svc.focus()).toBe(2);
    expect(svc.channels().length).toBe(3);
  });

  it('keeps focus when the channel stays selected, moves it when deselected', () => {
    svc.handle(hello([channel(1), channel(2)]));
    svc.setFocus(2);
    svc.handle(hello([channel(1), channel(2)]));
    expect(svc.focus()).toBe(2);
    svc.handle(hello([channel(1), channel(2, false)]));
    expect(svc.focus()).toBe(1);
  });

  it('ignores analysis for a channel that is not focused', () => {
    svc.handle(hello([channel(1), channel(2)]));
    svc.handle(analysis(2));
    expect(svc.analysis()).toBeNull();
    svc.handle(analysis(1));
    expect(svc.analysis()?.channel).toBe(1);
  });

  it('builds and trims the spectrogram history', () => {
    svc.handle(hello([channel(1)], 3));
    svc.handle(analysis(1, { full: true, spectrogram: [[1], [2]] }));
    expect(svc.spectrogram).toEqual([[1], [2]]);
    for (const v of [3, 4, 5]) svc.handle(analysis(1, { spectrogram_column: [v] }));
    expect(svc.spectrogram).toEqual([[3], [4], [5]]);
    expect(svc.spectroVersion()).toBeGreaterThan(3);
  });

  it('stores levels, recommendations and diagnostics', () => {
    svc.handle({ type: 'levels', tick: 3, levels: [{ channel: 1, rms: -20, peak: -6, peak_hold: -6, clip: false, active: true, selected: true, diagnostics: 0 }] });
    svc.handle({ type: 'recommendations', version: 4, active: [], recent: [] });
    svc.handle({ type: 'diagnostics', active: [] });
    expect(svc.levels()[0].rms).toBe(-20);
    expect(svc.recs().version).toBe(4);
    expect(svc.diagnostics()).toEqual([]);
  });
});
