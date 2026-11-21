// Shapes of the data served by the FastAPI backend (see docs/API.md).

export type Severity = 'low' | 'medium' | 'high' | 'critical';
export type RecStatus = 'new' | 'pending' | 'applied' | 'dismissed' | 'resolved' | 'expired';

export interface ChannelConfig {
  index: number;
  number: number;
  name: string;
  profile: string;
  group: string;
  selected: boolean;
  enabled: boolean;
}

export interface Profile {
  key: string;
  name: string;
  description: string;
  rules: string[];
  priority: string[];
  tolerance_db: number;
  hpf_range: [number, number];
  notes: string;
}

export interface Grid {
  freqs: number[];
  spectrogram_freqs: number[];
  spectrogram_floor_db: number;
  fft_size: number;
  bin_hz: number;
  sample_rate: number;
}

export interface Settings {
  device_index: number | null;
  device_name: string | null;
  device_hostapi: string | null;
  sample_rate: number;
  block_size: number;
  channel_count: number;
  auto_start: boolean;
  fft_size: number;
  analysis_rate_hz: number;
  spectral_smoothing_tc_s: number;
  display_smoothing_tc_s: number;
  spectrum_points: number;
  spectrogram_history: number;
  clip_threshold: number;
  activity_gate_dbfs: number;
  min_confidence: number;
  diagnostic_persistence_s: number;
  clear_time_s: number;
  recommendation_cooldown_s: number;
  recommendation_expiry_s: number;
  verification_window_s: number;
  new_to_pending_s: number;
  mains_frequency: 'auto' | '50' | '60';
  max_eq_boost_db: number;
  max_eq_cut_db: number;
  hpf_min_hz: number;
  hpf_max_hz: number;
  max_gain_change_db: number;
  ui_refresh_hz: number;
  [key: string]: unknown;
}

export interface Hello {
  type: 'hello';
  grid: Grid;
  channels: ChannelConfig[];
  groups: string[];
  profiles: Profile[];
  settings: Settings;
}

export interface Level {
  channel: number;
  rms: number | null;
  peak: number | null;
  peak_hold: number | null;
  clip: boolean;
  active: boolean;
  selected: boolean;
  diagnostics: number;
}

export interface CaptureStats {
  callbacks: number;
  frames: number;
  input_overflows: number;
  input_underflows: number;
  last_callback_age_s: number | null;
  callbacks_per_s: number;
  uptime_s: number;
  max_callback_ms: number;
}

export interface Health {
  capture: { state: string; ok: boolean; stats: CaptureStats | null; error: string | null; frames_lost_analysis: number };
  processing: {
    ok: boolean; tick_ms_avg: number; tick_ms_p95: number; tick_ms_max: number; budget_ms: number;
    overruns: number; ticks: number; analysis_rate_hz: number; fft_size: number;
  };
  cpu_percent: number;
  cpu_count: number;
  uptime_s: number;
}

export interface SourceInfo {
  kind: 'device';
  sample_rate: number;
  block_size: number;
  channels: number;
  device_name?: string;
  hostapi?: string;
  device_index?: number;
  latency_s?: number;
}

export interface CaptureStatus {
  state: 'stopped' | 'starting' | 'running' | 'stalled' | 'error';
  error: string | null;
  source: SourceInfo | null;
  stats: CaptureStats | null;
  configured: Partial<Settings>;
}

export interface Status {
  app: string;
  version: string;
  device: SourceInfo | null;
  capture: CaptureStatus;
  sample_rate: number;
  channels_total: number;
  channels_analyzed: number;
  channels_attention: number;
  active_recommendations: number;
  health: Health;
  auto_modifies_audio: boolean;
}

export interface Band { label: string; lo: number; hi: number; level_db: number }

export interface TrackedPeak {
  id: number; freq_hz: number; level_db: number; prominence_db: number; q: number;
  persistence: number; observed_s: number; harmonic: boolean;
}

