from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from ham.config import DeviceProfile, StorageConfig, load_config
from ham.correlate import correlate_dual_nodes
from ham.desensitize import (
    export_lowpass_desensitized_audio,
    export_share_bundle,
    redact_text,
)
from ham.dsp import rms_dbfs
from ham.evidence import EvidenceStore
from ham.trigger import CompletedEvent
from scripts.import_audio import import_audio_file


def _dummy_event(
    trigger_time: datetime,
    sr: int = 16000,
    amp_80hz: float = 0.2,
    amp_1500hz: float = 0.2,
    low_peak: float = -18.0,
    high_peak: float = -22.0,
) -> CompletedEvent:
    t = np.arange(sr * 2, dtype=np.float64) / float(sr)
    pcm = (
        amp_80hz * np.sin(2.0 * np.pi * 80.0 * t)
        + amp_1500hz * np.sin(2.0 * np.pi * 1500.0 * t)
    ).astype(np.float32)
    return CompletedEvent(
        start_time=trigger_time - timedelta(seconds=1),
        end_time=trigger_time + timedelta(seconds=1),
        trigger_time=trigger_time,
        sample_rate=sr,
        raw_pcm=pcm,
        trigger_band="dual_band_impact_and_vocal",
        peak_dbfs=-10.0,
        baseline_dbfs=-45.0,
        delta_db=25.0,
        low_peak_dbfs=low_peak,
        high_peak_dbfs=high_peak,
        inferred_tag="suspected_external_mixed",
    )


def _make_store(tmp_path: Path, node_id: str = "test_node", location: str = "room_center") -> EvidenceStore:
    storage = StorageConfig(
        base_dir=str(tmp_path),
        evidence_dir=str(tmp_path / "evidence"),
        raw_dir=str(tmp_path / "raw"),
        db_path=str(tmp_path / "ham_events.sqlite3"),
        reports_dir=str(tmp_path / "reports"),
    )
    return EvidenceStore(storage, DeviceProfile(node_id=node_id, location_tag=location))


def test_all_tiered_profiles_load_validly() -> None:
    profiles_dir = Path(__file__).resolve().parent.parent / "config" / "profiles"
    yamls = sorted(profiles_dir.glob("*.yaml"))
    assert len(yamls) == 3
    for p in yamls:
        cfg = load_config(p)
        assert cfg.audio.sample_rate == 48000
        assert cfg.trigger.pre_roll_sec == 15.0

    tier2 = load_config(profiles_dir / "tier2_calibrated_measurement.yaml")
    assert tier2.node.calibrated is True
    assert tier2.node.to_spl_db(-40.0) == 80.0


