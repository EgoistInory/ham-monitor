from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import soundfile as sf

from ham.capture import SyntheticAudioSource
from ham.config import (
    AppConfig,
    AudioConfig,
    DSPConfig,
    DeviceProfile,
    ScheduleConfig,
    StorageConfig,
    TriggerConfig,
    load_config,
)
from ham.dsp import DualBandAnalyzer
from ham.evidence import EvidenceStore
from ham.monitor import NoiseMonitor, is_within_schedule
from ham.ring_buffer import AudioRingBuffer


def _make_noise(n_samples: int, amp: float = 1e-4, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n_samples) * amp).astype(np.float32)


def _make_sine(n_samples: int, freq_hz: float, sr: int, amp: float = 0.2) -> np.ndarray:
    t = np.arange(n_samples, dtype=np.float64) / float(sr)
    return (amp * np.sin(2.0 * np.pi * freq_hz * t)).astype(np.float32)


def test_load_default_config() -> None:
    cfg = load_config(Path(__file__).resolve().parent.parent / "config" / "default.yaml")
    assert cfg.audio.sample_rate == 48000
    assert cfg.trigger.pre_roll_sec == 15.0
    assert cfg.node.calibrated is False
    assert cfg.node.to_spl_db(-20.0) is None


def test_ring_buffer_wraparound_and_exact_order() -> None:
    rb = AudioRingBuffer(capacity_samples=10, dtype=np.float32)
    rb.write(np.arange(6, dtype=np.float32))
    assert np.array_equal(rb.read_last(10), np.arange(6, dtype=np.float32))

    # Wrap around capacity=10: total written 0..13 -> last 10 should be 4..13
    rb.write(np.arange(6, 14, dtype=np.float32))
    assert rb.filled_samples == 10
    assert np.array_equal(rb.read_last(10), np.arange(4, 14, dtype=np.float32))
    assert np.array_equal(rb.read_last(4), np.arange(10, 14, dtype=np.float32))

    # Chunk larger than capacity keeps trailing capacity samples
    rb.write(np.arange(100, 125, dtype=np.float32))
    assert np.array_equal(rb.read_last(10), np.arange(115, 125, dtype=np.float32))


def test_dual_band_separation_and_raw_immutability() -> None:
    sr = 48000
    block = int(sr * 0.25)
    analyzer = DualBandAnalyzer(
        sample_rate=sr,
        block_duration_sec=0.25,
        baseline_window_sec=10.0,
        baseline_warmup_sec=1.0,
    )

    # Warmup quiet floor
    for i in range(6):
        quiet = _make_noise(block, amp=1e-4, seed=i)
        m = analyzer.analyze(quiet)
    assert m.baseline_ready is True

    # Low-frequency 80 Hz impact
    low_sig = _make_noise(block, amp=1e-4, seed=99) + _make_sine(block, 80.0, sr, amp=0.15)
    low_before = low_sig.copy()
    m_low = analyzer.analyze(low_sig, freeze_baseline=True)
    assert np.array_equal(low_sig, low_before), "Analyzer must never mutate raw PCM"
    assert m_low.low_delta_db > 25.0
    assert m_low.low_rms_dbfs > m_low.high_rms_dbfs + 20.0

    # Allow low-band IIR filter ring-down to settle before testing high-band isolation
    for i in range(2):
        analyzer.analyze(_make_noise(block, amp=1e-4, seed=200 + i), freeze_baseline=True)

    # High-frequency 1200 Hz vocal shout
    high_sig = _make_noise(block, amp=1e-4, seed=100) + _make_sine(block, 1200.0, sr, amp=0.15)
    m_high = analyzer.analyze(high_sig, freeze_baseline=True)
    assert m_high.high_delta_db > 25.0
    assert m_high.high_rms_dbfs > m_high.low_rms_dbfs + 20.0


