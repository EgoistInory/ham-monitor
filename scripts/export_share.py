#!/usr/bin/env python3
"""隐私安全的一键脱敏分享包导出工具。

向物业、社区、房东、12345 或邻居提交佐证时，严禁直接外发包含清晰人声的原始 FLAC 或包含本机路径/房号的日志。
本工具自动生成脱敏 ZIP 包：
  - MANIFEST.json（仅含加密哈希与脱敏指标，自动清除本地绝对路径）
  - SUMMARY_REPORT.md（可直接打印或提交的摘要报告）
  - TIMELINE.csv（表格时间轴）
  - TIMESTAMP_MANIFEST.txt（零音频哈希清单，可直接用于 OpenTimestamps 时间戳存证）
  - 可选 desensitized_audio/（仅保留 <=250Hz 低频撞击声，彻底剥离人声对话可懂度）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.config import load_config
from ham.desensitize import export_share_bundle
from ham.evidence import EvidenceStore


def main() -> int:
    parser = argparse.ArgumentParser(description="导出隐私脱敏的噪声举证分享包")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "default.yaml")
    parser.add_argument("--date", default=None, help="导出指定日期 YYYY-MM-DD（省略则导出全部日期）")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "data" / "exports",
        help="脱敏包输出目录",
    )
    parser.add_argument(
        "--include-lowpass-audio",
        action="store_true",
        help="附带 <=250Hz 低通脱敏音频（仅保留楼板撞击震动，消除人声对话可懂度）",
    )
    parser.add_argument(
        "--cutoff-hz",
        type=float,
        default=250.0,
        help="低通脱敏截止频率（默认 250 Hz）",
    )
    parser.add_argument(
        "--include-notes",
        action="store_true",
        help="包含经手机号/房号脱敏后的人工复核备注（默认不包含备注以最大化隐私安全）",
    )
    parser.add_argument("--no-zip", action="store_true", help="仅导出目录，不打包 ZIP")
    args = parser.parse_args()

    cfg = load_config(args.config)
    store = EvidenceStore(cfg.storage, cfg.node)
    out = export_share_bundle(
        store=store,
        output_dir=args.output_dir,
        date_str=args.date,
        include_lowpass_audio=args.include_lowpass_audio,
        lowpass_cutoff_hz=args.cutoff_hz,
        include_notes=args.include_notes,
        create_zip=not args.no_zip,
    )
    print(f"脱敏分享包已导出: {out}")
    print("安全提示：分享前仍请抽听低通音频并复核摘要文本，确认无任何敏感隐私信息。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
