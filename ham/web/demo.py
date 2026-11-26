from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from ham.config import AppConfig, DeviceProfile, StorageConfig
from ham.evidence import EvidenceStore
from ham.trigger import CompletedEvent


def _synth_clip(
    sr: int,
    duration_sec: float,
    low_freq: float,
    low_amp: float,
    high_freq: float,
    high_amp: float,
    seed: int,
) -> np.ndarray:
    n = int(sr * duration_sec)
    t = np.arange(n, dtype=np.float64) / float(sr)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n) * 0.002

    # Build realistic bursts (quiet pre-roll -> impact/vocal bursts -> quiet tail)
    env = np.zeros(n, dtype=np.float64)
    burst_start = int(n * 0.28)
    burst_end = int(n * 0.72)
    env[burst_start:burst_end] = 1.0
    # Add pulsing envelope for rhythmic footsteps/shouting feel
    pulse = 0.55 + 0.45 * np.sin(2.0 * np.pi * 2.2 * t)

    low_wave = low_amp * env * pulse * np.sin(2.0 * np.pi * low_freq * t)
    high_wave = high_amp * env * np.sin(2.0 * np.pi * high_freq * t) * (
        0.5 + 0.5 * np.sin(2.0 * np.pi * 5.0 * t)
    )
    pcm = np.clip(noise + low_wave + high_wave, -0.98, 0.98)
    return pcm.astype(np.float32)


