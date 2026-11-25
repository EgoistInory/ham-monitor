from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List


@dataclass(frozen=True)
class CorrelatedPair:
    """Coarse time-window match between a ceiling node and a room_center node.

    Because independent devices (e.g. an idle Android phone + a MacBook) have
    drifting quartz clocks, Phase 2 uses coarse window matching + relative
    energy ratio rather than sample-level TDOA.
    """

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


def correlate_dual_nodes(
    events: List[Dict[str, Any]],
    ceiling_tag: str = "ceiling",
    center_tag: str = "room_center",
    max_time_skew_sec: float = 2.0,
    upstairs_margin_db: float = 3.0,
) -> List[CorrelatedPair]:
    """Compare events recorded by a ceiling-mounted node vs a room-center node.

    Heuristic interpretation:
    - ceiling energy >= center energy + margin -> `likely_ceiling_source` (supports upstairs origin)
    - center energy >= ceiling energy + margin -> `likely_room_or_floor_source` (possible self-noise)
    - otherwise -> `ambiguous_diffuse` (could be street traffic or reverberant sound)
    """
    ceiling_events = [e for e in events if e["raw_evidence"]["location_tag"] == ceiling_tag]
    center_events = [e for e in events if e["raw_evidence"]["location_tag"] == center_tag]

    used_center: set[str] = set()
    pairs: List[CorrelatedPair] = []

    for ce in ceiling_events:
        ce_ts = datetime.fromisoformat(ce["raw_evidence"]["trigger_time"])
        best_match: Dict[str, Any] | None = None
        best_dt = max_time_skew_sec + 1.0

        for re_ev in center_events:
            if re_ev["event_id"] in used_center:
                continue
            re_ts = datetime.fromisoformat(re_ev["raw_evidence"]["trigger_time"])
            dt = abs((ce_ts - re_ts).total_seconds())
            if dt <= max_time_skew_sec and dt < best_dt:
                best_dt = dt
                best_match = re_ev

        if best_match is None:
            continue

        used_center.add(best_match["event_id"])
        ce_m = ce["algo_metrics"]
        re_m = best_match["algo_metrics"]

        low_diff = round(ce_m["low_peak_dbfs"] - re_m["low_peak_dbfs"], 2)
        high_diff = round(ce_m["high_peak_dbfs"] - re_m["high_peak_dbfs"], 2)
        dominant_diff = low_diff if abs(low_diff) >= abs(high_diff) else high_diff

        if dominant_diff >= upstairs_margin_db:
            assessment = "likely_ceiling_source"
        elif dominant_diff <= -upstairs_margin_db:
            assessment = "likely_room_or_floor_source"
        else:
            assessment = "ambiguous_diffuse"

        pairs.append(
            CorrelatedPair(
                ceiling_event_id=ce["event_id"],
                center_event_id=best_match["event_id"],
                time_diff_sec=round(best_dt, 3),
                ceiling_low_peak_dbfs=ce_m["low_peak_dbfs"],
                center_low_peak_dbfs=re_m["low_peak_dbfs"],
                low_band_diff_db=low_diff,
                ceiling_high_peak_dbfs=ce_m["high_peak_dbfs"],
                center_high_peak_dbfs=re_m["high_peak_dbfs"],
                high_band_diff_db=high_diff,
                assessment=assessment,
            )
        )

    return pairs
