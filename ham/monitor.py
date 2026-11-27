from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, time as dt_time
from pathlib import Path
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
        heartbeat_interval_sec: float = 300.0,
        heartbeat_path: Optional[Path] = None,
    ) -> None:
        self._cfg = config
        self._source = source
        self._store = store
        self._ignore_schedule = ignore_schedule
        self._ntp_synced = ntp_synced
        self._sync_client = sync_client
        self._heartbeat_interval_sec = heartbeat_interval_sec
        self._heartbeat_path = heartbeat_path
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

    def _emit_heartbeat(
        self,
        status: str,
        blocks_processed: int,
        saved_count: int,
        last_metrics: Optional[Any],
        zero_block_streak: int,
        log_line: bool = True,
    ) -> None:
        """Record periodic liveliness & noise-floor baseline telemetry for audit."""
        low_floor = round(float(last_metrics.low_baseline_dbfs), 1) if last_metrics is not None else None
        high_floor = round(float(last_metrics.high_baseline_dbfs), 1) if last_metrics is not None else None
        peak_dbfs = round(float(last_metrics.raw_peak_dbfs), 1) if last_metrics is not None else None
        if log_line:
            if last_metrics is not None:
                logger.info(
                    "[HEARTBEAT] status=%s | blocks=%d | events=%d | low_floor=%.1f dBFS | high_floor=%.1f dBFS | peak=%.1f dBFS",
                    status,
                    blocks_processed,
                    saved_count,
                    low_floor,
                    high_floor,
                    peak_dbfs,
                )
            else:
                logger.info(
                    "[HEARTBEAT] status=%s | blocks=%d | events=%d | awaiting audio blocks",
                    status,
                    blocks_processed,
                    saved_count,
                )
        if self._heartbeat_path is not None:
            payload = {
                "timestamp": local_now().isoformat(timespec="seconds"),
                "node_id": self._cfg.node.node_id,
                "status": status,
                "blocks_processed": blocks_processed,
                "saved_events": saved_count,
                "low_baseline_dbfs": low_floor,
                "high_baseline_dbfs": high_floor,
                "raw_peak_dbfs": peak_dbfs,
                "zero_block_streak": zero_block_streak,
            }
            try:
                self._heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = self._heartbeat_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp_path.replace(self._heartbeat_path)
            except OSError as exc:
                logger.warning("Failed to write heartbeat file %s: %s", self._heartbeat_path, exc)

    def run(self, install_signals: bool = True) -> List[Dict[str, Any]]:
        """Run the monitoring loop until schedule ends, source EOF, or stop signal."""
        if install_signals:
            self._install_signal_handlers()

        saved_records: List[Dict[str, Any]] = []
        blocks_processed = 0
        zero_block_streak = 0
        last_metrics: Optional[Any] = None
        last_heartbeat_ts = time.monotonic()
        last_silence_warn_ts = 0.0

        self._store.cleanup_old_raw()
        if self._sync_client is not None and self._sync_client.enabled:
            self._sync_client.flush_queue(self._store)
        self._source.start()
        self._emit_heartbeat("running", blocks_processed, 0, last_metrics, zero_block_streak, log_line=False)
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
                    now_mono = time.monotonic()
                    if self._heartbeat_interval_sec > 0 and (now_mono - last_heartbeat_ts) >= self._heartbeat_interval_sec:
                        self._emit_heartbeat(
                            "stalled_no_audio",
                            blocks_processed,
                            len(saved_records),
                            last_metrics,
                            zero_block_streak,
                        )
                        last_heartbeat_ts = now_mono
                    continue

                raw_chunk, block_ts = item
                metrics = self._analyzer.analyze(
                    raw_chunk,
                    freeze_baseline=self._trigger.is_recording,
                )
                blocks_processed += 1
                last_metrics = metrics

                if metrics.raw_peak_dbfs <= -199.0:
                    zero_block_streak += 1
                    silence_sec = zero_block_streak * self._cfg.audio.block_duration_sec
                    now_mono = time.monotonic()
                    if silence_sec >= 20.0 and (now_mono - last_silence_warn_ts) >= 300.0:
                        logger.error(
                            "Microphone outputting pure digital silence (<= -199 dBFS) for %.0fs; "
                            "check macOS lock-screen privacy, mic mute, or audio device routing.",
                            silence_sec,
                        )
                        last_silence_warn_ts = now_mono
                else:
                    zero_block_streak = 0

                now_mono = time.monotonic()
                if self._heartbeat_interval_sec > 0 and (now_mono - last_heartbeat_ts) >= self._heartbeat_interval_sec:
                    self._emit_heartbeat(
                        "running",
                        blocks_processed,
                        len(saved_records),
                        last_metrics,
                        zero_block_streak,
                    )
                    last_heartbeat_ts = now_mono

                completed = self._trigger.process_block(raw_chunk, metrics, block_ts)
                if completed is not None:
                    try:
                        record = self._store.save_event(completed, ntp_synced=self._ntp_synced)
                    except RuntimeError as quota_err:
                        logger.error("Storage quota reached; skipping disk write: %s", quota_err)
                        continue
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
                try:
                    record = self._store.save_event(pending, ntp_synced=self._ntp_synced)
                    saved_records.append(record)
                    if self._sync_client is not None and self._sync_client.enabled:
                        self._sync_client.push_event(record)
                    logger.info("Flushed final evidence %s on shutdown", record["event_id"])
                except RuntimeError as quota_err:
                    logger.error("Storage quota reached on shutdown flush: %s", quota_err)
            self._emit_heartbeat(
                "stopped",
                blocks_processed,
                len(saved_records),
                last_metrics,
                zero_block_streak,
                log_line=False,
            )
            self._source.stop()

        return saved_records
