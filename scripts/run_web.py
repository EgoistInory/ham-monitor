#!/usr/bin/env python3
"""启动 ham-monitor 本地管理控制台（默认仅绑定 127.0.0.1 保障隐私安全）。

用法:
  python scripts/run_web.py                    # 连接正式本地证据库 (config/default.yaml)
  python scripts/run_web.py --demo             # 启动隔离演示沙箱（预置合成样本，不触碰正式证据库）
  python scripts/run_web.py --port 8765
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.web.app import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description="ham-monitor 本地管理平台后端与前端服务")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "default.yaml")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（强烈建议保持 127.0.0.1 本地访问）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="使用隔离演示沙箱 (data/demo_sandbox/) 预置合成数据，便于首次体验界面与导出功能",
    )
    args = parser.parse_args()

    application = create_app(config_path=args.config, demo_mode=args.demo)
    mode_label = "演示沙箱模式 (data/demo_sandbox)" if args.demo else f"本地证据库 ({args.config})"
    print(f"[ham-monitor] 管理控制台已启动: http://{args.host}:{args.port} | {mode_label}")
    uvicorn.run(application, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
