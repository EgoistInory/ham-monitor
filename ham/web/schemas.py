from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

CorroborationCategory = Literal[
    "property_notice",
    "community_mediation",
    "police_call",
    "neighbor_witness",
    "landlord_notice",
    "cma_test",
    "other",
]


class RawEvidenceSchema(BaseModel):
    file: str
    filename: str
    sha256: str
    prev_chain_hash: str
    chain_hash: str
    sample_rate: int
    channels: int
    ntp_synced: bool
    device_calibrated: bool
    node_id: str
    location_tag: str
    start_time: str
    end_time: str
    trigger_time: str


class AlgoMetricsSchema(BaseModel):
    trigger_band: str
    peak_dbfs: float
    baseline_dbfs: float
    delta_db: float
    low_peak_dbfs: float
    high_peak_dbfs: float
    duration_sec: float
    inferred_tag: str


class HumanReviewSchema(BaseModel):
    verified: bool
    label: Optional[str] = None
    direction: str = ""
    notes: str = ""


class EventRecordSchema(BaseModel):
    event_id: str
    raw_evidence: RawEvidenceSchema
    algo_metrics: AlgoMetricsSchema
    human_review: HumanReviewSchema


class ReviewUpdateRequest(BaseModel):
    verified: bool = True
    label: Optional[str] = Field(default=None, max_length=64)
    direction: Optional[str] = Field(default=None, max_length=64)
    notes: str = Field(default="", max_length=1000)


class CorroborationCreateRequest(BaseModel):
    category: CorroborationCategory
    summary: str = Field(min_length=1, max_length=1000)
    reference_code: str = Field(default="", max_length=128)
    related_event_id: str = Field(default="", max_length=128)
    logged_at: Optional[str] = None


class CorroborationSchema(BaseModel):
    log_id: int
    log_date: str
    logged_at: str
    category: str
    reference_code: str
    summary: str
    attachment_sha256: str
    related_event_id: str


class HourlyBucketSchema(BaseModel):
    hour: int
    label: str
    count: int
    total_duration_sec: float
    max_delta_db: float


class CategoryStatSchema(BaseModel):
    code: str
    label: str
    group: str
    group_label: str
    count: int
    total_duration_sec: float


class DirectionStatSchema(BaseModel):
    code: str
    label: str
    count: int


class OverviewResponse(BaseModel):
    selected_date: Optional[str]
    available_dates: list[str]
    node_id: str
    location_tag: str
    device_calibrated: bool
    schedule_start: str
    schedule_end: str
    demo_mode: bool
    total_events: int
    verified_events: int
    total_duration_sec: float
    max_delta_db: float
    band_counts: dict[str, int]
    category_counts: list[CategoryStatSchema]
    direction_counts: list[DirectionStatSchema]
    corroboration_count: int
    chain_valid: bool
    chain_errors: list[str]
    hourly_histogram: list[HourlyBucketSchema]


class ChainVerifyResponse(BaseModel):
    valid: bool
    checked_events: int
    errors: list[str]
    checked_at: str


class WaveformResponse(BaseModel):
    event_id: str
    sample_rate: int
    duration_sec: float
    buckets: int
    raw_peaks: list[float]
    low_energy: list[float]
    high_energy: list[float]


class ShareExportRequest(BaseModel):
    date: Optional[str] = None
    include_lowpass_audio: bool = False
    cutoff_hz: float = Field(default=250.0, ge=80.0, le=400.0)
    include_notes: bool = False


class ShareExportResponse(BaseModel):
    filename: str
    download_url: str
    size_bytes: int
    scope: str
    include_lowpass_audio: bool


class CorrelatedPairSchema(BaseModel):
    ceiling_event_id: str
    center_event_id: str
    time_diff_sec: float
    ceiling_low_peak_dbfs: float
    center_low_peak_dbfs: float
    low_band_diff_db: float
    ceiling_high_peak_dbfs: float
    center_high_peak_dbfs: float
    high_band_diff_db: float
    assessment: str


class SystemConfigResponse(BaseModel):
    node_id: str
    location_tag: str
    calibrated: bool
    sample_rate: int
    schedule_enabled: bool
    schedule_start: str
    schedule_end: str
    low_band_hz: list[float]
    high_band_hz: list[float]
    low_delta_db: float
    low_min_dbfs: float
    high_delta_db: float
    high_min_dbfs: float
    pre_roll_sec: float
    post_roll_sec: float
    raw_retention_days: int
    demo_mode: bool
