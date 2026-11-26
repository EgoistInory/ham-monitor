from __future__ import annotations

import dataclasses
import io
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

import numpy as np
import soundfile as sf
from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from scipy.signal import butter, sosfilt, sosfiltfilt

from ham.audio_io import load_mono_audio
from ham.capture import SyntheticAudioSource
from ham.config import AppConfig, load_config
from ham.correlate import correlate_dual_nodes
from ham.desensitize import export_share_bundle
from ham.dsp import rms_dbfs
from ham.evidence import EvidenceStore
from ham.monitor import NoiseMonitor
from ham.taxonomy import (
    get_category_group,
    get_category_label,
    get_direction_label,
    taxonomy_payload,
)
from ham.web.demo import prepare_demo_sandbox
from ham.web.schemas import (
    CategoryStatSchema,
    ChainVerifyResponse,
    CorrelatedPairSchema,
    CorroborationCreateRequest,
    CorroborationSchema,
    DirectionStatSchema,
    EventRecordSchema,
    HourlyBucketSchema,
    IngestEventResponse,
    IngestUploadResponse,
    NodeSummarySchema,
    OverviewResponse,
    ReviewUpdateRequest,
    ShareExportRequest,
    ShareExportResponse,
    SystemConfigResponse,
    WaveformResponse,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class WebState:
    config: AppConfig
    store: EvidenceStore
    exports_dir: Path
    demo_mode: bool = False
    ingest_token: str = ""


def _to_event_schema(rec: dict[str, Any]) -> EventRecordSchema:
    raw = dict(rec["raw_evidence"])
    raw["filename"] = Path(raw["file"]).name
    return EventRecordSchema(
        event_id=rec["event_id"],
        raw_evidence=raw,
        algo_metrics=rec["algo_metrics"],
        human_review=rec["human_review"],
    )


def get_state(request: Request) -> WebState:
    return request.app.state.ham_web


StateDep = Annotated[WebState, Depends(get_state)]


def verify_ingest_token(
    state: StateDep,
    x_ham_token: Annotated[Optional[str], Header(alias="X-HAM-Token")] = None,
) -> None:
    """Enforce X-HAM-Token when LAN ingest token is configured."""
    if not state.ingest_token:
        return
    if not x_ham_token or not secrets.compare_digest(x_ham_token, state.ingest_token):
        raise HTTPException(status_code=401, detail="Invalid or missing X-HAM-Token")


IngestAuthDep = Annotated[None, Depends(verify_ingest_token)]

api_router = APIRouter(prefix="/api", tags=["ham-monitor"])


@api_router.get("/taxonomy")
def get_taxonomy() -> dict[str, Any]:
    """Return single-source-of-truth fine-grained noise categories and source directions."""
    return taxonomy_payload()


@api_router.get("/overview")
def get_overview(
    state: StateDep,
    date: Annotated[Optional[str], Query(description="Filter date YYYY-MM-DD")] = None,
) -> OverviewResponse:
    all_events = state.store.list_all_events()
    available_dates = sorted(
        {
            datetime.fromisoformat(e["raw_evidence"]["trigger_time"]).strftime("%Y-%m-%d")
            for e in all_events
        },
        reverse=True,
    )
    events = state.store.list_events_by_date(date) if date else all_events
    corroborations = state.store.list_corroboration_logs(date)

    band_counts: dict[str, int] = {
        "low_impact_20_180hz": 0,
        "high_vocal_300_4000hz": 0,
        "dual_band_impact_and_vocal": 0,
    }
    hourly_map: dict[int, dict[str, float | int]] = {
        h: {"count": 0, "dur": 0.0, "max_delta": 0.0} for h in range(24)
    }

    verified_count = 0
    total_dur = 0.0
    max_delta = 0.0
    cat_agg: dict[str, dict[str, Any]] = {}
    dir_agg: dict[str, int] = {}

    for ev in events:
        m = ev["algo_metrics"]
        rev = ev["human_review"]
        band = m["trigger_band"]
        band_counts[band] = band_counts.get(band, 0) + 1
        if rev["verified"]:
            verified_count += 1
        dur = float(m["duration_sec"])
        total_dur += dur
        max_delta = max(max_delta, float(m["delta_db"]))

        cat_code = rev.get("label") or "unclassified"
        if cat_code not in cat_agg:
            grp, grp_label = get_category_group(rev.get("label"))
            cat_agg[cat_code] = {
                "code": cat_code,
                "label": get_category_label(rev.get("label")),
                "group": grp,
                "group_label": grp_label,
                "count": 0,
                "dur": 0.0,
            }
        cat_agg[cat_code]["count"] += 1
        cat_agg[cat_code]["dur"] += dur

        dir_code = rev.get("direction") or "uncertain"
        dir_agg[dir_code] = dir_agg.get(dir_code, 0) + 1

        hour = datetime.fromisoformat(ev["raw_evidence"]["trigger_time"]).hour
        bucket = hourly_map[hour]
        bucket["count"] = int(bucket["count"]) + 1
        bucket["dur"] = float(bucket["dur"]) + dur
        bucket["max_delta"] = max(float(bucket["max_delta"]), float(m["delta_db"]))

    category_stats = [
        CategoryStatSchema(
            code=item["code"],
            label=item["label"],
            group=item["group"],
            group_label=item["group_label"],
            count=item["count"],
            total_duration_sec=round(item["dur"], 1),
        )
        for item in sorted(cat_agg.values(), key=lambda x: (-x["count"], -x["dur"]))
    ]
    direction_stats = [
        DirectionStatSchema(
            code=code,
            label=get_direction_label(code, short=False),
            count=cnt,
        )
        for code, cnt in sorted(dir_agg.items(), key=lambda x: -x[1])
    ]

    # Order hours starting from 20:00 through 07:00 first for nocturnal readability, or 24h cycle from 20:00
    hour_order = list(range(20, 24)) + list(range(0, 20))
    histogram = [
        HourlyBucketSchema(
            hour=h,
            label=f"{h:02d}:00",
            count=int(hourly_map[h]["count"]),
            total_duration_sec=round(float(hourly_map[h]["dur"]), 1),
            max_delta_db=round(float(hourly_map[h]["max_delta"]), 1),
        )
        for h in hour_order
    ]

    chain_valid, chain_errors = state.store.verify_chain(verify_files=False)

    return OverviewResponse(
        selected_date=date,
        available_dates=available_dates,
        node_id=state.config.node.node_id,
        location_tag=state.config.node.location_tag,
        device_calibrated=state.config.node.calibrated,
        schedule_start=state.config.schedule.start_time,
        schedule_end=state.config.schedule.end_time,
        demo_mode=state.demo_mode,
        total_events=len(events),
        verified_events=verified_count,
        total_duration_sec=round(total_dur, 1),
        max_delta_db=round(max_delta, 1),
        band_counts=band_counts,
        category_counts=category_stats,
        direction_counts=direction_stats,
        corroboration_count=len(corroborations),
        chain_valid=chain_valid,
        chain_errors=chain_errors,
        hourly_histogram=histogram,
    )


@api_router.get("/events")
def list_events(
    state: StateDep,
    date: Annotated[Optional[str], Query()] = None,
    band: Annotated[Optional[str], Query()] = None,
    verified: Annotated[Optional[bool], Query()] = None,
    label: Annotated[Optional[str], Query()] = None,
    direction: Annotated[Optional[str], Query()] = None,
) -> list[EventRecordSchema]:
    events = state.store.list_events_by_date(date) if date else state.store.list_all_events()
    filtered: list[EventRecordSchema] = []
    for ev in reversed(events):
        if band and ev["algo_metrics"]["trigger_band"] != band:
            continue
        if verified is not None and bool(ev["human_review"]["verified"]) != verified:
            continue
        if label:
            ev_label = ev["human_review"].get("label") or "unclassified"
            if ev_label != label:
                continue
        if direction:
            ev_dir = ev["human_review"].get("direction") or "uncertain"
            if ev_dir != direction:
                continue
        filtered.append(_to_event_schema(ev))
    return filtered


@api_router.get("/events/{event_id}")
def get_event(event_id: str, state: StateDep) -> EventRecordSchema:
    rec = state.store.get_event(event_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")
    return _to_event_schema(rec)


@api_router.patch("/events/{event_id}/review")
def review_event(
    event_id: str,
    payload: ReviewUpdateRequest,
    state: StateDep,
) -> EventRecordSchema:
    try:
        updated = state.store.update_human_review(
            event_id=event_id,
            verified=payload.verified,
            label=payload.label,
            direction=payload.direction,
            notes=payload.notes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_event_schema(updated)


@api_router.get("/events/{event_id}/audio")
def stream_event_audio(
    event_id: str,
    request: Request,
    state: StateDep,
    mode: Annotated[Literal["desensitized", "raw"], Query()] = "desensitized",
    cutoff_hz: Annotated[float, Query(ge=80.0, le=400.0)] = 250.0,
) -> Response:
    """Stream WAV playback preview with HTTP Range support (defaults to <=250 Hz desensitized)."""
    rec = state.store.get_event(event_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")
    flac_path = Path(rec["raw_evidence"]["file"])
    if not flac_path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing on disk")

    samples, sr = sf.read(str(flac_path), dtype="float64")
    if samples.ndim > 1:
        samples = samples[:, 0]

    if mode == "desensitized":
        sos = butter(8, cutoff_hz, btype="lowpass", fs=sr, output="sos")
        out_pcm = np.clip(sosfiltfilt(sos, samples), -1.0, 1.0).astype(np.float32)
    else:
        out_pcm = samples.astype(np.float32)

    buf = io.BytesIO()
    sf.write(buf, out_pcm, sr, format="WAV", subtype="PCM_16")
    wav_bytes = buf.getvalue()
    total_len = len(wav_bytes)
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-HAM-Audio-Mode": mode,
    }

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        spec = range_header[6:].split(",")[0].strip()
        start_s, _, end_s = spec.partition("-")
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else total_len - 1
        end = min(end, total_len - 1)
        if 0 <= start <= end < total_len:
            chunk = wav_bytes[start : end + 1]
            return Response(
                content=chunk,
                status_code=206,
                media_type="audio/wav",
                headers={
                    **base_headers,
                    "Content-Range": f"bytes {start}-{end}/{total_len}",
                    "Content-Length": str(len(chunk)),
                },
            )

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={**base_headers, "Content-Length": str(total_len)},
    )


@api_router.get("/events/{event_id}/waveform")
def get_event_waveform(
    event_id: str,
    state: StateDep,
    buckets: Annotated[int, Query(ge=32, le=400)] = 160,
) -> WaveformResponse:
    rec = state.store.get_event(event_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")
    flac_path = Path(rec["raw_evidence"]["file"])
    if not flac_path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing on disk")

    samples, sr = sf.read(str(flac_path), dtype="float64")
    if samples.ndim > 1:
        samples = samples[:, 0]

    low_lo, low_hi = state.config.dsp.low_band_hz
    high_lo, high_hi = state.config.dsp.high_band_hz
    nyq = sr / 2.0
    low_hi = min(low_hi, nyq - 10.0)
    high_hi = min(high_hi, nyq - 10.0)

    low_sig = sosfilt(butter(4, (low_lo, low_hi), btype="bandpass", fs=sr, output="sos"), samples)
    high_sig = sosfilt(butter(4, (high_lo, high_hi), btype="bandpass", fs=sr, output="sos"), samples)

    n = samples.size
    actual_buckets = min(buckets, max(1, n))
    step = max(1, n // actual_buckets)

    raw_peaks: list[float] = []
    low_energy: list[float] = []
    high_energy: list[float] = []

    for i in range(actual_buckets):
        start = i * step
        end = n if i == actual_buckets - 1 else min(n, (i + 1) * step)
        seg_raw = samples[start:end]
        seg_low = low_sig[start:end]
        seg_high = high_sig[start:end]
        raw_peaks.append(round(float(np.max(np.abs(seg_raw))) if seg_raw.size else 0.0, 4))
        # Normalize dBFS [-70, 0] -> [0, 1] for crisp UI rendering
        low_db = rms_dbfs(seg_low)
        high_db = rms_dbfs(seg_high)
        low_energy.append(round(max(0.0, min(1.0, (low_db + 70.0) / 70.0)), 4))
        high_energy.append(round(max(0.0, min(1.0, (high_db + 70.0) / 70.0)), 4))

    return WaveformResponse(
        event_id=event_id,
        sample_rate=sr,
        duration_sec=round(n / float(sr), 2),
        buckets=actual_buckets,
        raw_peaks=raw_peaks,
        low_energy=low_energy,
        high_energy=high_energy,
    )


@api_router.post("/chain/verify")
def verify_hash_chain(state: StateDep) -> ChainVerifyResponse:
    events = state.store.list_all_events()
    valid, errors = state.store.verify_chain(verify_files=True)
    return ChainVerifyResponse(
        valid=valid,
        checked_events=len(events),
        errors=errors,
        checked_at=datetime.now().astimezone().isoformat(),
    )


@api_router.get("/corroborations")
def list_corroborations(
    state: StateDep,
    date: Annotated[Optional[str], Query()] = None,
) -> list[CorroborationSchema]:
    logs = state.store.list_corroboration_logs(date)
    return [CorroborationSchema(**item) for item in reversed(logs)]


@api_router.post("/corroborations", status_code=201)
def create_corroboration(
    payload: CorroborationCreateRequest,
    state: StateDep,
) -> CorroborationSchema:
    ts = datetime.fromisoformat(payload.logged_at) if payload.logged_at else None
    entry = state.store.add_corroboration_log(
        category=payload.category,
        summary=payload.summary,
        logged_at=ts,
        reference_code=payload.reference_code,
        related_event_id=payload.related_event_id,
    )
    return CorroborationSchema(**entry)


@api_router.get("/correlate")
def get_dual_node_correlation(
    state: StateDep,
    date: Annotated[Optional[str], Query()] = None,
    max_skew: Annotated[float, Query(ge=0.1, le=10.0)] = 2.0,
    margin_db: Annotated[float, Query(ge=0.5, le=20.0)] = 3.0,
) -> list[CorrelatedPairSchema]:
    events = state.store.list_events_by_date(date) if date else state.store.list_all_events()
    pairs = correlate_dual_nodes(
        events,
        max_time_skew_sec=max_skew,
        upstairs_margin_db=margin_db,
    )
    return [CorrelatedPairSchema(**p.__dict__) for p in pairs]


@api_router.post("/export/share")
def create_share_export(
    payload: ShareExportRequest,
    state: StateDep,
) -> ShareExportResponse:
    zip_path = export_share_bundle(
        store=state.store,
        output_dir=state.exports_dir,
        date_str=payload.date,
        include_lowpass_audio=payload.include_lowpass_audio,
        lowpass_cutoff_hz=payload.cutoff_hz,
        include_notes=payload.include_notes,
        create_zip=True,
    )
    return ShareExportResponse(
        filename=zip_path.name,
        download_url=f"/api/exports/{zip_path.name}",
        size_bytes=zip_path.stat().st_size,
        scope=payload.date or "all",
        include_lowpass_audio=payload.include_lowpass_audio,
    )


@api_router.get("/exports/{filename}")
def download_export(filename: str, state: StateDep) -> FileResponse:
    # Strict single-segment filename check prevents directory traversal
    if "/" in filename or "\\" in filename or ".." in filename or not filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Invalid export filename")
    target = (state.exports_dir / filename).resolve()
    if target.parent != state.exports_dir.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="Export archive not found")
    return FileResponse(path=str(target), filename=filename, media_type="application/zip")


@api_router.get("/nodes")
def list_nodes(state: StateDep) -> list[NodeSummarySchema]:
    """Summarize all capture nodes that have contributed evidence to this server."""
    events = state.store.list_all_events()
    nodes: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        r = ev["raw_evidence"]
        key = (r["node_id"], r["location_tag"])
        if key not in nodes:
            nodes[key] = {
                "node_id": r["node_id"],
                "location_tag": r["location_tag"],
                "event_count": 0,
                "last_seen": r["trigger_time"],
                "calibrated": bool(r["device_calibrated"]),
            }
        nodes[key]["event_count"] += 1
        if r["trigger_time"] > nodes[key]["last_seen"]:
            nodes[key]["last_seen"] = r["trigger_time"]
    return [
        NodeSummarySchema(**v)
        for v in sorted(nodes.values(), key=lambda x: x["last_seen"], reverse=True)
    ]


@api_router.post("/ingest/event")
def ingest_remote_event(
    state: StateDep,
    _auth: IngestAuthDep,
    file: Annotated[UploadFile, File(description="Lossless FLAC event slice from edge node")],
    metadata_json: Annotated[str, Form(description="JSON metadata including event_id and client_sha256")],
    response: Response,
) -> IngestEventResponse:
    """Receive a pre-sliced lossless FLAC event from a distributed edge node (idempotent + SHA-256 verified)."""
    try:
        metadata = json.loads(metadata_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid metadata_json: {exc}") from exc

    event_id = str(metadata.get("event_id") or "").strip()
    client_sha256 = str(metadata.get("client_sha256") or "").strip()
    if not event_id or not metadata.get("trigger_time"):
        raise HTTPException(status_code=400, detail="metadata_json requires event_id and trigger_time")

    flac_bytes = file.file.read()
    if not flac_bytes:
        raise HTTPException(status_code=400, detail="Empty audio payload")

    try:
        record, created = state.store.ingest_remote_flac(
            event_id=event_id,
            flac_bytes=flac_bytes,
            client_sha256=client_sha256,
            metadata=metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to ingest FLAC: {exc}") from exc

    response.status_code = 201 if created else 200
    return IngestEventResponse(
        status="created" if created else "duplicate",
        event_id=record["event_id"],
        sha256=record["raw_evidence"]["sha256"],
        chain_hash=record["raw_evidence"]["chain_hash"],
    )


@api_router.post("/ingest/upload")
def ingest_raw_audio_upload(
    state: StateDep,
    _auth: IngestAuthDep,
    file: Annotated[UploadFile, File(description="Offline audio recording (WAV/FLAC/OGG/MP3/M4A/CAF)")],
    start_time: Annotated[str, Form(description="Recording start ISO8601 timestamp")],
    node_id: Annotated[str, Form(description="Device node identifier")] = "phone_ceiling",
    location_tag: Annotated[str, Form(description="Placement location tag")] = "ceiling",
) -> IngestUploadResponse:
    """Accept an offline phone/recorder audio file, run dual-band detection, and save triggered events."""
    try:
        start_dt = datetime.fromisoformat(start_time)
        if start_dt.tzinfo is None:
            start_dt = start_dt.astimezone()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid start_time ISO8601: {exc}") from exc

    suffix = Path(file.filename or "upload.wav").suffix or ".wav"
    with tempfile.TemporaryDirectory(prefix="ham_upload_") as tmp_dir:
        tmp_path = Path(tmp_dir) / f"incoming{suffix}"
        tmp_path.write_bytes(file.file.read())
        try:
            samples, sr = load_mono_audio(tmp_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    clean_node = "".join(ch for ch in node_id if ch.isalnum() or ch in ("_", "-")) or "phone_node"
    clean_loc = "".join(ch for ch in location_tag if ch.isalnum() or ch in ("_", "-")) or "ceiling"

    new_node = dataclasses.replace(
        state.config.node,
        node_id=clean_node,
        location_tag=clean_loc,
        calibrated=False,
    )
    new_audio = dataclasses.replace(state.config.audio, sample_rate=sr, channels=1)
    # Use a fast warmup (<=2s) for short uploaded clips while respecting configured DSP bands
    fast_dsp = dataclasses.replace(
        state.config.dsp,
        baseline_warmup_sec=min(state.config.dsp.baseline_warmup_sec, 2.0),
    )
    run_cfg = dataclasses.replace(state.config, node=new_node, audio=new_audio, dsp=fast_dsp)

    source = SyntheticAudioSource(
        samples=samples,
        sample_rate=sr,
        block_samples=run_cfg.audio.block_samples,
        start_time=start_dt,
    )
    # Reuse the central EvidenceStore with node profile override via NoiseMonitor
    node_store = EvidenceStore(run_cfg.storage, new_node)
    monitor = NoiseMonitor(run_cfg, source, node_store, ignore_schedule=True)
    saved = monitor.run(install_signals=False)

    return IngestUploadResponse(
        status="ok",
        node_id=clean_node,
        location_tag=clean_loc,
        imported_count=len(saved),
        event_ids=[r["event_id"] for r in saved],
    )


@api_router.get("/config")
def get_system_config(state: StateDep) -> SystemConfigResponse:
    c = state.config
    return SystemConfigResponse(
        node_id=c.node.node_id,
        location_tag=c.node.location_tag,
        calibrated=c.node.calibrated,
        sample_rate=c.audio.sample_rate,
        schedule_enabled=c.schedule.enabled,
        schedule_start=c.schedule.start_time,
        schedule_end=c.schedule.end_time,
        low_band_hz=list(c.dsp.low_band_hz),
        high_band_hz=list(c.dsp.high_band_hz),
        low_delta_db=c.trigger.low_delta_db,
        low_min_dbfs=c.trigger.low_min_dbfs,
        high_delta_db=c.trigger.high_delta_db,
        high_min_dbfs=c.trigger.high_min_dbfs,
        pre_roll_sec=c.trigger.pre_roll_sec,
        post_roll_sec=c.trigger.post_roll_sec,
        raw_retention_days=c.storage.raw_retention_days,
        demo_mode=state.demo_mode,
        ingest_token_required=bool(state.ingest_token),
    )


def create_app(
    config_path: Optional[str | Path] = None,
    demo_mode: bool = False,
    demo_dir: Optional[Path] = None,
    ingest_token: Optional[str] = None,
) -> FastAPI:
    app = FastAPI(
        title="ham-monitor Console API",
        version="0.4.0",
        description="Local-first passive acoustic monitoring & privacy-safe evidence workbench",
    )

    if demo_mode:
        sandbox = demo_dir or (Path("data") / "demo_sandbox")
        cfg, store = prepare_demo_sandbox(sandbox)
        exports_dir = sandbox / "exports"
    else:
        resolved_cfg = Path(config_path or os.environ.get("HAM_CONFIG", "config/default.yaml"))
        cfg = load_config(resolved_cfg)
        store = EvidenceStore(cfg.storage, cfg.node)
        exports_dir = Path(cfg.storage.base_dir) / "exports"

    exports_dir.mkdir(parents=True, exist_ok=True)
    effective_token = (
        ingest_token if ingest_token is not None else os.environ.get("HAM_INGEST_TOKEN", "")
    )
    app.state.ham_web = WebState(
        config=cfg,
        store=store,
        exports_dir=exports_dir,
        demo_mode=demo_mode,
        ingest_token=effective_token,
    )

    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def serve_index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    return app


# Default ASGI entrypoint for `fastapi dev` / `uvicorn ham.web.app:app`
app = create_app(demo_mode=os.environ.get("HAM_DEMO", "0") == "1")