export interface Analysis {
  type?: 'analysis';
  full?: boolean;
  channel: number;
  name: string;
  profile: string;
  tick: number;
  active: boolean;
  rms_dbfs: number | null;
  peak_dbfs: number | null;
  peak_hold_dbfs: number | null;
  crest_db: number | null;
  clipping: boolean;
  clip_events_5s: number;
  noise_floor_dbfs: number | null;
  snr_estimate_db: number | null;
  activity_ratio: number | null;
  spectrum: { db: number[]; maxhold: number[] };
  bands: Band[];
  bands_long: Band[];
  stats: { centroid_hz: number | null; rolloff_hz: number | null; flatness: number | null };
  peaks: TrackedPeak[];
  spectrogram?: number[][];
  spectrogram_column?: number[] | null;
}

export interface Verification {
  state: 'pending' | 'resolved' | 'improved' | 'persists';
  due_at?: number;
  checked_at?: number;
  metric_before?: number | null;
  metric_after?: number | null;
  delta?: number | null;
  note?: string;
}

export interface Snapshot {
  at: number;
  diagnostic_active: boolean;
  metric: number | null;
  metric_unit: string | null;
  rms_dbfs: number | null;
  signal_dbfs: number | null;
  peak_hold_dbfs: number | null;
  bands: Band[];
}

export interface Recommendation {
  id: string;
  diagnostic_id: string;
  channel: number;
  channel_name: string;
  profile: string;
  category: string;
  issue: string;
  severity: Severity;
  confidence: number;
  confidence_label: 'low' | 'moderate' | 'high';
  control: string;
  filter_type: string | null;
  target_hz: number | null;
  gain_db: number | null;
  q: number | null;
  hpf_hz: number | null;
  hpf_slope: string | null;
  gain_change_db: number | null;
  actions: string[];
  explanation: string;
  expected_improvement: string;
  caveats: string[];
  fallback: Record<string, unknown> | null;
  supporting_analysis: { evidence: Record<string, unknown>; confidence: Record<string, unknown>; measured: { value: number; unit: string } };
  summary: string;
  status: RecStatus;
  created_at: number;
  updated_at: number;
  timestamp: number;
  history: { from: string | null; to: string; at: number; note: string | null }[];
  before: Snapshot | null;
  after: Snapshot | null;
  verification: Verification | null;
  applied_at: number | null;
  closed_at: number | null;
  note: string | null;
  latest_measurement?: { value: number; unit: string; confidence: number };
}

export interface RecPayload { version: number; active: Recommendation[]; recent: Recommendation[] }

export interface Diagnostic {
  id: string;
  channel: number;
  channel_name: string;
  profile: string;
  category: string;
  title: string;
  group: string;
  severity: Severity;
  confidence: number;
  confidence_label: string;
  confidence_breakdown: Record<string, unknown>;
  first_seen: number;
  last_seen: number;
  evidence: Record<string, unknown>;
  explanation: string;
  target_hz: number | null;
  status: string;
  suppressed_by: string | null;
  recommended_action: string;
}

export interface DeviceInfo {
  index: number;
  name: string;
  hostapi: string;
  max_input_channels: number;
  max_output_channels: number;
  default_samplerate: number;
  default_low_input_latency: number;
  default_high_input_latency: number;
  is_default_input: boolean;
  supported_sample_rates: number[];
}

export interface DevicesResponse {
  available: boolean;
  error?: string;
  devices: DeviceInfo[];
  history: { name: string; hostapi: string; sample_rate: number; channel_count: number; last_used: number }[];
  options?: { sample_rates: number[]; block_sizes: number[] };
}

export interface DeviceTestResult {
  ok: boolean;
  mode?: string;
  error?: string;
  channels?: { channel: number; peak_dbfs: number; rms_dbfs: number; signal_present: boolean }[];
}

export interface SettingsResponse {
  settings: Settings;
  defaults: Settings;
  options: { sample_rates: number[]; block_sizes: number[]; fft_sizes: number[] };
}
