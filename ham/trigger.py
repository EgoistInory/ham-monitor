from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

import numpy as np

from ham.config import TriggerConfig
from ham.dsp import BlockMetrics
from ham.ring_buffer import AudioRingBuffer


class TriggerState(str, Enum):
    IDLE = "IDLE"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"


@dataclass
class CompletedEvent:
    """Raw unmodified PCM + algorithm metrics produced when an event finishes."""

    start_time: datetime
    end_time: datetime
    trigger_time: datetime
    sample_rate: int
    raw_pcm: np.ndarray
    trigger_band: str
    peak_dbfs: float
    baseline_dbfs: float
    delta_db: float
    low_peak_dbfs: float
    high_peak_dbfs: float
    inferred_tag: str

    @property
    def duration_sec(self) -> float:
        return round(float(self.raw_pcm.size) / float(self.sample_rate), 3)


class EventTrigger:
    """State machine (IDLE -> PENDING -> ACTIVE -> COOLDOWN) with lossless pre-roll.

    Guarantees:
    - Pre-roll (`pre_roll_sec`) before the first exceeded block is preserved with
      sample-level continuity and zero duplication.
    - Consecutive bursts within `post_roll_sec` merge into a single event.
    - Raw PCM chunks are never filtered or modified.
    """

    def __init__(self, sample_rate: int, block_duration_sec: float, config: TriggerConfig) -> None:
        self._sample_rate = sample_rate
        self._block_duration = block_duration_sec
        self._cfg = config

        self._min_active_blocks = max(1, int(round(config.min_active_sec / block_duration_sec)))
        self._post_roll_blocks = max(1, int(round(config.post_roll_sec / block_duration_sec)))
        self._max_event_samples = int(round(config.max_event_sec * sample_rate))
        self._pre_roll_samples = int(round(config.pre_roll_sec * sample_rate))

        # Ring buffer holds full pre-roll + max pending confirmation blocks + 1 safety block
        ring_capacity = self._pre_roll_samples + (self._min_active_blocks + 2) * int(
            round(sample_rate * block_duration_sec)
        )
        self._ring = AudioRingBuffer(capacity_samples=ring_capacity, dtype=np.float32)

        self._state = TriggerState.IDLE
        self._pending_blocks = 0
        self._pending_samples = 0
        self._pending_start_ts: Optional[datetime] = None
        self._pending_metrics: List[BlockMetrics] = []
        self._pending_bands: List[str] = []

        self._event_chunks: List[np.ndarray] = []
        self._event_samples = 0
        self._event_start_ts: Optional[datetime] = None
        self._event_trigger_ts: Optional[datetime] = None
        self._event_end_ts: Optional[datetime] = None
        self._cooldown_blocks = 0

        self._low_hits = 0
        self._high_hits = 0
        self._peak_dbfs = -200.0
        self._low_peak_dbfs = -200.0
        self._high_peak_dbfs = -200.0
        self._max_low_delta = 0.0
        self._max_high_delta = 0.0
        self._low_baseline_ref = -200.0
        self._high_baseline_ref = -200.0

    @property
    def state(self) -> TriggerState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state in (TriggerState.ACTIVE, TriggerState.COOLDOWN)

    def _check_threshold(self, metrics: BlockMetrics) -> tuple[bool, bool]:
        if not metrics.baseline_ready:
            return False, False
        low_hit = (
            metrics.low_rms_dbfs >= self._cfg.low_min_dbfs
            and metrics.low_delta_db >= self._cfg.low_delta_db
        )
        high_hit = (
            metrics.high_rms_dbfs >= self._cfg.high_min_dbfs
            and metrics.high_delta_db >= self._cfg.high_delta_db
        )
        return low_hit, high_hit

    def _record_metrics(self, metrics: BlockMetrics, low_hit: bool, high_hit: bool) -> None:
        self._peak_dbfs = max(self._peak_dbfs, metrics.raw_peak_dbfs)
        self._low_peak_dbfs = max(self._low_peak_dbfs, metrics.low_rms_dbfs)
        self._high_peak_dbfs = max(self._high_peak_dbfs, metrics.high_rms_dbfs)
        if low_hit:
            self._low_hits += 1
            if metrics.low_delta_db >= self._max_low_delta:
                self._max_low_delta = metrics.low_delta_db
                self._low_baseline_ref = metrics.low_baseline_dbfs
        if high_hit:
            self._high_hits += 1
            if metrics.high_delta_db >= self._max_high_delta:
                self._max_high_delta = metrics.high_delta_db
                self._high_baseline_ref = metrics.high_baseline_dbfs

    def _reset_event_state(self) -> None:
        self._state = TriggerState.IDLE
        self._pending_blocks = 0
        self._pending_samples = 0
        self._pending_start_ts = None
        self._pending_metrics.clear()
        self._pending_bands.clear()

        self._event_chunks.clear()
        self._event_samples = 0
        self._event_start_ts = None
        self._event_trigger_ts = None
        self._event_end_ts = None
        self._cooldown_blocks = 0

        self._low_hits = 0
        self._high_hits = 0
        self._peak_dbfs = -200.0
        self._low_peak_dbfs = -200.0
        self._high_peak_dbfs = -200.0
        self._max_low_delta = 0.0
        self._max_high_delta = 0.0
        self._low_baseline_ref = -200.0
        self._high_baseline_ref = -200.0

    def _promote_pending_to_active(self, block_end_ts: datetime) -> None:
        snapshot = self._ring.read_last(self._pre_roll_samples + self._pending_samples)
        actual_pre_roll = max(0, snapshot.size - self._pending_samples)

        assert self._pending_start_ts is not None
        self._event_trigger_ts = self._pending_start_ts
        self._event_start_ts = self._pending_start_ts - timedelta(
            seconds=actual_pre_roll / float(self._sample_rate)
        )
        self._event_end_ts = block_end_ts
        self._event_chunks = [snapshot]
        self._event_samples = snapshot.size

        for m, band in zip(self._pending_metrics, self._pending_bands):
            self._record_metrics(m, low_hit=("low" in band), high_hit=("high" in band))

        self._pending_blocks = 0
        self._pending_samples = 0
        self._pending_metrics.clear()
        self._pending_bands.clear()
        self._cooldown_blocks = 0
        self._state = TriggerState.ACTIVE

    def _finalize_event(self) -> CompletedEvent:
        assert self._event_start_ts is not None
        assert self._event_trigger_ts is not None
        assert self._event_end_ts is not None

        raw_pcm = np.concatenate(self._event_chunks).astype(np.float32, copy=False)

        if self._low_hits > 0 and self._high_hits > 0:
            trigger_band = "dual_band_impact_and_vocal"
            inferred_tag = "suspected_external_mixed"
            if self._max_low_delta >= self._max_high_delta:
                delta_db = self._max_low_delta
                baseline_dbfs = self._low_baseline_ref
            else:
                delta_db = self._max_high_delta
                baseline_dbfs = self._high_baseline_ref
        elif self._low_hits > 0:
            trigger_band = "low_impact_20_180hz"
            inferred_tag = "suspected_external_impact"
            delta_db = self._max_low_delta
            baseline_dbfs = self._low_baseline_ref
        else:
            trigger_band = "high_vocal_300_4000hz"
            inferred_tag = "suspected_external_vocal"
            delta_db = self._max_high_delta
            baseline_dbfs = self._high_baseline_ref

        event = CompletedEvent(
            start_time=self._event_start_ts,
            end_time=self._event_end_ts,
            trigger_time=self._event_trigger_ts,
            sample_rate=self._sample_rate,
            raw_pcm=raw_pcm,
            trigger_band=trigger_band,
            peak_dbfs=round(self._peak_dbfs, 2),
            baseline_dbfs=round(baseline_dbfs, 2),
            delta_db=round(delta_db, 2),
            low_peak_dbfs=round(self._low_peak_dbfs, 2),
            high_peak_dbfs=round(self._high_peak_dbfs, 2),
            inferred_tag=inferred_tag,
        )
        self._reset_event_state()
        return event

    def process_block(
        self, raw_samples: np.ndarray, metrics: BlockMetrics, block_start_ts: datetime
    ) -> Optional[CompletedEvent]:
        """Feed one raw PCM block + its analysis metrics into the state machine."""
        chunk = np.asarray(raw_samples, dtype=np.float32).reshape(-1)
        block_end_ts = block_start_ts + timedelta(seconds=chunk.size / float(self._sample_rate))

        # Always keep ring buffer updated for future pre-rolls
        self._ring.write(chunk)

        low_hit, high_hit = self._check_threshold(metrics)
        exceeded = low_hit or high_hit
        band_label = (
            "low+high" if (low_hit and high_hit) else ("low" if low_hit else ("high" if high_hit else ""))
        )

        if self._state == TriggerState.IDLE:
            if exceeded:
                self._state = TriggerState.PENDING
                self._pending_blocks = 1
                self._pending_samples = chunk.size
                self._pending_start_ts = block_start_ts
                self._pending_metrics = [metrics]
                self._pending_bands = [band_label]
                if self._pending_blocks >= self._min_active_blocks:
                    self._promote_pending_to_active(block_end_ts)
            return None

        if self._state == TriggerState.PENDING:
            if exceeded:
                self._pending_blocks += 1
                self._pending_samples += chunk.size
                self._pending_metrics.append(metrics)
                self._pending_bands.append(band_label)
                if self._pending_blocks >= self._min_active_blocks:
                    self._promote_pending_to_active(block_end_ts)
            else:
                self._reset_event_state()
            return None

        # ACTIVE or COOLDOWN: append unmodified chunk
        self._event_chunks.append(chunk.copy())
        self._event_samples += chunk.size
        self._event_end_ts = block_end_ts

        if self._state == TriggerState.ACTIVE:
            if exceeded:
                self._record_metrics(metrics, low_hit, high_hit)
            else:
                self._state = TriggerState.COOLDOWN
                self._cooldown_blocks = 1
                if self._cooldown_blocks >= self._post_roll_blocks:
                    return self._finalize_event()
        elif self._state == TriggerState.COOLDOWN:
            if exceeded:
                self._record_metrics(metrics, low_hit, high_hit)
                self._state = TriggerState.ACTIVE
                self._cooldown_blocks = 0
            else:
                self._cooldown_blocks += 1
                if self._cooldown_blocks >= self._post_roll_blocks:
                    return self._finalize_event()

        if self._event_samples >= self._max_event_samples:
            return self._finalize_event()

        return None

    def flush(self) -> Optional[CompletedEvent]:
        """Flush any in-progress ACTIVE/COOLDOWN event on graceful shutdown."""
        if self._state in (TriggerState.ACTIVE, TriggerState.COOLDOWN) and self._event_chunks:
            return self._finalize_event()
        self._reset_event_state()
        return None
