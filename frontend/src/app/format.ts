// Pure presentation helpers (no signal processing happens in the browser).

export function fmtHz(f: number | null | undefined): string {
  if (f === null || f === undefined || !isFinite(f)) return '—';
  if (f >= 1000) {
    const k = f / 1000;
    return `${k >= 10 ? k.toFixed(0) : k.toFixed(1).replace(/\.0$/, '')} kHz`;
  }
  return `${Math.round(f)} Hz`;
}

export function fmtDb(v: number | null | undefined, digits = 1, signed = false): string {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  const s = v.toFixed(digits);
  return signed && v > 0 ? `+${s}` : s;
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '—';
  return `${Math.round(v * 100)}%`;
}

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function fmtAgo(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return '—';
  const s = Math.max(0, Math.round(now - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

/** Map a dBFS value to 0..1 for meters (floor..0 dBFS). */
export function meterFraction(db: number | null | undefined, floor = -60): number {
  if (db === null || db === undefined || !isFinite(db)) return 0;
  return Math.max(0, Math.min(1, (db - floor) / -floor));
}

export const SEVERITY_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

export const CATEGORY_LABEL: Record<string, string> = {
  muddy: 'Muddiness', boxy: 'Boxiness', boomy: 'Boominess', low_mid_excess: 'Low-mid build-up',
  harsh: 'Harshness', sibilance: 'Sibilance', weak_presence: 'Weak presence',
  spectral_concentration: 'Spectral concentration', rumble: 'Rumble', hum: 'Hum',
  broadband_noise: 'Broadband noise', hiss: 'Hiss', clipping: 'Clipping', low_headroom: 'Low headroom',
  weak_signal: 'Weak signal', feedback_risk: 'Feedback risk',
};

export function categoryLabel(c: string): string {
  return CATEGORY_LABEL[c] ?? c;
}

export function profileName(key: string, profiles: { key: string; name: string }[]): string {
  return profiles.find((p) => p.key === key)?.name ?? key;
}

export function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

/** Human-readable rendering of evidence values. */
export function evidenceValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return Number.isInteger(v) ? `${v}` : v.toFixed(Math.abs(v) < 1 ? 3 : 1);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (Array.isArray(v)) {
    if (v.length && typeof v[0] === 'object') return `${v.length} item(s)`;
    return v.map((x) => evidenceValue(x)).join(', ');
  }
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function humanKey(k: string): string {
  return k.replace(/_/g, ' ').replace(/\bdb\b/gi, 'dB').replace(/\bdbfs\b/gi, 'dBFS').replace(/\bhz\b/gi, 'Hz');
}
