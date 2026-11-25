#!/usr/bin/env python3
"""Day 1 现场声学探针脚本：验证 2017 MacBook 内置麦或外置麦的真实底噪与频段信噪比。

用法:
  python scripts/probe_mic.py --list-devices
  python scripts/probe_mic.py --duration 60
  python scripts/probe_mic.py --duration 30 --save-raw
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import numpy as np
from scipy.signal import butter, sosfilt

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.dsp import peak_dbfs, rms_dbfs


def list_input_devices() -> None:
    import sounddevice as sd

    devices = sd.query_devices()
    print("可用音频输入设备:")
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            default_mark = " [默认]" if idx == sd.default.device[0] else ""
            print(
                f"  [{idx}] {dev['name']} "
                f"(in={dev['max_input_channels']}, default_sr={int(dev['default_samplerate'])} Hz){default_mark}"
            )


def analyze_probe_signal(
    samples: np.ndarray, sample_rate: int, block_sec: float = 0.25
) -> dict[str, dict[str, float]]:
    """Compute per-band floor (p20), median (p50), and peak (p99/max) dBFS across blocks."""
    bands: List[Tuple[str, Tuple[float, float]]] = [
        ("sub_low_20_80hz", (20.0, 80.0)),
        ("low_80_180hz", (80.0, 180.0)),
        ("low_total_20_180hz", (20.0, 180.0)),
        ("high_vocal_300_4000hz", (300.0, 4000.0)),
    ]

    block_size = max(1, int(round(sample_rate * block_sec)))
    n_blocks = max(1, samples.size // block_size)
    trimmed = samples[: n_blocks * block_size].astype(np.float64)

    results: dict[str, dict[str, float]] = {}
    for name, (lo, hi) in bands:
        sos = butter(4, (lo, hi), btype="bandpass", fs=sample_rate, output="sos")
        filtered = sosfilt(sos, trimmed)
        blocks = filtered.reshape(n_blocks, block_size)
        block_rms = np.array([rms_dbfs(b) for b in blocks], dtype=np.float64)
        results[name] = {
            "floor_p20_dbfs": round(float(np.quantile(block_rms, 0.20)), 2),
            "median_p50_dbfs": round(float(np.quantile(block_rms, 0.50)), 2),
            "max_block_rms_dbfs": round(float(np.max(block_rms)), 2),
            "snr_headroom_db": round(float(np.max(block_rms) - np.quantile(block_rms, 0.20)), 2),
        }

    results["fullband_raw"] = {
        "rms_dbfs": round(rms_dbfs(trimmed), 2),
        "peak_dbfs": round(peak_dbfs(trimmed), 2),
    }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="ham-monitor 现场声学探针脚本")
    parser.add_argument("--list-devices", action="store_true", help="列出所有可用音频输入设备")
    parser.add_argument("--duration", type=float, default=60.0, help="探针录制时长（秒，默认 60）")
    parser.add_argument("--sample-rate", type=int, default=48000, help="采样率（默认 48000）")
    parser.add_argument("--device", default=None, help="指定输入设备索引或名称子串")
    parser.add_argument("--save-raw", action="store_true", help="将探针音频存至 data/raw/ 便于回听检查")
    args = parser.parse_args()

    if args.list_devices:
        list_input_devices()
        return 0

    import sounddevice as sd

    device = int(args.device) if (args.device is not None and str(args.device).isdigit()) else args.device
    total_samples = int(round(args.duration * args.sample_rate))

    print(
        f"[探针启动] 录制 {args.duration:.1f}s (sr={args.sample_rate}Hz, device={device or 'default'})...\n"
        "提示：前 20 秒请保持室内安静测底噪，后半段可轻敲天花板/桌面或模拟人声观察频段突刺。"
    )
    recording = sd.rec(
        total_samples,
        samplerate=args.sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    mono = recording[:, 0]

    stats = analyze_probe_signal(mono, args.sample_rate)
    print("\n=== 探针频段统计 (相对数字电平 dBFS，非校准 dB SPL) ===")
    for band in ("sub_low_20_80hz", "low_80_180hz", "low_total_20_180hz", "high_vocal_300_4000hz"):
        s = stats[band]
        print(
            f"  {band:24s} | 底噪(P20): {s['floor_p20_dbfs']:6.1f} dBFS | "
            f"中位: {s['median_p50_dbfs']:6.1f} dBFS | "
            f"最大块: {s['max_block_rms_dbfs']:6.1f} dBFS | 突刺增量: +{s['snr_headroom_db']:.1f} dB"
        )
    fb = stats["fullband_raw"]
    print(f"  {'fullband_raw':24s} | 全频RMS: {fb['rms_dbfs']:6.1f} dBFS | 瞬时峰值: {fb['peak_dbfs']:6.1f} dBFS")

    low_suggest = round(stats["low_total_20_180hz"]["floor_p20_dbfs"] + 8.0, 1)
    high_suggest = round(stats["high_vocal_300_4000hz"]["floor_p20_dbfs"] + 8.0, 1)
    print("\n=== 建议配置参考 (config/default.yaml) ===")
    print(f"  trigger.low_min_dbfs:  {low_suggest}")
    print(f"  trigger.high_min_dbfs: {high_suggest}")
    print("请将本次实测数据填入 docs/FIELD_TEST.md 留档。")

    if args.save_raw:
        import soundfile as sf

        raw_dir = REPO_ROOT / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        out_path = raw_dir / f"probe_{datetime.now().strftime('%Y%m%d_%H%M%S')}.flac"
        sf.write(str(out_path), mono, args.sample_rate, format="FLAC", subtype="PCM_16")
        print(f"已保存探针原始录音（将在 7 天后自动清理）: {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
