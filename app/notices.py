from __future__ import annotations

from typing import Any

from .database import Database
from .services import AppError
from .utils import safe_json_dumps, utc_now


PAGE_SIZE = 20
MAX_PAGE = 10_000
TITLE_MAX_LENGTH = 120
BODY_MAX_LENGTH = 10_000
NOTICE_KINDS = {"general", "release"}


class NoticeService:
    def __init__(self, database: Database):
        self.database = database

    # Public reads always filter drafts and deleted records in SQL.
    def public_list(self, page: int) -> dict[str, Any]:
        offset = self._offset(page)
        with self.database.session() as conn:
            rows = conn.execute(
                """
                SELECT id, kind, title, published_at
                FROM notices
                WHERE status = 'published' AND deleted_at IS NULL
                ORDER BY published_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (PAGE_SIZE + 1, offset),
            ).fetchall()
        return {"items": rows[:PAGE_SIZE], "page": page, "has_next": len(rows) > PAGE_SIZE}

    def public_detail(self, notice_id: int) -> dict[str, Any]:
        with self.database.session() as conn:
            row = conn.execute(
                """
                SELECT id, kind, title, body, published_at
                FROM notices
                WHERE id = ? AND status = 'published' AND deleted_at IS NULL
                """,
                (notice_id,),
            ).fetchone()
        if row is None:
            raise AppError(404, "not found")
        return dict(row)

    # Administrator reads keep deleted content inside the protected area.
    def admin_list(self, actor_user_id: int, *, deleted: bool, page: int) -> dict[str, Any]:
        offset = self._offset(page)
        deleted_filter = "deleted_at IS NOT NULL" if deleted else "deleted_at IS NULL"
        with self.database.session() as conn:
            self._require_admin(conn, actor_user_id)
            rows = conn.execute(
                f"""
                SELECT id, kind, title, status, revision, updated_at, published_at, deleted_at
                FROM notices
                WHERE {deleted_filter}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (PAGE_SIZE + 1, offset),
            ).fetchall()
        return {"items": rows[:PAGE_SIZE], "page": page, "has_next": len(rows) > PAGE_SIZE}

    def admin_detail(self, actor_user_id: int, notice_id: int) -> dict[str, Any]:
        with self.database.session() as conn:
            self._require_admin(conn, actor_user_id)
            row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
        if row is None:
            raise AppError(404, "not found")
        return dict(row)

    # Changes require an active administrator and lock both actor and record.
    def create(self, actor_user_id: int, *, kind: str, title: str, body: str) -> dict[str, Any]:
        safe_kind, safe_title, safe_body = self._validate_content(kind, title, body)
        now = utc_now()
        with self.database.session() as conn:
            self._require_admin(conn, actor_user_id, lock=True)
            cursor = conn.execute(
                """
                INSERT INTO notices (kind, title, body, status, revision, created_at, updated_at)
                VALUES (?, ?, ?, 'draft', 1, ?, ?)
                """,
                (safe_kind, safe_title, safe_body, now, now),
            )
            notice_id = int(cursor.lastrowid)
            self._audit(conn, actor_user_id, notice_id, "create", None, "draft", 1)
            row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
        return dict(row)

    def update(
        self,
        actor_user_id: int,
        notice_id: int,
        expected_revision: int,
        *,
        kind: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        safe_kind, safe_title, safe_body = self._validate_content(kind, title, body)
        with self.database.session() as conn:
            self._require_admin(conn, actor_user_id, lock=True)
            current = self._locked_notice(conn, notice_id, expected_revision)
            if current["deleted_at"] is not None or current["status"] != "draft":
                raise AppError(409, "notice must be an active draft")
            conn.execute(
                """
                UPDATE notices
                SET kind = ?, title = ?, body = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (safe_kind, safe_title, safe_body, utc_now(), notice_id),
            )
            self._audit(conn, actor_user_id, notice_id, "update", "draft", "draft", expected_revision + 1)
            row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
        return dict(row)

    def transition(
        self,
        actor_user_id: int,
        notice_id: int,
        expected_revision: int,
        action: str,
    ) -> dict[str, Any]:
        if action not in {"publish", "unpublish", "delete", "restore"}:
            raise AppError(404, "not found")
        with self.database.session() as conn:
            self._require_admin(conn, actor_user_id, lock=True)
            current = self._locked_notice(conn, notice_id, expected_revision)
            status = str(current["status"])
            deleted = current["deleted_at"] is not None
            if action == "publish" and (deleted or status != "draft"):
                raise AppError(409, "notice must be an active draft")
            if action == "unpublish" and (deleted or status != "published"):
                raise AppError(409, "notice must be published")
            if action == "delete" and deleted:
                raise AppError(409, "notice is already deleted")
            if action == "restore" and not deleted:
                raise AppError(409, "notice is not deleted")

            now = utc_now()
            next_status = "published" if action == "publish" else "draft"
            conn.execute(
                """
                UPDATE notices
                SET status = ?, published_at = ?, deleted_at = ?, deleted_by = ?,
                    updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (
                    next_status,
                    now if action == "publish" else None,
                    now if action == "delete" else None,
                    actor_user_id if action == "delete" else None,
                    now,
                    notice_id,
                ),
            )
            self._audit(conn, actor_user_id, notice_id, action, status, next_status, expected_revision + 1)
            row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
        return dict(row)

    def _require_admin(self, conn: Any, actor_user_id: int, *, lock: bool = False) -> None:
        suffix = " FOR UPDATE" if lock else ""
        actor = conn.execute(
            "SELECT id FROM users WHERE id = ? AND role = 'admin' AND status = 'active'" + suffix,
            (actor_user_id,),
        ).fetchone()
        if actor is None:
            raise AppError(403, "admin access required")

    def _locked_notice(self, conn: Any, notice_id: int, expected_revision: int) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM notices WHERE id = ? FOR UPDATE", (notice_id,)).fetchone()
        if row is None:
            raise AppError(404, "not found")
        if expected_revision < 1 or int(row["revision"]) != expected_revision:
            raise AppError(409, "notice changed; reload before continuing")
        return dict(row)

    def _validate_content(self, kind: str, title: str, body: str) -> tuple[str, str, str]:
        if not all(isinstance(value, str) for value in (kind, title, body)):
            raise AppError(400, "notice fields must be text")
        kind = kind.strip()
        title = title.strip()
        body = body.strip()
        if kind not in NOTICE_KINDS:
            raise AppError(400, "invalid notice kind")
        if not title or len(title) > TITLE_MAX_LENGTH:
            raise AppError(400, "notice title must be 1-120 characters")
        if not body or len(body) > BODY_MAX_LENGTH:
            raise AppError(400, "notice body must be 1-10000 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in title):
            raise AppError(400, "notice title contains control characters")
        if any(
            (ord(character) < 32 and character not in "\n\r\t")
            or ord(character) == 127
            for character in body
        ):
            raise AppError(400, "notice body contains control characters")
        return kind, title, body

    def _offset(self, page: int) -> int:
        if page < 1 or page > MAX_PAGE:
            raise AppError(400, "invalid page")
        return (page - 1) * PAGE_SIZE

    def _audit(
        self,
        conn: Any,
        actor_user_id: int,
        notice_id: int,
        action: str,
        previous_status: str | None,
        next_status: str,
        revision: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO decision_audit_logs
              (actor_type, actor_id, action, target_type, target_id, before_json, after_json)
            VALUES ('admin', ?, ?, 'notice', ?, ?, ?)
            """,
            (
                str(actor_user_id),
                f"notice_{action}",
                notice_id,
                safe_json_dumps({"status": previous_status}),
                safe_json_dumps({"status": next_status, "revision": revision}),
            ),
        )
