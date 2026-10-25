from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import padding
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.integrations import IntegrationError, NaverLoginClient
from app.oauth_security import (
    OAuthTokenCipher,
    oauth_subject_hmac,
    verify_naver_disconnect_payload,
)
from app.services import AppError, RequestContext, RestaurantService
from tests.postgres_test import fresh_postgres_database


TEST_TOKEN_KEY = Fernet.generate_key().decode("ascii")
TEST_SUBJECT_SECRET = "oauth-lifecycle-test-subject-secret"


class FakeHttpResponse:
    def __init__(self, body: bytes = b"", status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class FakeNaverLoginClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.failure: IntegrationError | None = None

    def revoke_token(self, token: str, token_type_hint: str = "refresh_token") -> None:
        self.calls.append((token, token_type_hint))
        if self.failure is not None:
            raise self.failure


class BlockingNaverLoginClient(FakeNaverLoginClient):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def revoke_token(self, token: str, token_type_hint: str = "refresh_token") -> None:
        self.calls.append((token, token_type_hint))
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("test revocation was not released")


def naver_disconnect_payload(
    subject: str,
    *,
    client_id: str,
    client_secret: str,
    timestamp: int,
) -> dict[str, str]:
    protocol_key = hashlib.md5(
        client_secret.encode("utf-8"), usedforsecurity=False
    ).digest()[:16]
    iv = b"0123456789abcdef"
    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(subject.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(protocol_key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    encrypted_subject = base64.urlsafe_b64encode(iv + ciphertext).decode("ascii").rstrip("=")
    signature_base = (
        f"clientId={client_id}&encryptUniqueId={encrypted_subject}&timestamp={timestamp}"
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(protocol_key, signature_base.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    return {
        "clientId": client_id,
        "encryptUniqueId": encrypted_subject,
        "timestamp": str(timestamp),
        "signature": signature,
    }


class OAuthSecurityTests(unittest.TestCase):
    def test_multifernet_encrypts_rotates_and_never_contains_plaintext(self) -> None:
        old_key = Fernet.generate_key().decode("ascii")
        new_key = Fernet.generate_key().decode("ascii")
        old_cipher = OAuthTokenCipher(old_key)
        ciphertext = old_cipher.encrypt("refresh-token-value")
        self.assertNotIn("refresh-token-value", ciphertext)

        rotating_cipher = OAuthTokenCipher(f"{new_key},{old_key}")
        self.assertEqual(rotating_cipher.decrypt(ciphertext), "refresh-token-value")
        self.assertFalse(rotating_cipher.uses_primary_key(ciphertext))
        rotated = rotating_cipher.rotate(ciphertext)
        self.assertEqual(rotating_cipher.decrypt(rotated), "refresh-token-value")
        self.assertTrue(rotating_cipher.uses_primary_key(rotated))
        with self.assertRaises(ValueError):
            OAuthTokenCipher("not-a-fernet-key")

    def test_naver_disconnect_verifies_signature_timestamp_and_encrypted_subject(self) -> None:
        payload = naver_disconnect_payload(
            "provider-subject",
            client_id="client-id",
            client_secret="client-secret",
            timestamp=1_000,
        )
        verified = verify_naver_disconnect_payload(
            payload,
            client_id="client-id",
            client_secret="client-secret",
            subject_hmac_secret=TEST_SUBJECT_SECRET,
            now=1_001,
        )
        self.assertEqual(verified.provider_subject, "provider-subject")
        self.assertNotIn("provider-subject", verified.event_digest)

        with self.assertRaises(PermissionError):
            verify_naver_disconnect_payload(
                {**payload, "signature": payload["signature"] + "x"},
                client_id="client-id",
                client_secret="client-secret",
                subject_hmac_secret=TEST_SUBJECT_SECRET,
                now=1_001,
            )
        with self.assertRaises(ValueError):
            verify_naver_disconnect_payload(
                payload,
                client_id="client-id",
                client_secret="client-secret",
                subject_hmac_secret=TEST_SUBJECT_SECRET,
                now=2_000,
            )


class NaverOAuthClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = NaverLoginClient(
            "client-id",
            "client-secret",
            "https://service.example/auth/callback/naver",
        )

    def test_token_exchange_uses_post_form_and_keeps_secrets_out_of_url(self) -> None:
        responses = [
            FakeHttpResponse(
                json.dumps(
                    {
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": "3600",
                    }
                ).encode("utf-8")
            ),
            FakeHttpResponse(
                json.dumps(
                    {
                        "resultcode": "00",
                        "response": {"id": "subject", "nickname": "사용자"},
                    }
                ).encode("utf-8")
            ),
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return responses[len(requests) - 1]

        with patch("app.integrations.urlopen", side_effect=fake_urlopen):
            result = self.client.exchange_code("authorization-code", "state-value")

        token_request, profile_request = requests
        self.assertEqual(token_request.full_url, "https://nid.naver.com/oauth2.0/token")
        self.assertEqual(token_request.get_method(), "POST")
        self.assertNotIn("client-secret", token_request.full_url)
        self.assertNotIn("authorization-code", token_request.full_url)
        form = parse_qs(token_request.data.decode("utf-8"))
        self.assertEqual(form["grant_type"], ["authorization_code"])
        self.assertEqual(form["client_secret"], ["client-secret"])
        self.assertEqual(form["code"], ["authorization-code"])
        self.assertEqual(profile_request.full_url, "https://openapi.naver.com/v1/nid/me")
        self.assertNotIn("access-token", profile_request.full_url)
        self.assertEqual(result.profile.subject, "subject")
        self.assertEqual(result.refresh_token, "refresh-token")

    def test_token_revocation_uses_official_post_form_endpoint(self) -> None:
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return FakeHttpResponse(status=200)

        with patch("app.integrations.urlopen", side_effect=fake_urlopen):
            self.client.revoke_token("refresh-token", "refresh_token")

        request = requests[0]
        self.assertEqual(request.full_url, "https://nid.naver.com/oauth2.0/revoke")
        self.assertEqual(request.get_method(), "POST")
        self.assertNotIn("refresh-token", request.full_url)
        self.assertNotIn("client-secret", request.full_url)
        form = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(form["token"], ["refresh-token"])
        self.assertEqual(form["token_type_hint"], ["refresh_token"])


class OAuthLifecycleServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = fresh_postgres_database()
        self.naver = FakeNaverLoginClient()
        self.cipher = OAuthTokenCipher(TEST_TOKEN_KEY)
        self.service = RestaurantService(
            self.db,
            oauth_token_cipher=self.cipher,
            oauth_subject_hmac_secret=TEST_SUBJECT_SECRET,
            naver_login_client=self.naver,
        )

    def test_encrypted_refresh_token_revoke_blocks_then_allows_clean_rejoin(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver",
            "lifecycle-user",
            "수명주기 사용자",
            refresh_token="stored-refresh-token",
        )
        user_id = int(account["user"]["id"])
        serialized = json.dumps(account, ensure_ascii=False)
        self.assertNotIn("lifecycle-user", serialized)
        self.assertNotIn("stored-refresh-token", serialized)
        with self.db.session() as conn:
            oauth = conn.execute(
                "SELECT refresh_token_ciphertext FROM oauth_accounts WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        self.assertNotEqual(oauth["refresh_token_ciphertext"], "stored-refresh-token")
        self.assertEqual(
            self.cipher.decrypt(str(oauth["refresh_token_ciphertext"])),
            "stored-refresh-token",
        )

        deleted = self.service.delete_account(user_id)
        self.assertEqual(deleted["provider_revocation"], "queued")
        with self.assertRaises(AppError) as blocked:
            self.service.upsert_oauth_account(
                "naver",
                "lifecycle-user",
                "차단 사용자",
            )
        self.assertEqual(blocked.exception.status, 423)

        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.naver.calls, [("stored-refresh-token", "refresh_token")])
        with self.db.session() as conn:
            job = conn.execute(
                "SELECT token_ciphertext FROM oauth_lifecycle_jobs WHERE id = ?",
                (deleted["lifecycle_job_id"],),
            ).fetchone()
        self.assertIsNone(job["token_ciphertext"])

        rejoined = self.service.upsert_oauth_account(
            "naver",
            "lifecycle-user",
            "새 사용자",
            refresh_token="new-refresh-token",
        )
        self.assertTrue(rejoined["created"])
        self.assertNotEqual(int(rejoined["user"]["id"]), user_id)

    def test_retryable_failure_uses_backoff_and_login_supplies_fresh_token(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "retry-user", "재시도 사용자", refresh_token="old-token"
        )
        deleted = self.service.delete_account(int(account["user"]["id"]))
        self.naver.failure = IntegrationError(
            "temporarily unavailable", status_code=503, retryable=True
        )
        retrying = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(retrying["status"], "retry_wait")
        self.assertEqual(retrying["last_error_code"], "provider_503")
        self.assertIsNotNone(retrying["next_attempt_at"])

        with self.assertRaises(AppError) as blocked:
            self.service.upsert_oauth_account(
                "naver",
                "retry-user",
                "재시도 사용자",
                access_token="fresh-access-token",
            )
        self.assertEqual(blocked.exception.status, 423)
        self.naver.failure = None
        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.naver.calls[-1], ("fresh-access-token", "access_token"))
        self.assertEqual(int(completed["job_id"]), int(deleted["lifecycle_job_id"]))
        self.assertEqual(
            [self.service._oauth_retry_delay_seconds(value) for value in (1, 2, 3, 4, 9)],
            [60, 5 * 60, 15 * 60, 60 * 60, 60 * 60],
        )

    def test_nonretryable_provider_error_stays_manual_until_admin_retry(self) -> None:
        with self.db.session() as conn:
            admin = conn.execute(
                "INSERT INTO users (display_name, role) VALUES ('관리자', 'admin')"
            )
        account = self.service.upsert_oauth_account(
            "naver", "manual-user", "수동 검토 사용자", refresh_token="rejected-token"
        )
        deleted = self.service.delete_account(int(account["user"]["id"]))
        self.naver.failure = IntegrationError(
            "unauthorized", status_code=401, retryable=False
        )

        manual = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(manual["status"], "manual_review")
        self.assertEqual(manual["last_error_code"], "provider_401")
        self.assertTrue(manual["local_delete_completed"])
        with self.assertRaises(AppError) as blocked:
            self.service.upsert_oauth_account(
                "naver",
                "manual-user",
                "수동 검토 사용자",
                access_token="replacement-token",
            )
        self.assertEqual(blocked.exception.status, 423)
        self.assertIsNone(self.service.process_next_oauth_lifecycle_job("oauth-test-worker"))
        listed = self.service.oauth_lifecycle_jobs()["jobs"][0]
        self.assertEqual(listed["status"], "manual_review")
        self.assertEqual(listed["last_error_code"], "provider_401")

        retried = self.service.retry_oauth_lifecycle_job(
            int(deleted["lifecycle_job_id"]),
            actor_user_id=int(admin.lastrowid),
        )
        self.assertEqual(retried["status"], "queued")
        self.naver.failure = None
        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.naver.calls[-1], ("replacement-token", "access_token"))

    def test_delete_intent_rejects_other_or_expired_subject_without_deleting(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "intent-user", "삭제 의도 사용자"
        )
        user_id = int(account["user"]["id"])
        intent = self.service.create_account_delete_intent(user_id)

        with self.assertRaises(AppError) as mismatch:
            self.service.complete_account_delete_intent(
                intent,
                provider_subject="different-user",
                access_token="different-token",
            )
        self.assertEqual(mismatch.exception.status, 409)
        self.assertIsNotNone(self.service.get_active_user(user_id))

        with self.db.session() as conn:
            conn.execute(
                "UPDATE oauth_flow_intents SET expires_at = '2000-01-01T00:00:00+00:00'"
            )
        with self.assertRaises(AppError) as expired:
            self.service.complete_account_delete_intent(
                intent,
                provider_subject="intent-user",
                access_token="expired-token",
            )
        self.assertEqual(expired.exception.status, 401)
        self.assertIsNotNone(self.service.get_active_user(user_id))
        self.assertEqual(self.service.oauth_lifecycle_jobs()["counts"]["total"], 0)

    def test_concurrent_claim_is_unique_and_new_login_token_is_revoked_next(self) -> None:
        blocking_naver = BlockingNaverLoginClient()
        service = RestaurantService(
            self.db,
            oauth_token_cipher=self.cipher,
            oauth_subject_hmac_secret=TEST_SUBJECT_SECRET,
            naver_login_client=blocking_naver,
        )
        account = service.upsert_oauth_account(
            "naver",
            "concurrent-user",
            "동시 처리 사용자",
            refresh_token="old-refresh-token",
        )
        service.delete_account(int(account["user"]["id"]))

        worker_result: list[dict | None] = []
        worker_error: list[BaseException] = []

        def run_worker() -> None:
            try:
                worker_result.append(service.process_next_oauth_lifecycle_job("worker-one"))
            except BaseException as exc:  # pragma: no cover - assertion reports the exception
                worker_error.append(exc)

        thread = threading.Thread(target=run_worker, daemon=True)
        thread.start()
        self.assertTrue(blocking_naver.entered.wait(timeout=5))

        self.assertIsNone(service.process_next_oauth_lifecycle_job("worker-two"))
        with self.assertRaises(AppError) as blocked:
            service.upsert_oauth_account(
                "naver",
                "concurrent-user",
                "동시 처리 사용자",
                access_token="new-access-token",
            )
        self.assertEqual(blocked.exception.status, 423)

        blocking_naver.release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(worker_error, [])
        self.assertEqual(worker_result[0]["status"], "queued")

        completed = service.process_next_oauth_lifecycle_job("worker-two")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            blocking_naver.calls,
            [
                ("old-refresh-token", "refresh_token"),
                ("new-access-token", "access_token"),
            ],
        )

    def test_admin_legacy_delete_waits_for_token_from_blocked_login(self) -> None:
        with self.db.session() as conn:
            admin = conn.execute(
                "INSERT INTO users (display_name, role) VALUES ('관리자', 'admin')"
            )
        account = self.service.upsert_oauth_account(
            "naver", "legacy-user", "기존 사용자"
        )
        deleted = self.service.admin_delete_account(
            int(account["user"]["id"]),
            actor_user_id=int(admin.lastrowid),
        )
        self.assertEqual(deleted["provider_revocation"], "awaiting_token")
        with self.assertRaises(AppError) as blocked:
            self.service.upsert_oauth_account(
                "naver",
                "legacy-user",
                "기존 사용자",
                access_token="legacy-access-token",
            )
        self.assertEqual(blocked.exception.status, 423)
        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.naver.calls, [("legacy-access-token", "access_token")])

    def test_provider_disconnect_deletes_account_and_is_idempotent(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "provider-delete-user", "철회 사용자"
        )
        user_id = int(account["user"]["id"])
        first = self.service.accept_naver_disconnect(
            provider_subject="provider-delete-user",
            provider_timestamp=1_000,
            event_digest="provider-delete-event",
        )
        second = self.service.accept_naver_disconnect(
            provider_subject="provider-delete-user",
            provider_timestamp=1_000,
            event_digest="provider-delete-event",
        )
        self.assertEqual(first["provider_revocation"], "completed")
        self.assertEqual(second["result"], "duplicate")
        self.assertIsNone(self.service.get_active_user(user_id))
        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertTrue(completed["local_delete_completed"])
        rejoined = self.service.upsert_oauth_account(
            "naver", "provider-delete-user", "새 철회 사용자"
        )
        self.assertTrue(rejoined["created"])
        self.assertNotEqual(int(rejoined["user"]["id"]), user_id)

    def test_provider_disconnect_reconsent_token_is_revoked_before_rejoin(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "provider-reconsent-user", "재동의 사용자"
        )
        user_id = int(account["user"]["id"])
        self.service.accept_naver_disconnect(
            provider_subject="provider-reconsent-user",
            provider_timestamp=1_000,
            event_digest="provider-reconsent-event",
        )

        with self.assertRaises(AppError) as blocked:
            self.service.upsert_oauth_account(
                "naver",
                "provider-reconsent-user",
                "재동의 사용자",
                refresh_token="fresh-reconsent-token",
            )
        self.assertEqual(blocked.exception.status, 423)

        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            self.naver.calls,
            [("fresh-reconsent-token", "refresh_token")],
        )
        rejoined = self.service.upsert_oauth_account(
            "naver", "provider-reconsent-user", "새 재동의 사용자"
        )
        self.assertTrue(rejoined["created"])
        self.assertNotEqual(int(rejoined["user"]["id"]), user_id)

    def test_disconnect_confirmation_does_not_discard_pending_job_token(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver",
            "pending-confirmation-user",
            "철회 확인 사용자",
            refresh_token="pending-confirmation-token",
        )
        self.service.delete_account(int(account["user"]["id"]))
        confirmed = self.service.accept_naver_disconnect(
            provider_subject="pending-confirmation-user",
            provider_timestamp=1_500,
            event_digest="pending-confirmation-event",
        )
        self.assertEqual(confirmed["result"], "revocation_confirmed")

        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            self.naver.calls,
            [("pending-confirmation-token", "refresh_token")],
        )

    def test_login_upsert_and_disconnect_are_serialized_by_subject(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "serialized-subject", "직렬화 사용자"
        )
        user_id = int(account["user"]["id"])
        original_capture = self.service._capture_pending_oauth_login
        login_checked = threading.Event()
        release_login = threading.Event()
        callback_started = threading.Event()
        callback_done = threading.Event()
        errors: list[BaseException] = []

        def blocking_capture(*args, **kwargs):
            result = original_capture(*args, **kwargs)
            login_checked.set()
            if not release_login.wait(timeout=5):
                raise RuntimeError("test login was not released")
            return result

        self.service._capture_pending_oauth_login = blocking_capture

        def login() -> None:
            try:
                self.service.upsert_oauth_account(
                    "naver",
                    "serialized-subject",
                    "직렬화 사용자",
                    refresh_token="serialized-refresh-token",
                )
            except BaseException as exc:  # pragma: no cover - assertion reports it
                errors.append(exc)

        def disconnect() -> None:
            try:
                callback_started.set()
                self.service.accept_naver_disconnect(
                    provider_subject="serialized-subject",
                    provider_timestamp=2_000,
                    event_digest="serialized-disconnect-event",
                )
            except BaseException as exc:  # pragma: no cover - assertion reports it
                errors.append(exc)
            finally:
                callback_done.set()

        login_thread = threading.Thread(target=login, daemon=True)
        login_thread.start()
        self.assertTrue(login_checked.wait(timeout=5))
        callback_thread = threading.Thread(target=disconnect, daemon=True)
        callback_thread.start()
        self.assertTrue(callback_started.wait(timeout=5))
        self.assertFalse(callback_done.wait(timeout=0.2))

        release_login.set()
        login_thread.join(timeout=5)
        callback_thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertTrue(callback_done.is_set())
        self.assertIsNone(self.service.get_active_user(user_id))

    def test_merged_naver_connections_each_get_revocation_job(self) -> None:
        first = self.service.upsert_oauth_account(
            "naver", "merged-naver-one", "첫 계정", refresh_token="first-token"
        )
        second = self.service.upsert_oauth_account(
            "naver", "merged-naver-two", "둘째 계정", refresh_token="second-token"
        )
        target_user_id = int(second["user"]["id"])
        self.service.merge_account(
            int(first["user"]["id"]),
            target_user_id,
            RequestContext(actor_id="test-admin"),
        )

        deleted = self.service.delete_account(target_user_id)
        self.assertEqual(len(deleted["lifecycle_job_ids"]), 2)
        completed = []
        while True:
            job = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
            if job is None:
                break
            completed.append(job)
        self.assertEqual(len(completed), 2)
        self.assertTrue(all(job["status"] == "completed" for job in completed))
        self.assertEqual(
            set(self.naver.calls),
            {("first-token", "refresh_token"), ("second-token", "refresh_token")},
        )

    def test_provider_disconnect_revokes_other_merged_naver_connection(self) -> None:
        first = self.service.upsert_oauth_account(
            "naver", "callback-merged-one", "첫 계정", refresh_token="first-token"
        )
        second = self.service.upsert_oauth_account(
            "naver", "callback-merged-two", "둘째 계정", refresh_token="second-token"
        )
        self.service.merge_account(
            int(first["user"]["id"]),
            int(second["user"]["id"]),
            RequestContext(actor_id="test-admin"),
        )

        accepted = self.service.accept_naver_disconnect(
            provider_subject="callback-merged-one",
            provider_timestamp=3_000,
            event_digest="callback-merged-event",
        )
        self.assertEqual(len(accepted["lifecycle_job_ids"]), 2)
        first_job = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        second_job = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(first_job["status"], "completed")
        self.assertEqual(second_job["status"], "completed")
        self.assertEqual(self.naver.calls, [("second-token", "refresh_token")])

    def test_provider_disconnect_waits_for_other_merged_connection_token(self) -> None:
        first = self.service.upsert_oauth_account(
            "naver", "callback-awaiting-one", "첫 계정"
        )
        second = self.service.upsert_oauth_account(
            "naver", "callback-awaiting-two", "둘째 계정"
        )
        self.service.merge_account(
            int(first["user"]["id"]),
            int(second["user"]["id"]),
            RequestContext(actor_id="test-admin"),
        )
        accepted = self.service.accept_naver_disconnect(
            provider_subject="callback-awaiting-one",
            provider_timestamp=3_500,
            event_digest="callback-awaiting-event",
        )
        self.assertEqual(accepted["provider_revocation"], "awaiting_token")

        callback_job = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        related_job = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(callback_job["status"], "completed")
        self.assertEqual(related_job["status"], "awaiting_token")
        with self.assertRaises(AppError) as blocked:
            self.service.upsert_oauth_account(
                "naver",
                "callback-awaiting-two",
                "둘째 계정",
                access_token="related-access-token",
            )
        self.assertEqual(blocked.exception.status, 423)
        completed = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.naver.calls, [("related-access-token", "access_token")])

    def test_local_delete_failure_respects_next_attempt_at(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "local-delete-backoff", "로컬 삭제 실패", refresh_token="token"
        )
        self.service.delete_account(int(account["user"]["id"]))
        self.service._complete_oauth_local_delete = lambda job_id: (_ for _ in ()).throw(
            RuntimeError("forced local delete failure")
        )

        first = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(first["status"], "retry_wait")
        self.assertEqual(first["attempts"], 1)
        self.assertIsNone(self.service.process_next_oauth_lifecycle_job("oauth-test-worker"))
        with self.db.session() as conn:
            conn.execute(
                "UPDATE oauth_lifecycle_jobs SET next_attempt_at = '2000-01-01T00:00:00+00:00'"
            )
        second = self.service.process_next_oauth_lifecycle_job("oauth-test-worker")
        self.assertEqual(second["attempts"], 2)

    def test_persisted_oauth_ciphertexts_rotate_to_primary_key(self) -> None:
        old_key = Fernet.generate_key().decode("ascii")
        new_key = Fernet.generate_key().decode("ascii")
        old_service = RestaurantService(
            self.db,
            oauth_token_cipher=OAuthTokenCipher(old_key),
            oauth_subject_hmac_secret=TEST_SUBJECT_SECRET,
            naver_login_client=self.naver,
        )
        account = old_service.upsert_oauth_account(
            "naver", "rotation-user", "키 회전", refresh_token="rotation-token"
        )
        old_service.delete_account(int(account["user"]["id"]))

        rotating_service = RestaurantService(
            self.db,
            oauth_token_cipher=OAuthTokenCipher(f"{new_key},{old_key}"),
        )
        before = rotating_service.oauth_token_encryption_status()
        self.assertEqual(before["non_primary"], 2)
        rotated = rotating_service.rotate_oauth_token_ciphertexts()
        self.assertEqual(rotated["rotated"], 2)
        self.assertEqual(rotated["non_primary"], 0)

        new_only_service = RestaurantService(
            self.db,
            oauth_token_cipher=OAuthTokenCipher(new_key),
        )
        verified = new_only_service.oauth_token_encryption_status()
        self.assertEqual(verified["total"], 2)
        self.assertEqual(verified["non_primary"], 0)
        self.assertEqual(verified["unreadable"], 0)

    def test_concurrent_duplicate_disconnect_is_claimed_once(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "duplicate-callback-user", "중복 Callback 사용자"
        )
        user_id = int(account["user"]["id"])
        barrier = threading.Barrier(3)
        results: list[dict] = []
        errors: list[BaseException] = []

        def send_disconnect() -> None:
            try:
                barrier.wait(timeout=5)
                results.append(
                    self.service.accept_naver_disconnect(
                        provider_subject="duplicate-callback-user",
                        provider_timestamp=2_000,
                        event_digest="duplicate-callback-event",
                    )
                )
            except BaseException as exc:  # pragma: no cover - assertion reports it
                errors.append(exc)

        threads = [threading.Thread(target=send_disconnect, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted(result["result"] for result in results),
            ["deletion_pending", "duplicate"],
        )
        self.assertIsNone(self.service.get_active_user(user_id))
        with self.db.session() as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) AS count FROM oauth_disconnect_events"
            ).fetchone()["count"]
            job_count = conn.execute(
                "SELECT COUNT(*) AS count FROM oauth_lifecycle_jobs"
            ).fetchone()["count"]
        self.assertEqual(int(event_count), 1)
        self.assertEqual(int(job_count), 1)

    def test_subject_hmac_is_keyed_and_does_not_reveal_subject(self) -> None:
        digest = oauth_subject_hmac(TEST_SUBJECT_SECRET, "naver", "private-subject")
        self.assertNotIn("private-subject", digest)
        self.assertNotEqual(
            digest,
            oauth_subject_hmac("different-secret", "naver", "private-subject"),
        )


if __name__ == "__main__":
    unittest.main()
