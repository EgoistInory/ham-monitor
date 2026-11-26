#!/usr/bin/env python3
"""ham-monitor 主运行入口：夜间无人值守监听、无损留证与早间日报生成。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.capture import SoundDeviceAudioSource, local_now
from ham.config import load_config
from ham.evidence import EvidenceStore
from ham.monitor import NoiseMonitor
from ham.sync import RemoteSyncClient


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ham-monitor 夜间声学监测与自动取证主程序")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "config" / "default.yaml",
        help="YAML 配置文件路径",
    )
    parser.add_argument(
        "--ignore-schedule",
        action="store_true",
        help="忽略夜间时间窗限制（用于白天调试或全天试跑）",
    )
    parser.add_argument("--device", default=None, help="覆盖配置中的音频输入设备索引或名称")
    parser.add_argument("--report-only", action="store_true", help="仅生成指定日期的日报并退出")
    parser.add_argument(
        "--report-date",
        default=None,
        help="日报日期 YYYY-MM-DD（默认今日）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细调试日志")
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args.config)
    store = EvidenceStore(cfg.storage, cfg.node)

    if args.report_only:
        target_date = args.report_date or local_now().strftime("%Y-%m-%d")
        report_path = store.generate_daily_report(target_date)
        print(f"日报已生成: {report_path}")
        return 0

    device = args.device if args.device is not None else cfg.audio.device
    if isinstance(device, str) and device.isdigit():
        device = int(device)

    source = SoundDeviceAudioSource(
        sample_rate=cfg.audio.sample_rate,
        channels=cfg.audio.channels,
        block_samples=cfg.audio.block_samples,
        device=device,
        reconnect_interval_sec=cfg.audio.reconnect_interval_sec,
    )
    sync_client = None
    if cfg.sync.enabled:
        sync_client = RemoteSyncClient(
            config=cfg.sync,
            queue_path=Path(cfg.storage.base_dir) / "sync_queue.json",
        )

    monitor = NoiseMonitor(
        config=cfg,
        source=source,
        store=store,
        ignore_schedule=args.ignore_schedule,
        sync_client=sync_client,
    )

    start_date = local_now().strftime("%Y-%m-%d")
    saved = monitor.run(install_signals=True)
    end_date = local_now().strftime("%Y-%m-%d")

    # Cross-midnight runs span two dates; generate reports for any involved date
    dates_to_report = sorted({start_date, end_date})
    for d in dates_to_report:
        report_path = store.generate_daily_report(d)
        logging.getLogger(__name__).info("已更新日报 (%s): %s", d, report_path)

    logging.getLogger(__name__).info("本次运行共保存 %d 条噪声证据事件。", len(saved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
