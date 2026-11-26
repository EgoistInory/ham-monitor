from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Tuple

import numpy as np
import soundfile as sf


def load_mono_audio(file_path: Path) -> Tuple[np.ndarray, int]:
    """Decode WAV/FLAC/OGG/MP3/CAF natively via soundfile, falling back to ffmpeg for M4A/AAC/MP4."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    try:
        data, sr = sf.read(str(path), dtype="float32")
        if data.ndim > 1:
            data = data[:, 0]
        return np.asarray(data, dtype=np.float32), int(sr)
    except Exception as native_err:
        ffmpeg_bin = shutil.which("ffmpeg")
        if not ffmpeg_bin:
            raise RuntimeError(
                f"无法直接解码 {path.suffix or path.name}（原生解码报错: {native_err}）。"
                "若为 iPhone/安卓录制的 .m4a/.aac 文件，请先安装 ffmpeg（macOS: brew install ffmpeg / "
                "Windows: winget install Gyan.FFmpeg / Linux: apt install ffmpeg）或录制为 WAV/FLAC 格式。"
            ) from native_err

        with tempfile.TemporaryDirectory(prefix="ham_decode_") as tmp_dir:
            wav_out = Path(tmp_dir) / "decoded.wav"
            cmd = [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                str(path),
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(wav_out),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0 or not wav_out.exists():
                raise RuntimeError(f"ffmpeg 解码失败: {proc.stderr.strip() or proc.returncode}") from native_err

            data, sr = sf.read(str(wav_out), dtype="float32")
            if data.ndim > 1:
                data = data[:, 0]
            return np.asarray(data, dtype=np.float32), int(sr)
