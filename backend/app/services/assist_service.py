"""Application service: owns capture, analysis loop, diagnostics and
recommendations, and produces compact snapshots for the API / WebSocket.

Threads:
* PortAudio callback thread -> ring buffer only.
* ``analysis`` thread -> DSP, diagnostics, recommendations, DB writes.
* asyncio event loop (FastAPI) -> reads snapshots, handles operator actions.

Control operations (start/stop/settings) take ``self._ctl`` which the analysis
loop also takes per tick, so they never race with a tick in progress.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np
import psutil

from app.audio.devices import DeviceManager, test_device
from app.audio.sources import AudioSource, SoundDeviceSource
from app.config.settings import APP_NAME, APP_VERSION, AnalysisSettings, ServerConfig
from app.database.repository import Repository
from app.diagnostics.engine import Diagnostic, DiagnosticEngine
from app.diagnostics.profiles import PROFILES
from app.dsp import levels as lv
from app.dsp.analyzer import ChannelAnalysis, MultiChannelAnalyzer
from app.logging.setup import get_logger
from app.models.schemas import ChannelConfig, default_channels
from app.recommendations.lifecycle import Recommendation, RecommendationManager
from app.utils.timeutil import now

log = get_logger("service")

STALL_SECONDS = 2.0
# Diagnostics (and the slow statistics they use) are evaluated for each channel
# every DIAG_EVERY analysis ticks, staggered across channels. Levels, spectrum,
# spectrogram and peak tracking still run every tick.
DIAG_EVERY = 2
RECOVERY_INTERVAL_S = 5.0


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(float(x), nd)


class AssistService:
    def __init__(self, config: ServerConfig, repo: Repository, devices: DeviceManager | None = None,
                 run_thread: bool = True,
                 source_factory: Callable[[AnalysisSettings], AudioSource] | None = None):
        self.config = config
        self.repo = repo
        self.devices = devices or DeviceManager()
        self.settings = repo.load_settings()
        self.groups = repo.load_groups()
        self.channels = repo.load_channels() or default_channels(self.settings.channel_count)
        self._ensure_channel_count(self.settings.channel_count)
        self._ctl = threading.RLock()
        self.source: AudioSource | None = None
        self.capture_state = "stopped"
        self.capture_error: str | None = None
        self._cursor = 0
        self.analyzer: MultiChannelAnalyzer | None = None
        self.engine = DiagnosticEngine(self.settings, self.settings.analysis_rate_hz / DIAG_EVERY)
        self.recs = RecommendationManager(self.settings, persist=self._persist_rec, on_event=self._rec_event,
                                          snapshot=self.verification_snapshot, reset_channel=self.reset_channel,
                                          clock=self.analysis_clock)
        self._build_analyzer()
        # health
        self.tick_ms: deque[float] = deque(maxlen=100)
        self.overruns = 0
        self.frames_lost = 0
        self.ticks = 0
        self._analysed_s = 0.0
        self._proc = psutil.Process()
        self._proc.cpu_percent(None)
        self.cpu_percent = 0.0
        self._last_cpu = 0.0
        self._last_recovery = 0.0
        self.started_at = now()
        # outputs
        self._snap_lock = threading.Lock()
        self.levels: list[dict[str, Any]] = []
        self.analyses: dict[int, ChannelAnalysis] = {}
        self.diag_version = 0
        self._diag_persist_at: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_thread = run_thread
        self._source_factory = source_factory  # tests inject a synthetic source

    def analysis_clock(self) -> float:
        """Analysed audio time in seconds (advances with processed ticks)."""
        return self._analysed_s

    # ================================================================== lifecycle
    def start(self) -> None:
        if self.settings.auto_start:
            try:
                self.start_capture()
            except Exception as exc:  # noqa: BLE001 - app must still start without audio
                log.error("capture failed at startup", extra={"error": str(exc)})
        if self._run_thread:
            self._thread = threading.Thread(target=self._loop, name="analysis", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self.stop_capture()
        log.info("service stopped")

    # ================================================================== configuration
    def _ensure_channel_count(self, n: int) -> None:
        existing = {c.index: c for c in self.channels}
        defaults = default_channels(n)
        self.channels = [existing.get(i, defaults[i]) for i in range(n)] + \
                        [c for i, c in sorted(existing.items()) if i >= n]

    def visible_channels(self) -> list[ChannelConfig]:
        n = self.source.channels if self.source else self.settings.channel_count
        return [c for c in self.channels if c.index < n]

    def selected_indices(self) -> list[int]:
        n = self.source.channels if self.source else self.settings.channel_count
        return [c.index for c in self.channels if c.selected and c.enabled and c.index < n]

    def channel(self, index: int) -> ChannelConfig | None:
        return next((c for c in self.channels if c.index == index), None)

    def _build_analyzer(self) -> None:
        s = self.settings
        sr = self.source.sample_rate if self.source else s.sample_rate
        self.analyzer = MultiChannelAnalyzer(
            sr, s.fft_size, s.analysis_rate_hz, display_points=s.spectrum_points,
            long_tc_s=s.spectral_smoothing_tc_s, display_tc_s=s.display_smoothing_tc_s,
            gate_dbfs=s.activity_gate_dbfs, clip_threshold=s.clip_threshold,
            spectrogram_history=s.spectrogram_history)

    def update_settings(self, values: dict[str, Any]) -> AnalysisSettings:
        unknown = set(values) - set(AnalysisSettings.model_fields)
        if unknown:
            raise ValueError(f"unknown settings: {sorted(unknown)}")
        merged = AnalysisSettings(**(self.settings.model_dump() | values))
        return self._apply_settings(merged, reason="update")

    def reset_settings(self) -> AnalysisSettings:
        """Restore factory defaults but keep the current audio device."""
        keep = {k: getattr(self.settings, k) for k in ("device_index", "device_name", "device_hostapi",
                                                        "channel_count", "sample_rate", "block_size")}
        return self._apply_settings(AnalysisSettings(**keep), reason="reset")

    def _apply_settings(self, new: AnalysisSettings, reason: str) -> AnalysisSettings:
        with self._ctl:
            old = self.settings
            changed = {k for k in AnalysisSettings.model_fields if getattr(old, k) != getattr(new, k)}
            self.settings = new
            self.repo.save_settings(new)
            self.engine.update_settings(new, new.analysis_rate_hz / DIAG_EVERY)
            self.recs.settings = new
            if "channel_count" in changed:
                self._ensure_channel_count(new.channel_count)
                self.repo.save_channels(self.channels)
            dsp_fields = {"fft_size", "analysis_rate_hz", "spectral_smoothing_tc_s", "display_smoothing_tc_s",
                          "spectrum_points", "spectrogram_history", "clip_threshold", "activity_gate_dbfs"}
            if changed & AnalysisSettings.CAPTURE_FIELDS:
                if self.source is not None or new.auto_start:
                    self.restart_capture()
            elif changed & dsp_fields:
                self._build_analyzer()
            log.info("settings applied", extra={"reason": reason, "changed": sorted(changed)})
            self.repo.log_action("settings_" + reason, payload={"changed": sorted(changed)})
            return new

    def update_channels(self, channels: list[ChannelConfig]) -> list[ChannelConfig]:
        with self._ctl:
            by_idx = {c.index: c for c in self.channels}
            for c in channels:
                by_idx[c.index] = c
            self.channels = [by_idx[i] for i in sorted(by_idx)]
            self.repo.save_channels(self.channels)
            self._apply_selection_change()
            self.repo.log_action("channels_updated", payload={"count": len(channels)})
            return self.visible_channels()

    def update_selection(self, *, selected: list[int] | None = None, select_all: bool | None = None,
                         deselect_all: bool | None = None, group: str | None = None,
                         add_group: str | None = None) -> list[int]:
        with self._ctl:
            vis = {c.index for c in self.visible_channels()}
            for c in self.channels:
                if c.index not in vis:
                    continue
                if select_all:
                    c.selected = True
                elif deselect_all:
                    c.selected = False
                elif group is not None:
                    c.selected = c.group == group
                elif add_group is not None:
                    c.selected = c.selected or c.group == add_group
                elif selected is not None:
                    c.selected = c.number in set(selected)
            self.repo.save_channels(self.channels)
            self._apply_selection_change()
            sel = [i + 1 for i in self.selected_indices()]
            self.repo.log_action("selection_changed", payload={"selected": sel})
            return sel

    def _apply_selection_change(self) -> None:
        keep = set(self.selected_indices())
        for d in self.engine.drop_channels_except(keep):
            self.repo.upsert_diagnostic(d.to_dict())
        self.recs.expire_channels_except(keep)
        if self.analyzer:
            self.analyzer.drop_except(keep)
        self.diag_version += 1

    def update_groups(self, groups: list[str]) -> list[str]:
        clean = [g.strip() for g in groups if g.strip()]
        if not clean:
            raise ValueError("at least one group is required")
        self.groups = list(dict.fromkeys(clean))
        self.repo.save_groups(self.groups)
        return self.groups

    # ================================================================== capture
    def _make_source(self) -> AudioSource:
        s = self.settings
        if self._source_factory is not None:
            return self._source_factory(s)
        idx = s.device_index
        info = None
        if s.device_name:
            info = self.devices.find_by_name(s.device_name, s.device_hostapi)
            if info is None and s.device_index is not None:
                cand = self.devices.get_device(s.device_index, probe_rates=False)
                info = cand if cand and cand.name == s.device_name else None
        elif idx is not None:
            info = self.devices.get_device(idx, probe_rates=False)
        else:  # nothing chosen yet: use the system default input
            info = next((d for d in self.devices.list_input_devices(probe_rates=False) if d.is_default_input), None)
            if info is None:
                raise RuntimeError("No audio input device selected. Choose one under Audio & Settings.")
        if info is None:
            raise RuntimeError(f"Audio device not found: {s.device_name or idx}. Is it connected?")
        channels = min(s.channel_count, info.max_input_channels)
        return SoundDeviceSource(info.index, s.sample_rate, s.block_size, channels, info.name, info.hostapi)

    def start_capture(self) -> dict[str, Any]:
        with self._ctl:
            if self.source is not None and self.source.running:
                return self.capture_status()
            self.capture_state = "starting"
            self.capture_error = None
            try:
                src = self._make_source()
                src.start()
            except Exception as exc:  # noqa: BLE001
                self.capture_state = "error"
                self.capture_error = str(exc)
                self.source = None
                log.error("audio capture start failed", extra={"error": str(exc)})
                raise
            self.source = src
            self._cursor = src.ring.total_written
            self._build_analyzer()
            self.capture_state = "running"
            if isinstance(src, SoundDeviceSource):
                self.repo.remember_device(src.device_name, src.hostapi, src.device_index, src.sample_rate,
                                          src.block_size, src.channels)
            self.repo.log_action("capture_start", payload=src.describe())
            return self.capture_status()

    def stop_capture(self) -> dict[str, Any]:
        with self._ctl:
            src, self.source = self.source, None
            if src is not None:
                src.stop()
                self.repo.log_action("capture_stop")
            self.capture_state = "stopped"
            return self.capture_status()

    def restart_capture(self) -> dict[str, Any]:
        with self._ctl:
            self.stop_capture()
            return self.start_capture()

    def change_device(self, *, device_index: int, sample_rate: int | None, block_size: int | None,
                      channel_count: int | None, start: bool = True) -> dict[str, Any]:
        info = self.devices.get_device(device_index)
        if info is None:
            raise ValueError(f"input device {device_index} not found")
        sr = sample_rate or self.settings.sample_rate
        if info.supported_sample_rates and sr not in info.supported_sample_rates:
            raise ValueError(f"{sr} Hz not supported by this device (supported: {info.supported_sample_rates})")
        ch = min(channel_count or info.max_input_channels, info.max_input_channels)
        values: dict[str, Any] = {"device_index": info.index, "device_name": info.name,
                                  "device_hostapi": info.hostapi, "sample_rate": sr, "channel_count": ch}
        if block_size:
            values["block_size"] = block_size
        with self._ctl:
            self.stop_capture()
            merged = AnalysisSettings(**(self.settings.model_dump() | values))
            self.settings = merged
            self.repo.save_settings(merged)
            self.engine.update_settings(merged, merged.analysis_rate_hz / DIAG_EVERY)
            self.recs.settings = merged
            self._ensure_channel_count(merged.channel_count)
            self.repo.save_channels(self.channels)
            self.recs.expire_channels_except(set(), "audio device changed")
            self.engine.drop_channels_except(set())
            log.info("device selected", extra=values)
            self.repo.log_action("device_change", payload=values)
            if start:
                return self.start_capture()
            return self.capture_status()

    def test_device(self, index: int, channels: int | None, sample_rate: int | None, duration_s: float) -> dict[str, Any]:
        info = self.devices.get_device(index, probe_rates=False)
        if info is None:
            raise ValueError(f"input device {index} not found")
        src = self.source
        if isinstance(src, SoundDeviceSource) and src.running and src.device_index == index:
            # device already open by the capture engine: report its live levels instead of reopening it
            return {"ok": True, "mode": "live", "device": index,
                    "channels": [{"channel": l["channel"], "peak_dbfs": l["peak"], "rms_dbfs": l["rms"],
                                  "signal_present": (l["rms"] or -120) > -70} for l in self.levels]}
        ch = min(channels or info.max_input_channels, info.max_input_channels)
        sr = sample_rate or int(info.default_samplerate) or self.settings.sample_rate
        return test_device(index, ch, sr, self.settings.block_size, duration_s) | {"mode": "test-stream"}

    def capture_status(self) -> dict[str, Any]:
        src = self.source
        return {
            "state": self.capture_state, "error": self.capture_error,
            "source": src.describe() if src else None,
            "stats": src.stats.snapshot() if src else None,
            "configured": {"device_index": self.settings.device_index,
                           "device_name": self.settings.device_name, "sample_rate": self.settings.sample_rate,
                           "block_size": self.settings.block_size, "channel_count": self.settings.channel_count},
        }

    def _watchdog(self) -> None:
        src = self.source
        t = time.monotonic()
        if src is None:
            if (self.capture_state == "error" and self.settings.auto_start
                    and t - self._last_recovery > RECOVERY_INTERVAL_S):
                self._last_recovery = t
                log.info("attempting capture recovery")
                try:
                    self.devices.list_input_devices(refresh=True, probe_rates=False)
                    self.start_capture()
                    log.info("capture recovered")
                except Exception as exc:  # noqa: BLE001
                    log.warning("capture recovery failed", extra={"error": str(exc)})
            return
        age = src.stats.last_callback_mono and (t - src.stats.last_callback_mono)
        if src.finished.is_set() or (not src.running and self.capture_state == "running"):
            self.capture_state = "error"
            self.capture_error = "Audio stream stopped unexpectedly (device disconnected?)"
            log.error("audio device disconnected or stream aborted", extra=src.describe())
            try:
                src.stop()
            except Exception:  # noqa: BLE001
                pass
            self.source = None
            self._last_recovery = t
        elif age and age > STALL_SECONDS:
            if self.capture_state != "stalled":
                log.warning("audio callbacks stalled", extra={"seconds": round(age, 1)})
            self.capture_state = "stalled"
        elif self.capture_state == "stalled":
            self.capture_state = "running"
            log.info("audio callbacks resumed")

    # ================================================================== analysis loop
    def _loop(self) -> None:
        log.info("analysis loop started")
        next_t = time.perf_counter()
        while not self._stop.is_set():
            interval = 1.0 / self.settings.analysis_rate_hz
            try:
                with self._ctl:
                    self._watchdog()
                    self.process_tick()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("analysis tick failed")
            next_t += interval
            delay = next_t - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                self.overruns += 1
                if delay < -1.0:
                    next_t = time.perf_counter()

    def process_tick(self) -> None:
        """One analysis step. Public so tests can drive it deterministically."""
        src = self.source
        if src is None or self.analyzer is None:
            return
        t0 = time.perf_counter()
        new, self._cursor, lost = src.ring.read_since(self._cursor)
        if lost:
            self.frames_lost += lost
        window = src.ring.latest(self.analyzer.nfft)
        bl = lv.measure_block(new, self.settings.clip_threshold) if new.shape[0] else None
        selected = self.selected_indices()
        due = {ch for ch in selected if (self.ticks + ch) % DIAG_EVERY == 0}
        analyses = self.analyzer.process(new, window, selected, due) if new.shape[0] else {}
        diag_changed = False
        for ch, a in analyses.items():
            if ch not in due:
                continue
            cfg = self.channel(ch)
            active, cleared = self.engine.evaluate(ch, cfg.name, cfg.profile, a)
            self.recs.sync_channel(ch, active, cleared)
            diag_changed |= self._persist_diagnostics(active, cleared)
        self.recs.tick()
        if diag_changed:
            self.diag_version += 1
        levels = []
        sel = set(selected)
        diag_counts: dict[int, int] = {}
        for d in self.engine.active_for():
            if not d.suppressed_by:
                diag_counts[d.channel - 1] = diag_counts.get(d.channel - 1, 0) + 1
        for c in self.visible_channels():
            i = c.index
            a = analyses.get(i)
            rms = float(bl.rms_dbfs[i]) if bl is not None and i < bl.rms_dbfs.size else None
            peak = float(bl.peak_dbfs[i]) if bl is not None and i < bl.peak_dbfs.size else None
            levels.append({
                "channel": c.number, "rms": _r(max(rms, -120.0) if rms is not None else None),
                "peak": _r(max(peak, -120.0) if peak is not None else None),
                "peak_hold": _r(a.peak_hold_dbfs) if a else None,
                "clip": bool(bl is not None and i < bl.clipped_samples.size and bl.clipped_samples[i] > 0),
                "active": bool(a.active) if a else (rms is not None and rms > self.settings.activity_gate_dbfs),
                "selected": i in sel,
                "diagnostics": diag_counts.get(i, 0) if i in sel else 0,
            })
        with self._snap_lock:
            self.levels = levels
            self.analyses = analyses or self.analyses
        self.ticks += 1
        self._analysed_s += new.shape[0] / src.sample_rate
        self.tick_ms.append((time.perf_counter() - t0) * 1000.0)
        if time.monotonic() - self._last_cpu > 1.0:
            self._last_cpu = time.monotonic()
            self.cpu_percent = self._proc.cpu_percent(None)

    def _persist_diagnostics(self, active: list[Diagnostic], cleared: list[Diagnostic]) -> bool:
        changed = False
        t = now()
        for d in cleared:
            self.repo.upsert_diagnostic(d.to_dict())
            self._diag_persist_at.pop(d.id, None)
            changed = True
        for d in active:
            last = self._diag_persist_at.get(d.id)
            if last is None or t - last > 5.0:  # write on raise, then at most every 5 s
                self.repo.upsert_diagnostic(d.to_dict())
                self._diag_persist_at[d.id] = t
                changed = True
        return changed

    def _persist_rec(self, rec: Recommendation) -> None:
        self.repo.upsert_recommendation(rec.to_dict())

    def _rec_event(self, rec: Recommendation, old: str, new: str, note: str | None) -> None:
        if new in ("applied", "dismissed") and old != new:
            self.repo.log_action(f"recommendation_{new}", target=f"CH{rec.channel:02d}", recommendation_id=rec.id,
                                 payload={"category": rec.category, "note": note})
        elif note == "re-analysis requested":
            self.repo.log_action("reanalyze", target=f"CH{rec.channel:02d}", recommendation_id=rec.id)

    # ================================================================== verification / re-analysis
    def verification_snapshot(self, ch_index: int, category: str) -> dict[str, Any]:
        res, active = self.engine.latest_result(ch_index, category)
        a = self.analyses.get(ch_index)
        return {
            "at": now(), "diagnostic_active": active,
            "metric": None if res is None else round(float(res.magnitude), 2),
            "metric_unit": None if res is None else res.unit,
            "rms_dbfs": _r(a.rms_dbfs) if a else None,
            "signal_dbfs": _r(a.signal_dbfs) if a else None,
            "peak_hold_dbfs": _r(a.peak_hold_dbfs) if a else None,
            "bands": a.band_dict("long") if a else [],
        }

    def reset_channel(self, ch_index: int, category: str | None = None) -> None:
        with self._ctl:
            if self.analyzer and ch_index in self.analyzer.channels:
                self.analyzer.channels[ch_index].reset()
            self.engine.reset_channel(ch_index)
        log.info("channel analysis reset", extra={"channel": ch_index + 1, "reason": category or "re-analysis"})

    # Operator actions always take the control lock *before* the recommendation
    # lock (same order as the analysis tick) to rule out lock-order deadlocks.
    def apply_recommendation(self, rec_id: str, note: str | None = None) -> Recommendation:
        with self._ctl:
            return self.recs.mark_applied(rec_id, note)

    def dismiss_recommendation(self, rec_id: str, note: str | None = None) -> Recommendation:
        with self._ctl:
            return self.recs.dismiss(rec_id, note)

    def reanalyze_recommendation(self, rec_id: str) -> Recommendation:
        with self._ctl:
            return self.recs.reanalyze(rec_id)

    def reanalyze_channel(self, ch_index: int) -> dict[str, Any]:
        self.reset_channel(ch_index)
        self.repo.log_action("reanalyze_channel", target=f"CH{ch_index + 1:02d}")
        return {"channel": ch_index + 1, "status": "re-analysis started",
                "evidence_window_s": self.settings.diagnostic_persistence_s}

    # ================================================================== snapshots for API / WS
    def health(self) -> dict[str, Any]:
        src = self.source
        stats = src.stats.snapshot() if src else None
        tick = np.array(self.tick_ms) if self.tick_ms else np.zeros(1)
        budget = 1000.0 / self.settings.analysis_rate_hz
        capture_ok = self.capture_state == "running" and (stats or {}).get("input_overflows", 0) < 10
        proc_ok = float(np.percentile(tick, 95)) < 0.8 * budget
        return {
            "capture": {"state": self.capture_state, "ok": capture_ok, "stats": stats, "error": self.capture_error,
                        "frames_lost_analysis": self.frames_lost},
            "processing": {"ok": proc_ok, "tick_ms_avg": _r(tick.mean(), 2), "tick_ms_p95": _r(np.percentile(tick, 95), 2),
                           "tick_ms_max": _r(tick.max(), 2), "budget_ms": round(budget, 1), "overruns": self.overruns,
                           "ticks": self.ticks, "analysis_rate_hz": self.settings.analysis_rate_hz,
                           "fft_size": self.settings.fft_size},
            "cpu_percent": round(self.cpu_percent, 1),
            "cpu_count": psutil.cpu_count(),
            "uptime_s": round(now() - self.started_at, 1),
        }

    def status(self) -> dict[str, Any]:
        sel = self.selected_indices()
        attention = {d.channel for d in self.engine.active_for() if not d.suppressed_by}
        src = self.source
        return {
            "app": APP_NAME, "version": APP_VERSION,
            "device": src.describe() if src else None,
            "capture": self.capture_status(),
            "sample_rate": src.sample_rate if src else self.settings.sample_rate,
            "channels_total": len(self.visible_channels()),
            "channels_analyzed": len(sel),
            "channels_attention": len(attention),
            "active_recommendations": len(self.recs.list_active()),
            "health": self.health(),
            "auto_modifies_audio": False,
        }

    def diagnostics_payload(self, channel: int | None = None) -> list[dict[str, Any]]:
        idx = None if channel is None else channel - 1
        return [d.to_dict() for d in self.engine.active_for(idx)]

    def recommendations_payload(self) -> dict[str, Any]:
        return {"version": self.recs.version,
                "active": [r.to_dict() for r in self.recs.list_active()],
                "recent": [r.to_dict() for r in self.recs.recent_closed[:10]]}

    def channel_analysis(self, channel: int, include_spectrogram: bool = False) -> dict[str, Any] | None:
        with self._snap_lock:
            a = self.analyses.get(channel - 1)
        if a is None or self.analyzer is None:
            return None
        cfg = self.channel(channel - 1)
        out = {
            "channel": channel, "name": cfg.name if cfg else "", "profile": cfg.profile if cfg else "general",
            "tick": a.tick, "active": a.active,
            "rms_dbfs": _r(a.rms_dbfs), "peak_dbfs": _r(a.peak_dbfs), "peak_hold_dbfs": _r(a.peak_hold_dbfs),
            "crest_db": _r(a.crest_db), "clipping": a.clip_ticks_recent > 0, "clip_events_5s": a.clip_ticks_recent,
            "noise_floor_dbfs": _r(a.noise_floor_dbfs), "snr_estimate_db": _r(a.snr_estimate_db),
            "activity_ratio": _r(a.activity_ratio, 2),
            "spectrum": {"db": [round(float(v), 1) for v in a.display_db],
                         "maxhold": [round(float(v), 1) for v in a.maxhold_db]},
            "bands": a.band_dict("fast"),
            "bands_long": a.band_dict("long"),
            "stats": {k: _r(v, 3 if k == "flatness" else 0) for k, v in a.stats.items()},
            "peaks": [t for t in a.tracks if t["persistence"] >= 0.7 and t["observed_s"] >= 1.0][:8],
        }
        if include_spectrogram:
            an = self.analyzer.channels.get(channel - 1)
            out["spectrogram"] = [list(col) for col in an.spectrogram] if an else []
        else:
            an = self.analyzer.channels.get(channel - 1)
            out["spectrogram_column"] = list(an.spectrogram[-1]) if an and an.spectrogram else None
        return out

    def grid(self) -> dict[str, Any]:
        if self.analyzer is None:
            return {}
        return {"freqs": [round(float(f), 2) for f in self.analyzer.grid_display.centers],
                "spectrogram_freqs": [round(float(f), 2) for f in self.analyzer.grid_spectro.centers],
                "spectrogram_floor_db": -120.0, "fft_size": self.analyzer.nfft,
                "bin_hz": round(self.analyzer.fft.bin_hz, 3), "sample_rate": self.analyzer.fs}

    def profiles(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in PROFILES.values()]
