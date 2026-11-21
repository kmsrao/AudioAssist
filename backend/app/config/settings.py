"""Application configuration.

Two layers:

* ``ServerConfig`` - process-level settings (host, port, paths). Read from
  environment variables / CLI flags. Not editable at runtime.
* ``AnalysisSettings`` - operator-tunable settings (audio, DSP, diagnostics,
  recommendation limits, UI rates). Persisted in SQLite and editable through
  the API. ``AnalysisSettings()`` always yields the factory defaults, which is
  what "reset to defaults" restores.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"

APP_NAME = "Live Audio Assist"
APP_VERSION = "0.1.0"

ALLOWED_SAMPLE_RATES = (44100, 48000, 88200, 96000)
ALLOWED_BLOCK_SIZES = (128, 256, 512, 1024, 2048)
ALLOWED_FFT_SIZES = (2048, 4096, 8192)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    data_dir: Path = PROJECT_ROOT / "data"
    logs_dir: Path = PROJECT_ROOT / "logs"
    frontend_dist: Path = PROJECT_ROOT / "frontend" / "dist" / "live-audio-assist" / "browser"
    log_level: str = "INFO"
    db_filename: str = "live_audio_assist.db"

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @classmethod
    def from_env(cls) -> "ServerConfig":
        cfg = cls()
        env = os.environ
        if "LAA_HOST" in env:
            cfg.host = env["LAA_HOST"]
        if "LAA_PORT" in env:
            cfg.port = int(env["LAA_PORT"])
        if env.get("LAA_NO_BROWSER", "").lower() in ("1", "true", "yes"):
            cfg.open_browser = False
        if "LAA_DATA_DIR" in env:
            cfg.data_dir = Path(env["LAA_DATA_DIR"])
        if "LAA_LOGS_DIR" in env:
            cfg.logs_dir = Path(env["LAA_LOGS_DIR"])
        if "LAA_LOG_LEVEL" in env:
            cfg.log_level = env["LAA_LOG_LEVEL"].upper()
        return cfg


class AnalysisSettings(BaseModel):
    """Operator-tunable settings. Defaults are conservative on purpose."""

    # --- audio source -----------------------------------------------------
    device_index: int | None = None
    device_name: str | None = None
    device_hostapi: str | None = None
    sample_rate: int = 48000
    block_size: int = 512
    channel_count: int = Field(8, ge=1, le=64)
    auto_start: bool = True

    # --- DSP ----------------------------------------------------------------
    fft_size: int = 4096
    analysis_rate_hz: float = Field(10.0, ge=2.0, le=30.0)
    spectral_smoothing_tc_s: float = Field(1.0, ge=0.1, le=10.0)
    display_smoothing_tc_s: float = Field(0.25, ge=0.0, le=5.0)
    spectrum_points: int = Field(240, ge=64, le=1024)
    spectrogram_history: int = Field(120, ge=20, le=600)
    clip_threshold: float = Field(0.999, gt=0.5, le=1.0)
    activity_gate_dbfs: float = Field(-60.0, ge=-100.0, le=-20.0)

    # --- diagnostics --------------------------------------------------------
    min_confidence: float = Field(0.55, ge=0.0, le=1.0)
    diagnostic_persistence_s: float = Field(3.0, ge=0.5, le=60.0)
    clear_time_s: float = Field(4.0, ge=0.5, le=120.0)
    recommendation_cooldown_s: float = Field(120.0, ge=0.0, le=3600.0)
    recommendation_expiry_s: float = Field(45.0, ge=5.0, le=3600.0)
    verification_window_s: float = Field(8.0, ge=2.0, le=120.0)
    new_to_pending_s: float = Field(10.0, ge=0.0, le=600.0)
    mains_frequency: Literal["auto", "50", "60"] = "auto"

    # --- recommendation limits ---------------------------------------------
    max_eq_boost_db: float = Field(3.0, ge=0.0, le=12.0)
    max_eq_cut_db: float = Field(6.0, ge=0.5, le=18.0)
    hpf_min_hz: float = Field(20.0, ge=10.0, le=500.0)
    hpf_max_hz: float = Field(250.0, ge=20.0, le=1000.0)
    max_gain_change_db: float = Field(12.0, ge=1.0, le=30.0)

    # --- UI -------------------------------------------------------------------
    ui_refresh_hz: float = Field(10.0, ge=1.0, le=30.0)

    @field_validator("sample_rate")
    @classmethod
    def _sr(cls, v: int) -> int:
        if v not in ALLOWED_SAMPLE_RATES:
            raise ValueError(f"sample_rate must be one of {ALLOWED_SAMPLE_RATES}")
        return v

    @field_validator("block_size")
    @classmethod
    def _bs(cls, v: int) -> int:
        if v not in ALLOWED_BLOCK_SIZES:
            raise ValueError(f"block_size must be one of {ALLOWED_BLOCK_SIZES}")
        return v

    @field_validator("fft_size")
    @classmethod
    def _fft(cls, v: int) -> int:
        if v not in ALLOWED_FFT_SIZES:
            raise ValueError(f"fft_size must be one of {ALLOWED_FFT_SIZES}")
        return v

    @model_validator(mode="after")
    def _hpf_range(self) -> "AnalysisSettings":
        if self.hpf_min_hz >= self.hpf_max_hz:
            raise ValueError("hpf_min_hz must be lower than hpf_max_hz")
        return self

    # Fields whose change requires the capture stream to be restarted.
    CAPTURE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"device_index", "sample_rate", "block_size", "channel_count"}
    )
