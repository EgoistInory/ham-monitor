from __future__ import annotations

import logging
import queue
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def local_now() -> datetime:
    """Return timezone-aware local datetime."""
    return datetime.now().astimezone()


class AudioSource(ABC):
    """Abstract audio capture interface (Phase 1 single mic -> Phase 3 multi-channel)."""

    @abstractmethod
    def start(self) -> None:
        """Open and start the underlying audio stream."""

    @abstractmethod
    def read_block(self, timeout_sec: float = 1.0) -> Optional[Tuple[np.ndarray, datetime]]:
        """Read next (mono_float32_samples, block_start_timestamp) or None on timeout/EOF."""

    @abstractmethod
    def stop(self) -> None:
        """Stop and release the audio stream."""


class SoundDeviceAudioSource(AudioSource):
    """PortAudio/sounddevice live capture with automatic stream recovery."""

    def __init__(
        self,
        sample_rate: int = 48000,
        channels: int = 1,
        block_samples: int = 12000,
        device: Optional[Any] = None,
        reconnect_interval_sec: float = 3.0,
        max_queue_blocks: int = 64,
        stall_timeout_sec: float = 10.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._block_samples = block_samples
        self._device = device
        self._reconnect_interval = reconnect_interval_sec
        self._stall_timeout_sec = stall_timeout_sec
        self._queue: queue.Queue[Tuple[np.ndarray, datetime]] = queue.Queue(maxsize=max_queue_blocks)
        self._stream: Any = None
        self._running = False
        self._last_block_monotonic: float = time.monotonic()

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        if status:
            logger.warning("Audio input status flag: %s", status)
        self._last_block_monotonic = time.monotonic()
        ts = local_now()
        mono = indata[:, 0].copy() if indata.ndim > 1 else indata.copy()
        try:
            self._queue.put_nowait((mono.astype(np.float32, copy=False), ts))
        except queue.Full:
            logger.warning("Audio queue full; dropping oldest block to stay real-time")
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((mono.astype(np.float32, copy=False), ts))
            except queue.Empty:
                pass

    def _open_stream(self) -> None:
        import sounddevice as sd

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            blocksize=self._block_samples,
            dtype="float32",
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()
        self._last_block_monotonic = time.monotonic()
        logger.info(
            "Audio stream started (device=%s, sr=%d, block=%d)",
            self._device if self._device is not None else "default",
            self._sample_rate,
            self._block_samples,
        )

    def start(self) -> None:
        self._running = True
        self._open_stream()

    def _reconnect(self) -> None:
        self._close_stream()
        while self._running:
            try:
                logger.info("Attempting to reconnect audio stream...")
                self._open_stream()
                return
            except Exception as exc:
                logger.error("Audio reconnect failed: %s; retrying in %.1fs", exc, self._reconnect_interval)
                time.sleep(self._reconnect_interval)

    def read_block(self, timeout_sec: float = 1.0) -> Optional[Tuple[np.ndarray, datetime]]:
        if not self._running:
            return None

        if self._stream is None or not self._stream.active:
            logger.warning("Audio stream inactive; triggering self-healing reconnect")
            self._reconnect()

        try:
            return self._queue.get(timeout=timeout_sec)
        except queue.Empty:
            if self._stream is not None and not self._stream.active:
                self._reconnect()
            elif (
                self._stall_timeout_sec > 0
                and (time.monotonic() - self._last_block_monotonic) >= self._stall_timeout_sec
            ):
                logger.warning(
                    "Audio callback stalled for %.1fs while stream reports active; triggering self-healing reconnect",
                    time.monotonic() - self._last_block_monotonic,
                )
                self._reconnect()
            return None

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def stop(self) -> None:
        self._running = False
        self._close_stream()


class SyntheticAudioSource(AudioSource):
    """Deterministic offline audio source for pytest & simulation."""

    def __init__(
        self,
        samples: np.ndarray,
        sample_rate: int = 48000,
        block_samples: int = 12000,
        start_time: Optional[datetime] = None,
    ) -> None:
        self._samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        self._sample_rate = sample_rate
        self._block_samples = block_samples
        self._start_time = start_time or datetime(2026, 11, 25, 23, 30, 0, tzinfo=timezone.utc)
        self._cursor = 0
        self._running = False

    def start(self) -> None:
        self._running = True
        self._cursor = 0

    def read_block(self, timeout_sec: float = 1.0) -> Optional[Tuple[np.ndarray, datetime]]:
        if not self._running or self._cursor >= self._samples.size:
            return None
        end = min(self._cursor + self._block_samples, self._samples.size)
        chunk = self._samples[self._cursor : end]
        if chunk.size < self._block_samples:
            chunk = np.pad(chunk, (0, self._block_samples - chunk.size))
        from datetime import timedelta

        ts = self._start_time + timedelta(seconds=self._cursor / float(self._sample_rate))
        self._cursor = end
        return chunk, ts

    def stop(self) -> None:
        self._running = False

    def __iter__(self) -> Iterator[Tuple[np.ndarray, datetime]]:
        self.start()
        while True:
            item = self.read_block()
            if item is None:
                break
            yield item
        self.stop()
