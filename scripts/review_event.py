#!/usr/bin/env python3
"""证据完整性校验与人工复核打标工具（仅修改 human_review，绝不改动原始证据与算法指标）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ham.config import load_config
from ham.evidence import EvidenceStore
from ham.taxonomy import taxonomy_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="ham-monitor 证据校验与人工复核工具")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config" / "default.yaml")
    parser.add_argument("--list-taxonomy", action="store_true", help="列出全部标准细化噪声分类与声源方位代码")
    parser.add_argument("--list-date", help="列出指定日期 YYYY-MM-DD 的全部事件")
    parser.add_argument("--verify", metavar="EVENT_ID", help="校验指定事件的 FLAC SHA-256 完整性")
    parser.add_argument(
        "--verify-chain",
        action="store_true",
        help="校验全部事件的追加哈希链与 FLAC 文件完整性（防增删篡改）",
    )
    parser.add_argument("--event-id", help="要人工复核打标的事件 ID")
    parser.add_argument(
        "--label",
        help="人工细化噪声分类代码（使用 --list-taxonomy 查看全部 19 项标准分类）",
    )
    parser.add_argument(
        "--direction",
        default=None,
        help="声源方位归属（upstairs / diagonal_upstairs / downstairs / adjacent / public_facility / outdoor / self_household / uncertain）",
    )
    parser.add_argument("--notes", default="", help="人工复核备注")
    args = parser.parse_args()

    if args.list_taxonomy:
        print(json.dumps(taxonomy_payload(), ensure_ascii=False, indent=2))
        return 0

    cfg = load_config(args.config)
    store = EvidenceStore(cfg.storage, cfg.node)

    if args.list_date:
        events = store.list_events_by_date(args.list_date)
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return 0

    if args.verify:
        ok = store.verify_integrity(args.verify)
        print(f"Event {args.verify} SHA-256 完整性校验: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    if args.verify_chain:
        ok, errors = store.verify_chain(verify_files=True)
        if ok:
            print("全库哈希链与 FLAC 文件完整性校验: PASS")
            return 0
        print("全库哈希链校验: FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1

    if args.event_id:
        updated = store.update_human_review(
            event_id=args.event_id,
            verified=True,
            label=args.label,
            direction=args.direction,
            notes=args.notes,
        )
        print("已更新人工复核记录:")
        print(json.dumps(updated["human_review"], ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
