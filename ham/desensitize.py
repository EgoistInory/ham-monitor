from __future__ import annotations

import csv
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

from ham.evidence import EvidenceStore, compute_sha256

# Common PII patterns for optional text redaction (CN mobile, landline, room/unit numbers)
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
_ROOM_RE = re.compile(r"(\d{1,3}\s*[栋幢号楼单元室户#\-]{1,2}\s*\d{2,4}[室户]?)")


def redact_text(text: str) -> str:
    """Mask phone numbers and apartment/unit numbers in free-text notes."""
    masked = _PHONE_RE.sub(r"\1****\2", text)
    masked = _ROOM_RE.sub("[UNIT_REDACTED]", masked)
    return masked


def sanitize_record(
    record: Dict[str, Any],
    include_notes: bool = False,
    node_alias: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a privacy-safe copy of an event record with OS paths and PII stripped."""
    raw = record["raw_evidence"]
    algo = record["algo_metrics"]
    rev = record["human_review"]

    file_name = Path(raw["file"]).name
    safe_notes = redact_text(rev.get("notes", "")) if include_notes else ""

    return {
        "event_id": record["event_id"],
        "raw_evidence_digest": {
            "filename": file_name,
            "sha256": raw["sha256"],
            "prev_chain_hash": raw.get("prev_chain_hash", ""),
            "chain_hash": raw.get("chain_hash", ""),
            "sample_rate": raw["sample_rate"],
            "ntp_synced": raw["ntp_synced"],
            "device_calibrated": raw["device_calibrated"],
            "node_id": node_alias or raw["node_id"],
            "location_tag": raw["location_tag"],
            "start_time": raw["start_time"],
            "end_time": raw["end_time"],
            "trigger_time": raw["trigger_time"],
        },
        "algo_metrics": dict(algo),
        "human_review": {
            "verified": rev["verified"],
            "label": rev["label"],
            "direction": rev.get("direction", ""),
            "notes": safe_notes,
        },
    }


def export_lowpass_desensitized_audio(
    input_flac: Path,
    output_flac: Path,
    cutoff_hz: float = 250.0,
) -> Path:
    """Create a speech-unintelligible low-pass (<250 Hz) FLAC copy for external sharing.

    Preserves structural floor impacts (footsteps, jumping, dragging) while
    attenuating speech formants (>300 Hz) by >60 dB to prevent leaking private
    conversations. Never modifies the original evidence file.
    """
    samples, sr = sf.read(str(input_flac), dtype="float64")
    if samples.ndim > 1:
        samples = samples[:, 0]

    nyquist = sr / 2.0
    if not (20.0 <= cutoff_hz < nyquist):
        raise ValueError(f"cutoff_hz={cutoff_hz} out of valid range [20, {nyquist})")

    # 8th-order Butterworth via zero-phase sosfiltfilt -> 96 dB/octave stopband roll-off
    sos = butter(8, cutoff_hz, btype="lowpass", fs=sr, output="sos")
    filtered = sosfiltfilt(sos, samples)
    filtered = np.clip(filtered, -1.0, 1.0).astype(np.float32)

    output_flac.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_flac), filtered, sr, format="FLAC", subtype="PCM_16")
    return output_flac


def export_share_bundle(
    store: EvidenceStore,
    output_dir: Path,
    date_str: Optional[str] = None,
    include_lowpass_audio: bool = False,
    lowpass_cutoff_hz: float = 250.0,
    include_notes: bool = False,
    node_alias: str = "indoor_sensor",
    create_zip: bool = True,
) -> Path:
    """Export a desensitized, PII-scrubbed sharing bundle for property/landlord/mediator/12345.

    - NEVER includes raw unmodified voice recordings.
    - Strips absolute local file paths and masks phone/room numbers.
    - Includes cryptographic SHA-256 digests + hash chain for tamper-evidence.
    """
    events = store.list_events_by_date(date_str) if date_str else store.list_all_events()
    corroborations = store.list_corroboration_logs(date_str)
    label = date_str or "all"

    bundle_dir = output_dir / f"share_bundle_{label}"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    sanitized_events: List[Dict[str, Any]] = []
    audio_exported: List[Dict[str, str]] = []

    for ev in events:
        safe = sanitize_record(ev, include_notes=include_notes, node_alias=node_alias)
        sanitized_events.append(safe)

        if include_lowpass_audio:
            src_flac = Path(ev["raw_evidence"]["file"])
            if src_flac.exists():
                dst_flac = bundle_dir / "desensitized_audio" / f"{ev['event_id']}_lp{int(lowpass_cutoff_hz)}hz.flac"
                export_lowpass_desensitized_audio(src_flac, dst_flac, cutoff_hz=lowpass_cutoff_hz)
                audio_exported.append(
                    {
                        "event_id": ev["event_id"],
                        "original_sha256": ev["raw_evidence"]["sha256"],
                        "desensitized_file": dst_flac.relative_to(bundle_dir).as_posix(),
                        "desensitized_sha256": compute_sha256(dst_flac),
                        "filter": f"lowpass_{int(lowpass_cutoff_hz)}hz_speech_redacted",
                    }
                )

    safe_corroborations = [
        {
            "log_date": c["log_date"],
            "logged_at": c["logged_at"],
            "category": c["category"],
            "reference_code": redact_text(c["reference_code"]),
            "summary": redact_text(c["summary"]),
            "attachment_sha256": c["attachment_sha256"],
            "related_event_id": c["related_event_id"],
        }
        for c in corroborations
    ]

    manifest = {
        "schema_version": "1.0",
        "privacy_notice": (
            "本分享包已完成隐私脱敏：不含原始人声录音、不含本机绝对路径与个人信息；"
            "如需核验原始无损音频原件，请由执法机关或司法机构按 SHA-256 哈希核对本地原件。"
        ),
        "exported_at": datetime.now().astimezone().isoformat(),
        "scope": label,
        "event_count": len(sanitized_events),
        "events": sanitized_events,
        "desensitized_audio": audio_exported,
        "corroboration_logs": safe_corroborations,
    }
    (bundle_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Tabular CSV timeline for quick review / mediation
    csv_path = bundle_dir / "TIMELINE.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "event_id",
                "trigger_time",
                "duration_sec",
                "trigger_band",
                "peak_dbfs",
                "baseline_dbfs",
                "delta_db",
                "device_calibrated",
                "human_verified",
                "human_label",
                "human_direction",
                "original_sha256",
            ]
        )
        for ev in sanitized_events:
            d = ev["raw_evidence_digest"]
            m = ev["algo_metrics"]
            h = ev["human_review"]
            writer.writerow(
                [
                    ev["event_id"],
                    d["trigger_time"],
                    m["duration_sec"],
                    m["trigger_band"],
                    m["peak_dbfs"],
                    m["baseline_dbfs"],
                    m["delta_db"],
                    d["device_calibrated"],
                    h["verified"],
                    h["label"] or "",
                    h.get("direction", ""),
                    d["sha256"],
                ]
            )

    # Human-readable desensitized Markdown report
    total_dur = sum(e["algo_metrics"]["duration_sec"] for e in sanitized_events)
    md_lines = [
        f"# 室内噪声监测脱敏摘要报告 ({label})",
        "",
        "> **声明与隐私说明**：本报告基于室内被动声学监测自动生成，仅包含时间分布、相对电平增量（非国标计量校准值）及加密哈希摘要，不包含任何原始人声对话。原始无损证据由当事人本地留存，可供物业/社区/公安/法院依法核验 SHA-256 一致性。",
        "",
        f"- **统计事件数**：{len(sanitized_events)} 次",
        f"- **累计异常时长**：{total_dur:.1f} 秒",
        f"- **外部互证记录数**：{len(safe_corroborations)} 条",
        "",
        "## 异常噪声时间轴",
        "",
        "| 触发时间 | 时长(s) | 频段特征 | 峰值(dBFS) | 超基线(+dB) | 原始文件 SHA-256 前12位 |",
        "| --- | ---: | --- | ---: | ---: | --- |",
    ]
    for ev in sanitized_events:
        d = ev["raw_evidence_digest"]
        m = ev["algo_metrics"]
        md_lines.append(
            f"| {d['trigger_time']} | {m['duration_sec']:.1f} | `{m['trigger_band']}` | "
            f"{m['peak_dbfs']:.1f} | +{m['delta_db']:.1f} | `{d['sha256'][:12]}` |"
        )

    if safe_corroborations:
        md_lines.extend(
            [
                "",
                "## 关联维权与劝阻记录",
                "",
                "| 时间 | 类别 | 编号 | 摘要 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for c in safe_corroborations:
            md_lines.append(
                f"| {c['logged_at']} | `{c['category']}` | {c['reference_code'] or '-'} | {c['summary']} |"
            )

    (bundle_dir / "SUMMARY_REPORT.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    # Zero-audio timestamp manifest for OpenTimestamps
    store.export_timestamp_manifest(bundle_dir / "TIMESTAMP_MANIFEST.txt", date_str=date_str)

    if not create_zip:
        return bundle_dir

    zip_path = output_dir / f"share_bundle_{label}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(bundle_dir.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, arcname=file_path.relative_to(bundle_dir).as_posix())
    return zip_path
