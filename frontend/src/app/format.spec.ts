import { categoryLabel, evidenceValue, fmtDb, fmtHz, fmtPct, humanKey, meterFraction, pad2 } from './format';

describe('format helpers', () => {
  it('formats frequencies', () => {
    expect(fmtHz(315.4)).toBe('315 Hz');
    expect(fmtHz(2810)).toBe('2.8 kHz');
    expect(fmtHz(1000)).toBe('1 kHz');
    expect(fmtHz(12500)).toBe('13 kHz');
    expect(fmtHz(null)).toBe('—');
  });

  it('formats dB values with optional sign', () => {
    expect(fmtDb(-3.04)).toBe('-3.0');
    expect(fmtDb(2, 1, true)).toBe('+2.0');
    expect(fmtDb(undefined)).toBe('—');
    expect(fmtDb(Number.NaN)).toBe('—');
  });

  it('maps dBFS to meter fraction and clamps', () => {
    expect(meterFraction(0)).toBe(1);
    expect(meterFraction(-30)).toBeCloseTo(0.5);
    expect(meterFraction(-90)).toBe(0);
    expect(meterFraction(null)).toBe(0);
  });

  it('misc helpers', () => {
    expect(fmtPct(0.874)).toBe('87%');
    expect(pad2(7)).toBe('07');
    expect(categoryLabel('feedback_risk')).toBe('Feedback risk');
    expect(categoryLabel('unknown_x')).toBe('unknown_x');
    expect(humanKey('peak_excess_db')).toBe('peak excess dB');
    expect(evidenceValue([200, 500])).toBe('200, 500');
    expect(evidenceValue(true)).toBe('yes');
    expect(evidenceValue(0.12345)).toBe('0.123');
  });
});
