from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Tuple

import yaml


@dataclass(frozen=True)
class DeviceProfile:
    """Phase 1-3 device metadata & calibration contract.

    When calibrated=False, all levels are relative dBFS and must never be
    presented as statutory dB SPL.
    """

    node_id: str = "primary_node"
    location_tag: str = "room_center"
    calibrated: bool = False
    calibration_file: Optional[str] = None
    sensitivity_offset_db: float = 0.0

    def to_spl_db(self, dbfs: float) -> Optional[float]:
        """Convert dBFS to calibrated dB SPL only when device is calibrated."""
        if not self.calibrated:
            return None
        return dbfs + self.sensitivity_offset_db


@dataclass(frozen=True)
class AudioConfig:
    sample_rate: int = 48000
    channels: int = 1
    block_duration_sec: float = 0.25
    device: Optional[Any] = None
    reconnect_interval_sec: float = 3.0

    @property
    def block_samples(self) -> int:
        return int(round(self.sample_rate * self.block_duration_sec))


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool = True
    start_time: str = "22:30"
    end_time: str = "07:00"


@dataclass(frozen=True)
class DSPConfig:
    low_band_hz: Tuple[float, float] = (20.0, 180.0)
    high_band_hz: Tuple[float, float] = (300.0, 4000.0)
    baseline_window_sec: float = 120.0
    baseline_quantile: float = 0.20
    baseline_warmup_sec: float = 10.0


@dataclass(frozen=True)
class TriggerConfig:
    low_delta_db: float = 12.0
    low_min_dbfs: float = -55.0
    high_delta_db: float = 14.0
    high_min_dbfs: float = -50.0
    min_active_sec: float = 0.75
    pre_roll_sec: float = 15.0
    post_roll_sec: float = 10.0
    max_event_sec: float = 300.0


@dataclass(frozen=True)
class StorageConfig:
    base_dir: str = "data"
    evidence_dir: str = "data/evidence"
    raw_dir: str = "data/raw"
    db_path: str = "data/ham_events.sqlite3"
    reports_dir: str = "data/reports"
    raw_retention_days: int = 7
    audio_format: str = "FLAC"


@dataclass(frozen=True)
class SyncConfig:
    """Edge-First Store-and-Forward remote sync config (disabled in single-host mode)."""

    enabled: bool = False
    server_url: str = ""
    token: str = ""
    timeout_sec: float = 30.0


@dataclass(frozen=True)
class AppConfig:
    node: DeviceProfile = field(default_factory=DeviceProfile)
    audio: AudioConfig = field(default_factory=AudioConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    dsp: DSPConfig = field(default_factory=DSPConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)


def load_config(path: str | Path) -> AppConfig:
    """Load YAML configuration into typed AppConfig dataclasses."""
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    node_raw = raw.get("node", {})
    audio_raw = raw.get("audio", {})
    schedule_raw = raw.get("schedule", {})
    dsp_raw = raw.get("dsp", {})
    trigger_raw = raw.get("trigger", {})
    storage_raw = raw.get("storage", {})
    sync_raw = raw.get("sync", {})

    if "low_band_hz" in dsp_raw:
        dsp_raw = {**dsp_raw, "low_band_hz": tuple(dsp_raw["low_band_hz"])}
    if "high_band_hz" in dsp_raw:
        dsp_raw = {**dsp_raw, "high_band_hz": tuple(dsp_raw["high_band_hz"])}

    return AppConfig(
        node=DeviceProfile(**node_raw),
        audio=AudioConfig(**audio_raw),
        schedule=ScheduleConfig(**schedule_raw),
        dsp=DSPConfig(**dsp_raw),
        trigger=TriggerConfig(**trigger_raw),
        storage=StorageConfig(**storage_raw),
        sync=SyncConfig(**sync_raw),
    )
