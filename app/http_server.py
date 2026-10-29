from __future__ import annotations

import html
import hmac
import json
import logging
import mimetypes
import re
import secrets
import traceback
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from .auth import SESSION_MAX_AGE_SECONDS, SessionCodec
from .config import BASE_DIR, Settings
from .database import Database
from .integrations import (
    GeocodingNaverClient,
    GoogleOAuthClient,
    GroqReviewSummaryClient,
    IntegrationError,
    NaverLoginClient,
    NaverMapsGeocodingClient,
    NaverSearchLocalClient,
    OAuthLoginResult,
    OAuthProfile,
)
from .pipeline import (
    COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS,
    COLLECTION_SCAN_SAFETY_MAX_PAGES,
    BusanCityLiveAdapter,
    DailyPipeline,
)
from .operation_jobs import ACTIVE_JOB_STATUSES, OperationJobQueue
from .notices import NoticeService
from .oauth_security import OAuthTokenCipher, verify_naver_disconnect_payload
from .progress import VerificationProgressStore
from .services import AppError, RequestContext, RestaurantService
from .utils import stable_hash
from .views import (
    admin_accounts_index,
    admin_document_detail_index,
    admin_documents_index,
    admin_index,
    admin_review_index,
    admin_reviews_index,
    admin_restaurant_images_index,
    admin_workflow_index,
    map_issues_index,
    ops_logs_index,
    public_index,
    login_index,
    mypage_index,
    restaurant_photo_upload_index,
    privacy_index,
    terms_index,
    notices_index,
    notice_detail_index,
    admin_notices_index,
    admin_notice_editor_index,
    notice_error_index,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "public_restaurant_session"
OAUTH_STATE_COOKIE = "public_restaurant_oauth_state"
OAUTH_RETURN_COOKIE = "public_restaurant_oauth_return"
OAUTH_PROVIDER_COOKIE = "public_restaurant_oauth_provider"
OAUTH_PURPOSE_COOKIE = "public_restaurant_oauth_purpose"
OAUTH_INTENT_COOKIE = "public_restaurant_oauth_intent"
OAUTH_COOKIE_MAX_AGE_SECONDS = 10 * 60
ACCOUNT_DELETE_ACTION = "account-delete"
ADMIN_ACCOUNT_UPDATE_ACTION = "admin-account-update"
SESSION_CSRF_ACTION = "session-csrf"
ACCOUNT_ACTION_TOKEN_MAX_AGE_SECONDS = 5 * 60
RECENT_AUTH_MAX_AGE_SECONDS = 5 * 60
ACCOUNT_DELETE_RATE_LIMIT = 5
ACCOUNT_DELETE_IP_RATE_LIMIT = 10
ACCOUNT_DELETE_RATE_WINDOW_SECONDS = 10 * 60
MAX_REGULAR_POST_BYTES = 1024 * 1024
MAX_NAVER_DISCONNECT_POST_BYTES = 8 * 1024


LOGGER = logging.getLogger(__name__)


def _trusted_proxy_client_ip(peer_ip: str, forwarded_ip: str) -> str:
    try:
        peer = ip_address(peer_ip)
    except ValueError:
        return peer_ip
    if not peer.is_loopback or not forwarded_ip.strip():
        return peer.compressed
    try:
        return ip_address(forwarded_ip.strip()).compressed
    except ValueError:
        return peer.compressed


class PublicRestaurantApplication:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.app_env == "production":
            missing = [
                name
                for name, value in (
                    ("APP_SESSION_SECRET", settings.session_secret),
                    ("OAUTH_TOKEN_ENCRYPTION_KEYS", settings.oauth_token_encryption_keys),
                    ("OAUTH_SUBJECT_HMAC_SECRET", settings.oauth_subject_hmac_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError("APP_ENV=production requires " + ", ".join(missing))
        self.database = Database(settings.database_url)
        self.database.prepare()
        geocoding_client = None
        if settings.naver_maps_client_id and settings.naver_maps_client_secret:
            geocoding_client = NaverMapsGeocodingClient(
                settings.naver_maps_client_id,
                settings.naver_maps_client_secret,
            )
        naver_client = None
        if settings.naver_search_client_id and settings.naver_search_client_secret:
            naver_client = GeocodingNaverClient(
                NaverSearchLocalClient(
                    settings.naver_search_client_id,
                    settings.naver_search_client_secret,
                ),
                geocoding_client,
            )
        ai_summary_client = None
        if settings.groq_api_key:
            ai_summary_client = GroqReviewSummaryClient(
                settings.groq_api_key,
                settings.groq_model,
            )
        oauth_token_cipher = (
            OAuthTokenCipher(settings.oauth_token_encryption_keys)
            if settings.oauth_token_encryption_keys
            else None
        )
        self.naver_login_client = (
            NaverLoginClient(
                settings.naver_login_client_id,
                settings.naver_login_client_secret,
                settings.naver_login_redirect_uri,
            )
            if (
                settings.naver_login_client_id
                and settings.naver_login_client_secret
                and settings.naver_login_redirect_uri
            )
            else None
        )
        self.service = RestaurantService(
            self.database,
            review_rate_limit_per_hour=settings.review_rate_limit_per_hour,
            geocoding_client=geocoding_client,
            naver_client=naver_client,
            ai_summary_client=ai_summary_client,
            # Public photo search is disabled until image usage rights can be verified.
            restaurant_image_client=None,
            restaurant_image_upload_dir=(
                settings.restaurant_image_upload_dir
                or BASE_DIR / "var" / "restaurant_images"
            ),
            oauth_token_cipher=oauth_token_cipher,
            oauth_subject_hmac_secret=settings.oauth_subject_hmac_secret,
            naver_login_client=self.naver_login_client,
        )
        self.notice_service = NoticeService(self.database)
        self.verification_progress = VerificationProgressStore()
        self.operation_jobs = OperationJobQueue(self.database)
        # Development without an explicit key uses a process-local secret so it
        # never falls back to an OAuth client credential. Production is
        # rejected above unless APP_SESSION_SECRET is configured.
        session_secret = settings.session_secret or secrets.token_urlsafe(48)
        self.session_codec = SessionCodec(session_secret)

    def issue_session(self, user_id: int) -> str:
        token = self.session_codec.issue(user_id)
        claims = self.session_codec.verify_claims(token)
        if claims is None:  # pragma: no cover - issue/verify invariant
            raise RuntimeError("issued session could not be verified")
        self.service.register_session(
            user_id,
            token,
            issued_at=claims.issued_at,
            expires_at=claims.expires_at,
        )
        return token

    def session_user(self, token: str) -> dict | None:
        claims = self.session_codec.verify_claims(token)
        if claims is None:
            return None
        return self.service.active_session_user(claims.user_id, token)

    def issue_account_action_token(self, user_id: int, session_token: str) -> str:
        return self.service.issue_one_time_action_token(
            user_id,
            session_token,
            ACCOUNT_DELETE_ACTION,
            ttl_seconds=ACCOUNT_ACTION_TOKEN_MAX_AGE_SECONDS,
        )

    def run_daily(self) -> dict:
        return DailyPipeline(self.database, settings=self.settings).run()

    def run_busan_live(
        self,
        start_date: str = "",
        end_date: str = "",
    ) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(
                max_pages=COLLECTION_SCAN_SAFETY_MAX_PAGES,
                max_documents=COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS,
                start_date=start_date,
                end_date=end_date,
            ),
            settings=self.settings,
            verify_new_rows=False,
        ).run()

    def create_collection_plan(
        self,
        start_date: str = "",
        end_date: str = "",
        batch_size: int = 20,
    ) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(
                max_pages=COLLECTION_SCAN_SAFETY_MAX_PAGES,
                max_documents=COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS,
                start_date=start_date,
                end_date=end_date,
            ),
            settings=self.settings,
            verify_new_rows=False,
        ).create_collection_plan(
            start_date=start_date,
            end_date=end_date,
            max_pages=COLLECTION_SCAN_SAFETY_MAX_PAGES,
            max_documents=COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS,
            batch_size=batch_size,
        )

    def run_collection_plan_batch(self, plan_id: int, batch_size: int | None = None) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(),
            settings=self.settings,
            verify_new_rows=False,
        ).run_collection_plan_batch(plan_id=plan_id, batch_size=batch_size)

    def run_collection_plan_batches(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(),
            settings=self.settings,
            verify_new_rows=False,
        ).run_collection_plan_batches(
            plan_id=plan_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )

    def retry_collection_plan_failures(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(),
            settings=self.settings,
            verify_new_rows=False,
        ).retry_collection_plan_failures(
            plan_id=plan_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )

    def parse_collection_plan_batches(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(),
            settings=self.settings,
            verify_new_rows=False,
        ).parse_collection_plan_batches(
            plan_id=plan_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )

    def parse_collection_plan_batch(self, plan_id: int, batch_size: int | None = None) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(),
            settings=self.settings,
            verify_new_rows=False,
        ).parse_collection_plan_batch(plan_id=plan_id, batch_size=batch_size)

    def retry_collection_plan_parse_failures(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict:
        return DailyPipeline(
            self.database,
            adapter=BusanCityLiveAdapter(),
            settings=self.settings,
            verify_new_rows=False,
        ).retry_collection_plan_parse_failures(
            plan_id=plan_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )

    def verify_pending(
        self,
        limit: int = 100,
        sort: str = "verification_oldest",
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        return DailyPipeline(
            self.database,
            settings=self.settings,
            verification_progress_callback=progress_callback or self.verification_progress.update,
        ).verify_pending(limit=limit, sort=sort)

    def verify_collected(
        self,
        limit: int = 100,
        sort: str = "verification_oldest",
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict:
        return DailyPipeline(
            self.database,
            settings=self.settings,
            verification_progress_callback=progress_callback or self.verification_progress.update,
        ).verify_collected(limit=limit, sort=sort)

    def enqueue_operation(
        self,
        job_type: str,
        payload: dict[str, Any],
        context: RequestContext,
    ) -> dict[str, Any]:
        return self.operation_jobs.enqueue(
            job_type,
            payload,
            dedupe_key=f"{job_type}:{stable_hash(job_type, payload)}",
            requested_by=context.actor_id,
        )

    def operation_job(self, job_id: int) -> dict[str, Any]:
        job = self.operation_jobs.get(job_id)
        if job is None:
            raise AppError(404, "operation job not found")
        return job

    def operation_job_list(self, active_only: bool = False, limit: int = 20) -> dict[str, Any]:
        jobs = self.operation_jobs.list(
            statuses=ACTIVE_JOB_STATUSES if active_only else (),
            limit=limit,
        )
        return {"jobs": jobs, "active_count": sum(job["status"] in ACTIVE_JOB_STATUSES for job in jobs)}

    def verification_progress_snapshot(self) -> dict[str, Any]:
        latest = self.operation_jobs.latest(("verify_pending", "verify_collected"))
        if latest is not None:
            progress = dict(latest.get("progress") or {})
            if progress:
                progress["operation_job_id"] = latest["job_id"]
                progress["operation_job_status"] = latest["status"]
                return progress
            if latest["status"] in ACTIVE_JOB_STATUSES:
                return {
                    "active": True,
                    "batch_id": None,
                    "job_name": latest["job_type"],
                    "operation_job_id": latest["job_id"],
                    "operation_job_status": latest["status"],
                    "total": int((latest.get("payload") or {}).get("limit") or 0),
                    "limit": int((latest.get("payload") or {}).get("limit") or 0),
                    "processed": 0,
                    "summary": {},
                    "classifications": {
                        "approved": 0,
                        "needs_review": 0,
                        "rejected": 0,
                        "failed": 0,
                    },
                    "items": [],
                    "updated_at": latest["updated_at"],
                }
        return self.verification_progress.snapshot()

    def verification_status(self) -> dict:
        missing_env = {
            "naver_map_js": [] if self.settings.naver_map_key else ["NAVER_MAP_KEY 또는 NAVER_MAPS_CLIENT_ID"],
            "naver_search": self._missing_env(
                {
                    "NAVER_SEARCH_CLIENT_ID": self.settings.naver_search_client_id,
                    "NAVER_SEARCH_CLIENT_SECRET": self.settings.naver_search_client_secret,
                }
            ),
            "naver_image_search": self._missing_env(
                {
                    "NAVER_API_HUB_CLIENT_ID": self.settings.naver_api_hub_client_id,
                    "NAVER_API_HUB_CLIENT_SECRET": self.settings.naver_api_hub_client_secret,
                }
            )
            if not (
                self.settings.naver_search_client_id
                and self.settings.naver_search_client_secret
            )
            else [],
            "naver_maps_geocoding": self._missing_env(
                {
                    "NAVER_MAPS_CLIENT_ID": self.settings.naver_maps_client_id,
                    "NAVER_MAPS_CLIENT_SECRET": self.settings.naver_maps_client_secret,
                }
            ),
            "data_go_kr_permit": [] if self.settings.data_go_kr_service_key else ["DATA_GO_KR_SERVICE_KEY"],
        }
        return {
            "integrations": {
                "naver_map_js": bool(self.settings.naver_map_key),
                "naver_search": bool(
                    self.settings.naver_search_client_id and self.settings.naver_search_client_secret
                ),
                "naver_image_search": bool(
                    (
                        self.settings.naver_api_hub_client_id
                        and self.settings.naver_api_hub_client_secret
                    )
                    or (
                        self.settings.naver_search_client_id
                        and self.settings.naver_search_client_secret
                    )
                ),
                "naver_maps_geocoding": bool(
                    self.settings.naver_maps_client_id and self.settings.naver_maps_client_secret
                ),
                "data_go_kr_permit": bool(self.settings.data_go_kr_service_key),
            },
            "missing_env": missing_env,
            "overview": self.service.verification_overview(),
        }

    def api_usage_status(self) -> dict:
        now = datetime.now(timezone.utc)
        day = now.date().isoformat()
        month = day[:7]
        metrics = self.service.api_usage_metrics(day=day, month=month)

        def integration(
            *,
            key: str,
            name: str,
            description: str,
            configured: bool,
            provider_keys: tuple[str, ...],
            quota: int,
            period: str,
            usage_scope: str,
        ) -> dict:
            provider_metrics = [metrics.get(provider, {}) for provider in provider_keys]
            count_key = "month_count" if period == "month" else "day_count"
            used = sum(int(metric.get(count_key) or 0) for metric in provider_metrics)
            latest = max(
                provider_metrics,
                key=lambda metric: str(metric.get("last_called_at") or ""),
                default={},
            )
            last_success = latest.get("last_success")
            last_error = str(latest.get("last_error") or "").lower()
            last_status_code = latest.get("last_status_code")
            if not configured:
                state = "disconnected"
                state_label = "미연동"
                state_reason = "API 인증 정보가 설정되지 않았습니다."
            elif last_success is False:
                state = "warning"
                state_label = "확인 필요"
                if "timed out" in last_error or "timeout" in last_error:
                    state_reason = "최근 호출 실패 · 응답 시간 초과"
                elif last_status_code in {401, 403} or "credential" in last_error:
                    state_reason = "최근 호출 실패 · 인증 정보 확인 필요"
                elif last_status_code == 429 or "too many requests" in last_error:
                    state_reason = "최근 호출 실패 · 사용량 한도 확인 필요"
                else:
                    state_reason = "최근 호출 실패"
            elif last_success is True:
                state = "healthy"
                state_label = "정상"
                state_reason = "최근 호출 성공"
            else:
                state = "connected"
                state_label = "연동됨"
                state_reason = "최근 호출 없음"
            percentage = min(100, used / quota * 100) if quota else 0
            return {
                "key": key,
                "name": name,
                "description": description,
                "configured": configured,
                "state": state,
                "state_label": state_label,
                "state_reason": state_reason,
                "usage": {
                    "used": used,
                    "limit": quota,
                    "remaining": max(0, quota - used),
                    "percentage": round(percentage, 2),
                    "period": period,
                    "period_label": "이번 달" if period == "month" else "오늘",
                    "scope": usage_scope,
                },
                "last_call": latest.get("last_called_at"),
                "last_success": last_success,
            }

        has_legacy_image = bool(
            self.settings.naver_search_client_id
            and self.settings.naver_search_client_secret
            and not (
                self.settings.naver_api_hub_client_id
                and self.settings.naver_api_hub_client_secret
            )
        )
        integrations = [
            integration(
                key="naver_place",
                name="네이버 장소 검증",
                description="지역 검색 및 주소 좌표 보정",
                configured=bool(
                    self.settings.naver_search_client_id
                    and self.settings.naver_search_client_secret
                ),
                provider_keys=("naver", "naver_image_search") if has_legacy_image else ("naver",),
                quota=self.settings.naver_search_daily_quota,
                period="day",
                usage_scope="서버에 기록된 검증 작업" + (" · 이미지 검색과 한도 공유" if has_legacy_image else ""),
            ),
            integration(
                key="naver_image",
                name="네이버 이미지 검색",
                description="음식점 상세 이미지 보강",
                configured=bool(
                    (
                        self.settings.naver_api_hub_client_id
                        and self.settings.naver_api_hub_client_secret
                    )
                    or (
                        self.settings.naver_search_client_id
                        and self.settings.naver_search_client_secret
                    )
                ),
                provider_keys=("naver_image_search",) if not has_legacy_image else ("naver", "naver_image_search"),
                quota=(
                    self.settings.naver_search_daily_quota
                    if has_legacy_image
                    else self.settings.naver_api_hub_monthly_quota
                ),
                period="day" if has_legacy_image else "month",
                usage_scope="서버에 기록된 이미지 요청" + (" · 장소 검색과 한도 공유" if has_legacy_image else ""),
            ),
            integration(
                key="data_go_kr",
                name="공공데이터 인허가",
                description="식품 영업 상태 교차 확인",
                configured=bool(self.settings.data_go_kr_service_key),
                provider_keys=("data_go_kr",),
                quota=self.settings.data_go_kr_daily_quota,
                period="day",
                usage_scope="서버에 기록된 인허가 조회 작업",
            ),
            integration(
                key="groq",
                name="Groq 리뷰 요약",
                description=self.settings.groq_model,
                configured=bool(self.settings.groq_api_key),
                provider_keys=("groq",),
                quota=self.settings.groq_daily_quota,
                period="day",
                usage_scope="서버에 기록된 AI 요약 요청",
            ),
        ]
        return {
            "generated_at": now.isoformat(timespec="seconds"),
            "timezone": "UTC",
            "connected_count": sum(1 for item in integrations if item["configured"]),
            "integration_count": len(integrations),
            "integrations": integrations,
            "notice": "사용량은 이 서버가 기록한 호출 기준이며 공급자 콘솔 집계와 차이가 날 수 있습니다.",
        }

    def _missing_env(self, values: dict[str, str]) -> list[str]:
        return [key for key, value in values.items() if not value]


def make_handler(app: PublicRestaurantApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PublicRestaurantHTTP/1.0"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                query = self._query(parsed.query)
                if self._is_admin_route(path):
                    self._admin_user()
                if path == "/":
                    self._html(
                        public_index(
                            app.settings.naver_map_key,
                            app.settings.app_name,
                            current_user=self._current_user(),
                        )
                    )
                elif path == "/notices":
                    page = int(query.get("page", "1"))
                    self._html(notices_index(app.notice_service.public_list(page)), strict_notice_policy=True)
                elif path.startswith("/notices/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 2:
                        raise AppError(404, "not found")
                    notice = app.notice_service.public_detail(int(parts[1]))
                    self._html(notice_detail_index(notice), strict_notice_policy=True)
                elif path == "/login":
                    return_to = self._safe_return_to(str(query.get("return_to", "/")))
                    self._html(
                        login_index(
                            app_name=app.settings.app_name,
                            error=str(query.get("error", "")),
                            current_user=self._current_user(),
                            naver_configured=self._provider_configured("naver"),
                            return_to=return_to,
                            account_deleted=query.get("account_deleted") == "1",
                            provider_revocation=str(query.get("provider_revocation", "")),
                        )
                    )
                elif path == "/signup":
                    self._redirect("/login", status=302)
                elif path == "/privacy":
                    self._html(
                        privacy_index(
                            app.settings.app_name,
                            app.settings.privacy_contact_email,
                        )
                    )
                elif path == "/terms":
                    self._html(
                        terms_index(
                            app.settings.app_name,
                            app.settings.privacy_contact_email,
                        )
                    )
                elif path == "/mypage":
                    current_user = self._current_user()
                    if current_user is None:
                        self._redirect("/login?return_to=/mypage", status=302)
                    else:
                        session_token = self._cookies().get(SESSION_COOKIE, "")
                        self._html(
                            mypage_index(
                                app.settings.app_name,
                                app.service.my_page(int(current_user["id"])),
                                account_delete_token=app.issue_account_action_token(
                                    int(current_user["id"]),
                                    session_token,
                                ),
                                account_error=str(query.get("account_error", "")),
                            )
                        )
                elif path.startswith("/restaurants/") and path.endswith("/photos/add"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise AppError(404, "not found")
                    current_user = self._current_user()
                    restaurant_id = int(parts[1])
                    if current_user is None:
                        self._redirect(
                            f"/login?return_to={quote(path, safe='/')}",
                            status=302,
                        )
                    else:
                        self._html(
                            restaurant_photo_upload_index(
                                app.settings.app_name,
                                app.service.user_photo_upload_page(
                                    int(current_user["id"]),
                                    restaurant_id,
                                ),
                            )
                        )
                elif path in {"/admin", "/admin/dashboard"}:
                    self._html(admin_index())
                elif path == "/admin/notices":
                    actor = self._admin_user()
                    page = int(query.get("page", "1"))
                    deleted = query.get("deleted") == "1"
                    data = app.notice_service.admin_list(int(actor["id"]), deleted=deleted, page=page)
                    self._html(admin_notices_index(data, deleted=deleted), strict_notice_policy=True)
                elif path == "/admin/notices/new":
                    self._html(admin_notice_editor_index(), strict_notice_policy=True)
                elif path.startswith("/admin/notices/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 3:
                        raise AppError(404, "not found")
                    actor = self._admin_user()
                    notice = app.notice_service.admin_detail(int(actor["id"]), int(parts[2]))
                    self._html(admin_notice_editor_index(notice), strict_notice_policy=True)
                elif path == "/admin/accounts":
                    session_token = self._cookies().get(SESSION_COOKIE, "")
                    self._html(
                        admin_accounts_index(
                            app.session_codec.issue_action_token(
                                session_token,
                                ADMIN_ACCOUNT_UPDATE_ACTION,
                            )
                        )
                    )
                elif path == "/admin/accounts/data":
                    current_user = self._admin_user()
                    self._json(
                        app.service.admin_accounts(
                            q=str(query.get("q", "")),
                            role=str(query.get("role", "")),
                            status=str(query.get("status", "")),
                            limit=int(query.get("limit", "50") or "50"),
                            offset=int(query.get("offset", "0") or "0"),
                        )
                        | {"current_user_id": int(current_user["id"])}
                    )
                elif path == "/admin/collection":
                    self._html(admin_workflow_index("collection"))
                elif path in {"/admin/parsing", "/admin/workflow"}:
                    self._html(admin_workflow_index("parsing"))
                elif path == "/admin/review":
                    self._html(admin_workflow_index("review"))
                elif path in {"/admin/review/results", "/admin/review/queue"}:
                    self._html(admin_review_index())
                elif path == "/admin/reviews":
                    self._html(admin_reviews_index())
                elif path == "/admin/reviews/data":
                    self._json(
                        app.service.admin_reviews(
                            view=str(query.get("view", "open_reports")),
                            q=str(query.get("q", "")),
                            status=str(query.get("status", "all")),
                            page=int(query.get("page", "1")),
                        )
                    )
                elif path.startswith("/admin/reviews/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 3:
                        raise AppError(404, "not found")
                    self._json(app.service.admin_review_detail(int(parts[2])))
                elif path == "/admin/map-issues":
                    self._html(map_issues_index())
                elif path == "/admin/logs":
                    self._html(ops_logs_index())
                elif path == "/admin/photos":
                    self._html(admin_restaurant_images_index())
                elif path == "/admin/photos/restaurants":
                    self._json(
                        app.service.admin_restaurants_for_images(
                            q=str(query.get("q", "")),
                            limit=int(query.get("limit", "50") or "50"),
                        )
                    )
                elif path.startswith("/admin/photos/restaurants/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise AppError(404, "not found")
                    self._json(app.service.admin_restaurant_images(int(parts[3])))
                elif path == "/admin/documents":
                    self._html(admin_documents_index())
                elif path == "/admin/documents/data":
                    self._json(
                        app.service.admin_documents(
                            start_date=str(query.get("start_date", "")),
                            end_date=str(query.get("end_date", "")),
                            institution=str(query.get("institution", "")),
                            status=str(query.get("status", "")),
                            parse_status=str(query.get("parse_status", "")),
                            q=str(query.get("q", "")),
                            sort=str(query.get("sort", "published_desc")),
                            limit=int(query.get("limit", "10") or "10"),
                            offset=int(query.get("offset", "0") or "0"),
                            plan_id=int(query["plan_id"]) if query.get("plan_id") else None,
                        )
                    )
                elif path.startswith("/admin/documents/") and path.endswith("/data"):
                    document_id = int(path.split("/")[3])
                    self._json(
                        app.service.admin_document_detail(
                            document_id,
                            q=str(query.get("q", "")),
                            status=str(query.get("status", "")),
                            sort=str(query.get("sort", "row_asc")),
                            limit=int(query.get("limit", "25") or "25"),
                            offset=int(query.get("offset", "0") or "0"),
                        )
                    )
                elif path.startswith("/admin/documents/"):
                    self._path_int(path, "/admin/documents/")
                    self._html(admin_document_detail_index())
                elif path.startswith("/static/"):
                    self._static(path.removeprefix("/static/"))
                elif path.startswith("/media/community-photos/"):
                    self._restaurant_user_image_media(
                        unquote(path.removeprefix("/media/community-photos/"))
                    )
                elif path.startswith("/media/restaurant-images/"):
                    self._restaurant_image_media(
                        unquote(path.removeprefix("/media/restaurant-images/"))
                    )
                elif path == "/api/map/restaurants":
                    current_user = self._current_user()
                    self._json(
                        {
                            "restaurants": app.service.list_map_restaurants(
                                q=query.get("q", ""),
                                category=query.get("category", ""),
                                min_visit_count=query.get("min_visit_count", ""),
                                search_mode=query.get("search_mode", ""),
                                region=query.get("region", ""),
                                bounds=query.get("bounds", ""),
                                user_id=int(current_user["id"]) if current_user else None,
                                saved_only=query.get("saved_only", ""),
                            )
                        }
                    )
                elif path.startswith("/api/restaurants/"):
                    restaurant_id = self._path_int(path, "/api/restaurants/")
                    current_user = self._current_user()
                    self._json(
                        app.service.get_restaurant(
                            restaurant_id,
                            user_id=int(current_user["id"]) if current_user else None,
                        )
                    )
                elif path == "/api/rankings":
                    self._json(
                        {
                            "rankings": app.service.rankings(
                                category=query.get("category", ""),
                                region=query.get("region", ""),
                                period=query.get("period", "all"),
                            )
                        }
                    )
                elif path == "/api/search":
                    self._json(
                        {
                            "results": app.service.search(
                                q=query.get("q", ""),
                                category=query.get("category", ""),
                                bounds=query.get("bounds", ""),
                            )
                        }
                    )
                elif path.startswith("/auth/") and path.endswith("/start"):
                    provider = path.split("/")[2]
                    self._auth_start(provider, query)
                elif path.startswith("/auth/callback/"):
                    provider = path.removeprefix("/auth/callback/")
                    self._auth_callback(provider, query)
                elif path == "/auth/session":
                    self._json({"user": self._current_user()})
                elif path.startswith("/ops/batches/"):
                    batch_id = self._path_int(path, "/ops/batches/")
                    self._json(app.service.batch(batch_id))
                elif path == "/ops/sources":
                    self._json(app.service.source_registry())
                elif path == "/ops/verification-status":
                    self._json(app.verification_status())
                elif path == "/ops/api-usage":
                    self._json(app.api_usage_status())
                elif path == "/ops/verification-progress":
                    self._json(app.verification_progress_snapshot())
                elif path == "/ops/jobs":
                    self._json(
                        app.operation_job_list(
                            active_only=str(query.get("active", "")).lower()
                            in {"1", "true", "yes"},
                            limit=int(query.get("limit", "20") or "20"),
                        )
                    )
                elif path == "/ops/oauth-lifecycle":
                    self._json(
                        app.service.oauth_lifecycle_jobs(
                            limit=int(query.get("limit", "100") or "100")
                        )
                    )
                elif path.startswith("/ops/jobs/"):
                    self._json(app.operation_job(self._path_int(path, "/ops/jobs/")))
                elif path == "/ops/logs":
                    plan_id = int(query["plan_id"]) if query.get("plan_id") else None
                    limit = int(query.get("limit", "100") or "100")
                    self._json(app.service.ops_logs(plan_id=plan_id, limit=limit))
                elif path == "/ops/collection-progress":
                    self._json(
                        app.service.collection_progress(
                            start_date=str(query.get("start_date", "")),
                            end_date=str(query.get("end_date", "")),
                        )
                    )
                elif path == "/ops/dashboard":
                    self._json(
                        app.service.collection_dashboard(
                            start_date=str(query.get("start_date", "")),
                            end_date=str(query.get("end_date", "")),
                        )
                    )
                elif path == "/review":
                    if "application/json" in self.headers.get("Accept", ""):
                        limit = max(1, min(int(query.get("limit", "50") or "50"), 100))
                        self._json(
                            {
                                "reviews": app.service.admin_review_queue(limit=limit),
                                "total": app.service.admin_review_queue_count(),
                                "limit": limit,
                            }
                        )
                    else:
                        self._html(admin_workflow_index("review"))
                elif path == "/admin/review-reports":
                    self._json({"reports": app.service.review_reports()})
                elif path == "/admin/map-issues/data":
                    self._json(app.service.map_issue_candidates(limit=int(query.get("limit", "100") or "100")))
                elif path == "/admin/candidates":
                    limit = int(query.get("limit", "50") or "50")
                    self._json(
                        app.service.admin_candidates(
                            limit=limit,
                            offsets={
                                "needs_review": int(query.get("needs_review_offset", "0") or "0"),
                                "verified": int(query.get("verified_offset", "0") or "0"),
                                "rejected": int(query.get("rejected_offset", "0") or "0"),
                            },
                            q=str(query.get("q", "")),
                            sort=str(query.get("sort", "id_desc")),
                            start_date=str(query.get("start_date", "")),
                            end_date=str(query.get("end_date", "")),
                            institution=str(query.get("institution", "")),
                            status=str(query.get("status", "")),
                            offset=int(query.get("offset", "0") or "0"),
                        )
                    )
                else:
                    raise AppError(404, "not found")
            except AppError as exc:
                self._error(exc.status, exc.message)
            except ValueError:
                self._error(400, "invalid request")
            except Exception as exc:  # pragma: no cover - server guard
                self._internal_error(exc)

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/auth/naver/deauthorize":
                    self._naver_deauthorize()
                    return
                if self._is_admin_route(path):
                    self._admin_user()
                payload = self._payload()
                if (
                    path.startswith("/admin/notices")
                    or path.startswith("/admin/reviews/")
                ) and not isinstance(payload, dict):
                    raise AppError(400, "invalid admin request")
                self._require_session_csrf(payload)
                context = self._context(payload)
                if path == "/admin/notices" or path.startswith("/admin/notices/"):
                    self._notice_post(path, payload)
                elif path.startswith("/admin/reviews/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4 or parts[3] != "moderate":
                        raise AppError(404, "not found")
                    actor = self._admin_user()
                    self._json(
                        app.service.moderate_review(
                            int(parts[2]),
                            actor_user_id=int(actor["id"]),
                            action=payload.get("action", ""),
                            reason=payload.get("reason", ""),
                        )
                    )
                elif path == "/ops/run-daily":
                    self._json(app.run_daily(), status=201)
                elif path == "/ops/run-busan-live":
                    self._json(
                        app.run_busan_live(
                            start_date=str(payload.get("start_date", "")),
                            end_date=str(payload.get("end_date", "")),
                        ),
                        status=201,
                    )
                elif path.startswith("/ops/oauth-lifecycle/") and path.endswith("/retry"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise AppError(404, "not found")
                    current_user = self._admin_user()
                    self._json(
                        app.service.retry_oauth_lifecycle_job(
                            int(parts[2]),
                            actor_user_id=int(current_user["id"]),
                        )
                    )
                elif path == "/ops/collection-plans":
                    self._json(
                        app.enqueue_operation(
                            "collection_plan_create",
                            {
                                "start_date": str(payload.get("start_date", "")),
                                "end_date": str(payload.get("end_date", "")),
                                "batch_size": int(payload.get("batch_size", 20) or 20),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/run"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.enqueue_operation(
                            "collection_plan_run",
                            {
                                "plan_id": plan_id,
                                "batch_size": int(payload["batch_size"])
                                if payload.get("batch_size")
                                else None,
                                "repeat": bool(payload.get("repeat", True)),
                                "max_batches": int(payload.get("max_batches", 100) or 100),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/retry-failed"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.enqueue_operation(
                            "collection_plan_retry",
                            {
                                "plan_id": plan_id,
                                "batch_size": int(payload["batch_size"])
                                if payload.get("batch_size")
                                else None,
                                "max_batches": int(payload.get("max_batches", 100) or 100),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/parse"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.enqueue_operation(
                            "collection_plan_parse",
                            {
                                "plan_id": plan_id,
                                "batch_size": int(payload["batch_size"])
                                if payload.get("batch_size")
                                else None,
                                "repeat": bool(payload.get("repeat", True)),
                                "max_batches": int(payload.get("max_batches", 100) or 100),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/retry-parse-failed"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.enqueue_operation(
                            "collection_plan_parse_retry",
                            {
                                "plan_id": plan_id,
                                "batch_size": int(payload["batch_size"])
                                if payload.get("batch_size")
                                else None,
                                "max_batches": int(payload.get("max_batches", 100) or 100),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path == "/ops/verify-pending":
                    self._json(
                        app.enqueue_operation(
                            "verify_pending",
                            {
                                "limit": int(payload.get("limit", 100)),
                                "sort": str(payload.get("sort", "verification_oldest")),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path == "/ops/verify-collected":
                    self._json(
                        app.enqueue_operation(
                            "verify_collected",
                            {
                                "limit": int(payload.get("limit", 100)),
                                "sort": str(payload.get("sort", "verification_oldest")),
                            },
                            context,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                elif path.startswith("/admin/photos/restaurants/"):
                    parts = path.strip("/").split("/")
                    if len(parts) == 5 and parts[4] == "images":
                        restaurant_id = int(parts[3])
                        image = payload.get("image")
                        if not isinstance(image, dict) or not isinstance(image.get("content"), bytes):
                            raise AppError(400, "image file is required")
                        self._json(
                            app.service.save_admin_restaurant_image(
                                restaurant_id=restaurant_id,
                                filename=str(image.get("filename") or "image"),
                                image_bytes=image["content"],
                                context=context,
                                alt_text=str(payload.get("alt_text", "")),
                                sort_order=(
                                    int(payload["sort_order"])
                                    if str(payload.get("sort_order", "")).strip()
                                    else None
                                ),
                                image_id=(
                                    int(payload["image_id"])
                                    if str(payload.get("image_id", "")).strip()
                                    else None
                                ),
                            ),
                            status=201,
                        )
                    elif len(parts) == 6 and parts[4] == "images":
                        self._json(
                            app.service.update_admin_restaurant_image(
                                restaurant_id=int(parts[3]),
                                image_id=int(parts[5]),
                                alt_text=str(payload.get("alt_text", "")),
                                sort_order=int(payload.get("sort_order", 0) or 0),
                            )
                        )
                    elif len(parts) == 7 and parts[4] == "images" and parts[6] == "delete":
                        self._json(
                            app.service.delete_admin_restaurant_image(
                                restaurant_id=int(parts[3]),
                                image_id=int(parts[5]),
                            )
                        )
                    else:
                        raise AppError(404, "not found")
                elif path.startswith("/api/restaurants/") and path.endswith("/photos"):
                    restaurant_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    if str(payload.get("rights_confirmed", "")).lower() not in {"1", "true", "on", "yes"}:
                        raise AppError(400, "photo rights confirmation is required")
                    image = payload.get("image")
                    if not isinstance(image, dict) or not isinstance(image.get("content"), bytes):
                        raise AppError(400, "image file is required")
                    app.service.save_user_restaurant_image(
                        int(current_user["id"]),
                        restaurant_id,
                        str(image.get("filename") or "image"),
                        image["content"],
                        alt_text=str(payload.get("alt_text", "")),
                    )
                    self._redirect(f"/?restaurant_id={restaurant_id}", status=303)
                elif path.startswith("/api/photos/") and path.endswith("/update"):
                    image_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    image = payload.get("image")
                    app.service.update_user_restaurant_image(
                        int(current_user["id"]),
                        image_id,
                        alt_text=str(payload.get("alt_text", "")),
                        filename=str(image.get("filename") or "") if isinstance(image, dict) else "",
                        image_bytes=image.get("content") if isinstance(image, dict) else None,
                    )
                    self._redirect("/mypage", status=303)
                elif path.startswith("/api/photos/") and path.endswith("/delete"):
                    image_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    app.service.delete_user_restaurant_image(
                        int(current_user["id"]),
                        image_id,
                    )
                    self._redirect("/mypage", status=303)
                elif path.startswith("/api/restaurants/") and path.endswith("/save"):
                    restaurant_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    result = app.service.save_restaurant(int(current_user["id"]), restaurant_id)
                    if self._wants_json():
                        self._json(result, status=201)
                    else:
                        self._redirect(
                            self._safe_return_to(str(payload.get("return_to", "/mypage")))
                        )
                elif path.startswith("/api/restaurants/") and path.endswith("/unsave"):
                    restaurant_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    result = app.service.unsave_restaurant(int(current_user["id"]), restaurant_id)
                    if self._wants_json():
                        self._json(result)
                    else:
                        self._redirect(
                            self._safe_return_to(str(payload.get("return_to", "/mypage")))
                        )
                elif path.startswith("/api/restaurants/") and path.endswith("/reviews"):
                    restaurant_id = int(path.split("/")[3])
                    current_user = self._current_user()
                    ai_processing_consent = str(
                        payload.get("ai_processing_consent", "")
                    ).strip().lower() in {"1", "true", "on", "yes"}
                    if not ai_processing_consent:
                        raise AppError(400, "AI review processing consent is required")
                    review = app.service.add_review(
                        restaurant_id=restaurant_id,
                        rating=int(payload.get("rating", 0)),
                        body=str(payload.get("body", "")),
                        reviewer_label=(
                            str(current_user["display_name"])
                            if current_user
                            else str(payload.get("reviewer_label", "방문자"))
                        ),
                        context=context,
                        ai_processing_consent=ai_processing_consent,
                    )
                    self._json(review, status=201)
                elif path.startswith("/api/reviews/") and path.endswith("/reaction"):
                    review_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    self._json(
                        app.service.react_to_review(
                            int(current_user["id"]),
                            review_id,
                            str(payload.get("reaction", "")),
                        )
                    )
                elif path.startswith("/api/reviews/") and path.endswith("/report"):
                    review_id = int(path.split("/")[3])
                    report = app.service.report_review(
                        review_id,
                        reason=str(payload.get("reason", "")),
                        context=context,
                    )
                    self._json(report, status=201)
                elif path.startswith("/api/reviews/") and path.endswith("/delete"):
                    review_id = int(path.split("/")[3])
                    current_user = self._authenticated_user()
                    result = app.service.delete_own_review(int(current_user["id"]), review_id)
                    if self._wants_json():
                        self._json(result)
                    else:
                        self._redirect(
                            self._safe_return_to(str(payload.get("return_to", "/mypage")))
                        )
                elif path.startswith("/review/") and path.endswith("/candidate"):
                    review_id = int(path.split("/")[2])
                    self._json(
                        app.service.update_review_candidate(
                            review_id,
                            context,
                            original_place_name=str(payload.get("review_place_name", payload.get("original_place_name", ""))),
                            original_address=str(payload.get("review_address", payload.get("original_address", ""))),
                            place_major_category=str(payload.get("review_major_category", payload.get("place_major_category", "restaurant"))),
                        )
                    )
                elif path.startswith("/review/") and path.endswith("/approve-new"):
                    review_id = int(path.split("/")[2])
                    self._json(
                        app.service.approve_new(
                            review_id,
                            context,
                            reviewer_note=str(payload.get("reviewer_note", "")),
                            verification_id=int(payload["verification_id"]) if payload.get("verification_id") else None,
                        )
                    )
                elif path.startswith("/review/") and "/merge/" in path:
                    parts = path.split("/")
                    review_id = int(parts[2])
                    restaurant_id = int(parts[4])
                    self._json(
                        app.service.merge_candidate(
                            review_id,
                            restaurant_id,
                            context,
                            reviewer_note=str(payload.get("reviewer_note", "")),
                        )
                    )
                elif path.startswith("/review/") and path.endswith("/reject"):
                    review_id = int(path.split("/")[2])
                    self._json(
                        app.service.reject_candidate(
                            review_id,
                            context,
                            reason=str(payload.get("reason", "manual_reject")),
                            reviewer_note=str(payload.get("reviewer_note", "")),
                        )
                    )
                elif path.startswith("/admin/candidates/") and path.endswith("/refresh"):
                    candidate_id = int(path.split("/")[3])
                    self._json(
                        app.service.refresh_admin_candidate_providers(
                            candidate_id,
                            context,
                            review_place_name=str(payload.get("review_place_name", payload.get("original_place_name", ""))),
                            review_address=str(payload.get("review_address", payload.get("original_address", ""))),
                            review_major_category=str(payload.get("review_major_category", payload.get("place_major_category", "restaurant"))),
                            reviewer_note=str(payload.get("reviewer_note", "")),
                        )
                    )
                elif path.startswith("/admin/candidates/") and path.endswith("/geocode"):
                    candidate_id = int(path.split("/")[3])
                    self._json(
                        app.service.geocode_admin_candidate(
                            candidate_id,
                            context,
                            review_place_name=str(payload.get("review_place_name", payload.get("original_place_name", ""))),
                            review_address=str(payload.get("review_address", payload.get("original_address", ""))),
                            review_major_category=str(payload.get("review_major_category", payload.get("place_major_category", "restaurant"))),
                            reviewer_note=str(payload.get("reviewer_note", "")),
                        )
                    )
                elif path.startswith("/admin/candidates/"):
                    candidate_id = int(path.split("/")[3])
                    self._json(
                        app.service.update_admin_candidate(
                            candidate_id,
                            context,
                            review_place_name=str(payload.get("review_place_name", payload.get("original_place_name", ""))),
                            review_address=str(payload.get("review_address", payload.get("original_address", ""))),
                            review_major_category=str(payload.get("review_major_category", payload.get("place_major_category", "restaurant"))),
                            target_status=str(payload.get("target_status", "needs_review")),
                            rejection_reason=str(payload.get("rejection_reason", "manual_reject")),
                            reviewer_note=str(payload.get("reviewer_note", "")),
                            verification_id=int(payload["selected_verification_id"])
                            if payload.get("selected_verification_id")
                            else None,
                            allow_category_override=payload.get("allow_category_override") is True,
                        )
                    )
                elif path == "/account/delete":
                    current_user = self._authenticated_user()
                    session_token = self._cookies().get(SESSION_COOKIE, "")
                    app.service.enforce_security_rate_limit(
                        "account-delete-user",
                        str(int(current_user["id"])),
                        limit=ACCOUNT_DELETE_RATE_LIMIT,
                        window_seconds=ACCOUNT_DELETE_RATE_WINDOW_SECONDS,
                    )
                    app.service.enforce_security_rate_limit(
                        "account-delete-ip",
                        context.ip,
                        limit=ACCOUNT_DELETE_IP_RATE_LIMIT,
                        window_seconds=ACCOUNT_DELETE_RATE_WINDOW_SECONDS,
                    )
                    if str(payload.get("confirmation", "")).strip() != "계정 삭제":
                        if self._wants_json():
                            raise AppError(400, "account deletion confirmation is required")
                        self._redirect("/mypage?account_error=confirmation")
                        return
                    if not app.service.consume_one_time_action_token(
                        int(current_user["id"]),
                        session_token,
                        ACCOUNT_DELETE_ACTION,
                        str(payload.get("action_token", "")),
                    ):
                        raise AppError(403, "invalid or expired account action token")
                    intent_token = app.service.create_account_delete_intent(
                        int(current_user["id"]),
                        return_to="/login?account_deleted=1&provider_revocation=pending",
                    )
                    self._auth_start(
                        "naver",
                        {
                            "return_to": "/login?account_deleted=1&provider_revocation=pending",
                            "purpose": "account_delete",
                            "intent": intent_token,
                        },
                    )
                    return
                elif path == "/auth/logout":
                    session_token = self._cookies().get(SESSION_COOKIE, "")
                    app.service.revoke_session(session_token)
                    expired_session = self._expire_cookie(SESSION_COOKIE, path="/")
                    if self._wants_json():
                        self._json(
                            {"result": "logged_out"},
                            cookies=[expired_session],
                        )
                    else:
                        self._redirect(
                            self._safe_return_to(str(payload.get("return_to", "/"))),
                            cookies=[expired_session],
                        )
                elif path.startswith("/admin/accounts/") and path.endswith("/delete"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise AppError(404, "not found")
                    session_token = self._cookies().get(SESSION_COOKIE, "")
                    if not app.session_codec.verify_action_token(
                        session_token,
                        ADMIN_ACCOUNT_UPDATE_ACTION,
                        str(payload.get("action_token", "")),
                    ):
                        raise AppError(403, "invalid admin account action token")
                    if str(payload.get("confirmation", "")).strip() != "계정 삭제":
                        raise AppError(400, "account deletion confirmation is required")
                    current_user = self._admin_user()
                    self._json(
                        app.service.admin_delete_account(
                            int(parts[2]),
                            actor_user_id=int(current_user["id"]),
                        )
                    )
                elif path.startswith("/admin/accounts/") and path.endswith("/merge"):
                    user_id = int(path.split("/")[3])
                    target_user_id = int(payload.get("target_user_id", 0))
                    self._json(app.service.merge_account(user_id, target_user_id, context))
                elif path.startswith("/admin/accounts/"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 3:
                        raise AppError(404, "not found")
                    session_token = self._cookies().get(SESSION_COOKIE, "")
                    if not app.session_codec.verify_action_token(
                        session_token,
                        ADMIN_ACCOUNT_UPDATE_ACTION,
                        str(payload.get("action_token", "")),
                    ):
                        raise AppError(403, "invalid admin account action token")
                    current_user = self._admin_user()
                    self._json(
                        app.service.admin_update_account(
                            int(parts[2]),
                            role=str(payload.get("role", "")),
                            status=str(payload.get("status", "")),
                            actor_user_id=int(current_user["id"]),
                        )
                    )
                else:
                    raise AppError(404, "not found")
            except AppError as exc:
                if path.startswith("/admin/notices") and not self._notice_wants_json():
                    self._html(notice_error_index(exc.message), status=exc.status, strict_notice_policy=True)
                else:
                    self._error(exc.status, exc.message)
            except ValueError:
                self._error(400, "invalid request")
            except Exception as exc:  # pragma: no cover - server guard
                self._internal_error(exc)

        def _provider_configured(self, provider: str) -> bool:
            if provider == "naver":
                return bool(
                    app.settings.naver_login_client_id
                    and app.settings.naver_login_client_secret
                    and app.settings.naver_login_redirect_uri
                )
            if provider == "google":
                return bool(
                    app.settings.google_client_id
                    and app.settings.google_client_secret
                    and app.settings.google_redirect_uri
                )
            return False

        def _auth_start(self, provider: str, query: dict[str, str]) -> None:
            if provider not in {"google", "naver"}:
                raise AppError(404, "unsupported provider")
            return_to = self._safe_return_to(query.get("return_to", "/"))
            purpose = str(query.get("purpose", "")).strip()
            intent_token = str(query.get("intent", "")).strip()
            if purpose and purpose != "account_delete":
                raise AppError(400, "unsupported OAuth purpose")
            if purpose == "account_delete" and (provider != "naver" or not intent_token):
                raise AppError(400, "account deletion intent is required")
            if not self._provider_configured(provider):
                if self._wants_json():
                    self._json(
                        {
                            "provider": provider,
                            "configured": False,
                            "error": "provider credentials are missing",
                        },
                        status=503,
                    )
                else:
                    self._redirect("/login?error=not_configured")
                return

            state = secrets.token_urlsafe(16)
            if purpose != "account_delete":
                app.service.create_oauth_login_intent(
                    provider,
                    state,
                    return_to=return_to,
                    ttl_seconds=OAUTH_COOKIE_MAX_AGE_SECONDS,
                )
            if provider == "naver":
                authorization_params = {
                    "response_type": "code",
                    "client_id": app.settings.naver_login_client_id,
                    "redirect_uri": app.settings.naver_login_redirect_uri,
                    "state": state,
                }
                if purpose == "account_delete":
                    authorization_params["auth_type"] = "reauthenticate"
                params = urlencode(authorization_params)
                authorization_url = f"https://nid.naver.com/oauth2.0/authorize?{params}"
            else:
                params = urlencode(
                    {
                        "response_type": "code",
                        "client_id": app.settings.google_client_id,
                        "redirect_uri": app.settings.google_redirect_uri,
                        "scope": "openid profile",
                        "state": state,
                        "access_type": "online",
                    }
                )
                authorization_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"

            callback_path = f"/auth/callback/{provider}"
            cookies = [
                self._set_cookie(
                    OAUTH_STATE_COOKIE,
                    state,
                    max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
                    path=callback_path,
                ),
                self._set_cookie(
                    OAUTH_RETURN_COOKIE,
                    quote(return_to, safe=""),
                    max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
                    path=callback_path,
                ),
                self._set_cookie(
                    OAUTH_PROVIDER_COOKIE,
                    provider,
                    max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
                    path=callback_path,
                ),
                self._set_cookie(
                    OAUTH_PURPOSE_COOKIE,
                    purpose,
                    max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
                    path=callback_path,
                ),
                self._set_cookie(
                    OAUTH_INTENT_COOKIE,
                    intent_token,
                    max_age=OAUTH_COOKIE_MAX_AGE_SECONDS,
                    path=callback_path,
                ),
            ]
            if self._wants_json():
                self._json(
                    {
                        "provider": provider,
                        "configured": True,
                        "authorization_url": authorization_url,
                    },
                    cookies=cookies,
                )
            else:
                self._redirect(authorization_url, cookies=cookies, status=302)

        def _auth_callback(self, provider: str, query: dict[str, str]) -> None:
            if provider not in {"google", "naver"}:
                raise AppError(404, "unsupported provider")
            callback_path = f"/auth/callback/{provider}"
            oauth_cookies = self._oauth_expired_cookies(callback_path)
            cookies = self._cookies()
            stored_state = cookies.get(OAUTH_STATE_COOKIE, "")
            returned_state = query.get("state", "")
            stored_provider = cookies.get(OAUTH_PROVIDER_COOKIE, "")
            purpose = cookies.get(OAUTH_PURPOSE_COOKIE, "")
            intent_token = cookies.get(OAUTH_INTENT_COOKIE, "")
            if (
                not stored_state
                or not returned_state
                or stored_provider != provider
                or not hmac.compare_digest(stored_state, returned_state)
            ):
                self._auth_failure("expired", oauth_cookies, status=401)
                return

            if purpose != "account_delete":
                try:
                    app.service.consume_oauth_login_intent(provider, returned_state)
                except AppError:
                    self._auth_failure("expired", oauth_cookies, status=401)
                    return

            if query.get("error"):
                self._auth_failure("cancelled", oauth_cookies, status=400)
                return
            if not query.get("code"):
                self._auth_failure("provider_error", oauth_cookies, status=400)
                return

            try:
                if provider == "naver":
                    if app.naver_login_client is None:
                        raise IntegrationError("Naver Login is not configured")
                    login_response = app.naver_login_client.exchange_code(
                        query["code"], returned_state
                    )
                else:
                    login_response = GoogleOAuthClient(
                        app.settings.google_client_id,
                        app.settings.google_client_secret,
                        app.settings.google_redirect_uri,
                    ).exchange_code(query["code"])
            except IntegrationError:
                self._auth_failure("provider_error", oauth_cookies, status=502)
                return

            if isinstance(login_response, OAuthProfile):
                login_result = OAuthLoginResult(login_response, access_token="")
            else:
                login_result = login_response
            profile = login_result.profile

            if purpose == "account_delete":
                if provider != "naver" or not intent_token:
                    self._auth_failure("expired", oauth_cookies, status=401)
                    return
                try:
                    result = app.service.complete_account_delete_intent(
                        intent_token,
                        provider_subject=profile.subject,
                        access_token=login_result.access_token,
                        refresh_token=login_result.refresh_token,
                    )
                except AppError as exc:
                    error = "account_mismatch" if exc.status == 409 else "expired"
                    self._auth_failure(error, oauth_cookies, status=exc.status)
                    return
                expired_session = self._expire_cookie(SESSION_COOKIE, path="/")
                response_cookies = [*oauth_cookies, expired_session]
                if self._wants_json():
                    self._json(
                        {
                            "result": "deletion_pending",
                            "provider_revocation": result.get("provider_revocation"),
                            "lifecycle_job_id": result.get("lifecycle_job_id"),
                        },
                        status=HTTPStatus.ACCEPTED,
                        cookies=response_cookies,
                    )
                    return
                return_to = self._safe_return_to(
                    unquote(cookies.get(OAUTH_RETURN_COOKIE, "/login?account_deleted=1"))
                )
                self._redirect(return_to, cookies=response_cookies)
                return

            try:
                account = app.service.upsert_oauth_account(
                    provider=profile.provider,
                    provider_subject=profile.subject,
                    display_name=profile.display_name,
                    access_token=login_result.access_token,
                    refresh_token=login_result.refresh_token,
                )
            except AppError as exc:
                if exc.status == 423:
                    expired_session = self._expire_cookie(SESSION_COOKIE, path="/")
                    self._auth_failure(
                        "account_deletion_pending",
                        [*oauth_cookies, expired_session],
                        status=423,
                    )
                    return
                raise
            session_cookie = self._set_cookie(
                SESSION_COOKIE,
                app.issue_session(int(account["user"]["id"])),
                max_age=SESSION_MAX_AGE_SECONDS,
                path="/",
            )
            response_cookies = [*oauth_cookies, session_cookie]
            if self._wants_json():
                user = account["user"]
                self._json(
                    {
                        "user": {
                            "id": int(user["id"]),
                            "display_name": user["display_name"],
                            "role": user["role"],
                            "status": user["status"],
                        },
                        "created": bool(account["created"]),
                    },
                    cookies=response_cookies,
                )
                return
            return_to = self._safe_return_to(
                unquote(cookies.get(OAUTH_RETURN_COOKIE, "/"))
            )
            self._redirect(return_to, cookies=response_cookies)

        def _auth_failure(
            self,
            error: str,
            cookies: list[str],
            status: int,
        ) -> None:
            if self._wants_json():
                self._json({"error": error, "status": status}, status=status, cookies=cookies)
            else:
                self._redirect(f"/login?error={error}", cookies=cookies)

        def _oauth_expired_cookies(self, callback_path: str) -> list[str]:
            return [
                self._expire_cookie(cookie_name, path=callback_path)
                for cookie_name in (
                    OAUTH_STATE_COOKIE,
                    OAUTH_RETURN_COOKIE,
                    OAUTH_PROVIDER_COOKIE,
                    OAUTH_PURPOSE_COOKIE,
                    OAUTH_INTENT_COOKIE,
                )
            ]

        def _naver_deauthorize(self) -> None:
            if not (
                app.settings.naver_login_client_id
                and app.settings.naver_login_client_secret
                and app.settings.oauth_subject_hmac_secret
            ):
                raise AppError(503, "Naver disconnect is not configured")
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0") or 0)
            if (
                length <= 0
                or length > MAX_NAVER_DISCONNECT_POST_BYTES
                or not content_type.startswith("application/x-www-form-urlencoded")
            ):
                raise AppError(400, "invalid Naver disconnect request")
            payload = self._payload()
            try:
                verified = verify_naver_disconnect_payload(
                    {key: str(value) for key, value in payload.items()},
                    client_id=app.settings.naver_login_client_id,
                    client_secret=app.settings.naver_login_client_secret,
                    subject_hmac_secret=app.settings.oauth_subject_hmac_secret,
                )
            except PermissionError as exc:
                raise AppError(403, "forbidden Naver disconnect request") from exc
            except ValueError as exc:
                raise AppError(400, "invalid Naver disconnect request") from exc
            app.service.accept_naver_disconnect(
                provider_subject=verified.provider_subject,
                provider_timestamp=verified.provider_timestamp,
                event_digest=verified.event_digest,
            )
            self._no_content()

        def _static(self, relative_path: str) -> None:
            safe_path = (STATIC_DIR / relative_path).resolve()
            if STATIC_DIR not in safe_path.parents or not safe_path.exists() or not safe_path.is_file():
                raise AppError(404, "static file not found")
            body = safe_path.read_bytes()
            content_type = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _restaurant_image_media(self, storage_key: str) -> None:
            safe_path = app.service.public_admin_image_file_path(storage_key)
            self._image_media(safe_path)

        def _restaurant_user_image_media(self, public_name: str) -> None:
            safe_path = app.service.public_user_image_file_path(public_name)
            self._image_media(safe_path)

        def _image_media(self, safe_path: Path) -> None:
            if not safe_path.exists() or not safe_path.is_file():
                raise AppError(404, "restaurant image not found")
            body = safe_path.read_bytes()
            content_type = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
            self.send_response(200)
            self._security_headers()
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _query(self, raw_query: str) -> dict[str, str]:
            return {key: values[-1] for key, values in parse_qs(raw_query).items()}

        def _notice_post(self, path: str, payload: dict) -> None:
            actor_user_id = int(self._admin_user()["id"])
            if path == "/admin/notices":
                notice = app.notice_service.create(
                    actor_user_id,
                    kind=payload.get("kind", ""),
                    title=payload.get("title", ""),
                    body=payload.get("body", ""),
                )
                status = HTTPStatus.CREATED
            else:
                parts = path.strip("/").split("/")
                if len(parts) != 4 or parts[:2] != ["admin", "notices"]:
                    raise AppError(404, "not found")
                notice_id = int(parts[2])
                action = parts[3]
                raw_revision = payload.get("revision", 0)
                if isinstance(raw_revision, bool):
                    raise AppError(400, "invalid notice revision")
                try:
                    revision = int(raw_revision)
                except (TypeError, ValueError) as exc:
                    raise AppError(400, "invalid notice revision") from exc
                if action == "save":
                    notice = app.notice_service.update(
                        actor_user_id,
                        notice_id,
                        revision,
                        kind=payload.get("kind", ""),
                        title=payload.get("title", ""),
                        body=payload.get("body", ""),
                    )
                else:
                    if action == "delete" and str(payload.get("confirmation", "")).strip() != "공지 삭제":
                        raise AppError(400, "notice deletion confirmation is required")
                    notice = app.notice_service.transition(actor_user_id, notice_id, revision, action)
                status = HTTPStatus.OK
            if self._notice_wants_json():
                self._json(
                    {
                        "id": int(notice["id"]),
                        "revision": int(notice["revision"]),
                        "status": notice["status"],
                        "deleted": notice["deleted_at"] is not None,
                    },
                    status=status,
                )
            else:
                self._redirect(f'/admin/notices/{int(notice["id"])}')

        def _notice_wants_json(self) -> bool:
            return self._wants_json() or self.headers.get("Content-Type", "").startswith("application/json")

        def _payload(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length == 0:
                return {}
            content_type = self.headers.get("Content-Type", "")
            if content_type.startswith("multipart/form-data"):
                max_length = app.service.ADMIN_IMAGE_MAX_BYTES + (1024 * 1024)
                if length > max_length:
                    raise AppError(413, "image upload request is too large")
                return self._multipart_payload(length, content_type)
            if length > MAX_REGULAR_POST_BYTES:
                raise AppError(413, "request body is too large")
            raw = self.rfile.read(length).decode("utf-8")
            if content_type.startswith("application/x-www-form-urlencoded"):
                return {key: values[-1] for key, values in parse_qs(raw).items()}
            return json.loads(raw or "{}")

        def _multipart_payload(self, length: int, content_type: str) -> dict:
            raw = self.rfile.read(length)
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: "
                + content_type.encode("ascii", errors="ignore")
                + b"\r\nMIME-Version: 1.0\r\n\r\n"
                + raw
            )
            if not message.is_multipart():
                raise AppError(400, "invalid multipart request")
            payload: dict[str, object] = {}
            for part in message.iter_parts():
                if part.get_content_disposition() != "form-data":
                    continue
                field_name = part.get_param("name", header="content-disposition")
                if not field_name:
                    continue
                content = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename is not None:
                    payload[str(field_name)] = {
                        "filename": filename,
                        "content_type": part.get_content_type(),
                        "content": content,
                    }
                else:
                    charset = part.get_content_charset() or "utf-8"
                    payload[str(field_name)] = content.decode(charset, errors="replace")
            return payload

        def _context(self, payload: dict) -> RequestContext:
            current_user = self._current_user()
            user_id = int(current_user["id"]) if current_user else None
            return RequestContext(
                ip=_trusted_proxy_client_ip(
                    self.client_address[0],
                    self.headers.get("X-Real-IP", ""),
                ),
                user_id=user_id,
                actor_id=f"user:{user_id}" if user_id else str(payload.get("actor_id", "local-admin")),
            )

        def _cookies(self) -> dict[str, str]:
            raw_cookie = self.headers.get("Cookie", "")
            if not raw_cookie:
                return {}
            parsed = SimpleCookie()
            try:
                parsed.load(raw_cookie)
            except Exception:
                return {}
            return {key: morsel.value for key, morsel in parsed.items()}

        def _current_user(self) -> dict | None:
            token = self._cookies().get(SESSION_COOKIE, "")
            return app.session_user(token)

        def _require_session_csrf(self, payload: dict) -> None:
            session_token = self._cookies().get(SESSION_COOKIE, "")
            if self._current_user() is None:
                return
            supplied_token = str(
                self.headers.get("X-CSRF-Token", "")
                or payload.get("csrf_token", "")
            )
            if not app.session_codec.verify_action_token(
                session_token,
                SESSION_CSRF_ACTION,
                supplied_token,
            ):
                raise AppError(403, "invalid CSRF token")

        def _authenticated_user(self) -> dict:
            current_user = self._current_user()
            if current_user is None:
                raise AppError(401, "login required")
            return current_user

        def _admin_user(self) -> dict:
            current_user = self._authenticated_user()
            if str(current_user.get("role") or "").lower() != "admin":
                raise AppError(403, "admin access required")
            return current_user

        def _is_admin_route(self, path: str) -> bool:
            return path in {"/admin", "/ops", "/review"} or path.startswith(
                ("/admin/", "/ops/", "/review/")
            )

        def _safe_return_to(self, value: str) -> str:
            value = (value or "/").strip()
            parsed = urlparse(value)
            if (
                not value.startswith("/")
                or value.startswith("//")
                or parsed.scheme
                or parsed.netloc
                or "\\" in value
                or "\r" in value
                or "\n" in value
            ):
                return "/"
            return value

        def _secure_cookies(self) -> bool:
            return app.settings.app_env == "production"

        def _set_cookie(self, name: str, value: str, max_age: int, path: str) -> str:
            cookie = SimpleCookie()
            cookie[name] = value
            morsel = cookie[name]
            morsel["path"] = path
            morsel["max-age"] = str(max_age)
            morsel["httponly"] = True
            morsel["samesite"] = "Lax"
            if self._secure_cookies():
                morsel["secure"] = True
            return morsel.OutputString()

        def _expire_cookie(self, name: str, path: str) -> str:
            cookie = SimpleCookie()
            cookie[name] = ""
            morsel = cookie[name]
            morsel["path"] = path
            morsel["max-age"] = "0"
            morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
            morsel["httponly"] = True
            morsel["samesite"] = "Lax"
            if self._secure_cookies():
                morsel["secure"] = True
            return morsel.OutputString()

        def _wants_json(self) -> bool:
            return "application/json" in self.headers.get("Accept", "")

        def _path_int(self, path: str, prefix: str) -> int:
            tail = path.removeprefix(prefix).strip("/")
            if "/" in tail:
                tail = tail.split("/", 1)[0]
            return int(tail)

        def _json(
            self,
            payload: object,
            status: int = 200,
            cookies: list[str] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self._security_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for cookie in cookies or []:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _no_content(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._security_headers()
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _html(self, payload: str, status: int = 200, *, strict_notice_policy: bool = False) -> None:
            session_token = self._cookies().get(SESSION_COOKIE, "")
            if self._current_user() is not None:
                csrf_token = app.session_codec.issue_action_token(
                    session_token,
                    SESSION_CSRF_ACTION,
                )
                escaped_token = html.escape(csrf_token, quote=True)
                payload = payload.replace(
                    "<head>",
                    f'<head>\n  <meta name="csrf-token" content="{escaped_token}">',
                    1,
                )
                payload = re.sub(
                    r'(<form\b(?=[^>]*\bmethod=["\']post["\'])[^>]*>)',
                    lambda match: (
                        f'{match.group(1)}\n'
                        f'        <input type="hidden" name="csrf_token" value="{escaped_token}">'
                    ),
                    payload,
                    flags=re.IGNORECASE,
                )
            body = payload.encode("utf-8")
            self.send_response(status)
            self._security_headers(strict_notice_policy=strict_notice_policy)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(
            self,
            location: str,
            cookies: list[str] | None = None,
            status: int = 303,
        ) -> None:
            self.send_response(status)
            self._security_headers()
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            for cookie in cookies or []:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _security_headers(self, *, strict_notice_policy: bool = False) -> None:
            self.send_header(
                "Content-Security-Policy",
                (
                    "default-src 'self'; script-src 'none'; style-src 'self'; img-src 'self'; "
                    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
                    if strict_notice_policy else
                    "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
                ),
            )
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header(
                "Permissions-Policy",
                "camera=(), microphone=(), geolocation=()",
            )

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message, "status": status}, status=status)

        def _internal_error(self, exc: Exception) -> None:
            request_id = secrets.token_urlsafe(9)
            # PostgreSQL errors may echo bound values. Keep the exception type
            # and traceback for diagnosis without recording its potentially
            # sensitive message (OAuth subjects and tokens must never leak).
            LOGGER.error(
                "Unhandled request error request_id=%s method=%s path=%s type=%s traceback=%s",
                request_id,
                self.command,
                urlparse(self.path).path,
                type(exc).__name__,
                "".join(traceback.format_tb(exc.__traceback__)),
            )
            self._json(
                {
                    "error": "internal server error",
                    "status": 500,
                    "request_id": request_id,
                },
                status=500,
            )

    return Handler


def serve(settings: Settings) -> ThreadingHTTPServer:
    app = PublicRestaurantApplication(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(app))
    return server
