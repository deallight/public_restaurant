from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.config import Settings
from app.http_server import (
    SESSION_COOKIE,
    PublicRestaurantApplication,
    _trusted_proxy_client_ip,
    make_handler,
)
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

    def get_text(self, path: str, headers: dict[str, str] | None = None) -> str:
        request = Request(f"{self.base_url}{path}", headers=headers or {})
        with urlopen(request, timeout=5) as response:
            return response.read().decode("utf-8")

    def test_public_admin_and_ops_api(self) -> None:
        admin_headers = self.session_headers("admin")
        index = urlopen(f"{self.base_url}/", timeout=5).read().decode("utf-8")
        self.assertIn("공기밥", index)
        self.assertIn("filter-index", index)
        self.assertIn("visit-filter-toggle", index)
        self.assertIn("visit-filter-label", index)
        self.assertIn('data-filter="min_visit_count"', index)
        self.assertIn('data-filter="saved"', index)
        self.assertIn("관심 가게", index)
        self.assertIn("10회 이상", index)
        admin = self.get_text("/admin", admin_headers)
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
        collection = self.get_text("/admin/collection", admin_headers)
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
        parsing = self.get_text("/admin/parsing", admin_headers)
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
        workflow_alias = self.get_text("/admin/workflow", admin_headers)
        self.assertIn("파싱 작업판", workflow_alias)
        workflow_js = urlopen(f"{self.base_url}/static/workflow.js", timeout=5).read().decode("utf-8")
        self.assertIn("publicRestaurant.workflow.localLogs", workflow_js)
        self.assertIn("sessionStorage", workflow_js)
        self.assertIn("retry-parse-failed", workflow_js)
        documents_admin = self.get_text("/admin/documents", admin_headers)
        self.assertIn("기관별 수집 문서", documents_admin)
        self.assertIn("document-board-list", documents_admin)
        documents = self.get_json(
            "/admin/documents/data?start_date=2026-01-01&end_date=2026-12-31",
            headers=admin_headers,
        )
        self.assertEqual(documents["total"], 0)
        review_admin = self.get_text("/admin/review", admin_headers)
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
        review_results = self.get_text("/admin/review/results", admin_headers)
        self.assertIn("검증 결과 수정", review_results)
        self.assertIn("승인·수동검토·반려 항목", review_results)
        self.assertIn("review-queue", review_results)
        progress = self.get_json(
            "/ops/collection-progress?start_date=2026-01-01&end_date=2026-12-31",
            headers=admin_headers,
        )
        self.assertEqual(progress["start_date"], "2026-01-01")
        self.assertEqual(progress["end_date"], "2026-12-31")
        self.assertEqual(progress["document_count"], 0)
        dashboard = self.get_json(
            "/ops/dashboard?start_date=2026-01-01&end_date=2026-12-31",
            headers=admin_headers,
        )
        self.assertEqual(dashboard["city"]["name"], "부산광역시")
        self.assertEqual(len(dashboard["priorities"]), 8)

        batch = self.post_json("/ops/run-daily", headers=admin_headers)
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
        queue = self.get_json("/review", headers=admin_headers)
        self.assertEqual(len(queue["reviews"]), 1)
        self.assertEqual(queue["total"], 1)
        self.assertEqual(queue["limit"], 50)
        self.assertIn("institution_name", queue["reviews"][0])
        self.assertIn("source_title", queue["reviews"][0])
        self.assertIn("payment_method", queue["reviews"][0])
        verify = self.post_json("/ops/verify-pending", {"limit": 10}, admin_headers)
        self.assertEqual(verify["status"], "success")
        self.assertEqual(verify["summary"]["rows_seen"], 1)
        self.assertEqual(verify["summary"]["rows_processed"], 1)
        batch_lookup = self.get_json(f"/ops/batches/{batch['batch_id']}", admin_headers)
        self.assertEqual(batch_lookup["status"], "success")
        sources = self.get_json("/ops/sources", admin_headers)
        self.assertGreaterEqual(sources["summary"]["source_count"], 1)
        self.assertEqual(sources["groups"][0]["group_key"], "busan_city_core")
        verification = self.get_json("/ops/verification-status", admin_headers)
        self.assertIn("integrations", verification)
        self.assertIn("missing_env", verification)
        self.assertIn("overview", verification)
        self.assertIn("NAVER_SEARCH_CLIENT_ID", verification["missing_env"]["naver_search"])
        self.assertIn("pending_reviews", verification["overview"]["counts"])
        api_usage = self.get_json("/ops/api-usage", admin_headers)
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
        progress = self.get_json("/ops/verification-progress", admin_headers)
        self.assertIn("active", progress)
        self.assertIn("items", progress)
        candidates = self.get_json(
            "/admin/candidates?start_date=2026-01-01&end_date=2026-12-31&limit=5",
            headers=admin_headers,
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
        admin_headers = self.session_headers("admin")
        self.post_json("/ops/run-daily", headers=admin_headers)
        restaurant_id = self.get_json("/api/map/restaurants")["restaurants"][0]["id"]

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
        self.assertEqual(public_detail["restaurant_images"], [])
        self.assertIsNone(public_detail["restaurant_image"])

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

    def test_admin_can_list_and_manage_accounts_without_provider_subject(self) -> None:
        admin_headers = self.session_headers("admin")
        with self.app.database.session() as conn:
            member = conn.execute(
                "INSERT INTO users (display_name) VALUES ('실제 가입 회원')"
            )
            member_id = int(member.lastrowid)
            conn.execute(
                """
                INSERT INTO oauth_accounts
                  (user_id, provider, provider_subject, display_name, last_login_at)
                VALUES (?, 'naver', 'private-subject', '실제 가입 회원', CURRENT_TIMESTAMP)
                """,
                (member_id,),
            )

        page = self.get_text("/admin/accounts", admin_headers)
        self.assertIn("가입 계정 관리", page)
        self.assertIn("admin_accounts.js", page)
        token_match = re.search(r'data-action-token="([^"]+)"', page)
        self.assertIsNotNone(token_match)
        action_token = token_match.group(1)

        payload = self.get_json(
            "/admin/accounts/data?q=%EC%8B%A4%EC%A0%9C",
            admin_headers,
        )
        self.assertEqual(payload["total"], 1)
        self.assertNotIn("provider_subject", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["accounts"][0]["provider"], "naver")

        with self.assertRaises(HTTPError) as missing_token_error:
            self.post_json(
                f"/admin/accounts/{member_id}",
                {"role": "admin", "status": "active"},
                headers=admin_headers,
            )
        self.assertEqual(missing_token_error.exception.code, 403)
        missing_token_error.exception.close()

        updated = self.post_json(
            f"/admin/accounts/{member_id}",
            {"role": "admin", "status": "active", "action_token": action_token},
            headers=admin_headers,
        )
        self.assertEqual(updated["role"], "admin")
        self.assertEqual(updated["status"], "active")

        with self.assertRaises(HTTPError) as confirmation_error:
            self.post_json(
                f"/admin/accounts/{member_id}/delete",
                {"confirmation": "삭제", "action_token": action_token},
                headers=admin_headers,
            )
        self.assertEqual(confirmation_error.exception.code, 400)
        confirmation_error.exception.close()

        deleted = self.post_json(
            f"/admin/accounts/{member_id}/delete",
            {"confirmation": "계정 삭제", "action_token": action_token},
            headers=admin_headers,
        )
        self.assertEqual(deleted["result"], "deleted")
        with self.app.database.session() as conn:
            account = conn.execute(
                "SELECT display_name, role, status FROM users WHERE id = ?",
                (member_id,),
            ).fetchone()
            oauth_count = conn.execute(
                "SELECT COUNT(*) AS count FROM oauth_accounts WHERE user_id = ?",
                (member_id,),
            ).fetchone()["count"]
        self.assertEqual(account["display_name"], "탈퇴한 사용자")
        self.assertEqual(account["role"], "user")
        self.assertEqual(account["status"], "deleted")
        self.assertEqual(int(oauth_count), 0)

    def test_admin_routes_require_admin_role(self) -> None:
        admin_headers = self.session_headers("admin")
        self.post_json("/ops/run-daily", headers=admin_headers)
        restaurant_id = self.get_json("/api/map/restaurants")["restaurants"][0]["id"]
        user_headers = self.session_headers("user")
        protected_paths = [
            "/admin",
            "/ops/sources",
            "/review",
            "/admin/photos",
            "/admin/accounts",
            "/admin/accounts/data",
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

        protected_posts = [
            "/ops/run-daily",
            "/review/1/reject",
            "/admin/accounts/1/merge",
            "/admin/accounts/1/delete",
        ]
        for path in protected_posts:
            for headers, expected_status in [({}, 401), (user_headers, 403)]:
                with self.subTest(path=path, expected_status=expected_status):
                    with self.assertRaises(HTTPError) as raised:
                        self.post_json(path, headers=headers)
                    self.assertEqual(raised.exception.code, expected_status)
                    raised.exception.close()

    def test_review_api_rate_limit_status_code(self) -> None:
        admin_headers = self.session_headers("admin")
        self.post_json("/ops/run-daily", headers=admin_headers)
        restaurant_id = self.get_json("/api/map/restaurants")["restaurants"][0]["id"]
        first_client = {"X-Real-IP": "203.0.113.10"}
        with self.assertRaises(HTTPError) as missing_consent:
            self.post_json(
                f"/api/restaurants/{restaurant_id}/reviews",
                {
                    "rating": 5,
                    "body": "동의 없는 리뷰",
                    "reviewer_label": "방문자",
                },
                headers=first_client,
            )
        self.assertEqual(missing_consent.exception.code, 400)
        missing_consent.exception.close()

        for index in range(3):
            review = self.post_json(
                f"/api/restaurants/{restaurant_id}/reviews",
                {
                    "rating": 5,
                    "body": f"좋아요 {index}",
                    "reviewer_label": "방문자",
                    "ai_processing_consent": True,
                },
                headers=first_client,
            )
            self.assertEqual(review["status"], "visible")
        with self.assertRaises(HTTPError) as raised:
            self.post_json(
                f"/api/restaurants/{restaurant_id}/reviews",
                {
                    "rating": 5,
                    "body": "반복 리뷰",
                    "reviewer_label": "방문자",
                    "ai_processing_consent": True,
                },
                headers=first_client,
            )
        self.assertEqual(raised.exception.code, 429)
        raised.exception.close()

        different_client = self.post_json(
            f"/api/restaurants/{restaurant_id}/reviews",
            {
                "rating": 5,
                "body": "다른 사용자 리뷰",
                "reviewer_label": "방문자",
                "ai_processing_consent": True,
            },
            headers={"X-Real-IP": "203.0.113.11"},
        )
        self.assertEqual(different_client["status"], "visible")
        with self.app.database.session() as conn:
            audit = conn.execute(
                """
                SELECT after_json FROM decision_audit_logs
                WHERE action = 'review_create'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        self.assertTrue(json.loads(audit["after_json"])["ai_processing_consent"])

    def test_forwarded_ip_is_only_trusted_from_loopback_proxy(self) -> None:
        self.assertEqual(
            _trusted_proxy_client_ip("127.0.0.1", "203.0.113.20"),
            "203.0.113.20",
        )
        self.assertEqual(
            _trusted_proxy_client_ip("::1", "2001:db8::20"),
            "2001:db8::20",
        )
        self.assertEqual(
            _trusted_proxy_client_ip("198.51.100.20", "203.0.113.20"),
            "198.51.100.20",
        )
        self.assertEqual(
            _trusted_proxy_client_ip("127.0.0.1", "not-an-ip"),
            "127.0.0.1",
        )


if __name__ == "__main__":
    unittest.main()
