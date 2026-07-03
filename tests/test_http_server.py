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
        self.assertIn("filter-index", index)
        self.assertIn("visit-filter-toggle", index)
        self.assertIn("visit-filter-label", index)
        self.assertIn('data-filter="min_visit_count"', index)
        self.assertIn("10회 이상", index)
        admin = urlopen(f"{self.base_url}/admin", timeout=5).read().decode("utf-8")
        self.assertNotIn("live-max-pages", admin)
        self.assertNotIn("live-max-documents", admin)
        self.assertIn("관리자 대시보드", admin)
        self.assertIn("수집 데이터 현황", admin)
        self.assertIn("dashboard-start-date", admin)
        self.assertIn("dashboard-end-date", admin)
        self.assertIn("priority-chart", admin)
        self.assertIn("source-section", admin)
        self.assertIn("collection-operations", admin)
        self.assertIn("/admin/collection", admin)
        self.assertIn("대시보드", admin)
        self.assertNotIn("workflow-steps", admin)
        self.assertNotIn("workflow-fetch-list", admin)
        collection = urlopen(f"{self.base_url}/admin/collection", timeout=5).read().decode("utf-8")
        self.assertIn("수집 작업판", collection)
        self.assertIn("workflow-start-date", collection)
        self.assertIn("workflow-end-date", collection)
        self.assertIn("workflow-steps", collection)
        self.assertIn("수집 완료", admin)
        self.assertIn("기간 내 게시물", collection)
        self.assertIn("상태 로그", collection)
        self.assertNotIn("검증 항목", collection)
        self.assertNotIn("workflow-candidate-rows", collection)
        self.assertNotIn("workflow-db-list", collection)
        self.assertIn("/admin/parsing", collection)
        self.assertIn("workflow-fetch-list", collection)
        self.assertIn("workflow-run-collection", collection)
        self.assertIn("수집 배치 크기", collection)
        self.assertNotIn("검증 배치 크기", collection)
        self.assertNotIn("parsing-run", collection)
        self.assertNotIn("review-queue", collection)
        parsing = urlopen(f"{self.base_url}/admin/parsing", timeout=5).read().decode("utf-8")
        self.assertIn("파싱 작업판", parsing)
        self.assertIn('data-workflow-mode="parsing"', parsing)
        self.assertIn("workflow-run-parse", parsing)
        self.assertIn("workflow-retry-parse", parsing)
        self.assertIn("workflow-document-rows", parsing)
        self.assertIn("파싱 배치 크기", parsing)
        self.assertNotIn("수집 배치 크기", parsing)
        self.assertNotIn("검증 배치 크기", parsing)
        self.assertIn("상태 로그", parsing)
        self.assertNotIn("workflow-candidate-rows", parsing)
        self.assertNotIn("검증 항목", parsing)
        self.assertIn("전체 파싱 상태", parsing)
        workflow_alias = urlopen(f"{self.base_url}/admin/workflow", timeout=5).read().decode("utf-8")
        self.assertIn("파싱 작업판", workflow_alias)
        workflow_js = urlopen(f"{self.base_url}/static/workflow.js", timeout=5).read().decode("utf-8")
        self.assertIn("publicRestaurant.workflow.localLogs", workflow_js)
        self.assertIn("sessionStorage", workflow_js)
        self.assertIn("retry-parse-failed", workflow_js)
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
        self.assertIn("검토 작업판", review_admin)
        self.assertIn("workflow-run-verification", review_admin)
        self.assertIn("검증 항목", review_admin)
        self.assertIn('<option value="pending">검증 대기</option>', review_admin)
        self.assertIn('<option value="needs_review">수동검토</option>', review_admin)
        self.assertIn("검증 배치 크기", review_admin)
        self.assertNotIn("수집 배치 크기", review_admin)
        self.assertNotIn("파싱 배치 크기", review_admin)
        self.assertIn("상태 로그", review_admin)
        self.assertNotIn("기간 내 게시물", review_admin)
        self.assertNotIn("workflow-document-rows", review_admin)
        self.assertNotIn("workflow-db-list", review_admin)
        review_results = urlopen(f"{self.base_url}/admin/review/results", timeout=5).read().decode("utf-8")
        self.assertIn("검증 결과 수정", review_results)
        self.assertIn("승인·수동검토·반려 항목", review_results)
        self.assertIn("review-queue", review_results)
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
        visit_filtered = self.get_json("/api/map/restaurants?min_visit_count=10")
        self.assertEqual(visit_filtered["restaurants"], [])
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
        progress = self.get_json("/ops/verification-progress")
        self.assertIn("active", progress)
        self.assertIn("items", progress)
        candidates = self.get_json(
            "/admin/candidates?start_date=2026-01-01&end_date=2026-12-31&limit=5"
        )
        self.assertIn("needs_review", candidates["groups"])

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
