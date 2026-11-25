from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

MIN_AMPLITUDE = 1e-10


def rms_dbfs(samples: np.ndarray) -> float:
    """Compute RMS level in dBFS for float PCM in [-1.0, 1.0]."""
    if samples.size == 0:
        return -200.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return float(20.0 * np.log10(max(rms, MIN_AMPLITUDE)))


def peak_dbfs(samples: np.ndarray) -> float:
    """Compute peak amplitude in dBFS for float PCM in [-1.0, 1.0]."""
    if samples.size == 0:
        return -200.0
    peak = float(np.max(np.abs(samples)))
    return float(20.0 * np.log10(max(peak, MIN_AMPLITUDE)))


class AnalysisPreprocessor(ABC):
    """Phase 2 extension hook for analysis-only denoising (e.g. spectral subtraction).

    Implementations MUST return a new/copied array and MUST NEVER mutate the
    raw evidence PCM buffer.
    """

    @abstractmethod
    def process(self, samples: np.ndarray) -> np.ndarray:
        """Return a denoised copy of samples for analysis."""


class PassthroughPreprocessor(AnalysisPreprocessor):
    """Default Phase 1 analysis preprocessor: returns a read-safe copy."""

    def process(self, samples: np.ndarray) -> np.ndarray:
        return np.array(samples, dtype=np.float64, copy=True)


@dataclass(frozen=True)
class BlockMetrics:
    raw_rms_dbfs: float
    raw_peak_dbfs: float
    low_rms_dbfs: float
    low_baseline_dbfs: float
    low_delta_db: float
    high_rms_dbfs: float
    high_baseline_dbfs: float
    high_delta_db: float
    baseline_ready: bool


class DualBandAnalyzer:
    """Dual-band Butterworth SOS analyzer with dynamic quantile noise floor.

    - Low band (default 20-180 Hz): floor impacts, stomping, furniture dragging.
    - High band (default 300-4000 Hz): shouting, screaming, loud vocals.

    Operates strictly on an isolated analysis copy of incoming audio.
    """

    def __init__(
        self,
        sample_rate: int,
        block_duration_sec: float,
        low_band_hz: Tuple[float, float] = (20.0, 180.0),
        high_band_hz: Tuple[float, float] = (300.0, 4000.0),
        baseline_window_sec: float = 120.0,
        baseline_quantile: float = 0.20,
        baseline_warmup_sec: float = 10.0,
        preprocessor: Optional[AnalysisPreprocessor] = None,
    ) -> None:
        nyquist = sample_rate / 2.0
        for name, (lo, hi) in (("low_band_hz", low_band_hz), ("high_band_hz", high_band_hz)):
            if not (0.0 < lo < hi < nyquist):
                raise ValueError(f"{name}={lo, hi} must satisfy 0 < lo < hi < {nyquist}")

        self._sample_rate = sample_rate
        self._quantile = float(baseline_quantile)
        self._window_blocks = max(4, int(round(baseline_window_sec / block_duration_sec)))
        self._warmup_blocks = max(1, int(round(baseline_warmup_sec / block_duration_sec)))
        self._preprocessor = preprocessor or PassthroughPreprocessor()

        self._sos_low = butter(4, low_band_hz, btype="bandpass", fs=sample_rate, output="sos")
        self._sos_high = butter(4, high_band_hz, btype="bandpass", fs=sample_rate, output="sos")
        self._zi_low = sosfilt_zi(self._sos_low) * 0.0
        self._zi_high = sosfilt_zi(self._sos_high) * 0.0

        self._low_history: Deque[float] = deque(maxlen=self._window_blocks)
        self._high_history: Deque[float] = deque(maxlen=self._window_blocks)

    def analyze(self, raw_samples: np.ndarray, freeze_baseline: bool = False) -> BlockMetrics:
        """Analyze one audio block without mutating raw_samples."""
        analysis_copy = self._preprocessor.process(raw_samples)

        raw_rms = rms_dbfs(analysis_copy)
        raw_peak = peak_dbfs(analysis_copy)

        low_sig, self._zi_low = sosfilt(self._sos_low, analysis_copy, zi=self._zi_low)
        high_sig, self._zi_high = sosfilt(self._sos_high, analysis_copy, zi=self._zi_high)

        low_rms = rms_dbfs(low_sig)
        high_rms = rms_dbfs(high_sig)

        # Compute baseline against prior quiet floor before updating history
        if self._low_history:
            low_base = float(np.quantile(self._low_history, self._quantile))
            high_base = float(np.quantile(self._high_history, self._quantile))
        else:
            low_base = low_rms
            high_base = high_rms

        baseline_ready = len(self._low_history) >= self._warmup_blocks

        if not freeze_baseline or not baseline_ready:
            self._low_history.append(low_rms)
            self._high_history.append(high_rms)

        return BlockMetrics(
            raw_rms_dbfs=raw_rms,
            raw_peak_dbfs=raw_peak,
            low_rms_dbfs=low_rms,
            low_baseline_dbfs=low_base,
            low_delta_db=low_rms - low_base,
            high_rms_dbfs=high_rms,
            high_baseline_dbfs=high_base,
            high_delta_db=high_rms - high_base,
            baseline_ready=baseline_ready,
        )
