from __future__ import annotations

import http.client
import json
import re
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from app.auth import SessionCodec
from app.config import Settings
from app.http_server import (
    OAUTH_PROVIDER_COOKIE,
    OAUTH_RETURN_COOKIE,
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
    PublicRestaurantApplication,
    make_handler,
)
from app.integrations import OAuthProfile
from app.services import RequestContext
from app.views import login_index, public_index


class SessionCodecTests(unittest.TestCase):
    def test_session_round_trip_tampering_and_expiry(self) -> None:
        codec = SessionCodec("test-session-secret", max_age_seconds=60)
        token = codec.issue(42, now=1_000)

        self.assertEqual(codec.verify(token, now=1_059), 42)
        self.assertIsNone(codec.verify(f"{token}x", now=1_059))
        self.assertIsNone(codec.verify(token, now=1_060))

    def test_action_token_is_bound_to_session_and_action(self) -> None:
        codec = SessionCodec("test-session-secret", max_age_seconds=60)
        session = codec.issue(42, now=1_000)
        other_session = codec.issue(42, now=1_000)
        with patch("app.auth.time.time", return_value=1_030):
            token = codec.issue_action_token(session, "account-delete")

            self.assertTrue(codec.verify_action_token(session, "account-delete", token))
            self.assertFalse(codec.verify_action_token(session, "other-action", token))
            self.assertFalse(codec.verify_action_token(other_session, "account-delete", token))
            self.assertFalse(codec.verify_action_token(session, "account-delete", f"{token}x"))


class AuthViewTests(unittest.TestCase):
    def test_login_page_uses_single_naver_entry_for_login_and_signup(self) -> None:
        login = login_index(naver_configured=True)
        unavailable = login_index(naver_configured=False)

        self.assertIn("부산의 맛집을 더 가깝게", login)
        self.assertIn("공공기관의 실제 방문 기록", login)
        self.assertIn("/auth/naver/start?return_to=/", login)
        self.assertNotIn("/signup", login)
        self.assertNotIn("auth-tabs", login)
        self.assertNotIn("auth-flow-badge", login)
        self.assertIn("네이버 로그인 준비 중", unavailable)
        self.assertNotIn("demo-naver", login)
        self.assertIn('href="/privacy"', login)
        self.assertIn('href="/terms"', login)

    def test_public_page_shows_session_aware_account_action(self) -> None:
        anonymous = public_index("")
        signed_in = public_index("", current_user={"display_name": "테스터"})

        self.assertIn('href="/login"', anonymous)
        self.assertIn("테스터", signed_in)
        self.assertIn('action="/auth/logout"', signed_in)


class NaverAuthHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        settings = Settings(
            db_path=Path(self.tmp.name) / "auth.db",
            port=0,
            naver_login_client_id="test-client-id",
            naver_login_client_secret="test-client-secret",
            naver_login_redirect_uri="http://127.0.0.1/auth/callback/naver",
            privacy_contact_email="privacy@example.test",
        )
        self.app = PublicRestaurantApplication(settings)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, http.client.HTTPMessage, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def response_cookies(self, headers: http.client.HTTPMessage) -> dict[str, str]:
        values: dict[str, str] = {}
        for raw_cookie in headers.get_all("Set-Cookie", []):
            parsed = SimpleCookie()
            parsed.load(raw_cookie)
            values.update({name: morsel.value for name, morsel in parsed.items()})
        return values

    def cookie_header(self, values: dict[str, str]) -> str:
        return "; ".join(f"{name}={value}" for name, value in values.items())

    def test_signup_path_redirects_to_single_login_page(self) -> None:
        status, headers, _ = self.request("GET", "/signup")

        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/login")

    def test_privacy_and_terms_are_public(self) -> None:
        for path, marker in [
            ("/privacy", "개인정보처리방침"),
            ("/terms", "이용약관"),
        ]:
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                page = body.decode("utf-8")
                self.assertEqual(status, 200)
                self.assertIn(marker, page)
                self.assertIn("privacy@example.test", page)

    def test_naver_login_creates_missing_user_session_and_logout_clears_cookie(self) -> None:
        start_status, start_headers, _ = self.request(
            "GET", "/auth/naver/start?return_to=/"
        )
        self.assertEqual(start_status, 302)
        self.assertTrue(start_headers["Location"].startswith("https://nid.naver.com/oauth2.0/authorize?"))
        oauth_cookies = self.response_cookies(start_headers)
        self.assertEqual(oauth_cookies[OAUTH_PROVIDER_COOKIE], "naver")
        self.assertEqual(oauth_cookies[OAUTH_RETURN_COOKIE], "%2F")
        state = oauth_cookies[OAUTH_STATE_COOKIE]

        with patch(
            "app.http_server.NaverLoginClient.exchange_code",
            return_value=OAuthProfile("naver", "naver-user-1", "네이버 테스터"),
        ) as exchange_code:
            callback_status, callback_headers, _ = self.request(
                "GET",
                f"/auth/callback/naver?code=test-code&state={state}",
                headers={"Cookie": self.cookie_header(oauth_cookies)},
            )

        self.assertEqual(callback_status, 303)
        self.assertEqual(callback_headers["Location"], "/")
        exchange_code.assert_called_once_with("test-code", state)
        callback_cookies = self.response_cookies(callback_headers)
        session_cookie = callback_cookies[SESSION_COOKIE]

        session_status, _, session_body = self.request(
            "GET",
            "/auth/session",
            headers={"Cookie": f"{SESSION_COOKIE}={session_cookie}"},
        )
        self.assertEqual(session_status, 200)
        self.assertEqual(json.loads(session_body)["user"]["display_name"], "네이버 테스터")

        home_status, _, home_body = self.request(
            "GET", "/", headers={"Cookie": f"{SESSION_COOKIE}={session_cookie}"}
        )
        self.assertEqual(home_status, 200)
        self.assertIn("네이버 테스터", home_body.decode("utf-8"))

        logout_status, logout_headers, _ = self.request(
            "POST", "/auth/logout", headers={"Cookie": f"{SESSION_COOKIE}={session_cookie}"}
        )
        self.assertEqual(logout_status, 303)
        self.assertEqual(logout_headers["Location"], "/")
        self.assertIn("Max-Age=0", "\n".join(logout_headers.get_all("Set-Cookie", [])))

    def test_callback_rejects_mismatched_state(self) -> None:
        cookies = {
            OAUTH_STATE_COOKIE: "stored-state",
            OAUTH_RETURN_COOKIE: "%2F",
            OAUTH_PROVIDER_COOKIE: "naver",
        }

        with patch("app.http_server.NaverLoginClient.exchange_code") as exchange_code:
            status, headers, _ = self.request(
                "GET",
                "/auth/callback/naver?code=test-code&state=wrong-state",
                headers={"Cookie": self.cookie_header(cookies)},
            )

        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login?error=expired")
        exchange_code.assert_not_called()
        self.assertEqual(self.app.database.count("users"), 0)

    def test_my_page_requires_login_and_manages_saved_place_and_review(self) -> None:
        anonymous_status, anonymous_headers, _ = self.request("GET", "/mypage")
        self.assertEqual(anonymous_status, 302)
        self.assertEqual(anonymous_headers["Location"], "/login?return_to=/mypage")

        self.app.run_daily()
        account = self.app.service.upsert_oauth_account(
            "naver", "mypage-http-user", "마이페이지 테스터"
        )
        user_id = int(account["user"]["id"])
        session = self.app.issue_session(user_id)
        auth_headers = {
            "Cookie": f"{SESSION_COOKIE}={session}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        restaurant_id = int(self.app.service.list_map_restaurants()[0]["id"])

        save_status, _, save_body = self.request(
            "POST",
            f"/api/restaurants/{restaurant_id}/save",
            headers=auth_headers,
            body=b"{}",
        )
        self.assertEqual(save_status, 201)
        self.assertTrue(json.loads(save_body)["is_saved"])

        saved_filter_status, _, saved_filter_body = self.request(
            "GET",
            "/api/map/restaurants?saved_only=1",
            headers={
                "Cookie": f"{SESSION_COOKIE}={session}",
                "Accept": "application/json",
            },
        )
        self.assertEqual(saved_filter_status, 200)
        saved_restaurants = json.loads(saved_filter_body)["restaurants"]
        self.assertEqual([item["id"] for item in saved_restaurants], [restaurant_id])

        review_status, _, review_body = self.request(
            "POST",
            f"/api/restaurants/{restaurant_id}/reviews",
            headers=auth_headers,
            body=json.dumps(
                {
                    "rating": 5,
                    "body": "로그인 사용자의 마이페이지 리뷰",
                    "reviewer_label": "위조 이름",
                    "ai_processing_consent": True,
                }
            ).encode("utf-8"),
        )
        self.assertEqual(review_status, 201)
        review = json.loads(review_body)
        self.assertEqual(review["reviewer_label"], "마이페이지 테스터")

        page_status, _, page_body = self.request(
            "GET",
            "/mypage",
            headers={"Cookie": f"{SESSION_COOKIE}={session}"},
        )
        page = page_body.decode("utf-8")
        self.assertEqual(page_status, 200)
        self.assertIn("마이페이지 테스터님의 맛집 기록", page)
        self.assertIn("로그인 사용자의 마이페이지 리뷰", page)
        self.assertIn("저장 해제", page)

        delete_status, _, delete_body = self.request(
            "POST",
            f"/api/reviews/{review['id']}/delete",
            headers=auth_headers,
            body=b"{}",
        )
        self.assertEqual(delete_status, 200)
        self.assertEqual(json.loads(delete_body)["status"], "deleted")

    def test_account_delete_requires_csrf_and_confirmation_then_invalidates_session(self) -> None:
        self.app.run_daily()
        account = self.app.service.upsert_oauth_account(
            "naver", "delete-http-user", "탈퇴 테스터"
        )
        user_id = int(account["user"]["id"])
        session = self.app.issue_session(user_id)
        restaurant_id = int(self.app.service.list_map_restaurants()[0]["id"])
        context = RequestContext(
            user_id=user_id,
            ip="203.0.113.91",
            actor_id=f"user:{user_id}",
        )
        self.app.service.save_restaurant(user_id, restaurant_id)
        review = self.app.service.add_review(
            restaurant_id,
            5,
            "탈퇴 후 익명화할 리뷰",
            "탈퇴 테스터",
            context,
        )
        self.app.service.report_review(int(review["id"]), "spam_or_abuse", context)

        page_status, _, page_body = self.request(
            "GET",
            "/mypage",
            headers={"Cookie": f"{SESSION_COOKIE}={session}"},
        )
        self.assertEqual(page_status, 200)
        token_match = re.search(
            r'name="action_token" value="([^"]+)"',
            page_body.decode("utf-8"),
        )
        self.assertIsNotNone(token_match)
        action_token = token_match.group(1)

        invalid_status, _, _ = self.request(
            "POST",
            "/account/delete",
            headers={
                "Cookie": f"{SESSION_COOKIE}={session}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=urlencode(
                {"action_token": "invalid", "confirmation": "계정 삭제"}
            ).encode("utf-8"),
        )
        self.assertEqual(invalid_status, 403)
        self.assertIsNotNone(self.app.service.get_active_user(user_id))

        wrong_status, wrong_headers, _ = self.request(
            "POST",
            "/account/delete",
            headers={
                "Cookie": f"{SESSION_COOKIE}={session}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=urlencode(
                {"action_token": action_token, "confirmation": "삭제"}
            ).encode("utf-8"),
        )
        self.assertEqual(wrong_status, 303)
        self.assertEqual(wrong_headers["Location"], "/mypage?account_error=confirmation")

        delete_status, delete_headers, _ = self.request(
            "POST",
            "/account/delete",
            headers={
                "Cookie": f"{SESSION_COOKIE}={session}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=urlencode(
                {"action_token": action_token, "confirmation": "계정 삭제"}
            ).encode("utf-8"),
        )
        self.assertEqual(delete_status, 303)
        self.assertEqual(delete_headers["Location"], "/login?account_deleted=1")
        self.assertIn("Max-Age=0", "\n".join(delete_headers.get_all("Set-Cookie", [])))

        session_status, _, session_body = self.request(
            "GET",
            "/auth/session",
            headers={"Cookie": f"{SESSION_COOKIE}={session}"},
        )
        self.assertEqual(session_status, 200)
        self.assertIsNone(json.loads(session_body)["user"])
        login_status, _, login_body = self.request("GET", "/login?account_deleted=1")
        self.assertEqual(login_status, 200)
        self.assertIn("계정과 네이버 연결 정보가 삭제되었습니다", login_body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