def test_preroll_15s_event_merging_flac_and_sha256(tmp_path: Path) -> None:
    sr = 16000  # Use 16kHz in test for fast execution while exercising identical DSP/buffer logic
    block_sec = 0.25
    total_sec = 44  # 20s quiet + 2s low burst + 4s gap + 2s high burst + 16s quiet tail
    signal = _make_noise(sr * total_sec, amp=2e-4, seed=7)

    # Inject a tiny sub-threshold marker in the 15s pre-roll window (t=8s..9s, still quiet)
    marker_start = 8 * sr
    marker_end = 9 * sr
    signal[marker_start:marker_end] += _make_sine(sr, 500.0, sr, amp=5e-4)

    # Burst 1: Low-frequency impact (80 Hz) at t=20s..22s
    signal[20 * sr : 22 * sr] += _make_sine(2 * sr, 80.0, sr, amp=0.20)
    # Gap: t=22s..26s (4s < post_roll_sec=10s -> must merge!)
    # Burst 2: High-frequency shout (1200 Hz) at t=26s..28s
    signal[26 * sr : 28 * sr] += _make_sine(2 * sr, 1200.0, sr, amp=0.18)

    cfg = AppConfig(
        node=DeviceProfile(node_id="macbook_test", location_tag="room_center", calibrated=False),
        audio=AudioConfig(sample_rate=sr, channels=1, block_duration_sec=block_sec),
        schedule=ScheduleConfig(enabled=False),
        dsp=DSPConfig(
            low_band_hz=(20.0, 180.0),
            high_band_hz=(300.0, 4000.0),
            baseline_window_sec=30.0,
            baseline_quantile=0.20,
            baseline_warmup_sec=2.0,
        ),
        trigger=TriggerConfig(
            low_delta_db=12.0,
            low_min_dbfs=-55.0,
            high_delta_db=14.0,
            high_min_dbfs=-50.0,
            min_active_sec=0.75,
            pre_roll_sec=15.0,
            post_roll_sec=10.0,
            max_event_sec=120.0,
        ),
        storage=StorageConfig(
            base_dir=str(tmp_path),
            evidence_dir=str(tmp_path / "evidence"),
            raw_dir=str(tmp_path / "raw"),
            db_path=str(tmp_path / "ham_events.sqlite3"),
            reports_dir=str(tmp_path / "reports"),
            raw_retention_days=7,
        ),
    )

    source = SyntheticAudioSource(
        samples=signal,
        sample_rate=sr,
        block_samples=cfg.audio.block_samples,
        start_time=datetime(2026, 11, 25, 23, 40, 0, tzinfo=timezone.utc),
    )
    store = EvidenceStore(cfg.storage, cfg.node)
    monitor = NoiseMonitor(cfg, source, store, ignore_schedule=True)
    records = monitor.run(install_signals=False)

    # Must merge Burst 1 + Gap + Burst 2 into exactly ONE continuous event
    assert len(records) == 1
    rec = records[0]
    event_id = rec["event_id"]

    assert rec["algo_metrics"]["trigger_band"] == "dual_band_impact_and_vocal"
    assert rec["algo_metrics"]["inferred_tag"] == "suspected_external_mixed"
    assert rec["raw_evidence"]["device_calibrated"] is False

    # Expected audio span: 15s pre-roll (t=5s..20s) + 8s active/gap (t=20s..28s) + 10s post-roll (t=28s..38s) = 33.0s
    flac_path = Path(rec["raw_evidence"]["file"])
    assert flac_path.exists()
    decoded, decoded_sr = sf.read(str(flac_path), dtype="float32")
    assert decoded_sr == sr
    assert abs((decoded.size / sr) - 33.0) <= 0.5

    # Verify lossless preservation of raw waveform (PCM_16 quantization error < 1e-4)
    expected_slice = signal[5 * sr : 5 * sr + decoded.size]
    max_diff = float(np.max(np.abs(decoded - expected_slice)))
    assert max_diff < 1e-4, f"FLAC audio diverged from raw PCM (max_diff={max_diff})"

    # SHA-256 verification must pass, then fail if file is altered
    assert store.verify_integrity(event_id) is True

    # Human review updates ONLY human_review and leaves raw_evidence/algo_metrics intact
    before_raw = rec["raw_evidence"].copy()
    before_algo = rec["algo_metrics"].copy()
    updated = store.update_human_review(
        event_id=event_id,
        verified=True,
        label="upstairs_impact_and_scream",
        notes="23:40 连续蹦跳+鬼叫",
    )
    assert updated["raw_evidence"] == before_raw
    assert updated["algo_metrics"] == before_algo
    assert updated["human_review"] == {
        "verified": True,
        "label": "upstairs_impact_and_scream",
        "notes": "23:40 连续蹦跳+鬼叫",
    }

    # Daily report generation
    report_path = store.generate_daily_report("2026-11-25")
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "夜间噪声监测日报 (2026-11-25)" in report_text
    assert "dual_band_impact_and_vocal" in report_text
    assert "已复核 (upstairs_impact_and_scream)" in report_text

    # Tamper detection
    with flac_path.open("ab") as f:
        f.write(b"\x00")
    assert store.verify_integrity(event_id) is False


