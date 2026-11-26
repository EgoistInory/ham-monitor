from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from ham.web.app import create_app


def _make_client(tmp_path: Path) -> TestClient:
    app = create_app(demo_mode=True, demo_dir=tmp_path / "demo_sandbox")
    return TestClient(app)


def test_web_index_and_overview(tmp_path: Path) -> None:
    client = _make_client(tmp_path)

    home = client.get("/")
    assert home.status_code == 200
    assert "HAM Monitor" in home.text

    ov = client.get("/api/overview")
    assert ov.status_code == 200
    data = ov.json()
    assert data["demo_mode"] is True
    assert data["total_events"] == 6
    assert data["corroboration_count"] == 3
    assert data["chain_valid"] is True
    assert len(data["hourly_histogram"]) == 24
    assert len(data["category_counts"]) >= 4
    assert len(data["direction_counts"]) >= 2
    assert "2026-11-25" in data["available_dates"]

    tax = client.get("/api/taxonomy")
    assert tax.status_code == 200
    tax_data = tax.json()
    assert len(tax_data["category_groups"]) == 5
    assert sum(len(g["items"]) for g in tax_data["category_groups"]) == 19
    assert len(tax_data["directions"]) == 8


def test_web_events_waveform_audio_and_review(tmp_path: Path) -> None:
    client = _make_client(tmp_path)

    events = client.get("/api/events").json()
    assert len(events) == 6
    event_id = events[0]["event_id"]

    # Waveform endpoint
    wf = client.get(f"/api/events/{event_id}/waveform?buckets=64")
    assert wf.status_code == 200
    wf_data = wf.json()
    assert wf_data["buckets"] == 64
    assert len(wf_data["low_energy"]) == 64
    assert len(wf_data["high_energy"]) == 64

    # Default audio endpoint streams desensitized WAV with Accept-Ranges
    audio_desens = client.get(f"/api/events/{event_id}/audio")
    assert audio_desens.status_code == 200
    assert audio_desens.headers["content-type"] == "audio/wav"
    assert audio_desens.headers["x-ham-audio-mode"] == "desensitized"
    assert audio_desens.headers["accept-ranges"] == "bytes"
    assert audio_desens.content[:4] == b"RIFF"

    # Byte-range request returns 206 Partial Content for reliable HTML5 audio seeking
    audio_range = client.get(
        f"/api/events/{event_id}/audio", headers={"Range": "bytes=0-1023"}
    )
    assert audio_range.status_code == 206
    assert len(audio_range.content) == 1024
    assert audio_range.headers["content-range"].startswith("bytes 0-1023/")

    # Raw audio mode explicitly requested
    audio_raw = client.get(f"/api/events/{event_id}/audio?mode=raw")
    assert audio_raw.status_code == 200
    assert audio_raw.headers["x-ham-audio-mode"] == "raw"

    # Patch human review (fine-grained label + direction) without altering raw_evidence
    before_sha = events[0]["raw_evidence"]["sha256"]
    rev = client.patch(
        f"/api/events/{event_id}/review",
        json={
            "verified": True,
            "label": "drilling_renovation",
            "direction": "downstairs",
            "notes": "已通过控制台复核",
        },
    )
    assert rev.status_code == 200
    rev_data = rev.json()
    assert rev_data["raw_evidence"]["sha256"] == before_sha
    assert rev_data["human_review"]["verified"] is True
    assert rev_data["human_review"]["label"] == "drilling_renovation"
    assert rev_data["human_review"]["direction"] == "downstairs"

    # Multi-dimensional filter by label and direction
    filtered = client.get("/api/events?label=drilling_renovation&direction=downstairs").json()
    assert len(filtered) == 1
    assert filtered[0]["event_id"] == event_id


def test_web_chain_corroboration_correlate_and_export(tmp_path: Path) -> None:
    client = _make_client(tmp_path)

    # Full hash chain audit
    verify = client.post("/api/chain/verify")
    assert verify.status_code == 200
    assert verify.json()["valid"] is True
    assert verify.json()["checked_events"] == 6

    # Create external corroboration log
    created = client.post(
        "/api/corroborations",
        json={
            "category": "community_mediation",
            "reference_code": "SQ-20261125-01",
            "summary": "社区网格员已电话沟通房东并记录在案",
        },
    )
    assert created.status_code == 201
    assert created.json()["category"] == "community_mediation"

    logs = client.get("/api/corroborations").json()
    assert len(logs) == 4

    # Dual-node correlation
    corr = client.get("/api/correlate")
    assert corr.status_code == 200
    pairs = corr.json()
    assert len(pairs) == 2
    assert all(p["assessment"] == "likely_ceiling_source" for p in pairs)

    # Export desensitized ZIP & download
    exp = client.post(
        "/api/export/share",
        json={"date": "2026-11-25", "include_lowpass_audio": True, "cutoff_hz": 250.0},
    )
    assert exp.status_code == 200
    exp_data = exp.json()
    assert exp_data["filename"].endswith(".zip")

    dl = client.get(exp_data["download_url"])
    assert dl.status_code == 200
    with zipfile.ZipFile(BytesIO(dl.content), "r") as zf:
        names = set(zf.namelist())
        assert "MANIFEST.json" in names
        assert "SUMMARY_REPORT.md" in names
        assert "TIMESTAMP_MANIFEST.txt" in names

    # Path traversal must be rejected
    bad = client.get("/api/exports/..%5Csecret.zip")
    assert bad.status_code in (400, 404)

    # Read-only config endpoint
    cfg = client.get("/api/config")
    assert cfg.status_code == 200
    assert cfg.json()["demo_mode"] is True
