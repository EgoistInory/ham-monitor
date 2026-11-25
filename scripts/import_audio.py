#!/usr/bin/env python3
"""离线音频导入工具（适配 Tier 0 闲置安卓手机 / 独立录音笔录制的整晚 WAV/FLAC/OGG 文件）。

将手机录制的原始音频送入与实时监测完全一致的 DualBandAnalyzer -> EventTrigger -> EvidenceStore
流水线，自动切片提取超标事件、写入哈希链数据库并生成早间日报。

用法:
  python scripts/import_audio.py --input night_recording.wav \\
      --start-time "2026-11-25T23:00:00+08:00" \\
      --node-id android_ceiling --location-tag ceiling
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.capture import SyntheticAudioSource
from ham.config import load_config
from ham.evidence import EvidenceStore
from ham.monitor import NoiseMonitor


def import_audio_file(
    input_path: Path,
    config_path: Path,
    start_time: datetime,
    node_id: str | None = None,
    location_tag: str | None = None,
) -> list[dict]:
    cfg = load_config(config_path)
    data, sr = sf.read(str(input_path), dtype="float32")
    if data.ndim > 1:
        data = data[:, 0]

    new_node = dataclasses.replace(
        cfg.node,
        node_id=node_id or cfg.node.node_id,
        location_tag=location_tag or cfg.node.location_tag,
    )
    new_audio = dataclasses.replace(cfg.audio, sample_rate=int(sr), channels=1)
    run_cfg = dataclasses.replace(cfg, node=new_node, audio=new_audio)

    source = SyntheticAudioSource(
        samples=np.asarray(data, dtype=np.float32),
        sample_rate=int(sr),
        block_samples=run_cfg.audio.block_samples,
        start_time=start_time,
    )
    store = EvidenceStore(run_cfg.storage, run_cfg.node)
    monitor = NoiseMonitor(run_cfg, source, store, ignore_schedule=True)
    return monitor.run(install_signals=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="导入安卓手机/录音笔录制的离线音频并自动切分证据")
    parser.add_argument("--input", type=Path, required=True, help="音频文件路径 (WAV/FLAC/OGG)")
    parser.add_argument(
        "--start-time",
        required=True,
        help="录音开始时间 ISO8601（例如 2026-11-25T23:00:00+08:00）",
    )
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "default.yaml")
    parser.add_argument("--node-id", default=None, help="覆盖节点名称（如 android_ceiling）")
    parser.add_argument(
        "--location-tag",
        default=None,
        help="覆盖摆放位置标签（如 ceiling / room_center / window_side）",
    )
    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start_time)
    if start_dt.tzinfo is None:
        start_dt = start_dt.astimezone()

    records = import_audio_file(
        input_path=args.input,
        config_path=args.config,
        start_time=start_dt,
        node_id=args.node_id,
        location_tag=args.location_tag,
    )
    print(f"导入分析完成：共提取并留存 {len(records)} 条噪声事件（已写入哈希链）。")
    for rec in records:
        m = rec["algo_metrics"]
        print(
            f"  - {rec['event_id']} | {m['trigger_band']} | {m['duration_sec']:.1f}s | "
            f"peak={m['peak_dbfs']:.1f} dBFS | delta=+{m['delta_db']:.1f} dB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
