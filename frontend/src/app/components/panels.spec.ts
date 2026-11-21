import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ChannelConfig, Level, Recommendation } from '../models';
import { ApiService } from '../services/api.service';
import { ChannelPanel } from './channel-panel';
import { RecommendationPanel } from './recommendation-panel';

class ApiStub {
  setSelection = vi.fn().mockResolvedValue({ selected: [] });
  markApplied = vi.fn().mockResolvedValue({});
  dismiss = vi.fn().mockResolvedValue({});
  reanalyze = vi.fn().mockResolvedValue({});
}

const channels: ChannelConfig[] = [
  { index: 0, number: 1, name: 'Pastor Mic', profile: 'speech', group: 'Speech', selected: true, enabled: true },
  { index: 1, number: 2, name: 'Kick', profile: 'kick', group: 'Drums', selected: false, enabled: true },
];
const levels: Level[] = [
  { channel: 1, rms: -30, peak: -12, peak_hold: -10, clip: false, active: true, selected: true, diagnostics: 2 },
  { channel: 2, rms: -18, peak: 0, peak_hold: 0, clip: true, active: true, selected: false, diagnostics: 0 },
];

function rec(over: Partial<Recommendation> = {}): Recommendation {
  return {
    id: 'r1', diagnostic_id: 'd1', channel: 1, channel_name: 'Pastor Mic', profile: 'speech', category: 'muddy',
    issue: 'Potential muddiness', severity: 'medium', confidence: 0.82, confidence_label: 'high',
    control: 'Channel EQ - parametric band', filter_type: 'bell', target_hz: 320, gain_db: -4.5, q: 1.2, hpf_hz: null,
    hpf_slope: null, gain_change_db: null, actions: [], explanation: 'Persistent excess low-mid energy.',
    expected_improvement: 'Clearer low-mid.', caveats: [], fallback: null,
    supporting_analysis: { evidence: {}, confidence: {}, measured: { value: 9, unit: 'dB' } },
    summary: '', status: 'new', created_at: 0, updated_at: 0, timestamp: 0, history: [], before: null, after: null,
    verification: null, applied_at: null, closed_at: null, note: null, ...over,
  };
}

describe('ChannelPanel', () => {
  let fixture: ComponentFixture<ChannelPanel>;
  let api: ApiStub;

  beforeEach(async () => {
    api = new ApiStub();
    await TestBed.configureTestingModule({ imports: [ChannelPanel], providers: [{ provide: ApiService, useValue: api }] }).compileComponents();
    fixture = TestBed.createComponent(ChannelPanel);
    fixture.componentRef.setInput('channels', channels);
    fixture.componentRef.setInput('levels', levels);
    fixture.componentRef.setInput('groups', ['Speech', 'Drums']);
    await fixture.whenStable();
  });

  it('renders channels with live levels, diagnostics badge and clip LED', () => {
    const el: HTMLElement = fixture.nativeElement;
    const rows = el.querySelectorAll('.ch');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Pastor Mic');
    expect(rows[0].querySelector('.badge')?.textContent?.trim()).toBe('2');
    expect(rows[0].querySelector('.vals')?.textContent).toContain('-30');
    expect(rows[1].querySelector('.led.on:not(.act)')).not.toBeNull(); // clip LED
    expect(rows[0].classList).toContain('sel');
    expect(rows[1].classList).not.toContain('sel');
  });

  it('filters by search text', async () => {
    fixture.componentInstance.query.set('kick');
    await fixture.whenStable();
    expect(fixture.nativeElement.querySelectorAll('.ch').length).toBe(1);
  });

  it('sends selection changes to the API', () => {
    const cb = fixture.nativeElement.querySelectorAll('input[type=checkbox]')[1] as HTMLInputElement;
    cb.checked = true;
    cb.dispatchEvent(new Event('change'));
    expect(api.setSelection).toHaveBeenCalledWith({ selected: [1, 2] });
    fixture.componentInstance.selectGroup('Drums', false);
    expect(api.setSelection).toHaveBeenCalledWith({ group: 'Drums' });
    fixture.componentInstance.selectGroup('Speech', true);
    expect(api.setSelection).toHaveBeenCalledWith({ add_group: 'Speech' });
  });
});

describe('RecommendationPanel', () => {
  let fixture: ComponentFixture<RecommendationPanel>;
  let api: ApiStub;

  beforeEach(async () => {
    api = new ApiStub();
    await TestBed.configureTestingModule({ imports: [RecommendationPanel], providers: [{ provide: ApiService, useValue: api }] }).compileComponents();
    fixture = TestBed.createComponent(RecommendationPanel);
    fixture.componentRef.setInput('recs', [rec(), rec({ id: 'r2', channel: 2, channel_name: 'Kick', category: 'clipping', issue: 'Clipping',
      severity: 'critical', control: 'Input gain / trim (preamp)', filter_type: 'gain', target_hz: null, gain_db: null, q: null, gain_change_db: -6 })]);
    fixture.componentRef.setInput('focus', 1);
    await fixture.whenStable();
  });

  it('shows issue, frequency, adjustment, Q, confidence and explanation', () => {
    const card: HTMLElement = fixture.nativeElement.querySelector('.card');
    const t = card.textContent ?? '';
    expect(t).toContain('Potential muddiness');
    expect(t).toContain('320 Hz');
    expect(t).toContain('-4.5 dB');
    expect(t).toContain('1.2');
    expect(t).toContain('82%');
    expect(t).toContain('Persistent excess low-mid energy.');
    expect(fixture.nativeElement.querySelectorAll('.card')[1].textContent).toContain('-6.0 dB');
  });

  it('records operator actions through the API (never touches audio)', () => {
    const buttons = Array.from(fixture.nativeElement.querySelectorAll('.card button')) as HTMLButtonElement[];
    buttons.find((b) => b.textContent?.includes('Mark applied'))!.click();
    buttons.find((b) => b.textContent?.includes('Dismiss'))!.click();
    buttons.find((b) => b.textContent?.includes('Re-analyse'))!.click();
    expect(api.markApplied).toHaveBeenCalledWith('r1');
    expect(api.dismiss).toHaveBeenCalledWith('r1');
    expect(api.reanalyze).toHaveBeenCalledWith('r1');
  });

  it('can filter to the focused channel', async () => {
    fixture.componentInstance.onlyFocus.set(true);
    await fixture.whenStable();
    expect(fixture.nativeElement.querySelectorAll('.card').length).toBe(1);
  });
});
