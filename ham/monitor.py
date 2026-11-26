from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional

from ham.capture import AudioSource, local_now
from ham.config import AppConfig, ScheduleConfig
from ham.dsp import AnalysisPreprocessor, DualBandAnalyzer
from ham.evidence import EvidenceStore
from ham.trigger import EventTrigger

logger = logging.getLogger(__name__)


def parse_hhmm(value: str) -> dt_time:
    hour_s, min_s = value.strip().split(":")
    return dt_time(hour=int(hour_s), minute=int(min_s))


def is_within_schedule(now: datetime, schedule: ScheduleConfig) -> bool:
    """Return True if `now` falls inside the configured monitoring window (handles cross-midnight)."""
    if not schedule.enabled:
        return True
    start = parse_hhmm(schedule.start_time)
    end = parse_hhmm(schedule.end_time)
    current = now.time().replace(tzinfo=None)
    if start <= end:
        return start <= current < end
    # Cross-midnight window, e.g. 22:30 -> 07:00
    return current >= start or current < end


class NoiseMonitor:
    """Main orchestrator tying together Capture -> DualBandAnalyzer -> EventTrigger -> EvidenceStore."""

    def __init__(
        self,
        config: AppConfig,
        source: AudioSource,
        store: EvidenceStore,
        preprocessor: Optional[AnalysisPreprocessor] = None,
        ignore_schedule: bool = False,
        ntp_synced: bool = False,
        sync_client: Optional[Any] = None,
    ) -> None:
        self._cfg = config
        self._source = source
        self._store = store
        self._ignore_schedule = ignore_schedule
        self._ntp_synced = ntp_synced
        self._sync_client = sync_client
        self._stop_requested = False

        self._analyzer = DualBandAnalyzer(
            sample_rate=config.audio.sample_rate,
            block_duration_sec=config.audio.block_duration_sec,
            low_band_hz=config.dsp.low_band_hz,
            high_band_hz=config.dsp.high_band_hz,
            baseline_window_sec=config.dsp.baseline_window_sec,
            baseline_quantile=config.dsp.baseline_quantile,
            baseline_warmup_sec=config.dsp.baseline_warmup_sec,
            preprocessor=preprocessor,
        )
        self._trigger = EventTrigger(
            sample_rate=config.audio.sample_rate,
            block_duration_sec=config.audio.block_duration_sec,
            config=config.trigger,
        )

    def request_stop(self) -> None:
        self._stop_requested = True

    def _install_signal_handlers(self) -> None:
        def _handler(signum: int, _frame: Any) -> None:
            logger.info("Received signal %s; shutting down gracefully...", signum)
            self.request_stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # Not in main thread or unsupported platform signal
                pass

    def run(self, install_signals: bool = True) -> List[Dict[str, Any]]:
        """Run the monitoring loop until schedule ends, source EOF, or stop signal."""
        if install_signals:
            self._install_signal_handlers()

        saved_records: List[Dict[str, Any]] = []
        self._store.cleanup_old_raw()
        if self._sync_client is not None and self._sync_client.enabled:
            self._sync_client.flush_queue(self._store)
        self._source.start()
        logger.info(
            "NoiseMonitor started (node=%s, schedule=%s-%s, ignore_schedule=%s)",
            self._cfg.node.node_id,
            self._cfg.schedule.start_time,
            self._cfg.schedule.end_time,
            self._ignore_schedule,
        )

        try:
            while not self._stop_requested:
                if not self._ignore_schedule and not is_within_schedule(local_now(), self._cfg.schedule):
                    logger.info("Monitoring window ended (%s); exiting cleanly.", self._cfg.schedule.end_time)
                    break

                item = self._source.read_block(timeout_sec=1.0)
                if item is None:
                    # Synthetic source returns None on EOF; live source returns None on read timeout
                    if not hasattr(self._source, "_stream"):
                        break
                    continue

                raw_chunk, block_ts = item
                metrics = self._analyzer.analyze(
                    raw_chunk,
                    freeze_baseline=self._trigger.is_recording,
                )
                completed = self._trigger.process_block(raw_chunk, metrics, block_ts)
                if completed is not None:
                    record = self._store.save_event(completed, ntp_synced=self._ntp_synced)
                    saved_records.append(record)
                    if self._sync_client is not None and self._sync_client.enabled:
                        self._sync_client.push_event(record)
                        self._sync_client.flush_queue(self._store)
                    logger.info(
                        "Saved evidence %s | band=%s | dur=%.1fs | peak=%.1f dBFS | delta=+%.1f dB",
                        record["event_id"],
                        record["algo_metrics"]["trigger_band"],
                        record["algo_metrics"]["duration_sec"],
                        record["algo_metrics"]["peak_dbfs"],
                        record["algo_metrics"]["delta_db"],
                    )
        finally:
            pending = self._trigger.flush()
            if pending is not None:
                record = self._store.save_event(pending, ntp_synced=self._ntp_synced)
                saved_records.append(record)
                if self._sync_client is not None and self._sync_client.enabled:
                    self._sync_client.push_event(record)
                logger.info("Flushed final evidence %s on shutdown", record["event_id"])
            self._source.stop()

        return saved_records
