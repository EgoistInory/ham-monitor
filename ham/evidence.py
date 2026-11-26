from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import soundfile as sf

from ham.config import DeviceProfile, StorageConfig
from ham.taxonomy import get_category_label, get_direction_label
from ham.trigger import CompletedEvent

GENESIS_CHAIN_HASH = "0" * 64

VALID_CORROBORATION_TYPES = (
    "property_notice",
    "community_mediation",
    "police_call",
    "neighbor_witness",
    "landlord_notice",
    "cma_test",
    "other",
)


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of a file in streaming chunks."""
    digest = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_chain_hash(
    prev_chain_hash: str,
    event_id: str,
    file_sha256: str,
    trigger_time: str,
) -> str:
    """Link each event into an append-only SHA-256 hash chain to detect deletions or insertions."""
    payload = f"{prev_chain_hash}|{event_id}|{file_sha256}|{trigger_time}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class EvidenceStore:
    """Manages immutable lossless FLAC evidence, hash-chained SQLite ledger,
    corroboration logs, JSON sidecars, and daily reports.

    Strictly separates:
    - raw_evidence (immutable acoustic facts + cryptographic chain links)
    - algo_metrics (automated DSP inferences)
    - human_review (manual verification & labeling)
    - corroboration_logs (external non-acoustic proof: property, community, 110, neighbors)
    """

    def __init__(self, storage: StorageConfig, device_profile: DeviceProfile) -> None:
        self._storage = storage
        self._profile = device_profile

        self._evidence_dir = Path(storage.evidence_dir)
        self._raw_dir = Path(storage.raw_dir)
        self._reports_dir = Path(storage.reports_dir)
        self._db_path = Path(storage.db_path)

        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    seq_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    event_date TEXT NOT NULL,
                    -- raw_evidence (immutable)
                    evidence_file TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    prev_chain_hash TEXT NOT NULL DEFAULT '',
                    chain_hash TEXT NOT NULL DEFAULT '',
                    sample_rate INTEGER NOT NULL,
                    channels INTEGER NOT NULL,
                    ntp_synced INTEGER NOT NULL,
                    device_calibrated INTEGER NOT NULL,
                    node_id TEXT NOT NULL,
                    location_tag TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    trigger_time TEXT NOT NULL,
                    -- algo_metrics (algorithmic inference)
                    trigger_band TEXT NOT NULL,
                    peak_dbfs REAL NOT NULL,
                    baseline_dbfs REAL NOT NULL,
                    delta_db REAL NOT NULL,
                    low_peak_dbfs REAL NOT NULL,
                    high_peak_dbfs REAL NOT NULL,
                    duration_sec REAL NOT NULL,
                    inferred_tag TEXT NOT NULL,
                    -- human_review (mutable by user only)
                    human_verified INTEGER NOT NULL DEFAULT 0,
                    human_label TEXT,
                    human_direction TEXT NOT NULL DEFAULT '',
                    human_notes TEXT NOT NULL DEFAULT ''
                )
                """
            )
            # Forward-compatible migration for existing Phase 1 databases
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
            if "prev_chain_hash" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN prev_chain_hash TEXT NOT NULL DEFAULT ''")
            if "chain_hash" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN chain_hash TEXT NOT NULL DEFAULT ''")
            if "human_direction" not in cols:
                conn.execute("ALTER TABLE events ADD COLUMN human_direction TEXT NOT NULL DEFAULT ''")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS corroboration_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_date TEXT NOT NULL,
                    logged_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    reference_code TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL,
                    attachment_sha256 TEXT NOT NULL DEFAULT '',
                    related_event_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corroboration_date ON corroboration_logs(log_date)"
            )

    def _get_latest_chain_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT chain_hash FROM events ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None or not row["chain_hash"]:
            return GENESIS_CHAIN_HASH
        return str(row["chain_hash"])

    def save_event(
        self,
        event: CompletedEvent,
        ntp_synced: bool = False,
        profile_override: Optional[DeviceProfile] = None,
    ) -> Dict[str, Any]:
        """Persist raw PCM as lossless FLAC, hash & chain it, write sidecar JSON, index in SQLite."""
        profile = profile_override or self._profile
        event_date = event.trigger_time.strftime("%Y-%m-%d")
        ts_slug = event.trigger_time.strftime("%Y%m%d_%H%M%S")
        event_id = f"{ts_slug}_{profile.node_id}"

        day_dir = self._evidence_dir / event_date
        day_dir.mkdir(parents=True, exist_ok=True)

        flac_path = day_dir / f"{event_id}.flac"
        suffix = 1
        while flac_path.exists():
            event_id = f"{ts_slug}_{profile.node_id}_{suffix}"
            flac_path = day_dir / f"{event_id}.flac"
            suffix += 1

        sf.write(
            str(flac_path),
            event.raw_pcm,
            event.sample_rate,
            format="FLAC",
            subtype="PCM_16",
        )
        file_sha256 = compute_sha256(flac_path)
        trigger_iso = event.trigger_time.isoformat()

        with self._connect() as conn:
            prev_hash = self._get_latest_chain_hash(conn)
            chain_hash = compute_chain_hash(prev_hash, event_id, file_sha256, trigger_iso)

            record: Dict[str, Any] = {
                "event_id": event_id,
                "raw_evidence": {
                    "file": str(flac_path.as_posix()),
                    "sha256": file_sha256,
                    "prev_chain_hash": prev_hash,
                    "chain_hash": chain_hash,
                    "sample_rate": event.sample_rate,
                    "channels": 1,
                    "ntp_synced": bool(ntp_synced),
                    "device_calibrated": bool(profile.calibrated),
                    "node_id": profile.node_id,
                    "location_tag": profile.location_tag,
                    "start_time": event.start_time.isoformat(),
                    "end_time": event.end_time.isoformat(),
                    "trigger_time": trigger_iso,
                },
                "algo_metrics": {
                    "trigger_band": event.trigger_band,
                    "peak_dbfs": event.peak_dbfs,
                    "baseline_dbfs": event.baseline_dbfs,
                    "delta_db": event.delta_db,
                    "low_peak_dbfs": event.low_peak_dbfs,
                    "high_peak_dbfs": event.high_peak_dbfs,
                    "duration_sec": event.duration_sec,
                    "inferred_tag": event.inferred_tag,
                },
                "human_review": {
                    "verified": False,
                    "label": None,
                    "direction": "",
                    "notes": "",
                },
            }

            conn.execute(
                """
                INSERT INTO events (
                    event_id, event_date,
                    evidence_file, sha256, prev_chain_hash, chain_hash,
                    sample_rate, channels,
                    ntp_synced, device_calibrated, node_id, location_tag,
                    start_time, end_time, trigger_time,
                    trigger_band, peak_dbfs, baseline_dbfs, delta_db,
                    low_peak_dbfs, high_peak_dbfs, duration_sec, inferred_tag,
                    human_verified, human_label, human_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, '')
                """,
                (
                    event_id,
                    event_date,
                    record["raw_evidence"]["file"],
                    file_sha256,
                    prev_hash,
                    chain_hash,
                    event.sample_rate,
                    1,
                    int(bool(ntp_synced)),
                    int(bool(profile.calibrated)),
                    profile.node_id,
                    profile.location_tag,
                    record["raw_evidence"]["start_time"],
                    record["raw_evidence"]["end_time"],
                    trigger_iso,
                    event.trigger_band,
                    event.peak_dbfs,
                    event.baseline_dbfs,
                    event.delta_db,
                    event.low_peak_dbfs,
                    event.high_peak_dbfs,
                    event.duration_sec,
                    event.inferred_tag,
                ),
            )

        json_path = day_dir / f"{event_id}.json"
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def ingest_remote_flac(
        self,
        event_id: str,
        flac_bytes: bytes,
        client_sha256: str,
        metadata: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        """Atomically ingest a pre-sliced lossless FLAC from a remote edge node.

        Verifies bit-for-bit SHA-256 match against client_sha256, preserves the
        exact FLAC bytes without re-encoding, enforces idempotency on event_id,
        and appends into the central hash chain.
        Returns (record, created_bool).
        """
        actual_sha = hashlib.sha256(flac_bytes).hexdigest()
        if client_sha256 and actual_sha != client_sha256:
            raise ValueError(
                f"SHA-256 mismatch for {event_id}: expected {client_sha256}, got {actual_sha}"
            )

        existing = self.get_event(event_id)
        if existing is not None:
            return existing, False

        trigger_dt = datetime.fromisoformat(str(metadata["trigger_time"]))
        event_date = trigger_dt.strftime("%Y-%m-%d")
        day_dir = self._evidence_dir / event_date
        day_dir.mkdir(parents=True, exist_ok=True)

        # Sanitize event_id for safe filename
        safe_id = "".join(ch for ch in event_id if ch.isalnum() or ch in ("_", "-"))
        if not safe_id:
            raise ValueError("Invalid event_id")

        flac_path = day_dir / f"{safe_id}.flac"
        flac_path.write_bytes(flac_bytes)

        # Validate FLAC readability & compute duration from actual audio frames
        info = sf.info(str(flac_path))
        duration_sec = round(float(info.frames) / float(info.samplerate), 3) if info.samplerate else 0.0
        sample_rate = int(metadata.get("sample_rate") or info.samplerate)
        trigger_iso = trigger_dt.isoformat()

        with self._connect() as conn:
            prev_hash = self._get_latest_chain_hash(conn)
            chain_hash = compute_chain_hash(prev_hash, safe_id, actual_sha, trigger_iso)

            record: Dict[str, Any] = {
                "event_id": safe_id,
                "raw_evidence": {
                    "file": str(flac_path.as_posix()),
                    "sha256": actual_sha,
                    "prev_chain_hash": prev_hash,
                    "chain_hash": chain_hash,
                    "sample_rate": sample_rate,
                    "channels": int(info.channels or 1),
                    "ntp_synced": bool(metadata.get("ntp_synced", False)),
                    "device_calibrated": bool(metadata.get("device_calibrated", False)),
                    "node_id": str(metadata.get("node_id") or "edge_node"),
                    "location_tag": str(metadata.get("location_tag") or "unknown"),
                    "start_time": str(metadata.get("start_time") or trigger_iso),
                    "end_time": str(metadata.get("end_time") or trigger_iso),
                    "trigger_time": trigger_iso,
                },
                "algo_metrics": {
                    "trigger_band": str(metadata.get("trigger_band") or "low_impact_20_180hz"),
                    "peak_dbfs": float(metadata.get("peak_dbfs", -20.0)),
                    "baseline_dbfs": float(metadata.get("baseline_dbfs", -50.0)),
                    "delta_db": float(metadata.get("delta_db", 15.0)),
                    "low_peak_dbfs": float(metadata.get("low_peak_dbfs", -25.0)),
                    "high_peak_dbfs": float(metadata.get("high_peak_dbfs", -30.0)),
                    "duration_sec": duration_sec,
                    "inferred_tag": str(metadata.get("inferred_tag") or "suspected_external_impact"),
                },
                "human_review": {
                    "verified": False,
                    "label": None,
                    "direction": "",
                    "notes": "",
                },
            }

            conn.execute(
                """
                INSERT INTO events (
                    event_id, event_date,
                    evidence_file, sha256, prev_chain_hash, chain_hash,
                    sample_rate, channels,
                    ntp_synced, device_calibrated, node_id, location_tag,
                    start_time, end_time, trigger_time,
                    trigger_band, peak_dbfs, baseline_dbfs, delta_db,
                    low_peak_dbfs, high_peak_dbfs, duration_sec, inferred_tag,
                    human_verified, human_label, human_direction, human_notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, '', '')
                """,
                (
                    safe_id,
                    event_date,
                    record["raw_evidence"]["file"],
                    actual_sha,
                    prev_hash,
                    chain_hash,
                    sample_rate,
                    record["raw_evidence"]["channels"],
                    int(record["raw_evidence"]["ntp_synced"]),
                    int(record["raw_evidence"]["device_calibrated"]),
                    record["raw_evidence"]["node_id"],
                    record["raw_evidence"]["location_tag"],
                    record["raw_evidence"]["start_time"],
                    record["raw_evidence"]["end_time"],
                    trigger_iso,
                    record["algo_metrics"]["trigger_band"],
                    record["algo_metrics"]["peak_dbfs"],
                    record["algo_metrics"]["baseline_dbfs"],
                    record["algo_metrics"]["delta_db"],
                    record["algo_metrics"]["low_peak_dbfs"],
                    record["algo_metrics"]["high_peak_dbfs"],
                    duration_sec,
                    record["algo_metrics"]["inferred_tag"],
                ),
            )

        json_path = day_dir / f"{safe_id}.json"
        json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record, True

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_events_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE event_date = ? ORDER BY trigger_time ASC",
                (date_str,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_all_events(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY rowid ASC").fetchall()
        return [self._row_to_record(r) for r in rows]

    def update_human_review(
        self,
        event_id: str,
        verified: bool,
        label: Optional[str] = None,
        notes: str = "",
        direction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update ONLY human_review fields; raw_evidence and algo_metrics remain immutable."""
        with self._connect() as conn:
            if direction is None:
                cur = conn.execute(
                    """
                    UPDATE events
                    SET human_verified = ?, human_label = ?, human_notes = ?
                    WHERE event_id = ?
                    """,
                    (int(bool(verified)), label, notes, event_id),
                )
            else:
                cur = conn.execute(
                    """
                    UPDATE events
                    SET human_verified = ?, human_label = ?, human_direction = ?, human_notes = ?
                    WHERE event_id = ?
                    """,
                    (int(bool(verified)), label, direction, notes, event_id),
                )
            if cur.rowcount == 0:
                raise KeyError(f"Event not found: {event_id}")

        record = self.get_event(event_id)
        assert record is not None

        flac_path = Path(record["raw_evidence"]["file"])
        json_path = flac_path.with_suffix(".json")
        if json_path.exists():
            sidecar = json.loads(json_path.read_text(encoding="utf-8"))
            sidecar["human_review"] = record["human_review"]
            json_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")

        return record

    def add_corroboration_log(
        self,
        category: str,
        summary: str,
        logged_at: Optional[datetime] = None,
        reference_code: str = "",
        attachment_path: Optional[Path] = None,
        related_event_id: str = "",
    ) -> Dict[str, Any]:
        """Record external non-acoustic proof (110 receipt, property/community notice, neighbor witness)."""
        if category not in VALID_CORROBORATION_TYPES:
            raise ValueError(
                f"Invalid category '{category}'. Choose from: {', '.join(VALID_CORROBORATION_TYPES)}"
            )
        ts = logged_at or datetime.now().astimezone()
        log_date = ts.strftime("%Y-%m-%d")
        att_hash = ""
        if attachment_path is not None:
            att_hash = compute_sha256(Path(attachment_path))

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO corroboration_logs (
                    log_date, logged_at, category, reference_code,
                    summary, attachment_sha256, related_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_date,
                    ts.isoformat(),
                    category,
                    reference_code,
                    summary,
                    att_hash,
                    related_event_id,
                ),
            )
            log_id = int(cur.lastrowid)

        return {
            "log_id": log_id,
            "log_date": log_date,
            "logged_at": ts.isoformat(),
            "category": category,
            "reference_code": reference_code,
            "summary": summary,
            "attachment_sha256": att_hash,
            "related_event_id": related_event_id,
        }

    def list_corroboration_logs(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if date_str:
                rows = conn.execute(
                    "SELECT * FROM corroboration_logs WHERE log_date = ? ORDER BY logged_at ASC",
                    (date_str,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM corroboration_logs ORDER BY logged_at ASC"
                ).fetchall()
        return [dict(r) for r in rows]

    def verify_integrity(self, event_id: str) -> bool:
        """Verify stored FLAC file still matches its recorded SHA-256 digest."""
        record = self.get_event(event_id)
        if record is None:
            raise KeyError(f"Event not found: {event_id}")
        flac_path = Path(record["raw_evidence"]["file"])
        if not flac_path.exists():
            return False
        return compute_sha256(flac_path) == record["raw_evidence"]["sha256"]

    def verify_chain(self, verify_files: bool = True) -> Tuple[bool, List[str]]:
        """Verify the entire append-only hash chain and optionally every FLAC file's SHA-256."""
        events = self.list_all_events()
        errors: List[str] = []
        expected_prev = GENESIS_CHAIN_HASH

        for ev in events:
            eid = ev["event_id"]
            raw = ev["raw_evidence"]
            if raw["prev_chain_hash"] != expected_prev:
                errors.append(
                    f"{eid}: prev_chain_hash mismatch (expected {expected_prev[:12]}..., "
                    f"got {raw['prev_chain_hash'][:12]}...)"
                )
            recomputed = compute_chain_hash(
                raw["prev_chain_hash"],
                eid,
                raw["sha256"],
                raw["trigger_time"],
            )
            if raw["chain_hash"] != recomputed:
                errors.append(f"{eid}: chain_hash corrupted")
            if verify_files:
                flac_path = Path(raw["file"])
                if not flac_path.exists():
                    errors.append(f"{eid}: missing FLAC file {flac_path}")
                elif compute_sha256(flac_path) != raw["sha256"]:
                    errors.append(f"{eid}: FLAC file SHA-256 mismatch (tampered)")
            expected_prev = raw["chain_hash"]

        return (len(errors) == 0), errors

    def export_timestamp_manifest(self, output_path: Path, date_str: Optional[str] = None) -> Path:
        """Export a zero-audio cryptographic digest manifest for OpenTimestamps / RFC 3161 anchoring."""
        events = self.list_events_by_date(date_str) if date_str else self.list_all_events()
        lines = [
            "# ham-monitor Cryptographic Digest Manifest (Zero Audio / Privacy-Safe)",
            "# Anchor this file with OpenTimestamps (`ots stamp <file>`) or a trusted TSA",
            "# Format: event_id | trigger_time | flac_sha256 | chain_hash",
        ]
        for ev in events:
            raw = ev["raw_evidence"]
            lines.append(
                f"{ev['event_id']} | {raw['trigger_time']} | {raw['sha256']} | {raw['chain_hash']}"
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path

    def generate_daily_report(self, date_str: str) -> Path:
        """Export a concise Markdown morning review report for YYYY-MM-DD."""
        events = self.list_events_by_date(date_str)
        corroborations = self.list_corroboration_logs(date_str)
        report_path = self._reports_dir / f"{date_str}_report.md"

        total_duration = sum(e["algo_metrics"]["duration_sec"] for e in events)
        max_delta = max((e["algo_metrics"]["delta_db"] for e in events), default=0.0)
        calibrated_note = (
            "已校准 (dB SPL)"
            if self._profile.calibrated
            else "未校准相对电平 (dBFS / 超基线增量 delta_db，非国标分贝仪读数)"
        )

        lines = [
            f"# 夜间噪声监测日报 ({date_str})",
            "",
            f"- **节点**: `{self._profile.node_id}` (`{self._profile.location_tag}`)",
            f"- **计量状态**: {calibrated_note}",
            f"- **命中事件总数**: {len(events)} 次",
            f"- **累计录证时长**: {total_duration:.1f} 秒",
            f"- **最大超基线增量**: +{max_delta:.1f} dB",
            "",
        ]

        if not events:
            lines.append("今日监测窗口内未触发超标噪声事件。")
        else:
            lines.extend(
                [
                    "## 声学事件明细",
                    "",
                    "| 触发时间 | 时长(s) | 触发频段 | 峰值(dBFS) | 超基线(+dB) | 复核状态 | 证据文件 |",
                    "| --- | ---: | --- | ---: | ---: | --- | --- |",
                ]
            )
            for e in events:
                raw = e["raw_evidence"]
                algo = e["algo_metrics"]
                rev = e["human_review"]
                t_str = datetime.fromisoformat(raw["trigger_time"]).strftime("%H:%M:%S")
                if rev["verified"]:
                    cat_str = get_category_label(rev["label"]) if rev["label"] else "确认"
                    dir_str = f"/{get_direction_label(rev.get('direction'), short=True)}" if rev.get("direction") else ""
                    rev_str = f"已复核 ({cat_str}{dir_str})"
                else:
                    rev_str = "待复核"
                lines.append(
                    f"| {t_str} | {algo['duration_sec']:.1f} | `{algo['trigger_band']}` | "
                    f"{algo['peak_dbfs']:.1f} | +{algo['delta_db']:.1f} | {rev_str} | `{raw['file']}` |"
                )

        if corroborations:
            lines.extend(
                [
                    "",
                    "## 外部互证记录（物业 / 社区 / 报警 / 邻里）",
                    "",
                    "| 记录时间 | 类别 | 编号/回执 | 摘要 | 关联事件 |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for c in corroborations:
                t_str = datetime.fromisoformat(c["logged_at"]).strftime("%H:%M:%S")
                lines.append(
                    f"| {t_str} | `{c['category']}` | {c['reference_code'] or '-'} | "
                    f"{c['summary']} | `{c['related_event_id'] or '-'}` |"
                )

        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path

    def cleanup_old_raw(self) -> int:
        """Delete temporary files older than raw_retention_days in raw_dir ONLY.

        NEVER touches evidence_dir.
        """
        cutoff = time.time() - self._storage.raw_retention_days * 86400
        removed = 0
        for p in self._raw_dir.rglob("*"):
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        return removed

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "raw_evidence": {
                "file": row["evidence_file"],
                "sha256": row["sha256"],
                "prev_chain_hash": row["prev_chain_hash"],
                "chain_hash": row["chain_hash"],
                "sample_rate": row["sample_rate"],
                "channels": row["channels"],
                "ntp_synced": bool(row["ntp_synced"]),
                "device_calibrated": bool(row["device_calibrated"]),
                "node_id": row["node_id"],
                "location_tag": row["location_tag"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "trigger_time": row["trigger_time"],
            },
            "algo_metrics": {
                "trigger_band": row["trigger_band"],
                "peak_dbfs": row["peak_dbfs"],
                "baseline_dbfs": row["baseline_dbfs"],
                "delta_db": row["delta_db"],
                "low_peak_dbfs": row["low_peak_dbfs"],
                "high_peak_dbfs": row["high_peak_dbfs"],
                "duration_sec": row["duration_sec"],
                "inferred_tag": row["inferred_tag"],
            },
            "human_review": {
                "verified": bool(row["human_verified"]),
                "label": row["human_label"],
                "direction": row["human_direction"] if "human_direction" in row.keys() else "",
                "notes": row["human_notes"],
            },
        }
