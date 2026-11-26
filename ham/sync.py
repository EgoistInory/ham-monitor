from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from ham.config import SyncConfig
from ham.evidence import EvidenceStore

logger = logging.getLogger(__name__)


class RemoteSyncClient:
    """Edge-First Store-and-Forward client for distributed capture nodes.

    Guarantees:
    1. Every event is persisted locally FIRST (never lost if Wi-Fi or central server is down).
    2. Uploads lossless FLAC + client_sha256 + metadata to `POST /api/ingest/event`.
    3. Failed uploads are appended to a local JSON retry queue and automatically flushed
       when connectivity recovers (idempotent on server side).
    """

    def __init__(
        self,
        config: SyncConfig,
        queue_path: Path,
        http_client_factory: Optional[Callable[[], httpx.Client]] = None,
    ) -> None:
        self._cfg = config
        self._queue_path = Path(queue_path)
        self._queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._client_factory = http_client_factory or (
            lambda: httpx.Client(timeout=self._cfg.timeout_sec)
        )

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled and self._cfg.server_url.strip())

    def _load_queue(self) -> List[str]:
        if not self._queue_path.exists():
            return []
        try:
            data = json.loads(self._queue_path.read_text(encoding="utf-8"))
            return [str(x) for x in data] if isinstance(data, list) else []
        except Exception:
            return []

    def _save_queue(self, event_ids: List[str]) -> None:
        deduped = list(dict.fromkeys(event_ids))
        self._queue_path.write_text(
            json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def pending_count(self) -> int:
        return len(self._load_queue())

    def _enqueue(self, event_id: str) -> None:
        queue = self._load_queue()
        if event_id not in queue:
            queue.append(event_id)
            self._save_queue(queue)

    def _upload_record(self, record: Dict[str, Any]) -> bool:
        raw = record["raw_evidence"]
        algo = record["algo_metrics"]
        flac_path = Path(raw["file"])
        if not flac_path.exists():
            logger.error("Cannot sync %s: local FLAC missing at %s", record["event_id"], flac_path)
            return False

        metadata = {
            "event_id": record["event_id"],
            "client_sha256": raw["sha256"],
            "sample_rate": raw["sample_rate"],
            "ntp_synced": raw["ntp_synced"],
            "device_calibrated": raw["device_calibrated"],
            "node_id": raw["node_id"],
            "location_tag": raw["location_tag"],
            "start_time": raw["start_time"],
            "end_time": raw["end_time"],
            "trigger_time": raw["trigger_time"],
            "trigger_band": algo["trigger_band"],
            "peak_dbfs": algo["peak_dbfs"],
            "baseline_dbfs": algo["baseline_dbfs"],
            "delta_db": algo["delta_db"],
            "low_peak_dbfs": algo["low_peak_dbfs"],
            "high_peak_dbfs": algo["high_peak_dbfs"],
            "inferred_tag": algo["inferred_tag"],
        }

        url = self._cfg.server_url.rstrip("/") + "/api/ingest/event"
        headers = {}
        if self._cfg.token:
            headers["X-HAM-Token"] = self._cfg.token

        try:
            with self._client_factory() as client, flac_path.open("rb") as f:
                resp = client.post(
                    url,
                    headers=headers,
                    data={"metadata_json": json.dumps(metadata, ensure_ascii=False)},
                    files={"file": (flac_path.name, f, "audio/flac")},
                )
            if resp.status_code in (200, 201):
                logger.info("Synced event %s -> %s (%d)", record["event_id"], url, resp.status_code)
                return True
            logger.warning(
                "Sync rejected for %s (HTTP %d): %s",
                record["event_id"],
                resp.status_code,
                resp.text[:200],
            )
            return False
        except Exception as exc:
            logger.warning("Sync network error for %s: %s (queued for retry)", record["event_id"], exc)
            return False

    def push_event(self, record: Dict[str, Any]) -> bool:
        """Push one newly saved event; enqueue locally if central server is unavailable."""
        if not self.enabled:
            return False
        ok = self._upload_record(record)
        if not ok:
            self._enqueue(record["event_id"])
        return ok

    def flush_queue(self, store: EvidenceStore) -> tuple[int, int]:
        """Retry all queued events. Returns (succeeded_count, remaining_count)."""
        if not self.enabled:
            return 0, self.pending_count()
        queue = self._load_queue()
        if not queue:
            return 0, 0

        succeeded = 0
        remaining: List[str] = []
        for eid in queue:
            rec = store.get_event(eid)
            if rec is None:
                continue
            if self._upload_record(rec):
                succeeded += 1
            else:
                remaining.append(eid)

        self._save_queue(remaining)
        return succeeded, len(remaining)
