from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from ham.config import DeviceProfile, StorageConfig, SyncConfig
from ham.evidence import EvidenceStore
from ham.sync import RemoteSyncClient
from ham.trigger import CompletedEvent
from ham.web.app import create_app


def _make_sample_event(trigger_time: datetime, sr: int = 16000) -> CompletedEvent:
    t = np.arange(sr * 2, dtype=np.float64) / float(sr)
    pcm = (0.22 * np.sin(2.0 * np.pi * 85.0 * t)).astype(np.float32)
    return CompletedEvent(
        start_time=trigger_time - timedelta(seconds=1),
        end_time=trigger_time + timedelta(seconds=1),
        trigger_time=trigger_time,
        sample_rate=sr,
        raw_pcm=pcm,
        trigger_band="low_impact_20_180hz",
        peak_dbfs=-13.2,
        baseline_dbfs=-48.0,
        delta_db=28.5,
        low_peak_dbfs=-14.0,
        high_peak_dbfs=-46.0,
        inferred_tag="suspected_external_impact",
    )


def test_ingest_event_auth_sha256_and_idempotency(tmp_path: Path) -> None:
    app = create_app(
        demo_mode=True,
        demo_dir=tmp_path / "central",
        ingest_token="secret-lan-token",
    )
    client = TestClient(app)

    # Create an edge event on an independent edge node store
    edge_storage = StorageConfig(
        base_dir=str(tmp_path / "edge"),
        evidence_dir=str(tmp_path / "edge" / "evidence"),
        raw_dir=str(tmp_path / "edge" / "raw"),
        db_path=str(tmp_path / "edge" / "edge.sqlite3"),
        reports_dir=str(tmp_path / "edge" / "reports"),
    )
    edge_store = EvidenceStore(edge_storage, DeviceProfile(node_id="edge_pi", location_tag="ceiling"))
    rec = edge_store.save_event(_make_sample_event(datetime(2026, 11, 26, 1, 10, 0, tzinfo=timezone.utc)))

    flac_bytes = Path(rec["raw_evidence"]["file"]).read_bytes()
    meta = {
        "event_id": rec["event_id"],
        "client_sha256": rec["raw_evidence"]["sha256"],
        "sample_rate": rec["raw_evidence"]["sample_rate"],
        "node_id": "edge_pi",
        "location_tag": "ceiling",
        "start_time": rec["raw_evidence"]["start_time"],
        "end_time": rec["raw_evidence"]["end_time"],
        "trigger_time": rec["raw_evidence"]["trigger_time"],
        "trigger_band": rec["algo_metrics"]["trigger_band"],
        "peak_dbfs": rec["algo_metrics"]["peak_dbfs"],
        "baseline_dbfs": rec["algo_metrics"]["baseline_dbfs"],
        "delta_db": rec["algo_metrics"]["delta_db"],
        "low_peak_dbfs": rec["algo_metrics"]["low_peak_dbfs"],
        "high_peak_dbfs": rec["algo_metrics"]["high_peak_dbfs"],
        "inferred_tag": rec["algo_metrics"]["inferred_tag"],
    }

    # 1. Missing token -> 401
    r_unauth = client.post(
        "/api/ingest/event",
        data={"metadata_json": json.dumps(meta)},
        files={"file": ("edge.flac", flac_bytes, "audio/flac")},
    )
    assert r_unauth.status_code == 401

    # 2. Corrupted SHA-256 -> 400
    bad_meta = {**meta, "client_sha256": "0" * 64}
    r_bad = client.post(
        "/api/ingest/event",
        headers={"X-HAM-Token": "secret-lan-token"},
        data={"metadata_json": json.dumps(bad_meta)},
        files={"file": ("edge.flac", flac_bytes, "audio/flac")},
    )
    assert r_bad.status_code == 400

    # 3. Valid upload -> 201 created, bit-identical SHA-256 preserved
    r_ok = client.post(
        "/api/ingest/event",
        headers={"X-HAM-Token": "secret-lan-token"},
        data={"metadata_json": json.dumps(meta)},
        files={"file": ("edge.flac", flac_bytes, "audio/flac")},
    )
    assert r_ok.status_code == 201
    body = r_ok.json()
    assert body["status"] == "created"
    assert body["sha256"] == rec["raw_evidence"]["sha256"]

    # 4. Duplicate retry -> 200 idempotent
    r_dup = client.post(
        "/api/ingest/event",
        headers={"X-HAM-Token": "secret-lan-token"},
        data={"metadata_json": json.dumps(meta)},
        files={"file": ("edge.flac", flac_bytes, "audio/flac")},
    )
    assert r_dup.status_code == 200
    assert r_dup.json()["status"] == "duplicate"

    # 5. Central hash chain remains 100% valid & /api/nodes lists edge_pi
    verify = client.post("/api/chain/verify").json()
    assert verify["valid"] is True
    nodes = client.get("/api/nodes").json()
    assert any(n["node_id"] == "edge_pi" and n["location_tag"] == "ceiling" for n in nodes)