def prepare_demo_sandbox(sandbox_dir: Path) -> tuple[AppConfig, EvidenceStore]:
    """Create an isolated demo database with realistic synthetic events (never touches real data/)."""
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    storage = StorageConfig(
        base_dir=str(sandbox_dir),
        evidence_dir=str(sandbox_dir / "evidence"),
        raw_dir=str(sandbox_dir / "raw"),
        db_path=str(sandbox_dir / "ham_events.sqlite3"),
        reports_dir=str(sandbox_dir / "reports"),
    )
    profile = DeviceProfile(
        node_id="demo_primary_node",
        location_tag="room_center",
        calibrated=False,
    )
    cfg = AppConfig(node=profile, storage=storage)
    store = EvidenceStore(storage, profile)

    if store.list_all_events():
        return cfg, store

    tz = timezone(timedelta(hours=8))
    sr = 16000
    ceiling_profile = DeviceProfile(
        node_id="demo_android_top",
        location_tag="ceiling",
        calibrated=False,
    )

    # Seed realistic nocturnal events across 23:18 -> 02:15
    specs = [
        (
            datetime(2026, 11, 24, 23, 18, 12, tzinfo=tz),
            12.0,
            78.0,
            0.28,
            1100.0,
            0.0,
            "low_impact_20_180hz",
            -14.2,
            -46.5,
            24.8,
            -16.1,
            -44.0,
            "suspected_external_impact",
            profile,
            True,
            "furniture_drag",
            "upstairs",
            "23:18 连续桌椅拖拽与重物移动声，已微信提醒物业管家。",
        ),
        (
            datetime(2026, 11, 24, 23, 18, 13, tzinfo=tz),
            12.0,
            78.0,
            0.42,
            1100.0,
            0.0,
            "low_impact_20_180hz",
            -9.4,
            -44.0,
            29.6,
            -10.2,
            -43.5,
            "suspected_external_impact",
            ceiling_profile,
            True,
            "furniture_drag",
            "upstairs",
            "衣柜顶备用机同窗捕获，能量较室内中部高出约 5.9 dB，确认来自正楼上。",
        ),
        (
            datetime(2026, 11, 24, 23, 52, 40, tzinfo=tz),
            16.0,
            85.0,
            0.24,
            1350.0,
            0.26,
            "dual_band_impact_and_vocal",
            -11.5,
            -47.2,
            28.4,
            -15.4,
            -13.8,
            "suspected_external_mixed",
            profile,
            True,
            "shouting_screaming",
            "upstairs",
            "深夜突发尖叫喊麦叠加蹦跳，已留存记录并向社区网格员报备。",
        ),
        (
            datetime(2026, 11, 25, 0, 36, 5, tzinfo=tz),
            14.0,
            70.0,
            0.0,
            1500.0,
            0.30,
            "high_vocal_300_4000hz",
            -12.8,
            -48.1,
            26.2,
            -45.0,
            -14.2,
            "suspected_external_vocal",
            profile,
            True,
            "loud_audio_speaker",
            "uncertain",
            "凌晨音响外放伴随低频人声，方位暂难区分楼上或斜楼上，待进一步排查。",
        ),
        (
            datetime(2026, 11, 25, 1, 44, 22, tzinfo=tz),
            15.0,
            72.0,
            0.34,
            980.0,
            0.22,
            "dual_band_impact_and_vocal",
            -10.1,
            -47.8,
            31.5,
            -13.2,
            -16.0,
            "suspected_external_mixed",
            profile,
            True,
            "jumping_stomping",
            "upstairs",
            "凌晨一点半天花板明显剧烈蹦跳惊醒家人，已拨打 110 报备出警。",
        ),
        (
            datetime(2026, 11, 25, 1, 44, 23, tzinfo=tz),
            15.0,
            72.0,
            0.48,
            980.0,
            0.31,
            "dual_band_impact_and_vocal",
            -7.2,
            -45.1,
            34.8,
            -8.4,
            -11.9,
            "suspected_external_mixed",
            ceiling_profile,
            False,
            None,
            "uncertain",
            "",
        ),
    ]

    first_event_id = ""
    late_event_id = ""
    for idx, (
        ts,
        dur,
        lf,
        la,
        hf,
        ha,
        band,
        peak,
        base,
        delta,
        lpeak,
        hpeak,
        tag,
        prof,
        verified,
        label,
        direction,
        notes,
    ) in enumerate(specs):
        pcm = _synth_clip(sr, dur, lf, la, hf, ha, seed=100 + idx)
        ev = CompletedEvent(
            start_time=ts - timedelta(seconds=3),
            end_time=ts + timedelta(seconds=dur - 3),
            trigger_time=ts,
            sample_rate=sr,
            raw_pcm=pcm,
            trigger_band=band,
            peak_dbfs=peak,
            baseline_dbfs=base,
            delta_db=delta,
            low_peak_dbfs=lpeak,
            high_peak_dbfs=hpeak,
            inferred_tag=tag,
        )
        rec = store.save_event(ev, ntp_synced=True, profile_override=prof)
        if idx == 0:
            first_event_id = rec["event_id"]
        if idx == 4:
            late_event_id = rec["event_id"]
        if verified:
            store.update_human_review(
                rec["event_id"], verified=True, label=label, direction=direction, notes=notes
            )

    store.add_corroboration_log(
        category="property_notice",
        summary="微信向物业管家书面反映 23:18 楼上持续撞击，管家回复已电话联系租客并登门劝阻。",
        logged_at=datetime(2026, 11, 24, 23, 25, 0, tzinfo=tz),
        reference_code="WY-20261124-09",
        related_event_id=first_event_id,
    )
    store.add_corroboration_log(
        category="police_call",
        summary="凌晨 01:48 拨打 110，民警上门核实夜间喧哗并作口头警告，已登记出警编号。",
        logged_at=datetime(2026, 11, 25, 1, 48, 0, tzinfo=tz),
        reference_code="110-20261125-0148",
        related_event_id=late_event_id,
    )
    store.add_corroboration_log(
        category="neighbor_witness",
        summary="同楼层相邻住户在互助群证实凌晨 01:44 同步听到明显楼板震动与喊叫。",
        logged_at=datetime(2026, 11, 25, 8, 15, 0, tzinfo=tz),
        reference_code="WITNESS-02",
        related_event_id=late_event_id,
    )

    store.generate_daily_report("2026-11-24")
    store.generate_daily_report("2026-11-25")
    return cfg, store
