from __future__ import annotations

import numpy as np


class AudioRingBuffer:
    """Fixed-capacity 1D circular buffer for raw mono PCM pre-roll storage.

    Pre-allocates a single contiguous NumPy array so steady-state audio
    ingestion causes zero heap reallocations.
    """

    def __init__(self, capacity_samples: int, dtype: np.dtype = np.float32) -> None:
        if capacity_samples <= 0:
            raise ValueError("capacity_samples must be positive")
        self._capacity = int(capacity_samples)
        self._buffer = np.zeros(self._capacity, dtype=dtype)
        self._write_pos = 0
        self._filled = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def filled_samples(self) -> int:
        return self._filled

    def clear(self) -> None:
        self._buffer.fill(0)
        self._write_pos = 0
        self._filled = 0

    def write(self, samples: np.ndarray) -> None:
        """Append a 1D chunk of PCM samples into the ring buffer."""
        chunk = np.asarray(samples, dtype=self._buffer.dtype).reshape(-1)
        n = chunk.size
        if n == 0:
            return

        if n >= self._capacity:
            self._buffer[:] = chunk[-self._capacity :]
            self._write_pos = 0
            self._filled = self._capacity
            return

        first_part = min(n, self._capacity - self._write_pos)
        self._buffer[self._write_pos : self._write_pos + first_part] = chunk[:first_part]

        remaining = n - first_part
        if remaining > 0:
            self._buffer[:remaining] = chunk[first_part:]

        self._write_pos = (self._write_pos + n) % self._capacity
        self._filled = min(self._capacity, self._filled + n)

    def read_last(self, num_samples: int) -> np.ndarray:
        """Return a contiguous copy of the most recent samples in chronological order."""
        if num_samples <= 0 or self._filled == 0:
            return np.empty(0, dtype=self._buffer.dtype)

        count = min(int(num_samples), self._filled)
        start = (self._write_pos - count) % self._capacity

        if start < self._write_pos:
            return self._buffer[start : self._write_pos].copy()

        return np.concatenate((self._buffer[start:], self._buffer[: self._write_pos])).copy()
