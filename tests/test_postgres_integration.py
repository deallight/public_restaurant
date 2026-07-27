from __future__ import annotations

import importlib.util
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from app.config import Settings
from app.database import Database, SQLITE_TABLES
from app.http_server import SESSION_COOKIE, PublicRestaurantApplication, make_handler
from app.pipeline import DailyPipeline
from app.services import RequestContext, RestaurantService


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@unittest.skipUnless(
    TEST_DATABASE_URL.startswith(("postgresql://", "postgres://"))
    and importlib.util.find_spec("psycopg") is not None,
    "set TEST_DATABASE_URL to an isolated PostgreSQL test database",
)
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db = Database(TEST_DATABASE_URL)
        with cls.db.session() as conn:
            name = conn.execute("SELECT current_database() AS name").fetchone()["name"]
            if name == "public_restaurant" or "test" not in str(name).lower():
                raise unittest.SkipTest("TEST_DATABASE_URL database name must contain 'test'")
        cls.db.initialize()

    def setUp(self) -> None:
        with self.db.session() as conn:
            for table in SQLITE_TABLES:
                conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
        self.db.initialize()

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

        self.assertEqual(result["result"], "deleted")
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

    def test_postgres_http_and_major_screen_contracts(self) -> None:
        settings = Settings(
            db_path=Path("unused.db"),
            database_url=TEST_DATABASE_URL,
            app_env="development",
            port=0,
        )
        app = PublicRestaurantApplication(settings)
        with app.database.session() as conn:
            admin_user = conn.execute(
                "INSERT INTO users (display_name, role) VALUES (?, ?) RETURNING id",
                ("PostgreSQL admin test user", "admin"),
            ).fetchone()
        admin_headers = {
            "Cookie": f"{SESSION_COOKIE}={app.issue_session(int(admin_user['id']))}"
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
