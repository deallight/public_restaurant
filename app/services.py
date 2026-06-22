from __future__ import annotations

import sqlite3
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .alias_memory import remember_aliases
from .agents import NormalizedExpenseRow, PlaceCandidate, similarity
from .database import Database
from .integrations import IntegrationError
from .utils import (
    map_search_address,
    normalize_address,
    normalize_text,
    safe_json_dumps,
    safe_json_loads,
    stable_hash,
    strip_address_detail,
    utc_now,
)


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


def row_value(row: sqlite3.Row | dict[str, Any], key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if key in row.keys():
        return row[key]
    return None


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def candidate_effective_place_name(candidate: sqlite3.Row | dict[str, Any]) -> str:
    return first_text(row_value(candidate, "review_place_name"), row_value(candidate, "original_place_name"))


def candidate_effective_normalized_place_name(candidate: sqlite3.Row | dict[str, Any]) -> str:
    return first_text(
        row_value(candidate, "review_normalized_place_name"),
        normalize_text(candidate_effective_place_name(candidate)),
        row_value(candidate, "normalized_place_name"),
    )


def candidate_effective_address(candidate: sqlite3.Row | dict[str, Any]) -> str:
    return first_text(row_value(candidate, "review_address"), row_value(candidate, "original_address"))


def candidate_effective_normalized_address(candidate: sqlite3.Row | dict[str, Any]) -> str:
    return first_text(
        row_value(candidate, "review_normalized_address"),
        normalize_address(candidate_effective_address(candidate)),
        row_value(candidate, "normalized_address"),
    )


def candidate_effective_major_category(candidate: sqlite3.Row | dict[str, Any]) -> str:
    return first_text(row_value(candidate, "review_major_category"), row_value(candidate, "place_major_category"), "other")


def naver_search_address(address: str) -> str:
    return map_search_address(address)


def naver_map_query(name: str, address: str) -> str:
    cleaned_address = naver_search_address(address)
    compact_name = str(name or "").replace(" ", "")
    compact_address = cleaned_address.replace(" ", "")
    if compact_name and compact_name in compact_address:
        return cleaned_address
    return " ".join(part for part in [name, cleaned_address] if part)


def naver_map_url(query: str, appname: str = "public_restaurant") -> str:
    return f"https://map.naver.com/p/search/{quote(query, safe='')}"


class RestaurantService:
    def __init__(
        self,
        database: Database,
        review_rate_limit_per_hour: int = 3,
        geocoding_client: Any | None = None,
        naver_client: Any | None = None,
    ):
        self.database = database
        self.review_rate_limit_per_hour = review_rate_limit_per_hour
        self.geocoding_client = geocoding_client
        self.naver_client = naver_client

    def _add_effective_candidate_values(self, payload: dict[str, Any]) -> None:
        payload["effective_place_name"] = candidate_effective_place_name(payload)
        payload["effective_normalized_place_name"] = candidate_effective_normalized_place_name(payload)
        payload["effective_address"] = candidate_effective_address(payload)
        payload["effective_normalized_address"] = candidate_effective_normalized_address(payload)
        payload["effective_major_category"] = candidate_effective_major_category(payload)

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
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                      mrt.id AS review_id,
                      c.id AS candidate_id,
                      c.original_place_name,
                      c.review_place_name,
                      c.review_normalized_place_name,
                      c.original_address,
                      c.review_address,
                      c.review_normalized_address,
                      c.place_major_category,
                      c.review_major_category,
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
                    ORDER BY c.id DESC, mrt.id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                )
            ]
            for row in rows:
                self._add_effective_candidate_values(row)
                row["provider_candidates"] = self._provider_candidates(conn, int(row["candidate_id"]))
            return rows

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

    def admin_candidates(
        self,
        limit: int = 50,
        offsets: dict[str, int] | None = None,
        q: str = "",
        sort: str = "id_desc",
    ) -> dict[str, Any]:
        capped_limit = max(1, min(limit, 200))
        offsets = offsets or {}
        sort_sql = {
            "id_desc": "c.id DESC",
            "id_asc": "c.id ASC",
            "updated_desc": "c.updated_at DESC, c.id DESC",
            "used_date_desc": "COALESCE(c.used_date, '') DESC, c.id DESC",
            "amount_desc": "COALESCE(c.amount, 0) DESC, c.id DESC",
            "name_asc": "COALESCE(c.review_normalized_place_name, c.normalized_place_name) ASC, c.id DESC",
        }.get(str(sort or "id_desc"), "c.id DESC")
        search_text = normalize_text(q)
        search_clause = ""
        search_params: list[Any] = []
        if search_text:
            search_clause = """
              AND (
                c.normalized_place_name LIKE ?
                OR COALESCE(c.review_normalized_place_name, '') LIKE ?
                OR COALESCE(c.normalized_address, '') LIKE ?
                OR COALESCE(c.review_normalized_address, '') LIKE ?
                OR COALESCE(i.name, '') LIKE ?
                OR COALESCE(er.department_name, '') LIKE ?
                OR COALESCE(er.purpose, '') LIKE ?
                OR COALESCE(rd.source_title, '') LIKE ?
              )
            """
            pattern = f"%{search_text}%"
            search_params = [pattern, pattern, pattern, pattern, pattern, pattern, pattern, pattern]
        groups = {
            "needs_review": {"label": "수동검토", "items": [], "total": 0},
            "verified": {"label": "승인", "items": [], "total": 0},
            "rejected": {"label": "반려", "items": [], "total": 0},
        }
        with self.database.session() as conn:
            for status in groups:
                offset = max(0, int(offsets.get(status, 0) or 0))
                groups[status]["total"] = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*) AS count
                        FROM restaurant_candidates c
                        JOIN expense_records er ON er.id = c.expense_record_id
                        JOIN institutions i ON i.id = c.institution_id
                        JOIN raw_documents rd ON rd.id = er.raw_document_id
                        WHERE c.status = ?
                        {search_clause}
                        """,
                        [status, *search_params],
                    ).fetchone()["count"]
                )
                rows = [
                    dict(row)
                    for row in conn.execute(
                        f"""
                        SELECT
                          c.id AS candidate_id,
                          c.original_place_name,
                          c.review_place_name,
                          c.review_normalized_place_name,
                          c.original_address,
                          c.review_address,
                          c.review_normalized_address,
                          c.place_major_category,
                          c.review_major_category,
                          c.status AS candidate_status,
                          c.verification_status,
                          c.manual_review_status,
                          c.rejection_reason,
                          c.review_note,
                          c.used_date,
                          c.amount,
                          er.department_name,
                          er.purpose,
                          er.participants,
                          er.payment_method,
                          i.name AS institution_name,
                          rd.source_title,
                          rd.source_url,
                          rd.published_at AS source_published_at,
                          mrt.id AS review_id,
                          mrt.status AS review_status,
                          mrt.reason AS review_reason,
                          pv.provider_place_name,
                          pv.provider_category,
                          pv.provider_address,
                          pv.provider_road_address,
                          pv.name_similarity,
                          pv.address_similarity,
                          pv.verification_status AS provider_verification_status,
                          pv.verification_reason
                        FROM restaurant_candidates c
                        JOIN expense_records er ON er.id = c.expense_record_id
                        JOIN institutions i ON i.id = c.institution_id
                        JOIN raw_documents rd ON rd.id = er.raw_document_id
                        LEFT JOIN manual_review_tasks mrt ON mrt.candidate_id = c.id
                        LEFT JOIN place_verifications pv ON pv.id = (
                          SELECT id
                          FROM place_verifications
                          WHERE candidate_id = c.id
                          ORDER BY verified_at DESC, id DESC
                          LIMIT 1
                        )
                        WHERE c.status = ?
                        {search_clause}
                        ORDER BY {sort_sql}
                        LIMIT ? OFFSET ?
                        """,
                        [status, *search_params, capped_limit, offset],
                    )
                ]
                for row in rows:
                    self._add_effective_candidate_values(row)
                    row["provider_candidates"] = self._provider_candidates(conn, int(row["candidate_id"]))
                groups[status]["items"] = rows
                groups[status]["offset"] = offset
                groups[status]["limit"] = capped_limit
                groups[status]["has_prev"] = offset > 0
                groups[status]["has_next"] = offset + len(rows) < int(groups[status]["total"])
        return {"groups": groups, "limit": capped_limit, "sort": sort}

    def map_issue_candidates(self, limit: int = 100) -> dict[str, Any]:
        capped_limit = max(1, min(limit, 300))
        with self.database.session() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                      c.id AS candidate_id,
                      c.original_place_name,
                      c.review_place_name,
                      c.review_normalized_place_name,
                      c.original_address,
                      c.review_address,
                      c.review_normalized_address,
                      c.place_major_category,
                      c.review_major_category,
                      c.status AS candidate_status,
                      c.verification_status,
                      c.manual_review_status,
                      c.rejection_reason,
                      c.review_note,
                      c.used_date,
                      c.amount,
                      er.department_name,
                      er.purpose,
                      er.participants,
                      er.payment_method,
                      i.name AS institution_name,
                      rd.source_title,
                      rd.source_url,
                      rd.published_at AS source_published_at,
                      mrt.id AS review_id,
                      mrt.status AS review_status,
                      mrt.reason AS review_reason,
                      r.id AS restaurant_id,
                      r.canonical_name AS restaurant_name,
                      r.address AS restaurant_address,
                      r.road_address AS restaurant_road_address,
                      r.longitude AS restaurant_longitude,
                      r.latitude AS restaurant_latitude,
                      r.map_exposure_status,
                      pv.provider_place_name,
                      pv.provider_category,
                      pv.provider_address,
                      pv.provider_road_address,
                      pv.name_similarity,
                      pv.address_similarity,
                      pv.verification_status AS provider_verification_status,
                      pv.verification_reason,
                      CASE
                        WHEN r.id IS NULL THEN '지도 링크 없음'
                        WHEN r.map_exposure_status <> 'visible' THEN '지도 비표시'
                        WHEN r.address = '주소 미확인' OR COALESCE(r.road_address, '') = '' THEN '주소 미확인'
                        WHEN COALESCE(c.review_address, c.original_address, '') LIKE '%부산%'
                         AND (
                           r.latitude NOT BETWEEN 34.8 AND 35.4
                           OR r.longitude NOT BETWEEN 128.7 AND 129.4
                         ) THEN '부산 보정주소와 좌표 불일치'
                        ELSE '확인 필요'
                      END AS issue_reason
                    FROM restaurant_candidates c
                    JOIN expense_records er ON er.id = c.expense_record_id
                    JOIN institutions i ON i.id = c.institution_id
                    JOIN raw_documents rd ON rd.id = er.raw_document_id
                    LEFT JOIN manual_review_tasks mrt ON mrt.candidate_id = c.id
                    LEFT JOIN restaurant_expense_links rel ON rel.candidate_id = c.id
                    LEFT JOIN restaurants r ON r.id = rel.restaurant_id
                    LEFT JOIN place_verifications pv ON pv.id = (
                      SELECT id
                      FROM place_verifications
                      WHERE candidate_id = c.id
                      ORDER BY verified_at DESC, id DESC
                      LIMIT 1
                    )
                    WHERE c.status = 'verified'
                      AND (
                        r.id IS NULL
                        OR r.map_exposure_status <> 'visible'
                        OR r.address = '주소 미확인'
                        OR COALESCE(r.road_address, '') = ''
                        OR (
                          COALESCE(c.review_address, c.original_address, '') LIKE '%부산%'
                          AND (
                            r.latitude NOT BETWEEN 34.8 AND 35.4
                            OR r.longitude NOT BETWEEN 128.7 AND 129.4
                          )
                        )
                      )
                    ORDER BY c.updated_at DESC, c.id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                )
            ]
            for row in rows:
                self._add_effective_candidate_values(row)
                row["provider_candidates"] = self._provider_candidates(conn, int(row["candidate_id"]))
            return {"items": rows, "total": len(rows), "limit": capped_limit}

    def update_review_candidate(
        self,
        review_id: int,
        context: RequestContext,
        original_place_name: str,
        original_address: str = "",
        place_major_category: str = "restaurant",
    ) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(original_place_name or "")).strip()
        address = re.sub(r"\s+", " ", str(original_address or "")).strip()
        category = str(place_major_category or "restaurant").strip()
        if not name:
            raise AppError(400, "place name is required")
        if category not in {"restaurant", "cafe", "bar", "other"}:
            raise AppError(400, "unsupported category")
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            before = conn.execute(
                "SELECT * FROM restaurant_candidates WHERE id = ?",
                (task["candidate_id"],),
            ).fetchone()
            if before is None:
                raise AppError(404, "candidate not found")
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET review_place_name = ?,
                    review_normalized_place_name = ?,
                    review_address = ?,
                    review_normalized_address = ?,
                    review_major_category = ?,
                    verification_status = 'ambiguous',
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    name[:160],
                    normalize_text(name)[:160],
                    address[:300],
                    normalize_address(address)[:300],
                    category,
                    utc_now(),
                    task["candidate_id"],
                ),
            )
            after = conn.execute(
                "SELECT * FROM restaurant_candidates WHERE id = ?",
                (task["candidate_id"],),
            ).fetchone()
            self._audit(
                conn,
                "admin",
                "candidate_update",
                "restaurant_candidate",
                int(task["candidate_id"]),
                before=dict(before),
                after={
                    "review_id": review_id,
                    "actor_id": context.actor_id,
                    "review_place_name": after["review_place_name"],
                    "review_address": after["review_address"],
                    "review_major_category": after["review_major_category"],
                },
                reason_codes=["ADMIN_CANDIDATE_EDIT"],
            )
            return {
                "result": "updated",
                "candidate_id": int(task["candidate_id"]),
                "candidate": dict(after),
            }

    def update_admin_candidate(
        self,
        candidate_id: int,
        context: RequestContext,
        review_place_name: str,
        review_address: str = "",
        review_major_category: str = "restaurant",
        target_status: str = "needs_review",
        rejection_reason: str = "manual_reject",
        reviewer_note: str = "",
        verification_id: int | None = None,
    ) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(review_place_name or "")).strip()
        address = re.sub(r"\s+", " ", str(review_address or "")).strip()
        category = str(review_major_category or "restaurant").strip()
        status = str(target_status or "needs_review").strip()
        if not name:
            raise AppError(400, "place name is required")
        if category not in {"restaurant", "cafe", "bar", "other"}:
            raise AppError(400, "unsupported category")
        if status not in {"needs_review", "verified", "rejected"}:
            raise AppError(400, "unsupported status")
        note = str(reviewer_note or "").strip()
        with self.database.session() as conn:
            before = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
            if before is None:
                raise AppError(404, "candidate not found")
            self._update_candidate_review_values(conn, candidate_id, name, address, category, note)
            provider_refresh = self._refresh_provider_candidates(conn, candidate_id)
            geocoding_result = {"status": "skipped", "reason": "split_to_geocode_button"}
            if status == "verified":
                updated = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
                self._unlink_candidate_from_restaurants(conn, candidate_id)
                verification = (
                    self._food_verification_by_id(conn, candidate_id, int(verification_id))
                    if verification_id
                    else None
                )
                if verification is not None:
                    self._apply_provider_verification_to_candidate(conn, candidate_id, verification, note)
                    updated = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
                    restaurant_id = self._restaurant_from_provider_verification(conn, updated, verification)
                    self._link_candidate_expense(conn, restaurant_id, candidate_id, "admin_selected_provider")
                else:
                    restaurant_id = self._restaurant_from_candidate_override(conn, updated)
                self._set_candidate_approved(conn, candidate_id, context, note)
                result = "verified"
            elif status == "rejected":
                self._unlink_candidate_from_restaurants(conn, candidate_id)
                self._set_candidate_rejected(conn, candidate_id, context, rejection_reason, note)
                restaurant_id = None
                result = "rejected"
            else:
                self._unlink_candidate_from_restaurants(conn, candidate_id)
                self._set_candidate_needs_review(conn, candidate_id, context, note)
                restaurant_id = None
                result = "needs_review"
            after = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
            self._audit(
                conn,
                "admin",
                "candidate_admin_update",
                "restaurant_candidate",
                candidate_id,
                before=dict(before),
                after={
                    "actor_id": context.actor_id,
                    "target_status": status,
                    "restaurant_id": restaurant_id,
                    "provider_refresh": provider_refresh,
                    "geocoding": geocoding_result,
                    "verification_id": verification_id,
                    "candidate": dict(after),
                },
                reason_codes=["ADMIN_CANDIDATE_STATE_EDIT"],
            )
            return {
                "result": result,
                "candidate_id": candidate_id,
                "restaurant_id": restaurant_id,
                "provider_refresh": provider_refresh,
                "geocoding": geocoding_result,
                "candidate": dict(after),
            }

    def geocode_admin_candidate(
        self,
        candidate_id: int,
        context: RequestContext,
        review_place_name: str,
        review_address: str = "",
        review_major_category: str = "restaurant",
        reviewer_note: str = "",
    ) -> dict[str, Any]:
        name = re.sub(r"\s+", " ", str(review_place_name or "")).strip()
        address = re.sub(r"\s+", " ", str(review_address or "")).strip()
        category = str(review_major_category or "restaurant").strip()
        if not name:
            raise AppError(400, "place name is required")
        if category not in {"restaurant", "cafe", "bar", "other"}:
            raise AppError(400, "unsupported category")
        note = str(reviewer_note or "").strip()
        with self.database.session() as conn:
            before = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
            if before is None:
                raise AppError(404, "candidate not found")
            self._update_candidate_review_values(conn, candidate_id, name, address, category, note)
            geocoding_result = self._geocode_candidate_override(conn, candidate_id)
            after = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
            self._audit(
                conn,
                "admin",
                "candidate_admin_geocode",
                "restaurant_candidate",
                candidate_id,
                before=dict(before),
                after={
                    "actor_id": context.actor_id,
                    "geocoding": geocoding_result,
                    "candidate": dict(after),
                },
                reason_codes=["ADMIN_CANDIDATE_GEOCODE"],
            )
            return {
                "result": "geocoded",
                "candidate_id": candidate_id,
                "geocoding": geocoding_result,
                "candidate": dict(after),
            }

    def _update_candidate_review_values(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        name: str,
        address: str,
        category: str,
        note: str,
    ) -> None:
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET review_place_name = ?,
                review_normalized_place_name = ?,
                review_address = ?,
                review_normalized_address = ?,
                review_major_category = ?,
                review_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name[:160],
                normalize_text(name)[:160],
                address[:300],
                normalize_address(address)[:300],
                category,
                note,
                utc_now(),
                candidate_id,
            ),
        )

    def approve_new(
        self,
        review_id: int,
        context: RequestContext,
        reviewer_note: str = "",
        verification_id: int | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            candidate = conn.execute(
                "SELECT * FROM restaurant_candidates WHERE id = ?", (task["candidate_id"],)
            ).fetchone()
            if candidate is None:
                raise AppError(404, "candidate not found")
            if verification_id:
                verification = self._food_verification_by_id(conn, int(candidate["id"]), verification_id)
            else:
                verification = self._latest_food_verification(conn, int(candidate["id"]))
            if verification is not None:
                self._apply_provider_verification_to_candidate(conn, int(candidate["id"]), verification, reviewer_note)
                candidate = conn.execute(
                    "SELECT * FROM restaurant_candidates WHERE id = ?", (task["candidate_id"],)
                ).fetchone()
                restaurant_id = self._restaurant_from_provider_verification(conn, candidate, verification)
                conn.execute(
                    """
                    UPDATE restaurant_candidates
                    SET review_major_category = ?
                    WHERE id = ?
                    """,
                    (verification["provider_category"], candidate["id"]),
                )
                result = "approved_provider"
            else:
                restaurant_id = self._create_manual_restaurant_without_provider(conn, candidate)
                result = "approved_new"
            self._resolve_task(conn, task, "approved", context, {"restaurant_id": restaurant_id}, reviewer_note)
            return {"result": result, "restaurant_id": restaurant_id}

    def merge_candidate(
        self,
        review_id: int,
        restaurant_id: int,
        context: RequestContext,
        reviewer_note: str = "",
    ) -> dict[str, Any]:
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            if conn.execute("SELECT id FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone() is None:
                raise AppError(404, "restaurant not found")
            self._resolve_task(conn, task, "merged", context, {"restaurant_id": restaurant_id}, reviewer_note)
            return {"result": "merged", "restaurant_id": restaurant_id}

    def reject_candidate(
        self,
        review_id: int,
        context: RequestContext,
        reason: str = "manual_reject",
        reviewer_note: str = "",
    ) -> dict[str, Any]:
        rejection_reason = re.sub(r"[^A-Za-z0-9_,-]+", "_", reason or "manual_reject").strip("_")
        rejection_reason = rejection_reason or "manual_reject"
        note = str(reviewer_note or "").strip()
        with self.database.session() as conn:
            task = self._load_review_task(conn, review_id)
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET status = 'rejected',
                    manual_review_status = 'rejected',
                    rejection_reason = ?,
                    review_note = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (rejection_reason, note, utc_now(), task["candidate_id"]),
            )
            conn.execute(
                """
                UPDATE manual_review_tasks
                SET status = 'rejected',
                    reason = ?,
                    reviewer_note = ?,
                    reviewed_by = ?,
                    reviewed_at = ?
                WHERE id = ?
                """,
                (rejection_reason, note, context.actor_id, utc_now(), review_id),
            )
            self._audit(
                conn,
                "admin",
                "candidate_reject",
                "manual_review_task",
                review_id,
                after={"candidate_id": task["candidate_id"], "reviewer_note": note},
                reason_codes=[rejection_reason],
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

    def _restaurant_from_candidate_override(self, conn: sqlite3.Connection, candidate: sqlite3.Row) -> int:
        linked = conn.execute(
            """
            SELECT r.*
            FROM restaurants r
            JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
            WHERE rel.candidate_id = ?
            ORDER BY r.id
            LIMIT 1
            """,
            (candidate["id"],),
        ).fetchone()
        place_name = candidate_effective_place_name(candidate)
        normalized_place_name = candidate_effective_normalized_place_name(candidate)
        category = candidate_effective_major_category(candidate)
        address = candidate_effective_address(candidate) or "주소 미확인"
        road_address = candidate_effective_address(candidate)
        normalized_address = candidate_effective_normalized_address(candidate) or normalize_address(address)
        manual_place_id = f"manual-{stable_hash('manual_restaurant', normalized_place_name, normalized_address)}"
        existing = linked or conn.execute(
            "SELECT * FROM restaurants WHERE naver_place_id = ?",
            (manual_place_id,),
        ).fetchone()
        coordinate_source = conn.execute(
            """
            SELECT longitude, latitude
            FROM place_verifications
            WHERE candidate_id = ?
              AND is_coordinate_valid = 1
              AND longitude IS NOT NULL
              AND latitude IS NOT NULL
            ORDER BY
              CASE
                WHEN COALESCE(provider_road_address, provider_address, '') LIKE '%부산%' THEN 0
                ELSE 1
              END,
              address_similarity DESC,
              name_similarity DESC,
              verified_at DESC,
              id DESC
            LIMIT 1
            """,
            (candidate["id"],),
        ).fetchone()
        longitude_value = existing["longitude"] if existing is not None else None
        latitude_value = existing["latitude"] if existing is not None else None
        if longitude_value is None and coordinate_source is not None:
            longitude_value = coordinate_source["longitude"]
        if latitude_value is None and coordinate_source is not None:
            latitude_value = coordinate_source["latitude"]
        longitude = float(longitude_value or 129.0756416)
        latitude = float(latitude_value or 35.1795543)
        if existing is not None:
            restaurant_id = int(existing["id"])
            conn.execute(
                """
                UPDATE restaurants
                SET canonical_name = ?,
                    normalized_name = ?,
                    major_category = ?,
                    address = ?,
                    road_address = ?,
                    normalized_address = ?,
                    longitude = ?,
                    latitude = ?,
                    verification_status = 'success',
                    map_exposure_status = 'visible',
                    last_verified_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    place_name,
                    normalized_place_name,
                    category,
                    address,
                    road_address,
                    normalized_address,
                    longitude,
                    latitude,
                    utc_now(),
                    utc_now(),
                    restaurant_id,
                ),
            )
        else:
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
                    place_name,
                    normalized_place_name,
                    category,
                    manual_place_id,
                    address,
                    road_address,
                    normalized_address,
                    longitude,
                    latitude,
                ),
            )
            restaurant_id = int(cur.lastrowid)
        self._link_candidate_expense(conn, restaurant_id, int(candidate["id"]), "admin_candidate_override")
        remember_aliases(
            conn,
            restaurant_id,
            [candidate["original_place_name"], place_name],
            source="admin_candidate_override",
            confidence=1.0,
        )
        return restaurant_id

    def _link_candidate_expense(
        self,
        conn: sqlite3.Connection,
        restaurant_id: int,
        candidate_id: int,
        link_reason: str,
    ) -> None:
        candidate = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
        if candidate is None:
            raise AppError(404, "candidate not found")
        expense = conn.execute("SELECT * FROM expense_records WHERE id = ?", (candidate["expense_record_id"],)).fetchone()
        if expense is None:
            raise AppError(404, "expense record not found")
        conn.execute(
            """
            INSERT INTO restaurant_expense_links
              (restaurant_id, expense_record_id, candidate_id, used_date, amount, link_reason)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
              restaurant_id = excluded.restaurant_id,
              expense_record_id = excluded.expense_record_id,
              used_date = excluded.used_date,
              amount = excluded.amount,
              link_reason = excluded.link_reason
            """,
            (
                restaurant_id,
                expense["id"],
                candidate_id,
                expense["used_date"],
                expense["amount"],
                link_reason,
            ),
        )

    def _apply_provider_verification_to_candidate(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        verification: sqlite3.Row,
        note: str,
    ) -> None:
        name = verification["provider_place_name"] or ""
        address = verification["provider_road_address"] or verification["provider_address"] or ""
        category = verification["provider_category"] or "other"
        self._update_candidate_review_values(conn, candidate_id, name, address, category, note)

    def _geocode_candidate_override(self, conn: sqlite3.Connection, candidate_id: int) -> dict[str, Any]:
        candidate = conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
        if candidate is None:
            return {"status": "candidate_missing"}
        address = candidate_effective_address(candidate).strip()
        name = candidate_effective_place_name(candidate).strip()
        category = candidate_effective_major_category(candidate)
        if not address:
            return {"status": "skipped", "reason": "address_missing"}
        if self.geocoding_client is None:
            return {"status": "skipped", "reason": "geocoding_not_configured"}
        geocoded = None
        query_used = address
        for query in [address, f"{name} {address}".strip()]:
            if not query:
                continue
            try:
                geocoded = self.geocoding_client.geocode(query)
            except IntegrationError as exc:
                return {"status": "failed", "reason": str(exc)}
            except Exception as exc:
                return {"status": "failed", "reason": str(exc)}
            if geocoded:
                query_used = query
                break
        if not geocoded:
            return {"status": "not_found", "query": query_used}
        longitude = geocoded.get("x")
        latitude = geocoded.get("y")
        if longitude is None or latitude is None:
            return {"status": "not_found", "query": query_used}
        road_address = geocoded.get("roadAddress") or address
        provider_place_id = stable_hash("manual-geocode", candidate_id, name, address, road_address)
        normalized_name = normalize_text(name)
        normalized_geocoded_address = normalize_address(road_address)
        address_score = similarity(normalize_address(address), normalized_geocoded_address)
        conn.execute(
            """
            INSERT INTO place_verifications
              (candidate_id, provider, provider_place_id, provider_place_name,
               provider_category, provider_address, provider_road_address,
               normalized_provider_name, normalized_provider_address,
               longitude, latitude, name_similarity, address_similarity,
               is_name_match, is_address_match, is_coordinate_valid, is_category_valid,
               verification_status, verification_reason, raw_response_json)
            VALUES (?, 'naver_geocode', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, 1, ?, 1, ?, 'geocoded',
                    'ADMIN_ADDRESS_GEOCODE', ?)
            ON CONFLICT(candidate_id, provider, provider_place_id) DO UPDATE SET
              provider_place_name = excluded.provider_place_name,
              provider_category = excluded.provider_category,
              provider_address = excluded.provider_address,
              provider_road_address = excluded.provider_road_address,
              normalized_provider_name = excluded.normalized_provider_name,
              normalized_provider_address = excluded.normalized_provider_address,
              longitude = excluded.longitude,
              latitude = excluded.latitude,
              name_similarity = excluded.name_similarity,
              address_similarity = excluded.address_similarity,
              is_name_match = excluded.is_name_match,
              is_address_match = excluded.is_address_match,
              is_coordinate_valid = excluded.is_coordinate_valid,
              is_category_valid = excluded.is_category_valid,
              verification_status = excluded.verification_status,
              verification_reason = excluded.verification_reason,
              raw_response_json = excluded.raw_response_json,
              verified_at = CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                provider_place_id,
                name,
                category,
                address,
                road_address,
                normalized_name,
                normalized_geocoded_address,
                float(longitude),
                float(latitude),
                address_score,
                1 if address_score >= 0.82 else 0,
                1 if category in {"restaurant", "cafe", "bar"} else 0,
                safe_json_dumps({"query": query_used, "geocoded": geocoded}),
            ),
        )
        updated_restaurants = 0
        if candidate["status"] == "verified":
            result = conn.execute(
                """
                UPDATE restaurants
                SET address = ?,
                    road_address = ?,
                    normalized_address = ?,
                    longitude = ?,
                    latitude = ?,
                    verification_status = 'success',
                    map_exposure_status = 'visible',
                    last_verified_at = ?,
                    updated_at = ?
                WHERE id IN (
                  SELECT restaurant_id
                  FROM restaurant_expense_links
                  WHERE candidate_id = ?
                )
                """,
                (
                    address,
                    road_address,
                    normalized_geocoded_address,
                    float(longitude),
                    float(latitude),
                    utc_now(),
                    utc_now(),
                    candidate_id,
                ),
            )
            updated_restaurants = int(result.rowcount or 0)
        return {
            "status": "success",
            "query": query_used,
            "longitude": float(longitude),
            "latitude": float(latitude),
            "road_address": road_address,
            "updated_restaurants": updated_restaurants,
        }

    def _refresh_provider_candidates(self, conn: sqlite3.Connection, candidate_id: int) -> dict[str, Any]:
        if self.naver_client is None:
            return {"status": "skipped", "reason": "naver_search_not_configured"}
        candidate = conn.execute(
            """
            SELECT c.*, er.source_row_number, er.department_name, er.purpose
            FROM restaurant_candidates c
            JOIN expense_records er ON er.id = c.expense_record_id
            WHERE c.id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            return {"status": "candidate_missing"}
        row = NormalizedExpenseRow(
            row_number=int(candidate["source_row_number"] or 0),
            department_name=candidate["department_name"] or "",
            used_date=candidate["used_date"] or "",
            place_name=candidate_effective_place_name(candidate),
            address=candidate_effective_address(candidate),
            purpose=candidate["purpose"] or "",
            amount=int(candidate["amount"] or 0),
            normalized_place_name=candidate_effective_normalized_place_name(candidate),
            normalized_address=candidate_effective_normalized_address(candidate),
        )
        try:
            places = self.naver_client.search_local(row)
        except IntegrationError as exc:
            return {"status": "failed", "reason": str(exc)}
        except Exception as exc:
            return {"status": "failed", "reason": str(exc)}
        stored = 0
        for place in places[:10]:
            self._upsert_admin_provider_candidate(conn, candidate_id, row, place)
            stored += 1
        return {"status": "success", "stored": stored}

    def _upsert_admin_provider_candidate(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        row: NormalizedExpenseRow,
        place: PlaceCandidate,
    ) -> None:
        name_score = similarity(row.normalized_place_name, normalize_text(place.name))
        provider_address = place.road_address or place.address
        address_score = similarity(row.normalized_address, normalize_address(provider_address)) if row.normalized_address else 0.0
        conn.execute(
            """
            INSERT INTO place_verifications
              (candidate_id, provider, provider_place_id, provider_place_name,
               provider_category, provider_address, provider_road_address,
               normalized_provider_name, normalized_provider_address,
               longitude, latitude, name_similarity, address_similarity,
               is_name_match, is_address_match, is_coordinate_valid, is_category_valid,
               verification_status, verification_reason, raw_response_json)
            VALUES (?, 'naver', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'ambiguous',
                    'ADMIN_PROVIDER_REFRESH', ?)
            ON CONFLICT(candidate_id, provider, provider_place_id) DO UPDATE SET
              provider_place_name = excluded.provider_place_name,
              provider_category = excluded.provider_category,
              provider_address = excluded.provider_address,
              provider_road_address = excluded.provider_road_address,
              normalized_provider_name = excluded.normalized_provider_name,
              normalized_provider_address = excluded.normalized_provider_address,
              longitude = excluded.longitude,
              latitude = excluded.latitude,
              name_similarity = excluded.name_similarity,
              address_similarity = excluded.address_similarity,
              is_name_match = excluded.is_name_match,
              is_address_match = excluded.is_address_match,
              is_coordinate_valid = excluded.is_coordinate_valid,
              is_category_valid = excluded.is_category_valid,
              verification_status = excluded.verification_status,
              verification_reason = excluded.verification_reason,
              raw_response_json = excluded.raw_response_json,
              verified_at = CURRENT_TIMESTAMP
            """,
            (
                candidate_id,
                place.provider_place_id,
                place.name,
                place.category,
                place.address,
                place.road_address,
                normalize_text(place.name),
                normalize_address(provider_address),
                place.longitude,
                place.latitude,
                name_score,
                address_score,
                1 if name_score >= 0.78 else 0,
                1 if address_score >= 0.72 else 0,
                1 if place.category in {"restaurant", "cafe", "bar"} else 0,
                safe_json_dumps({"source": "admin_provider_refresh"}),
            ),
        )

    def _unlink_candidate_from_restaurants(self, conn: sqlite3.Connection, candidate_id: int) -> None:
        restaurant_ids = [
            int(row["restaurant_id"])
            for row in conn.execute(
                "SELECT DISTINCT restaurant_id FROM restaurant_expense_links WHERE candidate_id = ?",
                (candidate_id,),
            )
        ]
        conn.execute("DELETE FROM restaurant_expense_links WHERE candidate_id = ?", (candidate_id,))
        for restaurant_id in restaurant_ids:
            link_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM restaurant_expense_links WHERE restaurant_id = ?",
                    (restaurant_id,),
                ).fetchone()["count"]
            )
            if link_count == 0:
                conn.execute(
                    """
                    UPDATE restaurants
                    SET map_exposure_status = 'hidden',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (utc_now(), restaurant_id),
                )

    def _set_candidate_approved(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        context: RequestContext,
        reviewer_note: str,
    ) -> None:
        now = utc_now()
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET status = 'verified',
                verification_status = 'success',
                manual_review_status = 'approved',
                rejection_reason = NULL,
                review_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reviewer_note, now, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO manual_review_tasks
              (candidate_id, status, reason, reviewer_note, reviewed_by, reviewed_at)
            VALUES (?, 'approved', 'admin_state_update', ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
              status = 'approved',
              reason = 'admin_state_update',
              reviewer_note = excluded.reviewer_note,
              reviewed_by = excluded.reviewed_by,
              reviewed_at = excluded.reviewed_at
            """,
            (candidate_id, reviewer_note, context.actor_id, now),
        )

    def _set_candidate_rejected(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        context: RequestContext,
        rejection_reason: str,
        reviewer_note: str,
    ) -> None:
        reason = re.sub(r"[^A-Za-z0-9_,-]+", "_", rejection_reason or "manual_reject").strip("_")
        reason = reason or "manual_reject"
        now = utc_now()
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET status = 'rejected',
                verification_status = 'rejected',
                manual_review_status = 'rejected',
                rejection_reason = ?,
                review_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reason, reviewer_note, now, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO manual_review_tasks
              (candidate_id, status, reason, reviewer_note, reviewed_by, reviewed_at)
            VALUES (?, 'rejected', ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
              status = 'rejected',
              reason = excluded.reason,
              reviewer_note = excluded.reviewer_note,
              reviewed_by = excluded.reviewed_by,
              reviewed_at = excluded.reviewed_at
            """,
            (candidate_id, reason, reviewer_note, context.actor_id, now),
        )

    def _set_candidate_needs_review(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        context: RequestContext,
        reviewer_note: str,
    ) -> None:
        now = utc_now()
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET status = 'needs_review',
                verification_status = 'ambiguous',
                manual_review_status = 'pending',
                rejection_reason = NULL,
                review_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (reviewer_note, now, candidate_id),
        )
        conn.execute(
            """
            INSERT INTO manual_review_tasks
              (candidate_id, status, reason, reviewer_note, reviewed_by, reviewed_at)
            VALUES (?, 'pending', 'admin_state_update', ?, NULL, NULL)
            ON CONFLICT(candidate_id) DO UPDATE SET
              status = 'pending',
              reason = 'admin_state_update',
              reviewer_note = excluded.reviewer_note,
              reviewed_by = NULL,
              reviewed_at = NULL
            """,
            (candidate_id, reviewer_note),
        )

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
                    WHERE job_name IN (
                      'daily_busan_city_expense_v1',
                      'verify_collected',
                      'verify_pending',
                      'alias_memory_recheck',
                      'purpose_rule_recheck'
                    )
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

    def ops_logs(self, plan_id: int | None = None, limit: int = 100) -> dict[str, Any]:
        capped_limit = max(1, min(int(limit or 100), 300))
        with self.database.session() as conn:
            plans = [
                self._collection_plan_payload(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM collection_plans
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (50,),
                )
            ]
            selected_plan_id = plan_id or (plans[0]["id"] if plans else None)
            document_rows: list[dict[str, Any]] = []
            if selected_plan_id:
                document_rows = [
                    self._collection_document_payload(row)
                    for row in conn.execute(
                        """
                        SELECT
                          id,
                          plan_id,
                          source_title,
                          department_name,
                          published_at,
                          status,
                          rows_seen,
                          rows_inserted,
                          attempts,
                          error_message,
                          source_url,
                          raw_document_id,
                          batch_job_id,
                          metadata_json,
                          updated_at
                        FROM collection_plan_documents
                        WHERE plan_id = ?
                        ORDER BY
                          CASE status
                            WHEN 'failed' THEN 0
                            WHEN 'processing' THEN 1
                            WHEN 'pending' THEN 2
                            WHEN 'collected' THEN 3
                            WHEN 'duplicate' THEN 4
                            ELSE 5
                          END,
                          published_at DESC,
                          id ASC
                        LIMIT ?
                        """,
                        (selected_plan_id, capped_limit),
                    )
                ]
            progress = self._collection_progress(conn, selected_plan_id)
            batches = [
                self._batch_payload(row)
                for row in conn.execute(
                    """
                    SELECT *
                    FROM batch_jobs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                )
            ]
            api_calls = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT provider, endpoint, status_code, duration_ms, success,
                           error_message, called_at
                    FROM api_call_logs
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                )
            ]
            dlq = [
                {
                    **dict(row),
                    "payload": safe_json_loads(row["payload_json"], {}),
                }
                for row in conn.execute(
                    """
                    SELECT id, batch_job_id, stage, payload_json, error_message,
                           retry_count, status, created_at, resolved_at
                    FROM dead_letter_queue
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (capped_limit,),
                )
            ]
        return {
            "plans": plans,
            "selected_plan_id": selected_plan_id,
            "progress": progress,
            "documents": document_rows,
            "batches": batches,
            "api_calls": api_calls,
            "dlq": dlq,
        }

    def collection_progress(self, start_date: str, end_date: str) -> dict[str, Any]:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise AppError(400, "start_date and end_date must use YYYY-MM-DD") from exc
        if start > end:
            raise AppError(400, "start_date must not be after end_date")

        with self.database.session() as conn:
            rows = conn.execute(
                """
                SELECT
                  cpd.id,
                  cpd.source_url,
                  cpd.source_title,
                  cpd.department_name,
                  cpd.published_at,
                  cpd.status,
                  cp.id AS plan_id,
                  cp.source_key
                FROM collection_plan_documents cpd
                JOIN collection_plans cp ON cp.id = cpd.plan_id
                WHERE cpd.published_at >= ?
                  AND cpd.published_at <= ?
                ORDER BY cp.id DESC, cpd.id DESC
                """,
                (start_date, end_date),
            ).fetchall()
            latest_by_url: dict[str, sqlite3.Row] = {}
            for row in rows:
                latest_by_url.setdefault(str(row["source_url"]), row)
            documents = list(latest_by_url.values())

            institution_groups: dict[str, dict[str, int]] = {}
            priority_groups: dict[str, dict[str, int]] = {}
            source_configs = {
                str(row["source_key"]): safe_json_loads(row["config_json"], {})
                for row in conn.execute(
                    "SELECT source_key, config_json FROM source_registry"
                )
            }
            for row in documents:
                institution_label = self._collection_progress_label(
                    str(row["source_title"] or ""),
                    str(row["department_name"] or ""),
                )
                self._add_collection_progress_status(
                    institution_groups,
                    institution_label,
                    str(row["status"]),
                )
                config = source_configs.get(str(row["source_key"]), {})
                priority = int(config.get("priority") or 99)
                group_label = str(config.get("group_label") or row["source_key"])
                self._add_collection_progress_status(
                    priority_groups,
                    f"{priority}순위 · {group_label}",
                    str(row["status"]),
                )

        return {
            "start_date": start_date,
            "end_date": end_date,
            "document_count": len(documents),
            "plan_count": len({int(row["plan_id"]) for row in documents}),
            "by_institution": self._collection_progress_items(institution_groups),
            "by_priority": self._collection_progress_items(priority_groups),
        }

    def collection_dashboard(self, start_date: str, end_date: str) -> dict[str, Any]:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise AppError(400, "start_date and end_date must use YYYY-MM-DD") from exc
        if start > end:
            raise AppError(400, "start_date must not be after end_date")

        with self.database.session() as conn:
            source_rows = conn.execute(
                """
                SELECT
                  sr.source_key,
                  sr.config_json,
                  i.name AS institution_name
                FROM source_registry sr
                JOIN institutions i ON i.id = sr.institution_id
                ORDER BY i.name
                """
            ).fetchall()
            source_configs: dict[str, dict[str, Any]] = {}
            groups: dict[int, dict[str, Any]] = {}
            for row in source_rows:
                config = safe_json_loads(row["config_json"], {})
                priority = int(config.get("priority") or 99)
                source_configs[str(row["source_key"])] = config
                group = groups.setdefault(
                    priority,
                    {
                        "priority": priority,
                        "group_key": str(config.get("group_key") or "unknown"),
                        "label": str(config.get("group_label") or "미분류"),
                        "source_count": 0,
                        "ready_count": 0,
                        "documents": self._empty_progress_counts(),
                        "configured_institutions": [],
                        "document_institutions": {},
                    },
                )
                group["source_count"] += 1
                if str(config.get("status") or "") == "crawl_target_ready":
                    group["ready_count"] += 1
                group["configured_institutions"].append(
                    {
                        "label": str(row["institution_name"]),
                        "status": str(config.get("status") or "unknown"),
                    }
                )

            rows = conn.execute(
                """
                SELECT
                  cpd.source_url,
                  cpd.source_title,
                  cpd.department_name,
                  cpd.published_at,
                  cpd.status,
                  cp.id AS plan_id,
                  cp.source_key
                FROM collection_plan_documents cpd
                JOIN collection_plans cp ON cp.id = cpd.plan_id
                WHERE cpd.published_at >= ?
                  AND cpd.published_at <= ?
                ORDER BY cp.id DESC, cpd.id DESC
                """,
                (start_date, end_date),
            ).fetchall()
            latest_by_url: dict[str, sqlite3.Row] = {}
            for row in rows:
                latest_by_url.setdefault(str(row["source_url"]), row)
            documents = list(latest_by_url.values())
            for row in documents:
                config = source_configs.get(str(row["source_key"]), {})
                priority = int(config.get("priority") or 99)
                group = groups.setdefault(
                    priority,
                    {
                        "priority": priority,
                        "group_key": str(config.get("group_key") or "unknown"),
                        "label": str(config.get("group_label") or row["source_key"]),
                        "source_count": 0,
                        "ready_count": 0,
                        "documents": self._empty_progress_counts(),
                        "configured_institutions": [],
                        "document_institutions": {},
                    },
                )
                self._increment_progress_counts(group["documents"], str(row["status"]))
                institution_label = self._collection_progress_label(
                    str(row["source_title"] or ""),
                    str(row["department_name"] or ""),
                )
                institution_counts = group["document_institutions"].setdefault(
                    institution_label,
                    self._empty_progress_counts(),
                )
                self._increment_progress_counts(institution_counts, str(row["status"]))

            candidate_counts = conn.execute(
                """
                SELECT
                  COUNT(*) AS total_count,
                  SUM(CASE WHEN c.verification_status = 'not_requested' THEN 1 ELSE 0 END) AS pending_count,
                  SUM(CASE WHEN c.status = 'verified' THEN 1 ELSE 0 END) AS approved_count,
                  SUM(
                    CASE
                      WHEN c.status = 'needs_review'
                       AND c.verification_status <> 'not_requested'
                      THEN 1 ELSE 0
                    END
                  ) AS review_count,
                  SUM(CASE WHEN c.status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
                FROM restaurant_candidates c
                JOIN expense_records er ON er.id = c.expense_record_id
                WHERE er.used_date >= ?
                  AND er.used_date <= ?
                """,
                (start_date, end_date),
            ).fetchone()
            expense_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM expense_records
                    WHERE used_date >= ?
                      AND used_date <= ?
                    """,
                    (start_date, end_date),
                ).fetchone()["count"]
                or 0
            )

        priorities: list[dict[str, Any]] = []
        for priority, group in sorted(groups.items()):
            document_counts = group.pop("documents")
            configured = group.pop("configured_institutions")
            collected_institutions = group.pop("document_institutions")
            if collected_institutions:
                institutions = [
                    {
                        **self._collection_progress_item(label, counts),
                        "status": "collected",
                    }
                    for label, counts in sorted(
                        collected_institutions.items(),
                        key=lambda item: (-item[1]["total_count"], item[0]),
                    )
                ]
            else:
                institutions = [
                    {
                        "label": item["label"],
                        "total": 0,
                        "processed": 0,
                        "pending": 0,
                        "processing": 0,
                        "collected": 0,
                        "duplicate": 0,
                        "failed": 0,
                        "percent": 0.0,
                        "status": item["status"],
                    }
                    for item in configured
                ]
            priorities.append(
                {
                    **group,
                    **self._collection_progress_item(group["label"], document_counts),
                    "institutions": institutions,
                }
            )

        verification = {
            "pending": int(candidate_counts["pending_count"] or 0),
            "approved": int(candidate_counts["approved_count"] or 0),
            "needs_review": int(candidate_counts["review_count"] or 0),
            "rejected": int(candidate_counts["rejected_count"] or 0),
        }
        verification_total = sum(verification.values())
        return {
            "start_date": start_date,
            "end_date": end_date,
            "city": {
                "name": "부산광역시",
                "document_count": len(documents),
                "expense_count": expense_count,
                "candidate_count": int(candidate_counts["total_count"] or 0),
                "verification_total": verification_total,
                "verification": verification,
            },
            "priorities": priorities,
        }

    def _empty_progress_counts(self) -> dict[str, int]:
        return {
            "total_count": 0,
            "pending_count": 0,
            "processing_count": 0,
            "collected_count": 0,
            "duplicate_count": 0,
            "failed_count": 0,
        }

    def _increment_progress_counts(self, counts: dict[str, int], status: str) -> None:
        counts["total_count"] += 1
        status_key = f"{status}_count"
        if status_key in counts:
            counts[status_key] += 1

    def _add_collection_progress_status(
        self,
        groups: dict[str, dict[str, int]],
        label: str,
        status: str,
    ) -> None:
        counts = groups.setdefault(label, self._empty_progress_counts())
        self._increment_progress_counts(counts, status)

    def _collection_progress_items(
        self,
        groups: dict[str, dict[str, int]],
    ) -> list[dict[str, Any]]:
        return [
            self._collection_progress_item(label, counts)
            for label, counts in sorted(
                groups.items(),
                key=lambda item: (-item[1]["total_count"], item[0]),
            )
        ]

    def _collection_progress(
        self,
        conn: sqlite3.Connection,
        plan_id: int | None,
    ) -> dict[str, Any]:
        if not plan_id:
            return {"by_institution": [], "by_priority": []}
        plan = conn.execute(
            "SELECT source_key FROM collection_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if plan is None:
            return {"by_institution": [], "by_priority": []}

        rows = conn.execute(
            """
            SELECT
              source_title,
              department_name,
              status
            FROM collection_plan_documents
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchall()
        institution_groups: dict[str, dict[str, int]] = {}
        for row in rows:
            label = self._collection_progress_label(
                str(row["source_title"] or ""),
                str(row["department_name"] or ""),
            )
            self._add_collection_progress_status(
                institution_groups,
                label,
                str(row["status"]),
            )
        by_institution = self._collection_progress_items(institution_groups)

        totals = conn.execute(
            """
            SELECT
              COUNT(*) AS total_count,
              SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
              SUM(CASE WHEN status = 'processing' THEN 1 ELSE 0 END) AS processing_count,
              SUM(CASE WHEN status = 'collected' THEN 1 ELSE 0 END) AS collected_count,
              SUM(CASE WHEN status = 'duplicate' THEN 1 ELSE 0 END) AS duplicate_count,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count
            FROM collection_plan_documents
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        source = conn.execute(
            "SELECT config_json FROM source_registry WHERE source_key = ?",
            (plan["source_key"],),
        ).fetchone()
        config = safe_json_loads(source["config_json"], {}) if source else {}
        priority = int(config.get("priority") or 99)
        group_label = str(config.get("group_label") or plan["source_key"])
        by_priority = []
        if int(totals["total_count"] or 0):
            by_priority.append(
                self._collection_progress_item(
                    f"{priority}순위 · {group_label}",
                    totals,
                )
            )
        return {
            "by_institution": by_institution,
            "by_priority": by_priority,
        }

    def _collection_progress_item(
        self,
        label: str,
        row: sqlite3.Row | dict[str, int],
    ) -> dict[str, Any]:
        total = int(row["total_count"] or 0)
        pending = int(row["pending_count"] or 0)
        processing = int(row["processing_count"] or 0)
        collected = int(row["collected_count"] or 0)
        duplicate = int(row["duplicate_count"] or 0)
        failed = int(row["failed_count"] or 0)
        processed = max(0, total - pending - processing)
        return {
            "label": label,
            "total": total,
            "processed": processed,
            "pending": pending,
            "processing": processing,
            "collected": collected,
            "duplicate": duplicate,
            "failed": failed,
            "percent": round(processed * 100 / total, 1) if total else 0.0,
        }

    def _collection_progress_label(self, source_title: str, department_name: str) -> str:
        text = re.sub(r"\s+", " ", department_name).strip()
        if not text:
            return source_title.strip() or "기관 미상"
        text = re.sub(
            r"^(?:\d{4}년(?:도)?\s*)?(?:제?\s*\d+\s*분기\s*)?업무추진비"
            r"(?:\s+사용)?\s*(?:집행)?\s*내역(?:\s*\([^)]*\))?\s*",
            "",
            text,
        )
        if ")" in text and ">" in text:
            text = text.split(")", 1)[1].strip()
        parts = [part.strip(" -)") for part in text.split(">") if part.strip(" -)")]
        if not parts:
            return source_title.strip() or "기관 미상"
        if parts[0] == "합의제행정기관" and len(parts) > 1:
            label = parts[1]
        else:
            label = parts[0]
        return {
            "시도의회": "부산광역시의회",
            "자치경찰위원회": "부산광역시 자치경찰위원회",
        }.get(label, label)

    def _latest_food_verification(self, conn: sqlite3.Connection, candidate_id: int) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT *
            FROM place_verifications
            WHERE candidate_id = ?
              AND provider_place_id IS NOT NULL
              AND provider_category IN ('restaurant', 'cafe', 'bar')
              AND is_coordinate_valid = 1
            ORDER BY
              CASE verification_status WHEN 'success' THEN 0 ELSE 1 END,
              name_similarity DESC,
              address_similarity DESC,
              verified_at DESC,
              id DESC
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()

    def _food_verification_by_id(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        verification_id: int,
    ) -> sqlite3.Row | None:
        verification = conn.execute(
            """
            SELECT *
            FROM place_verifications
            WHERE id = ?
              AND candidate_id = ?
              AND provider_place_id IS NOT NULL
              AND provider_category IN ('restaurant', 'cafe', 'bar')
              AND is_coordinate_valid = 1
            """,
            (verification_id, candidate_id),
        ).fetchone()
        if verification is None:
            raise AppError(400, "selected provider candidate is not approvable")
        return verification

    def _provider_candidates(self, conn: sqlite3.Connection, candidate_id: int) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT
                  id AS verification_id,
                  provider_place_id,
                  provider_place_name,
                  provider_category,
                  provider_address,
                  provider_road_address,
                  longitude,
                  latitude,
                  name_similarity,
                  address_similarity,
                  verification_status,
                  verification_reason,
                  CASE
                    WHEN provider_category IN ('restaurant', 'cafe', 'bar')
                     AND is_coordinate_valid = 1
                    THEN 1 ELSE 0
                  END AS is_approvable
                FROM place_verifications
                WHERE candidate_id = ?
                  AND provider_place_id IS NOT NULL
                  AND is_coordinate_valid = 1
                ORDER BY
                  is_approvable DESC,
                  name_similarity DESC,
                  address_similarity DESC,
                  verified_at DESC,
                  id DESC
                LIMIT 10
                """,
                (candidate_id,),
            )
        ]

    def _restaurant_from_provider_verification(
        self,
        conn: sqlite3.Connection,
        candidate: sqlite3.Row,
        verification: sqlite3.Row,
    ) -> int:
        address = verification["provider_address"] or verification["provider_road_address"] or "주소 미확인"
        road_address = verification["provider_road_address"] or verification["provider_address"] or ""
        existing = conn.execute(
            "SELECT id FROM restaurants WHERE naver_place_id = ?",
            (verification["provider_place_id"],),
        ).fetchone()
        if existing is not None:
            restaurant_id = int(existing["id"])
            conn.execute(
                """
                UPDATE restaurants
                SET place_verification_id = ?,
                    canonical_name = ?,
                    normalized_name = ?,
                    major_category = ?,
                    address = ?,
                    road_address = ?,
                    normalized_address = ?,
                    longitude = ?,
                    latitude = ?,
                    verification_status = 'success',
                    map_exposure_status = 'visible',
                    last_verified_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    verification["id"],
                    verification["provider_place_name"],
                    normalize_text(verification["provider_place_name"]),
                    verification["provider_category"],
                    address,
                    road_address,
                    normalize_address(address),
                    float(verification["longitude"]),
                    float(verification["latitude"]),
                    utc_now(),
                    utc_now(),
                    restaurant_id,
                ),
            )
            return restaurant_id
        cur = conn.execute(
            """
            INSERT INTO restaurants
              (region_id, place_verification_id, canonical_name, normalized_name,
               major_category, naver_place_id, address, road_address, normalized_address,
               longitude, latitude, verification_status, map_exposure_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', 'visible')
            """,
            (
                candidate["region_id"],
                verification["id"],
                verification["provider_place_name"],
                normalize_text(verification["provider_place_name"]),
                verification["provider_category"],
                verification["provider_place_id"],
                address,
                road_address,
                normalize_address(address),
                float(verification["longitude"]),
                float(verification["latitude"]),
            ),
        )
        return int(cur.lastrowid)

    def _create_manual_restaurant_without_provider(self, conn: sqlite3.Connection, candidate: sqlite3.Row) -> int:
        place_name = candidate_effective_place_name(candidate)
        address = candidate_effective_address(candidate)
        normalized_address = candidate_effective_normalized_address(candidate)
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
                place_name,
                candidate_effective_normalized_place_name(candidate),
                candidate_effective_major_category(candidate),
                f"manual-{candidate['id']}",
                address or "주소 미확인",
                address,
                normalized_address,
                129.0756416,
                35.1795543,
            ),
        )
        return int(cur.lastrowid)

    def _batch_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["summary"] = safe_json_loads(payload.pop("summary_json"), {})
        return payload

    def _collection_plan_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["summary"] = safe_json_loads(payload.pop("summary_json"), {})
        return payload

    def _collection_document_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        metadata = safe_json_loads(payload.pop("metadata_json"), {})
        diagnostics = metadata.get("attachment_diagnostics") or []
        payload["metadata"] = metadata
        payload["failure_type"] = ""
        if diagnostics:
            payload["failure_type"] = ", ".join(
                sorted({str(item.get("detected_type") or "unknown") for item in diagnostics})
            )
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
        name = row.get("name") or row.get("canonical_name")
        query = naver_map_query(name, row.get("road_address") or row["address"])
        return {
            "id": row["id"],
            "name": name,
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
            "naver_map_query": query,
            "naver_map_url": naver_map_url(query),
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
        reviewer_note: str = "",
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
        restaurant = conn.execute("SELECT canonical_name FROM restaurants WHERE id = ?", (restaurant_id,)).fetchone()
        alias_texts = [candidate["original_place_name"], candidate_effective_place_name(candidate)]
        if restaurant is not None:
            alias_texts.append(restaurant["canonical_name"])
        remember_aliases(conn, restaurant_id, alias_texts, source=f"manual_{status}", confidence=1.0)
        note = str(reviewer_note or "").strip()
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET status = 'verified',
                manual_review_status = 'approved',
                verification_status = 'success',
                review_note = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (note, utc_now(), candidate["id"]),
        )
        conn.execute(
            """
            UPDATE manual_review_tasks
            SET status = ?,
                reviewer_note = ?,
                reviewed_by = ?,
                reviewed_at = ?
            WHERE id = ?
            """,
            (status, note, context.actor_id, utc_now(), task["id"]),
        )
        after = {**after, "reviewer_note": note}
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