def test_ingest_raw_audio_upload_auto_slices_and_indexes(tmp_path: Path) -> None:
    app = create_app(demo_mode=True, demo_dir=tmp_path / "central_upload")
    client = TestClient(app)

    # Synthesize a 16-second phone recording with an 80Hz floor thud at t=8s..10s
    sr = 16000
    rng = np.random.default_rng(77)
    sig = (rng.standard_normal(sr * 16) * 2e-4).astype(np.float32)
    t = np.arange(sr * 2, dtype=np.float64) / float(sr)
    sig[8 * sr : 10 * sr] += (0.28 * np.sin(2.0 * np.pi * 80.0 * t)).astype(np.float32)

    buf = BytesIO()
    sf.write(buf, sig, sr, format="WAV", subtype="PCM_16")

    resp = client.post(
        "/api/ingest/upload",
        data={
            "start_time": "2026-11-26T23:15:00+08:00",
            "node_id": "iphone_ceiling",
            "location_tag": "ceiling",
        },
        files={"file": ("night_phone.wav", buf.getvalue(), "audio/wav")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "iphone_ceiling"
    assert data["imported_count"] == 1
    assert len(data["event_ids"]) == 1

    # Chain remains intact after raw audio upload
    assert client.post("/api/chain/verify").json()["valid"] is True


def test_remote_sync_client_offline_queue_and_recovery(tmp_path: Path) -> None:
    app = create_app(
        demo_mode=True,
        demo_dir=tmp_path / "central_sync",
        ingest_token="sync-tok",
    )

    edge_storage = StorageConfig(
        base_dir=str(tmp_path / "edge_sync"),
        evidence_dir=str(tmp_path / "edge_sync" / "evidence"),
        raw_dir=str(tmp_path / "edge_sync" / "raw"),
        db_path=str(tmp_path / "edge_sync" / "edge.sqlite3"),
        reports_dir=str(tmp_path / "edge_sync" / "reports"),
    )
    edge_store = EvidenceStore(edge_storage, DeviceProfile(node_id="edge_laptop", location_tag="ceiling"))
    rec = edge_store.save_event(_make_sample_event(datetime(2026, 11, 26, 2, 5, 0, tzinfo=timezone.utc)))

    queue_file = tmp_path / "edge_sync" / "sync_queue.json"

    # Phase A: Central server unreachable -> event is queued locally, never lost
    offline_sync = RemoteSyncClient(
        config=SyncConfig(enabled=True, server_url="http://testserver", token="sync-tok"),
        queue_path=queue_file,
        http_client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("offline")))
        ),
    )
    assert offline_sync.push_event(rec) is False
    assert offline_sync.pending_count() == 1

    # Phase B: Central server recovers -> flush_queue delivers queued FLAC and clears queue
    online_sync = RemoteSyncClient(
        config=SyncConfig(enabled=True, server_url="http://testserver", token="sync-tok"),
        queue_path=queue_file,
        http_client_factory=lambda: TestClient(app),
    )
    ok_count, rem_count = online_sync.flush_queue(edge_store)
    assert ok_count == 1
    assert rem_count == 0
    assert online_sync.pending_count() == 0

    # Verify central server actually received it
    central_client = TestClient(app)
    ev = central_client.get(f"/api/events/{rec['event_id']}")
    assert ev.status_code == 200
    assert ev.json()["raw_evidence"]["sha256"] == rec["raw_evidence"]["sha256"]