def test_hash_chain_verification_and_deletion_detection(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t1 = datetime(2026, 11, 25, 23, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 25, 23, 25, 0, tzinfo=timezone.utc)

    r1 = store.save_event(_dummy_event(t1))
    r2 = store.save_event(_dummy_event(t2))

    assert r2["raw_evidence"]["prev_chain_hash"] == r1["raw_evidence"]["chain_hash"]
    ok, errors = store.verify_chain(verify_files=True)
    assert ok is True
    assert errors == []

    # Simulate illicit deletion of the first event from SQLite -> chain MUST break!
    conn = sqlite3.connect(str(tmp_path / "ham_events.sqlite3"))
    conn.execute("DELETE FROM events WHERE event_id = ?", (r1["event_id"],))
    conn.commit()
    conn.close()

    ok_after, errors_after = store.verify_chain(verify_files=True)
    assert ok_after is False
    assert any("prev_chain_hash mismatch" in e for e in errors_after)


def test_lowpass_desensitization_attenuates_speech_preserves_impact(tmp_path: Path) -> None:
    sr = 16000
    t = np.arange(sr * 2, dtype=np.float64) / float(sr)
    # Mix 80 Hz floor impact + 1500 Hz vocal formant at equal amplitude
    mixed = (0.25 * np.sin(2.0 * np.pi * 80.0 * t) + 0.25 * np.sin(2.0 * np.pi * 1500.0 * t)).astype(
        np.float32
    )
    src = tmp_path / "raw_mixed.flac"
    dst = tmp_path / "desensitized_lp250.flac"
    sf.write(str(src), mixed, sr, format="FLAC", subtype="PCM_16")

    export_lowpass_desensitized_audio(src, dst, cutoff_hz=250.0)
    out, out_sr = sf.read(str(dst), dtype="float64")
    assert out_sr == sr

    # Measure energy in impact band (40-140 Hz) vs speech formant band (1000-2000 Hz)
    sos_low = butter(4, (40.0, 140.0), btype="bandpass", fs=sr, output="sos")
    sos_speech = butter(4, (1000.0, 2000.0), btype="bandpass", fs=sr, output="sos")

    low_before = rms_dbfs(sosfilt(sos_low, mixed))
    low_after = rms_dbfs(sosfilt(sos_low, out))
    speech_before = rms_dbfs(sosfilt(sos_speech, mixed))
    speech_after = rms_dbfs(sosfilt(sos_speech, out))

    assert abs(low_after - low_before) < 2.0, "Low-frequency impact should be preserved"
    assert (speech_before - speech_after) > 45.0, "Speech formants must be heavily attenuated"


def test_share_bundle_strips_pii_paths_and_includes_corroboration(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t1 = datetime(2026, 11, 25, 23, 42, 0, tzinfo=timezone.utc)
    rec = store.save_event(_dummy_event(t1))

    # Add sensitive notes containing a mobile number & apartment unit
    store.update_human_review(
        event_id=rec["event_id"],
        verified=True,
        label="upstairs_impact",
        notes="联系物业13812345678，反映3栋1202室深夜蹦跳",
    )

    # Add external corroboration log with attachment
    receipt = tmp_path / "receipt_110.txt"
    receipt.write_text("110 dispatch receipt", encoding="utf-8")
    store.add_corroboration_log(
        category="police_call",
        summary="23:50 拨打110，民警上门劝阻3栋1202室，联系人13987654321",
        logged_at=t1 + timedelta(minutes=8),
        reference_code="110-20261125-0088",
        attachment_path=receipt,
        related_event_id=rec["event_id"],
    )

    # Daily report must include corroboration table
    report = store.generate_daily_report("2026-11-25")
    report_txt = report.read_text(encoding="utf-8")
    assert "110-20261125-0088" in report_txt
    assert "外部互证记录" in report_txt

    # Export desensitized ZIP bundle
    zip_path = export_share_bundle(
        store=store,
        output_dir=tmp_path / "exports",
        date_str="2026-11-25",
        include_lowpass_audio=True,
        include_notes=True,
        create_zip=True,
    )
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())
        assert "MANIFEST.json" in names
        assert "SUMMARY_REPORT.md" in names
        assert "TIMELINE.csv" in names
        assert "TIMESTAMP_MANIFEST.txt" in names
        assert any(n.startswith("desensitized_audio/") and n.endswith(".flac") for n in names)

        manifest_raw = zf.read("MANIFEST.json").decode("utf-8")
        manifest = json.loads(manifest_raw)

    # Verify zero PII leakage and zero absolute OS paths
    assert "13812345678" not in manifest_raw
    assert "13987654321" not in manifest_raw
    assert "3栋1202室" not in manifest_raw
    assert str(tmp_path.as_posix()) not in manifest_raw
    assert "138****5678" in manifest["events"][0]["human_review"]["notes"]
    assert "[UNIT_REDACTED]" in manifest["events"][0]["human_review"]["notes"]
    assert redact_text("13812345678") == "138****5678"


def test_dual_node_correlation_ceiling_vs_center(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t_upstairs = datetime(2026, 11, 25, 23, 15, 0, tzinfo=timezone.utc)
    t_self = datetime(2026, 11, 25, 23, 30, 0, tzinfo=timezone.utc)

    ceiling_prof = DeviceProfile(node_id="android_top", location_tag="ceiling")
    center_prof = DeviceProfile(node_id="macbook_mid", location_tag="room_center")

    # Event 1: ceiling is 8 dB louder than room_center (1.1s clock skew) -> likely_ceiling_source
    store.save_event(
        _dummy_event(t_upstairs, low_peak=-14.0, high_peak=-20.0),
        profile_override=ceiling_prof,
    )
    store.save_event(
        _dummy_event(t_upstairs + timedelta(seconds=1.1), low_peak=-22.0, high_peak=-26.0),
        profile_override=center_prof,
    )

    # Event 2: room_center is 7 dB louder than ceiling -> likely_room_or_floor_source
    store.save_event(
        _dummy_event(t_self, low_peak=-25.0, high_peak=-28.0),
        profile_override=ceiling_prof,
    )
    store.save_event(
        _dummy_event(t_self + timedelta(seconds=0.4), low_peak=-18.0, high_peak=-24.0),
        profile_override=center_prof,
    )

    pairs = correlate_dual_nodes(store.list_all_events(), max_time_skew_sec=2.0, upstairs_margin_db=3.0)
    assert len(pairs) == 2
    assert pairs[0].assessment == "likely_ceiling_source"
    assert pairs[0].low_band_diff_db == 8.0
    assert pairs[1].assessment == "likely_room_or_floor_source"
    assert pairs[1].low_band_diff_db == -7.0


def test_offline_audio_import_pipeline(tmp_path: Path) -> None:
    sr = 16000
    rng = np.random.default_rng(123)
    sig = (rng.standard_normal(sr * 20) * 2e-4).astype(np.float32)
    t = np.arange(sr * 2, dtype=np.float64) / float(sr)
    sig[12 * sr : 14 * sr] += (0.25 * np.sin(2.0 * np.pi * 90.0 * t)).astype(np.float32)

    wav_path = tmp_path / "android_night.wav"
    sf.write(str(wav_path), sig, sr, format="WAV", subtype="PCM_16")

    cfg_path = tmp_path / "import_cfg.yaml"
    cfg_path.write_text(
        f"""
node:
  node_id: "default_node"
  location_tag: "room_center"
  calibrated: false
audio:
  sample_rate: {sr}
  channels: 1
  block_duration_sec: 0.25
schedule:
  enabled: false
dsp:
  low_band_hz: [20.0, 180.0]
  high_band_hz: [300.0, 4000.0]
  baseline_window_sec: 15.0
  baseline_quantile: 0.20
  baseline_warmup_sec: 2.0
trigger:
  low_delta_db: 12.0
  low_min_dbfs: -55.0
  high_delta_db: 14.0
  high_min_dbfs: -50.0
  min_active_sec: 0.75
  pre_roll_sec: 5.0
  post_roll_sec: 3.0
  max_event_sec: 60.0
storage:
  base_dir: "{tmp_path.as_posix()}"
  evidence_dir: "{(tmp_path / 'evidence').as_posix()}"
  raw_dir: "{(tmp_path / 'raw').as_posix()}"
  db_path: "{(tmp_path / 'ham_events.sqlite3').as_posix()}"
  reports_dir: "{(tmp_path / 'reports').as_posix()}"
""",
        encoding="utf-8",
    )

    records = import_audio_file(
        input_path=wav_path,
        config_path=cfg_path,
        start_time=datetime(2026, 11, 25, 23, 0, 0, tzinfo=timezone.utc),
        node_id="android_ceiling",
        location_tag="ceiling",
    )
    assert len(records) == 1
    assert records[0]["raw_evidence"]["node_id"] == "android_ceiling"
    assert records[0]["raw_evidence"]["location_tag"] == "ceiling"
    assert records[0]["algo_metrics"]["trigger_band"] == "low_impact_20_180hz"
