from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import Settings
from app.http_server import PublicRestaurantApplication, make_handler
from http.server import ThreadingHTTPServer


class HttpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        settings = Settings(db_path=Path(self.tmp.name) / "test.db", port=0)
        app = PublicRestaurantApplication(settings)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def get_json(self, path: str) -> dict:
        with urlopen(Request(f"{self.base_url}{path}", headers={"Accept": "application/json"}), timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_public_admin_and_ops_api(self) -> None:
        index = urlopen(f"{self.base_url}/", timeout=5).read().decode("utf-8")
        self.assertIn("공공 맛집 지도", index)
        admin = urlopen(f"{self.base_url}/admin", timeout=5).read().decode("utf-8")
        self.assertNotIn("live-max-pages", admin)
        self.assertNotIn("live-max-documents", admin)
        self.assertIn("관리자 대시보드", admin)
        self.assertIn("dashboard-start-date", admin)
        self.assertIn("dashboard-end-date", admin)
        self.assertIn("priority-chart", admin)
        self.assertIn("수집 완료", admin)
        self.assertIn("검증 전", admin)
        self.assertNotIn("review-queue", admin)
        documents_admin = urlopen(
            f"{self.base_url}/admin/documents",
            timeout=5,
        ).read().decode("utf-8")
        self.assertIn("기관별 수집 문서", documents_admin)
        self.assertIn("document-board-list", documents_admin)
        documents = self.get_json(
            "/admin/documents/data?start_date=2026-01-01&end_date=2026-12-31"
        )
        self.assertEqual(documents["total"], 0)
        review_admin = urlopen(f"{self.base_url}/admin/review", timeout=5).read().decode("utf-8")
        self.assertIn("후보 데이터", review_admin)
        self.assertIn("review-queue", review_admin)
        progress = self.get_json(
            "/ops/collection-progress?start_date=2026-01-01&end_date=2026-12-31"
        )
        self.assertEqual(progress["start_date"], "2026-01-01")
        self.assertEqual(progress["end_date"], "2026-12-31")
        self.assertEqual(progress["document_count"], 0)
        dashboard = self.get_json(
            "/ops/dashboard?start_date=2026-01-01&end_date=2026-12-31"
        )
        self.assertEqual(dashboard["city"]["name"], "부산광역시")
        self.assertEqual(len(dashboard["priorities"]), 8)

        batch = self.post_json("/ops/run-daily")
        self.assertEqual(batch["status"], "success")
        restaurants = self.get_json("/api/map/restaurants")
        self.assertEqual(len(restaurants["restaurants"]), 3)
        ranking = self.get_json("/api/rankings?category=cafe")
        self.assertEqual(ranking["rankings"][0]["category"], "cafe")
        queue = self.get_json("/review")
        self.assertEqual(len(queue["reviews"]), 1)
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["limit"], 50)
        self.assertIn("institution_name", queue["reviews"][0])
        self.assertIn("source_title", queue["reviews"][0])
        self.assertIn("payment_method", queue["reviews"][0])
        verify = self.post_json("/ops/verify-pending", {"limit": 10})
        self.assertEqual(verify["status"], "success")
        self.assertEqual(verify["summary"]["rows_seen"], 1)
        self.assertEqual(verify["summary"]["rows_processed"], 1)
        batch_lookup = self.get_json(f"/ops/batches/{batch['batch_id']}")
        self.assertEqual(batch_lookup["status"], "success")
        sources = self.get_json("/ops/sources")
        self.assertGreaterEqual(sources["summary"]["source_count"], 1)
        self.assertEqual(sources["groups"][0]["group_key"], "busan_city_core")
        verification = self.get_json("/ops/verification-status")
        self.assertIn("integrations", verification)
        self.assertIn("missing_env", verification)
        self.assertIn("overview", verification)
        self.assertIn("NAVER_SEARCH_CLIENT_ID", verification["missing_env"]["naver_search"])
        self.assertIn("pending_reviews", verification["overview"]["counts"])

    def test_review_api_rate_limit_status_code(self) -> None:
        self.post_json("/ops/run-daily")
        restaurant_id = self.get_json("/api/map/restaurants")["restaurants"][0]["id"]
        for index in range(3):
            review = self.post_json(
                f"/api/restaurants/{restaurant_id}/reviews",
                {"rating": 5, "body": f"좋아요 {index}", "reviewer_label": "방문자"},
            )
            self.assertEqual(review["status"], "visible")
        with self.assertRaises(HTTPError) as raised:
            self.post_json(
                f"/api/restaurants/{restaurant_id}/reviews",
                {"rating": 5, "body": "반복 리뷰", "reviewer_label": "방문자"},
            )
        self.assertEqual(raised.exception.code, 429)
        raised.exception.close()


if __name__ == "__main__":
    unittest.main()
