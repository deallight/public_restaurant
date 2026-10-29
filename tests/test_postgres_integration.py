from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from app.config import Settings
from app.database import Database
from app.http_server import (
    SESSION_COOKIE,
    SESSION_CSRF_ACTION,
    PublicRestaurantApplication,
    make_handler,
)
from app.pipeline import DailyPipeline
from app.services import RequestContext, RestaurantService
from database.migrations.m0003_user_interactions import VERSION as USER_INTERACTIONS_VERSION
from database.migrations.m0005_auth_security import VERSION as AUTH_SECURITY_VERSION
from database.migrations.m0006_oauth_lifecycle import VERSION as OAUTH_LIFECYCLE_VERSION
from database.migrations.m0007_oauth_lifecycle_hardening import (
    VERSION as OAUTH_LIFECYCLE_HARDENING_VERSION,
)
from database.migrations.m0008_notices import VERSION as NOTICES_VERSION
from tests.postgres_test import postgres_test_url, reset_postgres_database


class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = Database(postgres_test_url())
        cls.db.initialize()

    def setUp(self) -> None:
        reset_postgres_database(self.db)

    def test_fixture_public_admin_and_review_contracts(self) -> None:
        DailyPipeline(self.db).run()
        service = RestaurantService(self.db, review_rate_limit_per_hour=3)
        restaurants = service.list_map_restaurants()
        self.assertEqual(len(restaurants), 3)
        self.assertTrue(service.rankings())
        self.assertEqual(service.search("광안리")[0]["name"], "광안리커피")
        detail = service.get_restaurant(int(restaurants[0]["id"]))
        self.assertIn("reviews", detail)
        self.assertIn("visits", detail)
        self.assertIn("ai_summary", detail)
        self.assertEqual(detail["ai_summary"]["next_summary_review_count"], 5)
        self.assertNotIn("department_name", detail["visits"][0])
        review = service.add_review(
            int(restaurants[0]["id"]), 5, "통합 테스트 리뷰", "테스터", RequestContext(ip="127.0.0.2")
        )
        self.assertEqual(review["rating"], 5)
        self.assertIn("summary", service.source_registry())
        self.assertIn("priorities", service.collection_dashboard("2026-01-01", "2026-12-31"))
        self.assertIn("groups", service.admin_candidates(limit=100))
        queue = service.admin_review_queue()
        self.assertEqual(len(queue), 1)
        rejected = service.reject_candidate(
            int(queue[0]["review_id"]),
            RequestContext(actor_id="postgres-test"),
            reason="POSTGRES_INTEGRATION_TEST",
        )
        self.assertEqual(rejected["result"], "rejected")
        self.assertEqual(service.admin_review_queue(), [])

    def test_existing_baseline_applies_user_interactions_migration(self) -> None:
        with self.db.session() as conn:
            conn.execute("DROP TABLE IF EXISTS restaurant_user_images CASCADE")
            conn.execute("DROP TABLE IF EXISTS review_reactions CASCADE")
            conn.execute(
                "DELETE FROM app_schema_migrations WHERE version = ?",
                (USER_INTERACTIONS_VERSION,),
            )

        try:
            issues = self.db.schema_issues()
            self.assertTrue(any(USER_INTERACTIONS_VERSION in issue for issue in issues))
            self.assertTrue(any("restaurant_user_images.id" in issue for issue in issues))
            self.assertTrue(any("review_reactions.review_id" in issue for issue in issues))

            self.db.initialize()

            self.assertEqual(self.db.schema_issues(), [])
            with self.db.session() as conn:
                migration = conn.execute(
                    "SELECT description FROM app_schema_migrations WHERE version = ?",
                    (USER_INTERACTIONS_VERSION,),
                ).fetchone()
            self.assertIsNotNone(migration)
        finally:
            self.db.initialize()

    def test_existing_database_applies_notices_migration(self) -> None:
        with self.db.session() as conn:
            conn.execute("DROP TABLE IF EXISTS notices CASCADE")
            conn.execute(
                "DELETE FROM app_schema_migrations WHERE version = ?",
                (NOTICES_VERSION,),
            )
        try:
            issues = self.db.schema_issues()
            self.assertTrue(any(NOTICES_VERSION in issue for issue in issues))
            self.assertTrue(any("notices.id" in issue for issue in issues))
            self.db.initialize()
            self.assertEqual(self.db.schema_issues(), [])
        finally:
            self.db.initialize()

    def test_account_delete_contract(self) -> None:
        DailyPipeline(self.db).run()
        service = RestaurantService(self.db, review_rate_limit_per_hour=10)
        account = service.upsert_oauth_account(
            "naver", "postgres-delete-user", "PostgreSQL 탈퇴 사용자"
        )
        user_id = int(account["user"]["id"])
        restaurant_id = int(service.list_map_restaurants()[0]["id"])
        context = RequestContext(user_id=user_id, ip="203.0.113.92")
        service.save_restaurant(user_id, restaurant_id)
        review = service.add_review(
            restaurant_id,
            4,
            "PostgreSQL 탈퇴 익명화 테스트",
            "PostgreSQL 탈퇴 사용자",
            context,
        )
        report = service.report_review(int(review["id"]), "spam_or_abuse", context)

        result = service.delete_account(user_id)

        self.assertEqual(result["result"], "deletion_pending")
        local_delete = service.process_next_oauth_lifecycle_job("postgres-test-worker")
        self.assertEqual(local_delete["status"], "awaiting_token")
        self.assertTrue(local_delete["local_delete_completed"])
        with self.db.session() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            oauth_count = conn.execute(
                "SELECT COUNT(*) AS count FROM oauth_accounts WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            saved_count = conn.execute(
                "SELECT COUNT(*) AS count FROM user_saved_restaurants WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            retained_review = conn.execute(
                "SELECT * FROM restaurant_reviews WHERE id = ?", (review["id"],)
            ).fetchone()
            retained_report = conn.execute(
                "SELECT * FROM review_reports WHERE id = ?", (report["id"],)
            ).fetchone()
        self.assertEqual(user["status"], "deleted")
        self.assertEqual(user["display_name"], "탈퇴한 사용자")
        self.assertEqual(oauth_count, 0)
        self.assertEqual(saved_count, 0)
        self.assertIsNone(retained_review["user_id"])
        self.assertEqual(retained_review["reviewer_label"], "탈퇴한 사용자")
        self.assertIsNone(retained_review["ip_hash"])
        self.assertIsNone(retained_report["reporter_user_id"])
        self.assertIsNone(retained_report["reporter_ip_hash"])

    def test_existing_database_applies_auth_security_migration(self) -> None:
        with self.db.session() as conn:
            conn.execute("DROP TABLE IF EXISTS action_tokens CASCADE")
            conn.execute("DROP TABLE IF EXISTS auth_sessions CASCADE")
            conn.execute("DROP TABLE IF EXISTS security_rate_limit_events CASCADE")
            conn.execute(
                "DELETE FROM app_schema_migrations WHERE version = ?",
                (AUTH_SECURITY_VERSION,),
            )

        try:
            issues = self.db.schema_issues()
            self.assertTrue(any(AUTH_SECURITY_VERSION in issue for issue in issues))
            self.assertTrue(any("auth_sessions.session_hash" in issue for issue in issues))
            self.assertTrue(any("action_tokens.token_hash" in issue for issue in issues))

            self.db.initialize()

            self.assertEqual(self.db.schema_issues(), [])
            with self.db.session() as conn:
                migration = conn.execute(
                    "SELECT description FROM app_schema_migrations WHERE version = ?",
                    (AUTH_SECURITY_VERSION,),
                ).fetchone()
            self.assertIsNotNone(migration)
        finally:
            self.db.initialize()

    def test_existing_database_applies_oauth_lifecycle_migration(self) -> None:
        with self.db.session() as conn:
            conn.execute("DROP TABLE IF EXISTS oauth_disconnect_events CASCADE")
            conn.execute("DROP TABLE IF EXISTS oauth_lifecycle_jobs CASCADE")
            conn.execute("DROP TABLE IF EXISTS oauth_flow_intents CASCADE")
            conn.execute(
                "ALTER TABLE oauth_accounts DROP COLUMN IF EXISTS refresh_token_ciphertext"
            )
            conn.execute(
                "ALTER TABLE oauth_accounts DROP COLUMN IF EXISTS token_updated_at"
            )
            conn.execute(
                "DELETE FROM app_schema_migrations WHERE version = ?",
                (OAUTH_LIFECYCLE_VERSION,),
            )

        try:
            issues = self.db.schema_issues()
            self.assertTrue(any(OAUTH_LIFECYCLE_VERSION in issue for issue in issues))
            self.assertTrue(
                any("oauth_accounts.refresh_token_ciphertext" in issue for issue in issues)
            )
            self.assertTrue(any("oauth_lifecycle_jobs.id" in issue for issue in issues))

            self.db.initialize()

            self.assertEqual(self.db.schema_issues(), [])
            with self.db.session() as conn:
                migration = conn.execute(
                    "SELECT description FROM app_schema_migrations WHERE version = ?",
                    (OAUTH_LIFECYCLE_VERSION,),
                ).fetchone()
            self.assertIsNotNone(migration)
        finally:
            self.db.initialize()

    def test_existing_oauth_lifecycle_constraint_is_hardened(self) -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                ALTER TABLE oauth_lifecycle_jobs
                  DROP CONSTRAINT IF EXISTS oauth_lifecycle_jobs_source_check
                """
            )
            conn.execute(
                """
                ALTER TABLE oauth_lifecycle_jobs
                  ADD CONSTRAINT oauth_lifecycle_jobs_source_check
                  CHECK (source IN ('user', 'admin', 'provider_callback'))
                """
            )
            conn.execute(
                "DELETE FROM app_schema_migrations WHERE version = ?",
                (OAUTH_LIFECYCLE_HARDENING_VERSION,),
            )

        try:
            issues = self.db.schema_issues()
            self.assertTrue(
                any(OAUTH_LIFECYCLE_HARDENING_VERSION in issue for issue in issues)
            )
            self.db.initialize()
            self.assertEqual(self.db.schema_issues(), [])
            with self.db.session() as conn:
                migration = conn.execute(
                    "SELECT description FROM app_schema_migrations WHERE version = ?",
                    (OAUTH_LIFECYCLE_HARDENING_VERSION,),
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO oauth_lifecycle_jobs
                      (user_id, provider, subject_hmac, source, status,
                       next_attempt_at, created_at, updated_at)
                    VALUES (NULL, 'naver', 'migration-test-subject',
                            'provider_callback_related', 'awaiting_token',
                            NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                )
            self.assertIsNotNone(migration)
        finally:
            self.db.initialize()

    def test_postgres_http_and_major_screen_contracts(self) -> None:
        settings = Settings(
            database_url=postgres_test_url(),
            app_env="development",
            port=0,
            session_secret="postgres-integration-test-session-secret",
        )
        app = PublicRestaurantApplication(settings)
        with app.database.session() as conn:
            admin_user = conn.execute(
                "INSERT INTO users (display_name, role) VALUES (?, ?)",
                ("PostgreSQL admin test user", "admin"),
            )
        admin_session = app.issue_session(int(admin_user.lastrowid))
        admin_headers = {
            "Cookie": f"{SESSION_COOKIE}={admin_session}",
            "X-CSRF-Token": app.session_codec.issue_action_token(
                admin_session,
                SESSION_CSRF_ACTION,
            ),
        }
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            for path, marker in [
                ("/", "공기밥"),
                ("/admin", "관리자 대시보드"),
                ("/admin/collection", "수집 작업판"),
                ("/admin/parsing", "파싱 작업판"),
                ("/admin/review", "검토 작업판"),
                ("/admin/documents", "기관별 수집 문서"),
                ("/admin/map-issues", "승인 항목 점검"),
                ("/admin/logs", "수집/검증 로그"),
            ]:
                request = Request(f"{base_url}{path}", headers=admin_headers)
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(marker, response.read().decode("utf-8"))
            request = Request(
                f"{base_url}/ops/run-daily",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json", **admin_headers},
            )
            with urlopen(request, timeout=10) as response:
                self.assertEqual(json.load(response)["status"], "success")
            for path, key in [
                ("/api/map/restaurants", "restaurants"),
                ("/api/rankings", "rankings"),
                ("/review", "reviews"),
                ("/admin/candidates?limit=10", "groups"),
                ("/ops/sources", "summary"),
            ]:
                with self.subTest(path=path):
                    headers = {"Accept": "application/json"}
                    if path.startswith(("/admin", "/ops", "/review")):
                        headers.update(admin_headers)
                    request = Request(
                        f"{base_url}{path}", headers=headers
                    )
                    with urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn(key, json.loads(response.read().decode("utf-8")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_schema_drift_fails_closed(self) -> None:
        self.assertEqual(self.db.schema_issues(), [])
        try:
            with self.db.session() as conn:
                conn.execute(
                    "ALTER TABLE restaurants ALTER COLUMN longitude TYPE REAL"
                )
            with self.assertRaisesRegex(RuntimeError, "schema is incompatible"):
                self.db.verify_schema()
            self.assertTrue(
                any("restaurants.longitude" in issue for issue in self.db.schema_issues())
            )
        finally:
            with self.db.session() as conn:
                conn.execute(
                    "ALTER TABLE restaurants ALTER COLUMN longitude TYPE DOUBLE PRECISION"
                )


if __name__ == "__main__":
    unittest.main()
