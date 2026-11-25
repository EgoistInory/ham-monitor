#!/usr/bin/env python3
"""Phase 2 跨节点粗时间窗对比工具（ceiling 衣柜顶节点 vs room_center 室内中部节点）。

通过比较两个独立设备在同一事件（允许 ±2s 时钟漂移）上的能量差，辅助排除“本户走动”或“窗外车流”干扰。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.config import load_config
from ham.correlate import correlate_dual_nodes
from ham.evidence import EvidenceStore


def main() -> int:
    parser = argparse.ArgumentParser(description="双节点声源方位粗窗对比工具")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "default.yaml")
    parser.add_argument("--date", default=None, help="对比指定日期 YYYY-MM-DD（省略则全量对比）")
    parser.add_argument("--max-skew", type=float, default=2.0, help="允许设备间最大时钟漂移（秒，默认 2.0）")
    parser.add_argument("--margin-db", type=float, default=3.0, help="判定能量差阈值（dB，默认 3.0）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    store = EvidenceStore(cfg.storage, cfg.node)
    events = store.list_events_by_date(args.date) if args.date else store.list_all_events()

    pairs = correlate_dual_nodes(
        events,
        max_time_skew_sec=args.max_skew,
        upstairs_margin_db=args.margin_db,
    )
    if not pairs:
        print("未找到可匹配的 ceiling / room_center 双节点同窗事件。")
        return 0

    print(f"共匹配到 {len(pairs)} 组双节点同窗事件：")
    print(
        f"{'ceiling_event':28s} | {'center_event':28s} | {'dt(s)':>5s} | "
        f"{'ΔLow(dB)':>8s} | {'ΔHigh(dB)':>9s} | 推断结论"
    )
    print("-" * 110)
    for p in pairs:
        print(
            f"{p.ceiling_event_id:28s} | {p.center_event_id:28s} | {p.time_diff_sec:5.2f} | "
            f"{p.low_band_diff_db:+8.1f} | {p.high_band_diff_db:+9.1f} | {p.assessment}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
