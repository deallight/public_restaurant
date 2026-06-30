from __future__ import annotations

from threading import Lock
from typing import Any

from .utils import utc_now


class VerificationProgressStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._batch_id: int | None = None
        self._payload: dict[str, Any] = self._empty_payload()

    def update(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event") or "")
        batch_id = int(event["batch_id"]) if event.get("batch_id") else None
        now = str(event.get("updated_at") or utc_now())
        with self._lock:
            if event_type == "batch_started":
                self._batch_id = batch_id
                self._payload = {
                    "active": True,
                    "batch_id": batch_id,
                    "job_name": str(event.get("job_name") or ""),
                    "total": int(event.get("total") or 0),
                    "limit": int(event.get("limit") or 0),
                    "processed": 0,
                    "summary": {},
                    "items": [],
                    "updated_at": now,
                }
                return
            if batch_id is not None and self._batch_id not in {None, batch_id}:
                return
            if batch_id is not None:
                self._batch_id = batch_id
                self._payload["batch_id"] = batch_id
            if event_type == "batch_finished":
                self._payload["active"] = False
                self._payload["summary"] = dict(event.get("summary") or {})
                self._payload["processed"] = int(event.get("processed") or self._payload.get("processed") or 0)
                self._payload["updated_at"] = now
                return

            candidate_id = event.get("candidate_id")
            if candidate_id is None:
                return
            item = self._item(int(candidate_id))
            item.update(
                {
                    "candidate_id": int(candidate_id),
                    "stage": str(event.get("stage") or item.get("stage") or ""),
                    "label": str(event.get("label") or item.get("label") or ""),
                    "percent": max(0, min(100, int(event.get("percent") or item.get("percent") or 0))),
                    "status": str(event.get("status") or item.get("status") or "running"),
                    "decision": str(event.get("decision") or item.get("decision") or ""),
                    "updated_at": now,
                }
            )
            if "order" in event:
                item["order"] = int(event.get("order") or item.get("order") or 0)
            if "place_name" in event:
                item["place_name"] = str(event.get("place_name") or "")
            if item["status"] in {"completed", "failed"}:
                self._payload["processed"] = sum(
                    1 for row in self._payload["items"] if row.get("status") in {"completed", "failed"}
                )
            self._payload["updated_at"] = now

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            items = sorted(
                (dict(item) for item in self._payload["items"]),
                key=lambda item: (int(item.get("order") or 0), int(item.get("candidate_id") or 0)),
            )
            return {**self._payload, "items": items}

    def _item(self, candidate_id: int) -> dict[str, Any]:
        for item in self._payload["items"]:
            if int(item.get("candidate_id") or 0) == candidate_id:
                return item
        item: dict[str, Any] = {
            "candidate_id": candidate_id,
            "order": len(self._payload["items"]) + 1,
            "stage": "queued",
            "label": "검증 대기",
            "percent": 4,
            "status": "queued",
            "decision": "",
            "place_name": "",
            "updated_at": utc_now(),
        }
        self._payload["items"].append(item)
        return item

    def _empty_payload(self) -> dict[str, Any]:
        return {
            "active": False,
            "batch_id": None,
            "job_name": "",
            "total": 0,
            "limit": 0,
            "processed": 0,
            "summary": {},
            "items": [],
            "updated_at": utc_now(),
        }
