from __future__ import annotations

import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from .config import Settings
from .database import Database
from .integrations import (
    GeocodingNaverClient,
    GoogleOAuthClient,
    IntegrationError,
    NaverLoginClient,
    NaverMapsGeocodingClient,
    NaverSearchLocalClient,
)
from .pipeline import (
    COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS,
    COLLECTION_SCAN_SAFETY_MAX_PAGES,
    BusanCityLiveAdapter,
    DailyPipeline,
)
from .progress import VerificationProgressStore
from .services import AppError, RequestContext, RestaurantService
from .views import (
    admin_document_detail_index,
    admin_documents_index,
    admin_index,
    admin_review_index,
    admin_workflow_index,
    map_issues_index,
    ops_logs_index,
    public_index,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"


class PublicRestaurantApplication:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings.db_path)
        self.database.initialize()
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
        self.service = RestaurantService(
            self.database,
            review_rate_limit_per_hour=settings.review_rate_limit_per_hour,
            geocoding_client=geocoding_client,
            naver_client=naver_client,
        )
        self.verification_progress = VerificationProgressStore()

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

    def verify_pending(self, limit: int = 100, sort: str = "verification_oldest") -> dict:
        return DailyPipeline(
            self.database,
            settings=self.settings,
            verification_progress_callback=self.verification_progress.update,
        ).verify_pending(limit=limit, sort=sort)

    def verify_collected(self, limit: int = 100, sort: str = "verification_oldest") -> dict:
        return DailyPipeline(
            self.database,
            settings=self.settings,
            verification_progress_callback=self.verification_progress.update,
        ).verify_collected(limit=limit, sort=sort)

    def verification_status(self) -> dict:
        missing_env = {
            "naver_map_js": [] if self.settings.naver_map_key else ["NAVER_MAP_KEY 또는 NAVER_MAPS_CLIENT_ID"],
            "naver_search": self._missing_env(
                {
                    "NAVER_SEARCH_CLIENT_ID": self.settings.naver_search_client_id,
                    "NAVER_SEARCH_CLIENT_SECRET": self.settings.naver_search_client_secret,
                }
            ),
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
                "naver_maps_geocoding": bool(
                    self.settings.naver_maps_client_id and self.settings.naver_maps_client_secret
                ),
                "data_go_kr_permit": bool(self.settings.data_go_kr_service_key),
            },
            "missing_env": missing_env,
            "overview": self.service.verification_overview(),
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
                if path == "/":
                    self._html(public_index(app.settings.naver_map_key, app.settings.app_name))
                elif path in {"/admin", "/admin/dashboard"}:
                    self._html(admin_index())
                elif path == "/admin/collection":
                    self._html(admin_workflow_index("collection"))
                elif path in {"/admin/parsing", "/admin/workflow"}:
                    self._html(admin_workflow_index("parsing"))
                elif path == "/admin/review":
                    self._html(admin_workflow_index("review"))
                elif path in {"/admin/review/results", "/admin/review/queue"}:
                    self._html(admin_review_index())
                elif path == "/admin/map-issues":
                    self._html(map_issues_index())
                elif path == "/admin/logs":
                    self._html(ops_logs_index())
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
                elif path == "/api/map/restaurants":
                    self._json(
                        {
                            "restaurants": app.service.list_map_restaurants(
                                q=query.get("q", ""),
                                category=query.get("category", ""),
                                min_visit_count=query.get("min_visit_count", ""),
                                search_mode=query.get("search_mode", ""),
                                region=query.get("region", ""),
                                bounds=query.get("bounds", ""),
                            )
                        }
                    )
                elif path.startswith("/api/restaurants/"):
                    restaurant_id = self._path_int(path, "/api/restaurants/")
                    self._json(app.service.get_restaurant(restaurant_id))
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
                    self._auth_start(provider)
                elif path.startswith("/auth/callback/"):
                    provider = path.removeprefix("/auth/callback/")
                    self._auth_callback(provider, query)
                elif path.startswith("/ops/batches/"):
                    batch_id = self._path_int(path, "/ops/batches/")
                    self._json(app.service.batch(batch_id))
                elif path == "/ops/sources":
                    self._json(app.service.source_registry())
                elif path == "/ops/verification-status":
                    self._json(app.verification_status())
                elif path == "/ops/verification-progress":
                    self._json(app.verification_progress.snapshot())
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
            except Exception as exc:  # pragma: no cover - server guard
                self._error(500, str(exc))

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                payload = self._payload()
                context = self._context(payload)
                if path == "/ops/run-daily":
                    self._json(app.run_daily(), status=201)
                elif path == "/ops/run-busan-live":
                    self._json(
                        app.run_busan_live(
                            start_date=str(payload.get("start_date", "")),
                            end_date=str(payload.get("end_date", "")),
                        ),
                        status=201,
                    )
                elif path == "/ops/collection-plans":
                    self._json(
                        app.create_collection_plan(
                            start_date=str(payload.get("start_date", "")),
                            end_date=str(payload.get("end_date", "")),
                            batch_size=int(payload.get("batch_size", 20) or 20),
                        ),
                        status=201,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/run"):
                    plan_id = int(path.split("/")[3])
                    repeat = bool(payload.get("repeat", True))
                    if repeat:
                        self._json(
                            app.run_collection_plan_batches(
                                plan_id,
                                batch_size=int(payload["batch_size"]) if payload.get("batch_size") else None,
                                max_batches=int(payload.get("max_batches", 100) or 100),
                            ),
                            status=201,
                        )
                        return
                    self._json(
                        app.run_collection_plan_batch(
                            plan_id,
                            batch_size=int(payload["batch_size"]) if payload.get("batch_size") else None,
                        ),
                        status=201,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/retry-failed"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.retry_collection_plan_failures(
                            plan_id,
                            batch_size=int(payload["batch_size"]) if payload.get("batch_size") else None,
                            max_batches=int(payload.get("max_batches", 100) or 100),
                        ),
                        status=201,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/parse"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.parse_collection_plan_batches(
                            plan_id,
                            batch_size=int(payload["batch_size"]) if payload.get("batch_size") else None,
                            max_batches=int(payload.get("max_batches", 100) or 100),
                        ),
                        status=201,
                    )
                elif path.startswith("/ops/collection-plans/") and path.endswith("/retry-parse-failed"):
                    plan_id = int(path.split("/")[3])
                    self._json(
                        app.retry_collection_plan_parse_failures(
                            plan_id,
                            batch_size=int(payload["batch_size"]) if payload.get("batch_size") else None,
                            max_batches=int(payload.get("max_batches", 100) or 100),
                        ),
                        status=201,
                    )
                elif path == "/ops/verify-pending":
                    self._json(
                        app.verify_pending(
                            limit=int(payload.get("limit", 100)),
                            sort=str(payload.get("sort", "verification_oldest")),
                        ),
                        status=201,
                    )
                elif path == "/ops/verify-collected":
                    self._json(
                        app.verify_collected(
                            limit=int(payload.get("limit", 100)),
                            sort=str(payload.get("sort", "verification_oldest")),
                        ),
                        status=201,
                    )
                elif path.startswith("/api/restaurants/") and path.endswith("/reviews"):
                    restaurant_id = int(path.split("/")[3])
                    review = app.service.add_review(
                        restaurant_id=restaurant_id,
                        rating=int(payload.get("rating", 0)),
                        body=str(payload.get("body", "")),
                        reviewer_label=str(payload.get("reviewer_label", "방문자")),
                        context=context,
                    )
                    self._json(review, status=201)
                elif path.startswith("/api/reviews/") and path.endswith("/report"):
                    review_id = int(path.split("/")[3])
                    report = app.service.report_review(
                        review_id,
                        reason=str(payload.get("reason", "")),
                        context=context,
                    )
                    self._json(report, status=201)
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
                        )
                    )
                elif path == "/auth/logout":
                    self._json({"result": "logged_out"})
                elif path.startswith("/admin/accounts/") and path.endswith("/merge"):
                    user_id = int(path.split("/")[3])
                    target_user_id = int(payload.get("target_user_id", 0))
                    self._json(app.service.merge_account(user_id, target_user_id, context))
                else:
                    raise AppError(404, "not found")
            except AppError as exc:
                self._error(exc.status, exc.message)
            except ValueError:
                self._error(400, "invalid request")
            except Exception as exc:  # pragma: no cover - server guard
                self._error(500, str(exc))

        def _auth_start(self, provider: str) -> None:
            if provider not in {"google", "naver"}:
                raise AppError(404, "unsupported provider")
            configured = False
            state = secrets.token_urlsafe(16)
            if provider == "naver" and app.settings.naver_login_client_id:
                configured = True
                params = urlencode(
                    {
                        "response_type": "code",
                        "client_id": app.settings.naver_login_client_id,
                        "redirect_uri": app.settings.naver_login_redirect_uri,
                        "state": state,
                    }
                )
                callback = f"https://nid.naver.com/oauth2.0/authorize?{params}"
            elif provider == "google" and app.settings.google_client_id:
                configured = True
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
                callback = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
            else:
                callback = f"/auth/callback/{provider}?sub=demo-{provider}&name={quote(provider + ' 사용자')}"
            self._json(
                {
                    "provider": provider,
                    "configured": configured,
                    "authorization_url": callback,
                    "note": "Live OAuth start URL generated." if configured else "Set provider credentials to enable live OAuth.",
                }
            )

        def _auth_callback(self, provider: str, query: dict[str, str]) -> None:
            if query.get("code"):
                try:
                    if provider == "naver":
                        if not app.settings.naver_login_client_id or not app.settings.naver_login_client_secret:
                            raise AppError(400, "Naver Login credentials are missing")
                        profile = NaverLoginClient(
                            app.settings.naver_login_client_id,
                            app.settings.naver_login_client_secret,
                            app.settings.naver_login_redirect_uri,
                        ).exchange_code(query["code"], query.get("state", ""))
                    elif provider == "google":
                        if not app.settings.google_client_id or not app.settings.google_client_secret:
                            raise AppError(400, "Google OAuth credentials are missing")
                        profile = GoogleOAuthClient(
                            app.settings.google_client_id,
                            app.settings.google_client_secret,
                            app.settings.google_redirect_uri,
                        ).exchange_code(query["code"])
                    else:
                        raise AppError(404, "unsupported provider")
                except IntegrationError as exc:
                    raise AppError(502, str(exc)) from exc
                account = app.service.upsert_oauth_account(
                    provider=profile.provider,
                    provider_subject=profile.subject,
                    display_name=profile.display_name,
                )
                self._json(account)
                return
            account = app.service.upsert_oauth_account(
                provider=provider,
                provider_subject=query.get("sub") or query.get("code") or f"demo-{provider}",
                display_name=query.get("name") or f"{provider} 사용자",
            )
            self._json(account)

        def _static(self, relative_path: str) -> None:
            safe_path = (STATIC_DIR / relative_path).resolve()
            if STATIC_DIR not in safe_path.parents or not safe_path.exists() or not safe_path.is_file():
                raise AppError(404, "static file not found")
            body = safe_path.read_bytes()
            content_type = mimetypes.guess_type(str(safe_path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _query(self, raw_query: str) -> dict[str, str]:
            return {key: values[-1] for key, values in parse_qs(raw_query).items()}

        def _payload(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length == 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            if self.headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded"):
                return {key: values[-1] for key, values in parse_qs(raw).items()}
            return json.loads(raw or "{}")

        def _context(self, payload: dict) -> RequestContext:
            user_id = payload.get("user_id")
            return RequestContext(
                ip=self.client_address[0],
                user_id=int(user_id) if user_id else None,
                actor_id=str(payload.get("actor_id", "local-admin")),
            )

        def _path_int(self, path: str, prefix: str) -> int:
            tail = path.removeprefix(prefix).strip("/")
            if "/" in tail:
                tail = tail.split("/", 1)[0]
            return int(tail)

        def _json(self, payload: object, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self, payload: str, status: int = 200) -> None:
            body = payload.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message, "status": status}, status=status)

    return Handler


def serve(settings: Settings) -> ThreadingHTTPServer:
    app = PublicRestaurantApplication(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(app))
    return server