def test_short_transient_click_ignored(tmp_path: Path) -> None:
    sr = 16000
    signal = _make_noise(sr * 10, amp=2e-4, seed=11)
    # Single 0.25s transient (< min_active_sec=0.75s)
    signal[5 * sr : int(5.25 * sr)] += _make_sine(int(0.25 * sr), 100.0, sr, amp=0.3)

    cfg = AppConfig(
        audio=AudioConfig(sample_rate=sr, block_duration_sec=0.25),
        schedule=ScheduleConfig(enabled=False),
        dsp=DSPConfig(baseline_warmup_sec=2.0),
        trigger=TriggerConfig(min_active_sec=0.75, pre_roll_sec=2.0, post_roll_sec=2.0),
        storage=StorageConfig(
            base_dir=str(tmp_path),
            evidence_dir=str(tmp_path / "evidence"),
            raw_dir=str(tmp_path / "raw"),
            db_path=str(tmp_path / "ham_events.sqlite3"),
            reports_dir=str(tmp_path / "reports"),
        ),
    )
    source = SyntheticAudioSource(signal, sample_rate=sr, block_samples=cfg.audio.block_samples)
    store = EvidenceStore(cfg.storage, cfg.node)
    records = NoiseMonitor(cfg, source, store, ignore_schedule=True).run(install_signals=False)
    assert records == []


def test_cleanup_only_deletes_old_raw_never_evidence(tmp_path: Path) -> None:
    storage = StorageConfig(
        base_dir=str(tmp_path),
        evidence_dir=str(tmp_path / "evidence"),
        raw_dir=str(tmp_path / "raw"),
        db_path=str(tmp_path / "ham_events.sqlite3"),
        reports_dir=str(tmp_path / "reports"),
        raw_retention_days=7,
    )
    store = EvidenceStore(storage, DeviceProfile())

    old_raw = Path(storage.raw_dir) / "old_probe.flac"
    new_raw = Path(storage.raw_dir) / "new_probe.flac"
    old_evidence = Path(storage.evidence_dir) / "keep_forever.flac"
    old_raw.write_bytes(b"old")
    new_raw.write_bytes(b"new")
    old_evidence.write_bytes(b"evidence")

    ten_days_ago = time.time() - 10 * 86400
    os.utime(old_raw, (ten_days_ago, ten_days_ago))
    os.utime(old_evidence, (ten_days_ago, ten_days_ago))

    removed = store.cleanup_old_raw()
    assert removed == 1
    assert not old_raw.exists()
    assert new_raw.exists()
    assert old_evidence.exists(), "Evidence files must NEVER be auto-deleted"


def test_cross_midnight_schedule() -> None:
    sched = ScheduleConfig(enabled=True, start_time="22:30", end_time="07:00")
    assert is_within_schedule(datetime(2026, 11, 25, 22, 29), sched) is False
    assert is_within_schedule(datetime(2026, 11, 25, 22, 30), sched) is True
    assert is_within_schedule(datetime(2026, 11, 26, 2, 15), sched) is True
    assert is_within_schedule(datetime(2026, 11, 26, 6, 59), sched) is True
    assert is_within_schedule(datetime(2026, 11, 26, 7, 0), sched) is False
    assert is_within_schedule(datetime(2026, 11, 26, 14, 0), sched) is False
