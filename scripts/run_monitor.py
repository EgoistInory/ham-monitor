#!/usr/bin/env python3
"""ham-monitor 主运行入口：夜间无人值守监听、无损留证与早间日报生成。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.capture import AudioSource, SoundDeviceAudioSource, local_now
from ham.config import AppConfig, load_config
from ham.evidence import EvidenceStore
from ham.monitor import NoiseMonitor, is_within_schedule
from ham.sync import RemoteSyncClient


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def attach_file_logger(log_path: Path, verbose: bool = False) -> None:
    """Persist runtime and heartbeat logs to a rotating local log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for existing in root.handlers:
        if isinstance(existing, RotatingFileHandler) and Path(existing.baseFilename) == log_path.resolve():
            return
    handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)


@contextmanager
def _active_caffeinate() -> Iterator[None]:
    """Keep macOS awake (CPU/idle/disk) strictly during an active nocturnal session."""
    proc: Optional[subprocess.Popen[Any]] = None
    if sys.platform == "darwin":
        caffeinate_bin = shutil.which("caffeinate") or "/usr/bin/caffeinate"
        if Path(caffeinate_bin).is_file():
            try:
                proc = subprocess.Popen(
                    [caffeinate_bin, "-ims", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as exc:
                logging.getLogger(__name__).warning("启动 caffeinate 失败（继续监测）: %s", exc)
    try:
        yield
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()


def _write_standby_heartbeat(heartbeat_path: Path, node_id: str) -> None:
    payload = {
        "timestamp": local_now().isoformat(timespec="seconds"),
        "node_id": node_id,
        "status": "standby_outside_schedule",
        "blocks_processed": 0,
        "saved_events": 0,
        "low_baseline_dbfs": None,
        "high_baseline_dbfs": None,
        "raw_peak_dbfs": None,
        "zero_block_streak": 0,
    }
    try:
        heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = heartbeat_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(heartbeat_path)
    except OSError:
        pass


def run_daemon_loop(
    cfg: AppConfig,
    store: EvidenceStore,
    source_factory: Callable[[], AudioSource],
    heartbeat_path: Path,
    sync_client: Optional[Any] = None,
    ignore_schedule: bool = False,
    now_fn: Callable[[], datetime] = local_now,
    poll_interval_sec: float = 15.0,
    max_sessions: Optional[int] = None,
    use_caffeinate: bool = True,
) -> int:
    """Stay resident in an authorized terminal: capture only inside schedule, release mic during daytime."""
    log = logging.getLogger(__name__)
    stop_requested = False
    active_monitor: Optional[NoiseMonitor] = None

    def _handle_stop(signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        log.info("Daemon received signal %s; stopping cleanly...", signum)
        stop_requested = True
        if active_monitor is not None:
            active_monitor.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass

    sessions_completed = 0
    standby_logged = False
    while not stop_requested:
        now = now_fn()
        if not ignore_schedule and not is_within_schedule(now, cfg.schedule):
            if not standby_logged:
                log.info(
                    "当前处于非监测时段（窗口 %s–%s），已释放麦克风与防休眠，静默等待下次自动开启...",
                    cfg.schedule.start_time,
                    cfg.schedule.end_time,
                )
                _write_standby_heartbeat(heartbeat_path, cfg.node.node_id)
                standby_logged = True
            slept = 0.0
            while slept < poll_interval_sec and not stop_requested:
                step = min(1.0, poll_interval_sec - slept)
                time.sleep(step)
                slept += step
            continue

        standby_logged = False
        source = source_factory()
        active_monitor = NoiseMonitor(
            config=cfg,
            source=source,
            store=store,
            ignore_schedule=ignore_schedule,
            sync_client=sync_client,
            heartbeat_path=heartbeat_path,
        )
        start_date = now_fn().strftime("%Y-%m-%d")
        if use_caffeinate:
            with _active_caffeinate():
                saved = active_monitor.run(install_signals=False)
        else:
            saved = active_monitor.run(install_signals=False)
        end_date = now_fn().strftime("%Y-%m-%d")
        stats = active_monitor.last_run_stats
        active_monitor = None

        for d in sorted({start_date, end_date}):
            report_path = store.generate_daily_report(d, monitor_stats=stats)
            log.info("已更新日报 (%s): %s", d, report_path)
        log.info("本轮夜间值守完成，共保存 %d 条噪声证据事件。", len(saved))

        sessions_completed += 1
        if max_sessions is not None and sessions_completed >= max_sessions:
            break
        if ignore_schedule or not cfg.schedule.enabled:
            break

    return sessions_completed


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
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="昼伏夜出常驻模式：仅在 schedule 时间窗内开启麦克风与防休眠，白天自动释放等待下一晚（推荐 macOS 在 iTerm2/终端中运行）",
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
    base_dir = Path(cfg.storage.base_dir)
    if not args.report_only:
        attach_file_logger(base_dir / "monitor.log", verbose=args.verbose)
    store = EvidenceStore(cfg.storage, cfg.node)

    if args.report_only:
        target_date = args.report_date or local_now().strftime("%Y-%m-%d")
        report_path = store.generate_daily_report(target_date)
        print(f"日报已生成: {report_path}")
        return 0

    device = args.device if args.device is not None else cfg.audio.device
    if isinstance(device, str) and device.isdigit():
        device = int(device)

    def _make_source() -> SoundDeviceAudioSource:
        return SoundDeviceAudioSource(
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

    if args.daemon:
        run_daemon_loop(
            cfg=cfg,
            store=store,
            source_factory=_make_source,
            heartbeat_path=base_dir / "monitor_heartbeat.json",
            sync_client=sync_client,
            ignore_schedule=args.ignore_schedule,
        )
        return 0

    monitor = NoiseMonitor(
        config=cfg,
        source=_make_source(),
        store=store,
        ignore_schedule=args.ignore_schedule,
        sync_client=sync_client,
        heartbeat_path=base_dir / "monitor_heartbeat.json",
    )

    start_date = local_now().strftime("%Y-%m-%d")
    saved = monitor.run(install_signals=True)
    end_date = local_now().strftime("%Y-%m-%d")

    # Cross-midnight runs span two dates; generate reports for any involved date
    dates_to_report = sorted({start_date, end_date})
    for d in dates_to_report:
        report_path = store.generate_daily_report(d, monitor_stats=monitor.last_run_stats)
        logging.getLogger(__name__).info("已更新日报 (%s): %s", d, report_path)

    logging.getLogger(__name__).info("本次运行共保存 %d 条噪声证据事件。", len(saved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
