from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import Settings
from app.http_server import SESSION_COOKIE, PublicRestaurantApplication, make_handler
from http.server import ThreadingHTTPServer


class HttpServerTests(unittest.TestCase):
    PNG_1X1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
    )

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        settings = Settings(
            db_path=Path(self.tmp.name) / "test.db",
            port=0,
            session_secret="http-server-test-session-secret",
        )
        self.app = PublicRestaurantApplication(settings)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def get_json(self, path: str, headers: dict[str, str] | None = None) -> dict:
        request_headers = {"Accept": "application/json"}
        request_headers.update(headers or {})
        with urlopen(Request(f"{self.base_url}{path}", headers=request_headers), timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_json(
        self,
        path: str,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        data = json.dumps(payload or {}).encode("utf-8")
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method="POST",
            headers=request_headers,
        )
        with urlopen(request, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def post_multipart(
        self,
        path: str,
        fields: dict[str, str],
        filename: str,
        content: bytes,
        content_type: str = "image/png",
        headers: dict[str, str] | None = None,
    ) -> dict:
        boundary = "----public-restaurant-test-boundary"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        parts.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(),
                f"Content-Type: {content_type}\r\n\r\n".encode(),
                content,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        request_headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        request_headers.update(headers or {})
        request = Request(
            f"{self.base_url}{path}",
            data=b"".join(parts),
            method="POST",
            headers=request_headers,
        )
        with urlopen(request, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def session_headers(self, role: str) -> dict[str, str]:
        with self.app.database.session() as conn:
            user = conn.execute(
                "INSERT INTO users (display_name, role) VALUES (?, ?)",
                (f"{role} test user", role),
            )
        token = self.app.issue_session(int(user.lastrowid))
        return {"Cookie": f"{SESSION_COOKIE}={token}"}

    def test_public_admin_and_ops_api(self) -> None:
        index = urlopen(f"{self.base_url}/", timeout=5).read().decode("utf-8")
        self.assertIn("공기밥", index)
        self.assertIn("filter-index", index)
        self.assertIn("visit-filter-toggle", index)
        self.assertIn("visit-filter-label", index)
        self.assertIn('data-filter="min_visit_count"', index)
        self.assertIn('data-filter="saved"', index)
        self.assertIn("관심 가게", index)
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
        self.assertIn("api-usage-section", admin)
        self.assertIn("API 연동 및 무료 사용량", admin)
        self.assertIn("api-usage-grid", admin)
        self.assertNotIn("부산시 수집 작업", admin)
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
        restaurant_detail = self.get_json(
            f"/api/restaurants/{restaurants['restaurants'][0]['id']}"
        )
        self.assertEqual(len(restaurant_detail["visits"]), 1)
        self.assertEqual(
            set(restaurant_detail["ai_summary"]),
            {
                "text",
                "status",
                "summarized_review_count",
                "current_review_count",
                "next_summary_review_count",
                "refresh_pending",
                "last_generated_at",
            },
        )
        self.assertEqual(restaurant_detail["ai_summary"]["next_summary_review_count"], 5)
        self.assertEqual(
            set(restaurant_detail["visits"][0]),
            {"visited_at", "institution_name", "purpose"},
        )
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
        api_usage = self.get_json("/ops/api-usage")
        self.assertEqual(api_usage["connected_count"], 0)
        self.assertEqual(api_usage["integration_count"], 4)
        self.assertEqual(
            {item["key"] for item in api_usage["integrations"]},
            {"naver_place", "naver_image", "data_go_kr", "groq"},
        )
        self.assertTrue(
            all(item["state"] == "disconnected" for item in api_usage["integrations"])
        )
        self.assertTrue(
            all("인증 정보" in item["state_reason"] for item in api_usage["integrations"])
        )
        self.assertEqual(
            next(item for item in api_usage["integrations"] if item["key"] == "groq")["usage"]["limit"],
            1000,
        )
        progress = self.get_json("/ops/verification-progress")
        self.assertIn("active", progress)
        self.assertIn("items", progress)
        candidates = self.get_json(
            "/admin/candidates?start_date=2026-01-01&end_date=2026-12-31&limit=5"
        )
        self.assertIn("needs_review", candidates["groups"])

    def test_api_usage_warning_explains_timeout_reason(self) -> None:
        warning_app = PublicRestaurantApplication(
            Settings(
                db_path=Path(self.tmp.name) / "warning.db",
                data_go_kr_service_key="configured-for-test",
            )
        )
        with warning_app.database.session() as conn:
            conn.execute(
                """
                INSERT INTO api_call_logs
                  (provider, endpoint, request_hash, success, error_message)
                VALUES ('data_go_kr', 'food_permit_lookup', 'test-timeout', 0,
                        'The read operation timed out')
                """
            )

        status = warning_app.api_usage_status()
        permit = next(
            item for item in status["integrations"] if item["key"] == "data_go_kr"
        )

        self.assertEqual(permit["state"], "warning")
        self.assertEqual(permit["state_label"], "확인 필요")
        self.assertEqual(permit["state_reason"], "최근 호출 실패 · 응답 시간 초과")

    def test_admin_can_upload_edit_and_delete_restaurant_image(self) -> None:
        self.post_json("/ops/run-daily")
        restaurant_id = self.get_json("/api/map/restaurants")["restaurants"][0]["id"]
        admin_headers = self.session_headers("admin")

        admin_page = urlopen(
            Request(f"{self.base_url}/admin/photos", headers=admin_headers),
            timeout=5,
        ).read().decode("utf-8")
        self.assertIn("음식점 사진 관리", admin_page)
        self.assertIn("admin_restaurant_images.js", admin_page)
        restaurant_list = self.get_json("/admin/photos/restaurants", headers=admin_headers)
        self.assertEqual(len(restaurant_list["restaurants"]), 3)

        uploaded = self.post_multipart(
            f"/admin/photos/restaurants/{restaurant_id}/images",
            {"alt_text": "관리자 대표 사진", "sort_order": "1"},
            "대표.png",
            self.PNG_1X1,
            headers=admin_headers,
        )
        self.assertEqual(len(uploaded["images"]), 1)
        image = uploaded["images"][0]
        self.assertTrue(image["is_admin_image"])
        with urlopen(f"{self.base_url}{image['source_url']}", timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertEqual(response.read(), self.PNG_1X1)

        public_detail = self.get_json(f"/api/restaurants/{restaurant_id}")
        self.assertEqual(public_detail["restaurant_images"][0]["provider"], "admin_upload")

        updated = self.post_json(
            f"/admin/photos/restaurants/{restaurant_id}/images/{image['id']}",
            {"alt_text": "수정된 설명", "sort_order": 3},
            headers=admin_headers,
        )
        self.assertEqual(updated["images"][0]["alt_text"], "수정된 설명")
        self.assertEqual(updated["images"][0]["sort_order"], 3)

        removed = self.post_json(
            f"/admin/photos/restaurants/{restaurant_id}/images/{image['id']}/delete",
            headers=admin_headers,
        )
        self.assertEqual(removed["images"], [])

    def test_admin_photo_routes_require_admin_role(self) -> None:
        self.post_json("/ops/run-daily")
        restaurant_id = self.get_json("/api/map/restaurants")["restaurants"][0]["id"]
        user_headers = self.session_headers("user")
        protected_paths = [
            "/admin/photos",
            "/admin/photos/restaurants",
            f"/admin/photos/restaurants/{restaurant_id}",
        ]

        for path in protected_paths:
            for headers, expected_status in [({}, 401), (user_headers, 403)]:
                with self.subTest(path=path, expected_status=expected_status):
                    request = Request(
                        f"{self.base_url}{path}",
                        headers={"Accept": "application/json", **headers},
                    )
                    with self.assertRaises(HTTPError) as raised:
                        urlopen(request, timeout=5)
                    self.assertEqual(raised.exception.code, expected_status)
                    raised.exception.close()

        for headers, expected_status in [({}, 401), (user_headers, 403)]:
            with self.subTest(upload_status=expected_status):
                with self.assertRaises(HTTPError) as raised:
                    self.post_multipart(
                        f"/admin/photos/restaurants/{restaurant_id}/images",
                        {"alt_text": "blocked upload"},
                        "blocked.png",
                        self.PNG_1X1,
                        headers=headers,
                    )
                self.assertEqual(raised.exception.code, expected_status)
                raised.exception.close()

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
