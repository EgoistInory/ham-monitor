#!/usr/bin/env python3
"""外部互证记录工具：将物业劝阻、社区调解、110 出警回执、邻居证言等非声学证据纳入同一条证据链。

司法实践中单靠音频很难独立构成充分证明，必须与前置劝阻及出警记录互证。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.config import load_config
from ham.evidence import EvidenceStore, VALID_CORROBORATION_TYPES


def main() -> int:
    parser = argparse.ArgumentParser(description="记录外部非声学互证（物业/社区/110/邻居/CMA）")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "default.yaml")
    parser.add_argument(
        "--category",
        choices=VALID_CORROBORATION_TYPES,
        help="互证类别",
    )
    parser.add_argument("--summary", help="客观事实摘要（建议写清时间、处理方、结果）")
    parser.add_argument("--ref", default="", help="受案回执编号 / 物业工单号 / 12345 编号")
    parser.add_argument(
        "--attachment",
        type=Path,
        default=None,
        help="关联截图/PDF原件路径（仅计算其 SHA-256 存入台账，不复制原图）",
    )
    parser.add_argument("--event-id", default="", help="关联的声学事件 ID（可选）")
    parser.add_argument("--time", default=None, help="记录时间 ISO8601（默认当前本地时间）")
    parser.add_argument("--list-date", default=None, help="列出指定日期 YYYY-MM-DD 的外部互证记录")
    args = parser.parse_args()

    cfg = load_config(args.config)
    store = EvidenceStore(cfg.storage, cfg.node)

    if args.list_date:
        logs = store.list_corroboration_logs(args.list_date)
        print(json.dumps(logs, ensure_ascii=False, indent=2))
        return 0

    if not args.category or not args.summary:
        parser.error("新增记录时必须同时提供 --category 和 --summary")

    ts = datetime.fromisoformat(args.time) if args.time else None
    entry = store.add_corroboration_log(
        category=args.category,
        summary=args.summary,
        logged_at=ts,
        reference_code=args.ref,
        attachment_path=args.attachment,
        related_event_id=args.event_id,
    )
    print("已写入外部互证台账:")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
