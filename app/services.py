from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import Database
from .utils import normalize_text, safe_json_dumps, safe_json_loads, stable_hash, utc_now


class AppError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class RequestContext:
    ip: str = "127.0.0.1"
    user_id: int | None = None
    actor_id: str = "system"


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RestaurantService:
    def __init__(self, database: Database, review_rate_limit_per_hour: int = 3):
        self.database = database
        self.review_rate_limit_per_hour = review_rate_limit_per_hour

    def list_map_restaurants(
        self,
        q: str = "",
        category: str = "",
        region: str = "",
        bounds: str = "",
    ) -> list[dict[str, Any]]:
        clauses = ["r.map_exposure_status = 'visible'", "r.verification_status = 'success'"]
        params: list[Any] = []
        if q:
            clauses.append("(r.normalized_name LIKE ? OR r.normalized_address LIKE ?)")
            normalized = f"%{normalize_text(q)}%"
            params.extend([normalized, normalized])
        if category:
            clauses.append("r.major_category = ?")
            params.append(category)
        if region:
            clauses.append("(rg.sido LIKE ? OR COALESCE(rg.sigungu, '') LIKE ?)")
            params.extend([f"%{region}%", f"%{region}%"])
        if bounds:
            parts = [float(part) for part in bounds.split(",")]
            if len(parts) == 4:
                south, west, north, east = parts
                clauses.append("r.latitude BETWEEN ? AND ? AND r.longitude BETWEEN ? AND ?")
                params.extend([south, north, west, east])
        sql = f"""
            SELECT
              r.id,
              r.canonical_name AS name,
              r.major_category,
              r.address,
              r.road_address,
              r.longitude,
              r.latitude,
              rg.sido,
              rg.sigungu,
              COUNT(rel.id) AS visit_count,
              COALESCE(SUM(rel.amount), 0) AS total_amount,
              COALESCE(AVG(CASE WHEN rv.status = 'visible' THEN rv.rating END), 0) AS average_rating,
              COUNT(CASE WHEN rv.status = 'visible' THEN rv.id END) AS review_count
            FROM restaurants r
            LEFT JOIN regions rg ON rg.id = r.region_id
            LEFT JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
            LEFT JOIN restaurant_reviews rv ON rv.restaurant_id = r.id
            WHERE {' AND '.join(clauses)}
            GROUP BY r.id, rg.id
            ORDER BY visit_count DESC, average_rating DESC, r.id ASC
        """
        with self.database.session() as conn:
            return [self._restaurant_payload(dict(row)) for row in conn.execute(sql, params)]

    def get_restaurant(self, restaurant_id: int) -> dict[str, Any]:
        with self.database.session() as conn:
            row = conn.execute(
                """
                SELECT
                  r.*,
                  rg.sido,
                  rg.sigungu,
                  COUNT(rel.id) AS visit_count,
                  COALESCE(SUM(rel.amount), 0) AS total_amount,
                  COALESCE(AVG(CASE WHEN rv.status = 'visible' THEN rv.rating END), 0) AS average_rating,
                  COUNT(CASE WHEN rv.status = 'visible' THEN rv.id END) AS review_count
                FROM restaurants r
                LEFT JOIN regions rg ON rg.id = r.region_id
                LEFT JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
                LEFT JOIN restaurant_reviews rv ON rv.restaurant_id = r.id
                WHERE r.id = ?
                GROUP BY r.id, rg.id
                """,
                (restaurant_id,),
            ).fetchone()
            if row is None:
                raise AppError(404, "restaurant not found")
            reviews = [
                dict(review)
                for review in conn.execute(
                    """
                    SELECT id, rating, body, reviewer_label, created_at
                    FROM restaurant_reviews
                    WHERE restaurant_id = ? AND status = 'visible'
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (restaurant_id,),
                )
            ]
            payload = self._restaurant_payload(dict(row))
            payload["reviews"] = reviews
            return payload

    def rankings(self, category: str = "", region: str = "", period: str = "all") -> list[dict[str, Any]]:
        clauses = ["r.map_exposure_status = 'visible'", "r.verification_status = 'success'"]
        params: list[Any] = []
        if category:
            clauses.append("r.major_category = ?")
            params.append(category)
        if region:
            clauses.append("(rg.sido LIKE ? OR COALESCE(rg.sigungu, '') LIKE ?)")
            params.extend([f"%{region}%", f"%{region}%"])
        if period == "6m":
            clauses.append("rel.used_date >= date('now', '-6 months')")
        sql = f"""
            SELECT
              r.id,
              r.canonical_name AS name,
              r.major_category,
              r.address,
              r.road_address,
              r.longitude,
              r.latitude,
              rg.sido,
              rg.sigungu,
              COUNT(rel.id) AS visit_count,
              COALESCE(SUM(rel.amount), 0) AS total_amount,
              COALESCE(AVG(CASE WHEN rv.status = 'visible' THEN rv.rating END), 0) AS average_rating,
              COUNT(CASE WHEN rv.status = 'visible' THEN rv.id END) AS review_count
            FROM restaurants r
            LEFT JOIN regions rg ON rg.id = r.region_id
            LEFT JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
            LEFT JOIN restaurant_reviews rv ON rv.restaurant_id = r.id
            WHERE {' AND '.join(clauses)}
            GROUP BY r.id, rg.id
            ORDER BY visit_count DESC, total_amount DESC, average_rating DESC
            LIMIT 50
        """
        with self.database.session() as conn:
            return [self._restaurant_payload(dict(row)) for row in conn.execute(sql, params)]

    def search(self, q: str, category: str = "", bounds: str = "") -> list[dict[str, Any]]:
        return self.list_map_restaurants(q=q, category=category, bounds=bounds)[:20]

    def add_review(
        self,
        restaurant_id: int,
        rating: int,
        body: str,
        reviewer_label: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        if rating < 1 or rating > 5:
            raise AppError(400, "rating must be between 1 and 5")
        if not body or len(body.strip()) < 3:
            raise AppError(400, "review body is too short")
        ip_hash = stable_hash("ip", context.ip)
        with self.database.session() as conn:
            if conn.execute("SELECT id FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone() is None:
                raise AppError(404, "restaurant not found")
            recent_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM restaurant_reviews
                WHERE restaurant_id = ?
                  AND COALESCE(user_id, -1) = COALESCE(?, -1)
                  AND COALESCE(ip_hash, '') = ?
                  AND created_at >= datetime('now', '-1 hour')
                """,
                (restaurant_id, context.user_id, ip_hash),
            ).fetchone()["c"]
            if recent_count >= self.review_rate_limit_per_hour:
                self._audit(
                    conn,
                    "system",
                    "review_rate_limit",
                    "restaurant",
                    restaurant_id,
                    after={"ip_hash": ip_hash, "recent_count": recent_count},
                    reason_codes=["REVIEW_RATE_LIMIT"],
                )
                raise AppError(429, "too many reviews")
            cur = conn.execute(
                """
                INSERT INTO restaurant_reviews
                  (restaurant_id, user_id, rating, body, reviewer_label, ip_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    restaurant_id,
                    context.user_id,
                    rating,
                    body.strip(),
                    reviewer_label.strip()[:40] or "방문자",
                    ip_hash,
                ),
            )
            review = dict(
                conn.execute("SELECT * FROM restaurant_reviews WHERE id = ?", (cur.lastrowid,)).fetchone()
            )
            self._audit(
                conn,
                "user",
                "review_create",
                "restaurant_review",
                int(cur.lastrowid),
                after=review,
                reason_codes=["USER_REVIEW"],
            )
            return review

    def report_review(
        self,
        review_id: int,
        reason: str,
        context: RequestContext,
    ) -> dict[str, Any]:
        if not reason:
            raise AppError(400, "reason is required")
        with self.database.session() as conn:
            if conn.execute("SELECT id FROM restaurant_reviews WHERE id = ?", (review_id,)).fetchone() is None:
                raise AppError(404, "review not found")
            cur = conn.execute(
                """
                INSERT INTO review_reports
                  (review_id, reason, reporter_user_id, reporter_ip_hash)
                VALUES (?, ?, ?, ?)
                """,
                (review_id, reason, context.user_id, stable_hash("ip", context.ip)),
            )
            report = dict(conn.execute("SELECT * FROM review_reports WHERE id = ?", (cur.lastrowid,)).fetchone())
            self._audit(
                conn,
                "user",
                "review_report",
                "review_report",
                int(cur.lastrowid),
                after=report,
                reason_codes=["REVIEW_REPORT"],
            )
            return report

    def admin_review_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        capped_limit = max(1, min(limit, 100))
        with self.database.session() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                      mrt.id AS review_id,
                      c.id AS candidate_id,
                      c.original_place_name,
                      c.original_address,
                      c.place_major_category,
                      c.used_date,
                      c.amount,
                      c.review_note,
                      mrt.reason,
                      mrt.status,
                      er.department_name,
                      er.purpose,
                      er.participants,
                      er.payment_method,
                      i.name AS institution_name,
                      rd.source_title,
                      rd.source_url,
                      rd.published_at AS source_published_at,
                      pv.provider_place_name,
                      pv.provider_category,
                      pv.provider_address,
                      pv.provider_road_address,
                      pv.name_similarity,
                      pv.address_similarity,
                      pv.verification_status AS provider_verification_status,
                      pv.verification_reason
                    FROM manual_review_tasks mrt
                    JOIN restaurant_candidates c ON c.id = mrt.candidate_id
                    JOIN expense_records er ON er.id = c.expense_record_id
                    JOIN institutions i ON i.id = c.institution_id
                    JOIN raw_documents rd ON rd.id = er.raw_document_id
                    LEFT JOIN place_verifications pv ON pv.id = (
                      SELECT id
                      FROM place_verifications
                      WHERE candidate_id = c.id
                      ORDER BY verified_at DESC, id DESC
                      LIMIT 1
                    )
                    WHERE mrt.status = 'pending'
                    ORDER BY c.updated_at DESC, mrt.created_at DESC, mrt.id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                )
            ]

    def admin_review_queue_count(self) -> int:
        with self.database.session() as conn:
            return int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM manual_review_tasks
                    WHERE status = 'pending'
                    """
                ).fetchone()["count"]
            )

    def approve_new(self, review_id: int, context: RequestContext) -> dict[str, Any]:
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            candidate = conn.execute(
                "SELECT * FROM restaurant_candidates WHERE id = ?", (task["candidate_id"],)
            ).fetchone()
            if candidate is None:
                raise AppError(404, "candidate not found")
            cur = conn.execute(
                """
                INSERT INTO restaurants
                  (region_id, canonical_name, normalized_name, major_category, naver_place_id,
                   address, road_address, normalized_address, longitude, latitude,
                   verification_status, map_exposure_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', 'visible')
                """,
                (
                    candidate["region_id"],
                    candidate["original_place_name"],
                    candidate["normalized_place_name"],
                    candidate["place_major_category"],
                    f"manual-{candidate['id']}",
                    candidate["original_address"] or "주소 미확인",
                    candidate["original_address"],
                    candidate["normalized_address"] or "",
                    129.0756416,
                    35.1795543,
                ),
            )
            restaurant_id = int(cur.lastrowid)
            self._resolve_task(conn, task, "approved", context, {"restaurant_id": restaurant_id})
            return {"result": "approved_new", "restaurant_id": restaurant_id}

    def merge_candidate(self, review_id: int, restaurant_id: int, context: RequestContext) -> dict[str, Any]:
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            if conn.execute("SELECT id FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone() is None:
                raise AppError(404, "restaurant not found")
            self._resolve_task(conn, task, "merged", context, {"restaurant_id": restaurant_id})
            return {"result": "merged", "restaurant_id": restaurant_id}

    def reject_candidate(self, review_id: int, context: RequestContext) -> dict[str, Any]:
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET status = 'rejected',
                    manual_review_status = 'rejected',
                    rejection_reason = 'manual_reject',
                    updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), task["candidate_id"]),
            )
            conn.execute(
                """
                UPDATE manual_review_tasks
                SET status = 'rejected', reviewed_by = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (context.actor_id, utc_now(), review_id),
            )
            self._audit(
                conn,
                "admin",
                "candidate_reject",
                "manual_review_task",
                review_id,
                after={"candidate_id": task["candidate_id"]},
                reason_codes=["MANUAL_REJECT"],
            )
            return {"result": "rejected"}

    def review_reports(self) -> list[dict[str, Any]]:
        with self.database.session() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT rr.*, rv.restaurant_id, rv.body, rv.rating, rv.status AS review_status
                    FROM review_reports rr
                    JOIN restaurant_reviews rv ON rv.id = rr.review_id
                    ORDER BY rr.created_at DESC
                    """
                )
            ]

    def source_registry(self) -> dict[str, Any]:
        with self.database.session() as conn:
            rows = [
                self._source_payload(row)
                for row in conn.execute(
                    """
                    SELECT
                      sr.*,
                      i.name AS institution_name,
                      i.institution_code,
                      COUNT(rd.id) AS documents_collected,
                      MAX(rd.collected_at) AS last_collected_at
                    FROM source_registry sr
                    JOIN institutions i ON i.id = sr.institution_id
                    LEFT JOIN raw_documents rd ON rd.source_registry_id = sr.id
                    GROUP BY sr.id, i.id
                    ORDER BY
                      CAST(json_extract(sr.config_json, '$.priority') AS INTEGER) ASC,
                      json_extract(sr.config_json, '$.group_label') ASC,
                      i.name ASC
                    """
                )
            ]
        groups: dict[tuple[int, str], dict[str, Any]] = {}
        by_status: dict[str, int] = {}
        for item in rows:
            by_status[item["status"]] = by_status.get(item["status"], 0) + 1
            key = (item["priority"], item["group_key"])
            if key not in groups:
                groups[key] = {
                    "priority": item["priority"],
                    "group_key": item["group_key"],
                    "group_label": item["group_label"],
                    "sources": [],
                    "source_count": 0,
                    "documents_collected": 0,
                }
            groups[key]["sources"].append(item)
            groups[key]["source_count"] += 1
            groups[key]["documents_collected"] += item["documents_collected"]
        return {
            "summary": {
                "source_count": len(rows),
                "active_count": sum(1 for item in rows if item["is_active"]),
                "by_status": by_status,
            },
            "groups": list(groups.values()),
        }

    def verification_overview(self) -> dict[str, Any]:
        with self.database.session() as conn:
            candidate_statuses = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT status, manual_review_status, COUNT(*) AS count
                    FROM restaurant_candidates
                    GROUP BY status, manual_review_status
                    ORDER BY status, manual_review_status
                    """
                )
            ]
            api_summary = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                      provider,
                      COUNT(*) AS call_count,
                      SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                      SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count,
                      ROUND(AVG(duration_ms), 1) AS average_duration_ms,
                      MAX(called_at) AS last_called_at
                    FROM api_call_logs
                    GROUP BY provider
                    ORDER BY provider
                    """
                )
            ]
            recent_errors = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT provider, endpoint, error_message, called_at
                    FROM api_call_logs
                    WHERE success = 0
                    ORDER BY called_at DESC
                    LIMIT 5
                    """
                )
            ]
            latest_batches = [
                self._batch_payload(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM batch_jobs
                    WHERE job_name IN ('daily_busan_city_expense_v1', 'verify_pending')
                    ORDER BY id DESC
                    LIMIT 5
                    """
                )
            ]
            counts = {
                "restaurants": conn.execute("SELECT COUNT(*) AS c FROM restaurants").fetchone()["c"],
                "candidates": conn.execute("SELECT COUNT(*) AS c FROM restaurant_candidates").fetchone()["c"],
                "pending_reviews": conn.execute(
                    "SELECT COUNT(*) AS c FROM manual_review_tasks WHERE status = 'pending'"
                ).fetchone()["c"],
                "permit_snapshots": conn.execute("SELECT COUNT(*) AS c FROM permit_snapshots").fetchone()["c"],
                "api_call_logs": conn.execute("SELECT COUNT(*) AS c FROM api_call_logs").fetchone()["c"],
            }
        return {
            "counts": counts,
            "candidate_statuses": candidate_statuses,
            "api_summary": api_summary,
            "recent_api_errors": recent_errors,
            "latest_batches": latest_batches,
        }

    def upsert_oauth_account(self, provider: str, provider_subject: str, display_name: str) -> dict[str, Any]:
        if provider not in {"google", "naver"}:
            raise AppError(400, "unsupported provider")
        if not provider_subject:
            raise AppError(400, "provider subject is required")
        with self.database.session() as conn:
            account = conn.execute(
                "SELECT * FROM oauth_accounts WHERE provider = ? AND provider_subject = ?",
                (provider, provider_subject),
            ).fetchone()
            if account:
                conn.execute(
                    "UPDATE oauth_accounts SET last_login_at = ? WHERE id = ?",
                    (utc_now(), account["id"]),
                )
                user = conn.execute("SELECT * FROM users WHERE id = ?", (account["user_id"],)).fetchone()
                return {"user": dict(user), "oauth_account": dict(account), "created": False}
            cur_user = conn.execute(
                "INSERT INTO users (display_name) VALUES (?)",
                (display_name.strip()[:40] or f"{provider} 사용자",),
            )
            user_id = int(cur_user.lastrowid)
            cur_account = conn.execute(
                """
                INSERT INTO oauth_accounts
                  (user_id, provider, provider_subject, display_name, last_login_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, provider, provider_subject, display_name, utc_now()),
            )
            self._audit(
                conn,
                "system",
                "oauth_account_create",
                "user",
                user_id,
                after={"provider": provider},
                reason_codes=["SSO_CREATE"],
            )
            return {
                "user": dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()),
                "oauth_account": dict(
                    conn.execute("SELECT * FROM oauth_accounts WHERE id = ?", (cur_account.lastrowid,)).fetchone()
                ),
                "created": True,
            }

    def request_account_merge(
        self, source_user_id: int, target_user_id: int, reason: str, context: RequestContext
    ) -> dict[str, Any]:
        if source_user_id == target_user_id:
            raise AppError(400, "source and target users must differ")
        with self.database.session() as conn:
            for user_id in [source_user_id, target_user_id]:
                if conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                    raise AppError(404, "user not found")
            cur = conn.execute(
                """
                INSERT INTO account_merge_requests
                  (source_user_id, target_user_id, reason, requested_by)
                VALUES (?, ?, ?, ?)
                """,
                (source_user_id, target_user_id, reason, context.actor_id),
            )
            return dict(
                conn.execute("SELECT * FROM account_merge_requests WHERE id = ?", (cur.lastrowid,)).fetchone()
            )

    def merge_account(self, user_id: int, target_user_id: int, context: RequestContext) -> dict[str, Any]:
        if user_id == target_user_id:
            raise AppError(400, "source and target users must differ")
        with self.database.session() as conn:
            source = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            target = conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if source is None or target is None:
                raise AppError(404, "user not found")
            conn.execute("UPDATE oauth_accounts SET user_id = ? WHERE user_id = ?", (target_user_id, user_id))
            conn.execute("UPDATE restaurant_reviews SET user_id = ? WHERE user_id = ?", (target_user_id, user_id))
            conn.execute("UPDATE users SET status = 'merged', updated_at = ? WHERE id = ?", (utc_now(), user_id))
            conn.execute(
                """
                UPDATE account_merge_requests
                SET status = 'approved', resolved_by = ?, resolved_at = ?
                WHERE source_user_id = ? AND target_user_id = ? AND status = 'pending'
                """,
                (context.actor_id, utc_now(), user_id, target_user_id),
            )
            self._audit(
                conn,
                "admin",
                "account_merge",
                "user",
                user_id,
                before=dict(source),
                after={"target_user_id": target_user_id},
                reason_codes=["ADMIN_ACCOUNT_MERGE"],
            )
            return {"result": "merged", "source_user_id": user_id, "target_user_id": target_user_id}

    def batch(self, batch_id: int) -> dict[str, Any]:
        with self.database.session() as conn:
            row = conn.execute("SELECT * FROM batch_jobs WHERE id = ?", (batch_id,)).fetchone()
            if row is None:
                raise AppError(404, "batch not found")
            return self._batch_payload(row)

    def _batch_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["summary"] = safe_json_loads(payload.pop("summary_json"), {})
        return payload

    def _source_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        config = safe_json_loads(payload.pop("config_json"), {})
        return {
            "id": payload["id"],
            "source_key": payload["source_key"],
            "institution_name": payload["institution_name"],
            "institution_code": payload["institution_code"],
            "priority": int(config.get("priority") or 99),
            "group_key": str(config.get("group_key") or "unknown"),
            "group_label": str(config.get("group_label") or "미분류"),
            "source_type": payload["source_type"],
            "adapter_name": payload["adapter_name"],
            "base_url": payload["base_url"],
            "crawl_frequency": payload["crawl_frequency"],
            "is_active": bool(payload["is_active"]),
            "status": str(config.get("status") or "unknown"),
            "expected_formats": config.get("expected_formats") or [],
            "notes": str(config.get("notes") or ""),
            "documents_collected": int(payload.get("documents_collected") or 0),
            "last_collected_at": payload.get("last_collected_at"),
        }

    def _restaurant_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row.get("name") or row.get("canonical_name"),
            "category": row["major_category"],
            "category_label": {
                "restaurant": "음식점",
                "cafe": "카페",
                "bar": "주점",
                "other": "기타",
            }.get(row["major_category"], "기타"),
            "address": row["address"],
            "road_address": row.get("road_address"),
            "longitude": row["longitude"],
            "latitude": row["latitude"],
            "region": {
                "sido": row.get("sido"),
                "sigungu": row.get("sigungu"),
            },
            "visit_count": int(row.get("visit_count") or 0),
            "total_amount": int(row.get("total_amount") or 0),
            "average_rating": round(float(row.get("average_rating") or 0), 2),
            "review_count": int(row.get("review_count") or 0),
        }

    def _load_review_task(self, conn: sqlite3.Connection, review_id: int) -> sqlite3.Row:
        task = conn.execute("SELECT * FROM manual_review_tasks WHERE id = ?", (review_id,)).fetchone()
        if task is None:
            raise AppError(404, "review task not found")
        if task["status"] != "pending":
            raise AppError(409, "review task already resolved")
        return task

    def _resolve_task(
        self,
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        status: str,
        context: RequestContext,
        after: dict[str, Any],
    ) -> None:
        candidate = conn.execute(
            "SELECT * FROM restaurant_candidates WHERE id = ?", (task["candidate_id"],)
        ).fetchone()
        expense = conn.execute(
            "SELECT * FROM expense_records WHERE id = ?", (candidate["expense_record_id"],)
        ).fetchone()
        restaurant_id = after["restaurant_id"]
        conn.execute(
            """
            INSERT OR IGNORE INTO restaurant_expense_links
              (restaurant_id, expense_record_id, candidate_id, used_date, amount, link_reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                restaurant_id,
                expense["id"],
                candidate["id"],
                expense["used_date"],
                expense["amount"],
                f"manual_{status}",
            ),
        )
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET status = 'verified',
                manual_review_status = 'approved',
                verification_status = 'success',
                updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), candidate["id"]),
        )
        conn.execute(
            """
            UPDATE manual_review_tasks
            SET status = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (status, context.actor_id, utc_now(), task["id"]),
        )
        self._audit(
            conn,
            "admin",
            f"candidate_{status}",
            "manual_review_task",
            task["id"],
            before=dict(task),
            after=after,
            reason_codes=[f"MANUAL_{status.upper()}"],
        )

    def _audit(
        self,
        conn: sqlite3.Connection,
        actor_type: str,
        action: str,
        target_type: str,
        target_id: int,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason_codes: list[str] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO decision_audit_logs
              (actor_type, actor_id, action, target_type, target_id, before_json, after_json,
               reason_codes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_type,
                "service",
                action,
                target_type,
                target_id,
                safe_json_dumps(before or {}),
                safe_json_dumps(after or {}),
                safe_json_dumps(reason_codes or []),
            ),
        )
