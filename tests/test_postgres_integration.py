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
from app.http_server import PublicRestaurantApplication, make_handler
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

    def test_postgres_http_and_major_screen_contracts(self) -> None:
        settings = Settings(
            db_path=Path("unused.db"),
            database_url=TEST_DATABASE_URL,
            app_env="development",
            port=0,
        )
        app = PublicRestaurantApplication(settings)
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
                with urlopen(f"{base_url}{path}", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(marker, response.read().decode("utf-8"))
            request = Request(
                f"{base_url}/ops/run-daily",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json"},
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
                    request = Request(
                        f"{base_url}{path}", headers={"Accept": "application/json"}
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
