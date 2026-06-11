from __future__ import annotations

import sqlite3
import re
import time
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .agents import (
    FOOD_CATEGORIES,
    NaverClient,
    NormalizedExpenseRow,
    PermitClient,
    PermitSnapshot,
    PlaceCandidate,
    VerificationDecision,
    VerifierAgent,
    non_food_purpose_decision,
)
from .alias_memory import alias_memory_decision, remember_aliases
from .config import BASE_DIR, Settings
from .database import Database
from .integrations import (
    DataGoKrPermitClient,
    GeocodingNaverClient,
    NaverMapsGeocodingClient,
    NaverSearchLocalClient,
)
from .utils import normalize_address, normalize_text, safe_json_dumps, safe_json_loads, stable_hash, utc_now
from .xlsx_parser import parse_expense_xlsx


@dataclass(frozen=True)
class SourceAttachment:
    filename: str
    url: str
    content_path: str
    content_hash: str
    media_type: str


@dataclass(frozen=True)
class SourceDocument:
    source_url: str
    source_title: str
    published_at: str
    content: str
    department_name: str = ""
    raw_content_path: str | None = None
    attachments: tuple[SourceAttachment, ...] = ()


class UnsupportedDocumentError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawExpenseRow:
    row_number: int
    department_name: str
    used_date: str
    place_name: str
    address: str
    purpose: str
    amount: int
    participants: str = ""
    payment_method: str = "card"


class ExpenseAdapter(Protocol):
    source_key: str

    def discover(self) -> list[SourceDocument]:
        ...

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        ...


class LoggedNaverClient:
    def __init__(self, conn: sqlite3.Connection, client: NaverClient):
        self.conn = conn
        self.client = client

    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        started = time.perf_counter()
        request_hash = stable_hash("naver", row.normalized_place_name, row.normalized_address)
        try:
            places = self.client.search_local(row)
            _insert_api_call_log(
                self.conn,
                provider="naver",
                endpoint="local_search_geocode",
                request_hash=request_hash,
                status_code=200,
                duration_ms=_elapsed_ms(started),
                success=True,
            )
            return places
        except Exception as exc:
            _insert_api_call_log(
                self.conn,
                provider="naver",
                endpoint="local_search_geocode",
                request_hash=request_hash,
                status_code=None,
                duration_ms=_elapsed_ms(started),
                success=False,
                error_message=str(exc)[:500],
            )
            raise


class CachedPermitClient:
    def __init__(self, conn: sqlite3.Connection, client: PermitClient):
        self.conn = conn
        self.client = client

    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        cached = self._cached(row)
        if cached:
            return cached
        started = time.perf_counter()
        request_hash = stable_hash("data_go_kr", row.normalized_place_name, row.normalized_address)
        try:
            snapshot = self.client.lookup(row)
            _insert_api_call_log(
                self.conn,
                provider="data_go_kr",
                endpoint="food_permit_lookup",
                request_hash=request_hash,
                status_code=200,
                duration_ms=_elapsed_ms(started),
                success=True,
            )
            if snapshot:
                self._store(row, snapshot)
            return snapshot
        except Exception as exc:
            _insert_api_call_log(
                self.conn,
                provider="data_go_kr",
                endpoint="food_permit_lookup",
                request_hash=request_hash,
                status_code=None,
                duration_ms=_elapsed_ms(started),
                success=False,
                error_message=str(exc)[:500],
            )
            raise

    def _cached(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        cached = self.conn.execute(
            """
            SELECT *
            FROM permit_snapshots
            WHERE normalized_place_name = ?
              AND COALESCE(normalized_address, '') = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (row.normalized_place_name, row.normalized_address),
        ).fetchone()
        if cached is None:
            return None
        raw = safe_json_loads(cached["raw_response_json"], {})
        return PermitSnapshot(
            permit_id=cached["permit_id"],
            category=cached["permit_category"],
            business_status=cached["business_status"],
            address=cached["road_address"] or cached["normalized_address"] or "",
            longitude=cached["longitude"],
            latitude=cached["latitude"],
            raw_response_json=raw,
        )

    def _store(self, row: NormalizedExpenseRow, snapshot: PermitSnapshot) -> None:
        permit_id = snapshot.permit_id or stable_hash(
            "permit",
            row.normalized_place_name,
            row.normalized_address,
            snapshot.category,
            snapshot.address,
        )
        self.conn.execute(
            """
            INSERT INTO permit_snapshots
              (normalized_place_name, normalized_address, permit_id, permit_category,
               business_status, road_address, longitude, latitude, fetched_at,
               raw_response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(permit_id) DO UPDATE SET
              normalized_place_name = excluded.normalized_place_name,
              normalized_address = excluded.normalized_address,
              permit_category = excluded.permit_category,
              business_status = excluded.business_status,
              road_address = excluded.road_address,
              longitude = excluded.longitude,
              latitude = excluded.latitude,
              fetched_at = excluded.fetched_at,
              raw_response_json = excluded.raw_response_json
            """,
            (
                row.normalized_place_name,
                row.normalized_address,
                permit_id,
                snapshot.category,
                snapshot.business_status,
                snapshot.address,
                snapshot.longitude,
                snapshot.latitude,
                utc_now(),
                safe_json_dumps(snapshot.raw_response_json),
            ),
        )


class StoredProviderNaverClient:
    def __init__(self, candidate: PlaceCandidate):
        self.candidate = candidate

    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [self.candidate]


class NoPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return None


def existing_success_verification_decision(
    conn: sqlite3.Connection,
    candidate_id: int,
    row: NormalizedExpenseRow,
) -> VerificationDecision | None:
    verification = conn.execute(
        """
        SELECT *
        FROM place_verifications
        WHERE candidate_id = ?
          AND verification_status = 'success'
          AND provider_category IN ('restaurant', 'cafe', 'bar')
          AND is_coordinate_valid = 1
        ORDER BY verified_at DESC, id DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    if verification is None:
        return None
    name_score = float(verification["name_similarity"] or 0.0)
    address_score = float(verification["address_similarity"] or 0.0)
    category = verification["provider_category"]
    if category not in FOOD_CATEGORIES or name_score < 0.9:
        return None
    if row.normalized_address and address_score < 0.82:
        return None
    candidate = PlaceCandidate(
        provider_place_id=verification["provider_place_id"],
        name=verification["provider_place_name"],
        category=category,
        address=verification["provider_address"] or verification["provider_road_address"] or "",
        road_address=verification["provider_road_address"] or verification["provider_address"] or "",
        longitude=float(verification["longitude"]),
        latitude=float(verification["latitude"]),
    )
    return VerificationDecision(
        decision="approved",
        confidence=0.98,
        approved_by="rule",
        selected_candidate=candidate,
        category=category,
        reason_codes=["EXISTING_SUCCESS_VERIFICATION"],
        evidence={
            "place_verification_id": int(verification["id"]),
            "name_similarity": name_score,
            "address_similarity": address_score,
        },
    )


def existing_provider_evidence_decision(
    conn: sqlite3.Connection,
    candidate_id: int,
    row: NormalizedExpenseRow,
) -> VerificationDecision | None:
    task = conn.execute(
        """
        SELECT reason
        FROM manual_review_tasks
        WHERE candidate_id = ? AND status = 'pending'
        """,
        (candidate_id,),
    ).fetchone()
    if task is not None and "PERMIT_NOT_ACTIVE" in (task["reason"] or ""):
        return None
    verification = conn.execute(
        """
        SELECT *
        FROM place_verifications
        WHERE candidate_id = ?
          AND provider_place_id IS NOT NULL
          AND is_coordinate_valid = 1
        ORDER BY verified_at DESC, id DESC
        LIMIT 1
        """,
        (candidate_id,),
    ).fetchone()
    if verification is None:
        return None
    candidate = PlaceCandidate(
        provider_place_id=verification["provider_place_id"],
        name=verification["provider_place_name"],
        category=verification["provider_category"],
        address=verification["provider_address"] or verification["provider_road_address"] or "",
        road_address=verification["provider_road_address"] or verification["provider_address"] or "",
        longitude=float(verification["longitude"]),
        latitude=float(verification["latitude"]),
    )
    decision = VerifierAgent(StoredProviderNaverClient(candidate), NoPermitClient()).verify(row)
    if decision.decision in {"approved", "rejected"}:
        return decision
    return None


class BusanFixtureAdapter:
    """Busan v1 adapter fixture used for local development and tests."""

    source_key = "busan_city_expense_v1"

    def discover(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                source_url="fixture://busan/2026-q1/main-office",
                source_title="2026년 1분기 부산광역시청 업무추진비 fixture",
                published_at="2026-05-01",
                content="busan-city-2026-q1-main-office-fixture",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        return [
            RawExpenseRow(
                row_number=1,
                department_name="기획담당관",
                used_date="2026-01-14",
                place_name="부산돼지국밥 시청점",
                address="부산광역시 연제구 중앙대로 1001",
                purpose="현안 업무 협의 간담",
                amount=68000,
                participants="6명",
            ),
            RawExpenseRow(
                row_number=2,
                department_name="관광진흥과",
                used_date="2026-02-03",
                place_name="광안리커피",
                address="부산광역시 수영구 광안해변로 219",
                purpose="축제 운영 회의",
                amount=22000,
                participants="4명",
            ),
            RawExpenseRow(
                row_number=3,
                department_name="청년정책과",
                used_date="2026-03-11",
                place_name="해운대포차",
                address="부산광역시 해운대구 구남로 29",
                purpose="청년 행사 관계자 간담",
                amount=94000,
                participants="8명",
            ),
            RawExpenseRow(
                row_number=4,
                department_name="국제협력과",
                used_date="2026-03-18",
                place_name="부산컨벤션센터 회의실",
                address="부산광역시 해운대구 APEC로 55",
                purpose="국제회의 장소 대관",
                amount=250000,
                participants="",
            ),
            RawExpenseRow(
                row_number=5,
                department_name="교통혁신과",
                used_date="2026-03-23",
                place_name="미상식당",
                address="부산광역시 부산진구 중앙대로 730",
                purpose="교통 현안 간담",
                amount=51000,
                participants="5명",
            ),
        ]


class BusanCityLiveAdapter:
    """Crawl the live Busan open-government expense board."""

    source_key = "busan_city_expense_v1"
    base_url = "https://www.busan.go.kr"
    list_path = "/ghopen12"

    def __init__(
        self,
        max_pages: int = 1,
        max_documents: int = 10,
        raw_dir: Path | None = None,
    ) -> None:
        self.max_pages = max_pages
        self.max_documents = max_documents
        self.raw_dir = raw_dir or BASE_DIR / "var" / "raw" / "busan_city"

    def discover(self) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            list_url = f"{self.base_url}{self.list_path}?curPage={page}"
            list_html = self._get_text(list_url)
            for item in self._parse_list(list_html, list_url):
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                detail_html = self._get_text(item["url"])
                documents.append(self._parse_detail(item, detail_html))
                if len(documents) >= self.max_documents:
                    return documents
        return documents

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        supported = [
            attachment
            for attachment in document.attachments
            if attachment.filename.lower().endswith(".xlsx")
        ]
        if not supported:
            raise UnsupportedDocumentError("no supported XLSX attachment found")
        rows: list[RawExpenseRow] = []
        for attachment in supported:
            for parsed in parse_expense_xlsx(Path(attachment.content_path).read_bytes()):
                rows.append(
                    RawExpenseRow(
                        row_number=parsed.row_number,
                        department_name=parsed.department_name
                        or document.department_name
                        or _department_from_title(document.source_title),
                        used_date=parsed.used_date,
                        place_name=parsed.place_name,
                        address="",
                        purpose=parsed.purpose,
                        amount=parsed.amount,
                        participants=parsed.participants,
                        payment_method=parsed.payment_method or "card",
                    )
                )
        return rows

    def _parse_list(self, html: str, list_url: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for row in re.findall(r"<tr.*?</tr>", html, flags=re.S | re.I):
            href_match = re.search(
                r'href="([^"]*/ghopen12/view\?[^"]*schIndx=\d+[^"]*)"',
                row,
                flags=re.I,
            )
            if not href_match:
                continue
            text = _strip_tags(row)
            if "업무추진비" not in text:
                continue
            title_match = re.search(
                r"\d+\s+(.+?)\s+([가-힣A-Za-z0-9·/() >]+)\s+20\d{2}-\d{2}-\d{2}",
                text,
            )
            published_match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
            items.append(
                {
                    "url": urljoin(list_url, unescape(href_match.group(1))),
                    "title": title_match.group(1).strip() if title_match else text,
                    "department": title_match.group(2).strip() if title_match else "",
                    "published_at": published_match.group(1) if published_match else "",
                }
            )
        return items

    def _parse_detail(self, item: dict[str, str], html: str) -> SourceDocument:
        title = _first_match(
            html,
            r'<h4 class="form-data-subject">(.+?)</h4>',
            default=item["title"],
        )
        department = _first_match(
            html,
            r"<dt><span>담당부서</span></dt>\s*<dd>(.*?)</dd>",
            default=item["department"],
        )
        published_at = _first_match(
            html,
            r"<dt><span>공표일</span></dt>\s*<dd>\s*(20\d{2}-\d{2}-\d{2})\s*</dd>",
            default=item["published_at"],
        )
        upper_no = _first_match(item["url"], r"schIndx=(\d+)", default=stable_hash(item["url"])[:12])
        detail_path = self._write_raw(f"{upper_no}_detail.html", html.encode("utf-8"))
        attachments = tuple(self._download_attachments(html, upper_no, item["url"]))
        return SourceDocument(
            source_url=item["url"],
            source_title=_strip_tags(title),
            published_at=published_at,
            content=safe_json_dumps(
                {
                    "url": item["url"],
                    "title": _strip_tags(title),
                    "department": _strip_tags(department),
                    "attachment_hashes": [attachment.content_hash for attachment in attachments],
                }
            ),
            department_name=_strip_tags(department),
            raw_content_path=str(detail_path),
            attachments=attachments,
        )

    def _download_attachments(
        self,
        html: str,
        upper_no: str,
        referer: str,
    ) -> list[SourceAttachment]:
        attachments: list[SourceAttachment] = []
        for href, label in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S | re.I):
            clean_href = unescape(href)
            if "/comm/getFile" not in clean_href:
                continue
            if "srvcId=OPENGOV" not in clean_href or "fileTy=ATTACH" not in clean_href:
                continue
            filename = _strip_tags(label)
            if "(" in filename:
                filename = filename.rsplit("(", 1)[0].strip()
            url = urljoin(self.base_url, clean_href)
            media_type = filename.rsplit(".", 1)[-1].lower() if "." in filename else "unknown"
            payload = self._get_bytes(url, referer=referer)
            content_hash = stable_hash(url, payload)
            safe_name = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", filename)[:120] or f"{upper_no}.{media_type}"
            path = self._write_raw(f"{upper_no}_{len(attachments) + 1}_{safe_name}", payload)
            attachments.append(
                SourceAttachment(
                    filename=filename,
                    url=url,
                    content_path=str(path),
                    content_hash=content_hash,
                    media_type=media_type,
                )
            )
        return attachments

    def _write_raw(self, filename: str, payload: bytes) -> Path:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / filename
        path.write_bytes(payload)
        return path

    def _get_text(self, url: str) -> str:
        return self._get_bytes(url).decode("utf-8", "replace")

    def _get_bytes(self, url: str, referer: str | None = None) -> bytes:
        headers = {"User-Agent": "Mozilla/5.0"}
        if referer:
            headers["Referer"] = referer
        with urlopen(Request(url, headers=headers), timeout=20) as response:
            return response.read()


class DailyPipeline:
    def __init__(
        self,
        database: Database,
        adapter: ExpenseAdapter | None = None,
        verifier: VerifierAgent | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.database = database
        self.adapter = adapter or BusanFixtureAdapter()
        self.verifier = verifier
        self.settings = settings

    def _active_verifier(self, conn: sqlite3.Connection) -> VerifierAgent:
        return self.verifier or self._build_verifier(self.settings, conn)

    def _build_verifier(self, settings: Settings | None, conn: sqlite3.Connection | None = None) -> VerifierAgent:
        if not settings:
            return VerifierAgent()
        naver_client = None
        permit_client = None
        if settings.naver_search_client_id and settings.naver_search_client_secret:
            geocoder = None
            if settings.naver_maps_client_id and settings.naver_maps_client_secret:
                geocoder = NaverMapsGeocodingClient(
                    settings.naver_maps_client_id,
                    settings.naver_maps_client_secret,
                )
            naver_client = GeocodingNaverClient(
                NaverSearchLocalClient(
                    settings.naver_search_client_id,
                    settings.naver_search_client_secret,
                ),
                geocoder,
            )
            if conn is not None:
                naver_client = LoggedNaverClient(conn, naver_client)
        if settings.data_go_kr_service_key:
            permit_client = DataGoKrPermitClient(settings.data_go_kr_service_key)
            if conn is not None:
                permit_client = CachedPermitClient(conn, permit_client)
        return VerifierAgent(naver_client=naver_client, permit_client=permit_client)

    def run(self) -> dict[str, Any]:
        self.database.initialize()
        with self.database.session() as conn:
            batch_id = self._create_batch(conn)
            summary = {
                "documents_seen": 0,
                "documents_inserted": 0,
                "rows_seen": 0,
                "rows_inserted": 0,
                "approved": 0,
                "rejected": 0,
                "needs_review": 0,
                "dlq": 0,
            }
            try:
                source = self._load_source(conn)
                verifier = self._active_verifier(conn)
                for document in self.adapter.discover():
                    summary["documents_seen"] += 1
                    raw_document_id, inserted_doc = self._upsert_document(conn, source, document)
                    if inserted_doc:
                        summary["documents_inserted"] += 1
                    try:
                        extracted_rows = self.adapter.extract(document)
                    except Exception as exc:
                        summary["dlq"] += 1
                        self._insert_dlq(
                            conn,
                            batch_id,
                            "document",
                            {
                                "source_url": document.source_url,
                                "source_title": document.source_title,
                                "attachments": [attachment.__dict__ for attachment in document.attachments],
                            },
                            str(exc),
                        )
                        continue
                    for raw_row in extracted_rows:
                        summary["rows_seen"] += 1
                        try:
                            normalized = self._normalize(raw_row)
                            expense_id, inserted_row = self._upsert_expense(
                                conn, source, raw_document_id, raw_row, normalized
                            )
                            if inserted_row:
                                summary["rows_inserted"] += 1
                            candidate_id = self._upsert_candidate(
                                conn, source, expense_id, raw_row, normalized
                            )
                            if self._candidate_is_resolved(conn, candidate_id):
                                continue
                            decision = (
                                non_food_purpose_decision(normalized)
                                or alias_memory_decision(conn, normalized)
                                or verifier.verify(normalized)
                            )
                            self._persist_decision(conn, source, expense_id, candidate_id, decision)
                            summary[decision.decision] += 1
                        except Exception as exc:  # pragma: no cover - defensive DLQ guard
                            summary["dlq"] += 1
                            self._insert_dlq(conn, batch_id, "row", raw_row.__dict__, str(exc))
                self._finish_batch(conn, batch_id, "success", summary)
                return {"batch_id": batch_id, "status": "success", "summary": summary}
            except Exception as exc:
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                raise

    def verify_pending(self, limit: int = 100) -> dict[str, Any]:
        self.database.initialize()
        capped_limit = max(1, min(limit, 500))
        with self.database.session() as conn:
            batch_id = self._create_batch(conn, "verify_pending")
            summary = {
                "documents_seen": 0,
                "documents_inserted": 0,
                "rows_seen": 0,
                "rows_inserted": 0,
                "approved": 0,
                "rejected": 0,
                "needs_review": 0,
                "dlq": 0,
            }
            try:
                verifier = self._active_verifier(conn)
                rows = conn.execute(
                    """
                    SELECT
                      c.id AS candidate_id,
                      c.expense_record_id,
                      c.region_id,
                      c.original_place_name,
                      c.original_address,
                      c.normalized_place_name,
                      c.normalized_address,
                      c.used_date,
                      c.amount,
                      er.source_row_number,
                      er.department_name,
                      er.purpose
                    FROM manual_review_tasks mrt
                    JOIN restaurant_candidates c ON c.id = mrt.candidate_id
                    JOIN expense_records er ON er.id = c.expense_record_id
                    WHERE mrt.status = 'pending'
                    ORDER BY mrt.created_at ASC
                    LIMIT ?
                    """,
                    (capped_limit,),
                ).fetchall()
                for row in rows:
                    summary["rows_seen"] += 1
                    try:
                        normalized = NormalizedExpenseRow(
                            row_number=int(row["source_row_number"] or 0),
                            department_name=row["department_name"] or "",
                            used_date=row["used_date"] or "",
                            place_name=row["original_place_name"] or "",
                            address=row["original_address"] or "",
                            purpose=row["purpose"] or "",
                            amount=int(row["amount"] or 0),
                            normalized_place_name=row["normalized_place_name"] or normalize_text(row["original_place_name"]),
                            normalized_address=row["normalized_address"] or normalize_address(row["original_address"]),
                        )
                        decision = (
                            non_food_purpose_decision(normalized)
                            or existing_success_verification_decision(conn, int(row["candidate_id"]), normalized)
                            or alias_memory_decision(conn, normalized)
                            or existing_provider_evidence_decision(conn, int(row["candidate_id"]), normalized)
                            or verifier.verify(normalized)
                        )
                        self._persist_decision(
                            conn,
                            {"region_id": row["region_id"]},
                            int(row["expense_record_id"]),
                            int(row["candidate_id"]),
                            decision,
                        )
                        summary[decision.decision] += 1
                    except Exception as exc:  # pragma: no cover - defensive DLQ guard
                        summary["dlq"] += 1
                        self._insert_dlq(
                            conn,
                            batch_id,
                            "pending_verification",
                            dict(row),
                            str(exc),
                        )
                summary["rows_processed"] = summary["rows_seen"]
                self._finish_batch(conn, batch_id, "success", summary)
                return {"batch_id": batch_id, "status": "success", "summary": summary}
            except Exception as exc:
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                raise

    def _create_batch(self, conn: sqlite3.Connection, job_name: str | None = None) -> int:
        cur = conn.execute(
            "INSERT INTO batch_jobs (job_name, status, started_at) VALUES (?, ?, ?)",
            (job_name or f"daily_{self.adapter.source_key}", "running", utc_now()),
        )
        return int(cur.lastrowid)

    def _load_source(self, conn: sqlite3.Connection) -> sqlite3.Row:
        source = conn.execute(
            """
            SELECT sr.*, i.region_id
            FROM source_registry sr
            JOIN institutions i ON i.id = sr.institution_id
            WHERE sr.source_key = ?
            """,
            (self.adapter.source_key,),
        ).fetchone()
        if source is None:
            raise RuntimeError(f"source registry not found: {self.adapter.source_key}")
        return source

    def _upsert_document(
        self, conn: sqlite3.Connection, source: sqlite3.Row, document: SourceDocument
    ) -> tuple[int, bool]:
        content_hash = stable_hash(document.content)
        existing = conn.execute(
            """
            SELECT id FROM raw_documents
            WHERE institution_id = ? AND (source_url = ? OR content_hash = ?)
            """,
            (source["institution_id"], document.source_url, content_hash),
        ).fetchone()
        if existing:
            return int(existing["id"]), False
        cur = conn.execute(
            """
            INSERT INTO raw_documents
              (institution_id, source_registry_id, source_url, source_title, published_at,
               collected_at, content_hash, raw_content_path, status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["institution_id"],
                source["id"],
                document.source_url,
                document.source_title,
                document.published_at,
                utc_now(),
                content_hash,
                document.raw_content_path,
                "parsed",
                safe_json_dumps(
                    {
                        "adapter": self.adapter.source_key,
                        "department_name": document.department_name,
                        "attachments": [attachment.__dict__ for attachment in document.attachments],
                    }
                ),
            ),
        )
        return int(cur.lastrowid), True

    def _normalize(self, raw_row: RawExpenseRow) -> NormalizedExpenseRow:
        return NormalizedExpenseRow(
            row_number=raw_row.row_number,
            department_name=raw_row.department_name,
            used_date=raw_row.used_date,
            place_name=raw_row.place_name,
            address=raw_row.address,
            purpose=raw_row.purpose,
            amount=raw_row.amount,
            normalized_place_name=normalize_text(raw_row.place_name),
            normalized_address=normalize_address(raw_row.address),
        )

    def _upsert_expense(
        self,
        conn: sqlite3.Connection,
        source: sqlite3.Row,
        raw_document_id: int,
        raw_row: RawExpenseRow,
        normalized: NormalizedExpenseRow,
    ) -> tuple[int, bool]:
        row_hash = stable_hash(raw_document_id, raw_row.row_number, raw_row.place_name, raw_row.amount)
        existing = conn.execute(
            "SELECT id FROM expense_records WHERE raw_document_id = ? AND row_hash = ?",
            (raw_document_id, row_hash),
        ).fetchone()
        if existing:
            return int(existing["id"]), False
        cur = conn.execute(
            """
            INSERT INTO expense_records
              (raw_document_id, institution_id, region_id, source_row_number, department_name,
               used_date, place_name, purpose, amount, participants, payment_method,
               original_row_json, normalized_place_name, normalized_purpose,
               is_food_candidate, candidate_reason, row_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                raw_document_id,
                source["institution_id"],
                source["region_id"],
                raw_row.row_number,
                raw_row.department_name,
                raw_row.used_date,
                raw_row.place_name,
                raw_row.purpose,
                raw_row.amount,
                raw_row.participants,
                raw_row.payment_method,
                safe_json_dumps(raw_row.__dict__),
                normalized.normalized_place_name,
                normalize_text(raw_row.purpose),
                1,
                self.adapter.source_key,
                row_hash,
            ),
        )
        return int(cur.lastrowid), True

    def _upsert_candidate(
        self,
        conn: sqlite3.Connection,
        source: sqlite3.Row,
        expense_id: int,
        raw_row: RawExpenseRow,
        normalized: NormalizedExpenseRow,
    ) -> int:
        existing = conn.execute(
            "SELECT id FROM restaurant_candidates WHERE expense_record_id = ?", (expense_id,)
        ).fetchone()
        if existing:
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO restaurant_candidates
              (expense_record_id, institution_id, region_id, original_place_name,
               normalized_place_name, original_address, normalized_address, used_date,
               amount, place_major_category, extraction_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                expense_id,
                source["institution_id"],
                source["region_id"],
                raw_row.place_name,
                normalized.normalized_place_name,
                raw_row.address,
                normalized.normalized_address,
                raw_row.used_date,
                raw_row.amount,
                "other",
                self.adapter.source_key,
            ),
        )
        return int(cur.lastrowid)

    def _candidate_is_resolved(self, conn: sqlite3.Connection, candidate_id: int) -> bool:
        row = conn.execute(
            """
            SELECT status, manual_review_status
            FROM restaurant_candidates
            WHERE id = ?
            """,
            (candidate_id,),
        ).fetchone()
        return bool(
            row
            and row["status"] in {"verified", "rejected"}
            or row
            and row["manual_review_status"] == "pending"
        )

    def _persist_decision(
        self,
        conn: sqlite3.Connection,
        source: sqlite3.Row,
        expense_id: int,
        candidate_id: int,
        decision: VerificationDecision,
    ) -> None:
        before = dict(
            conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
        )
        if decision.decision == "approved" and decision.selected_candidate:
            verification_id = self._upsert_verification(conn, candidate_id, decision, "success")
            restaurant_id = self._upsert_restaurant(
                conn, source["region_id"], verification_id, decision.selected_candidate, decision
            )
            remember_aliases(
                conn,
                restaurant_id,
                [before["original_place_name"], decision.selected_candidate.name],
                source=f"auto_{decision.approved_by}",
                confidence=decision.confidence,
            )
            self._link_expense(conn, restaurant_id, expense_id, candidate_id)
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET status = 'verified',
                    verification_status = 'success',
                    manual_review_status = 'not_required',
                    place_major_category = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (decision.category, utc_now(), candidate_id),
            )
            self._resolve_manual_task(conn, candidate_id, "auto_approved", ",".join(decision.reason_codes))
        elif decision.decision == "rejected":
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET status = 'rejected',
                    verification_status = 'category_mismatch',
                    manual_review_status = 'not_required',
                    place_major_category = ?,
                    rejection_reason = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (decision.category, ",".join(decision.reason_codes), utc_now(), candidate_id),
            )
            self._resolve_manual_task(conn, candidate_id, "auto_rejected", ",".join(decision.reason_codes))
        else:
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET status = 'needs_review',
                    verification_status = 'ambiguous',
                    manual_review_status = 'pending',
                    place_major_category = ?,
                    review_note = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (decision.category, ",".join(decision.reason_codes), utc_now(), candidate_id),
            )
            conn.execute(
                """
                INSERT INTO manual_review_tasks (candidate_id, status, reason)
                VALUES (?, 'pending', ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                  status = 'pending',
                  reason = excluded.reason,
                  reviewer_note = NULL,
                  reviewed_by = NULL,
                  reviewed_at = NULL
                """,
                (candidate_id, ",".join(decision.reason_codes) or "needs_review"),
            )
            if decision.selected_candidate:
                self._upsert_verification(conn, candidate_id, decision, "ambiguous")
            self._upsert_candidate_evidence_list(conn, candidate_id, decision, "ambiguous")
        after = dict(
            conn.execute("SELECT * FROM restaurant_candidates WHERE id = ?", (candidate_id,)).fetchone()
        )
        conn.execute(
            """
            INSERT INTO decision_audit_logs
              (actor_type, actor_id, action, target_type, target_id, before_json, after_json,
               reason_codes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.approved_by,
                "pipeline",
                decision.decision,
                "restaurant_candidate",
                candidate_id,
                safe_json_dumps(before),
                safe_json_dumps(after),
                safe_json_dumps(decision.reason_codes),
            ),
        )

    def _upsert_candidate_evidence_list(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        decision: VerificationDecision,
        verification_status: str,
    ) -> None:
        for evidence in decision.evidence.get("candidate_evidence", []):
            if not isinstance(evidence, dict):
                continue
            provider_place_id = str(evidence.get("provider_place_id") or "")
            name = str(evidence.get("name") or "")
            if not provider_place_id or not name:
                continue
            if decision.selected_candidate and provider_place_id == decision.selected_candidate.provider_place_id:
                continue
            candidate = PlaceCandidate(
                provider_place_id=provider_place_id,
                name=name,
                category=str(evidence.get("category") or "other"),
                address=str(evidence.get("address") or ""),
                road_address=str(evidence.get("road_address") or evidence.get("address") or ""),
                longitude=float(evidence.get("longitude") or 0.0),
                latitude=float(evidence.get("latitude") or 0.0),
            )
            candidate_decision = VerificationDecision(
                decision=decision.decision,
                confidence=decision.confidence,
                approved_by=decision.approved_by,
                selected_candidate=candidate,
                category=candidate.category,
                reason_codes=decision.reason_codes,
                evidence={
                    "name_similarity": float(evidence.get("name_similarity") or 0.0),
                    "address_similarity": float(evidence.get("address_similarity") or 0.0),
                    "source_reason_codes": decision.reason_codes,
                },
            )
            self._upsert_verification(conn, candidate_id, candidate_decision, verification_status)

    def _resolve_manual_task(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        status: str,
        reason: str | None = None,
    ) -> None:
        conn.execute(
            """
            UPDATE manual_review_tasks
            SET status = ?,
                reason = COALESCE(?, reason),
                reviewed_by = 'pipeline',
                reviewed_at = ?
            WHERE candidate_id = ? AND status = 'pending'
            """,
            (status, reason, utc_now(), candidate_id),
        )

    def _upsert_verification(
        self,
        conn: sqlite3.Connection,
        candidate_id: int,
        decision: VerificationDecision,
        verification_status: str,
    ) -> int:
        candidate = decision.selected_candidate
        if candidate is None:
            raise RuntimeError("verification evidence requires selected candidate")
        name_similarity = float(
            decision.evidence.get("name_similarity", 0.0 if verification_status != "success" else 1.0)
        )
        address_similarity = float(
            decision.evidence.get("address_similarity", 0.0 if verification_status != "success" else 1.0)
        )
        is_success = verification_status == "success"
        is_name_match = 1 if is_success or name_similarity >= 0.78 else 0
        is_address_match = 1 if is_success or address_similarity >= 0.72 else 0
        is_category_valid = 1 if is_success and candidate.category in {"restaurant", "cafe", "bar"} else 0
        verification_reason = ",".join(decision.reason_codes)
        raw_response_json = safe_json_dumps(decision.evidence)
        existing = conn.execute(
            """
            SELECT id FROM place_verifications
            WHERE candidate_id = ? AND provider = 'naver' AND provider_place_id = ?
            """,
            (candidate_id, candidate.provider_place_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE place_verifications
                SET provider_place_name = ?,
                    provider_category = ?,
                    provider_address = ?,
                    provider_road_address = ?,
                    normalized_provider_name = ?,
                    normalized_provider_address = ?,
                    longitude = ?,
                    latitude = ?,
                    name_similarity = ?,
                    address_similarity = ?,
                    is_name_match = ?,
                    is_address_match = ?,
                    is_coordinate_valid = 1,
                    is_category_valid = ?,
                    verification_status = ?,
                    verification_reason = ?,
                    raw_response_json = ?,
                    verified_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    candidate.name,
                    candidate.category,
                    candidate.address,
                    candidate.road_address,
                    normalize_text(candidate.name),
                    normalize_address(candidate.address),
                    candidate.longitude,
                    candidate.latitude,
                    name_similarity,
                    address_similarity,
                    is_name_match,
                    is_address_match,
                    is_category_valid,
                    verification_status,
                    verification_reason,
                    raw_response_json,
                    int(existing["id"]),
                ),
            )
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO place_verifications
              (candidate_id, provider, provider_place_id, provider_place_name, provider_category,
               provider_address, provider_road_address, normalized_provider_name,
               normalized_provider_address, longitude, latitude, name_similarity,
               address_similarity, is_name_match, is_address_match, is_coordinate_valid,
               is_category_valid, verification_status, verification_reason, raw_response_json)
            VALUES (?, 'naver', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                candidate.provider_place_id,
                candidate.name,
                candidate.category,
                candidate.address,
                candidate.road_address,
                normalize_text(candidate.name),
                normalize_address(candidate.address),
                candidate.longitude,
                candidate.latitude,
                name_similarity,
                address_similarity,
                is_name_match,
                is_address_match,
                is_category_valid,
                verification_status,
                verification_reason,
                raw_response_json,
            ),
        )
        return int(cur.lastrowid)

    def _upsert_restaurant(
        self,
        conn: sqlite3.Connection,
        region_id: int,
        verification_id: int,
        candidate: PlaceCandidate,
        decision: VerificationDecision,
    ) -> int:
        existing = conn.execute(
            "SELECT id FROM restaurants WHERE naver_place_id = ?", (candidate.provider_place_id,)
        ).fetchone()
        if existing:
            restaurant_id = int(existing["id"])
            conn.execute(
                """
                UPDATE restaurants
                SET place_verification_id = ?,
                    last_verified_at = ?,
                    verification_status = 'success',
                    map_exposure_status = 'visible'
                WHERE id = ?
                """,
                (verification_id, utc_now(), restaurant_id),
            )
            return restaurant_id
        cur = conn.execute(
            """
            INSERT INTO restaurants
              (region_id, place_verification_id, canonical_name, normalized_name,
               major_category, naver_place_id, address, road_address, normalized_address,
               longitude, latitude, verification_status, map_exposure_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', 'visible')
            """,
            (
                region_id,
                verification_id,
                candidate.name,
                normalize_text(candidate.name),
                decision.category,
                candidate.provider_place_id,
                candidate.address,
                candidate.road_address,
                normalize_address(candidate.address),
                candidate.longitude,
                candidate.latitude,
            ),
        )
        restaurant_id = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO entity_status_history (restaurant_id, status, reason, evidence_json)
            VALUES (?, 'active', 'initial_verification', ?)
            """,
            (restaurant_id, safe_json_dumps(decision.evidence)),
        )
        return restaurant_id

    def _link_expense(
        self, conn: sqlite3.Connection, restaurant_id: int, expense_id: int, candidate_id: int
    ) -> None:
        row = conn.execute(
            "SELECT used_date, amount FROM expense_records WHERE id = ?", (expense_id,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO restaurant_expense_links
              (restaurant_id, expense_record_id, candidate_id, used_date, amount, link_reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                restaurant_id,
                expense_id,
                candidate_id,
                row["used_date"],
                row["amount"],
                "verified_pipeline",
            ),
        )

    def _insert_dlq(
        self,
        conn: sqlite3.Connection,
        batch_id: int,
        stage: str,
        payload: dict[str, Any],
        error_message: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO dead_letter_queue (batch_job_id, stage, payload_json, error_message)
            VALUES (?, ?, ?, ?)
            """,
            (batch_id, stage, safe_json_dumps(payload), error_message),
        )

    def _finish_batch(
        self,
        conn: sqlite3.Connection,
        batch_id: int,
        status: str,
        summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        conn.execute(
            """
            UPDATE batch_jobs
            SET status = ?, finished_at = ?, summary_json = ?, error_message = ?
            WHERE id = ?
            """,
            (status, utc_now(), safe_json_dumps(summary), error_message, batch_id),
        )


def _strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _first_match(value: str, pattern: str, default: str = "") -> str:
    match = re.search(pattern, value or "", flags=re.S | re.I)
    return match.group(1).strip() if match else default


def _department_from_title(title: str) -> str:
    match = re.search(r"\(([^)]+)\)", title or "")
    return match.group(1).strip() if match else ""


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _insert_api_call_log(
    conn: sqlite3.Connection,
    provider: str,
    endpoint: str,
    request_hash: str,
    status_code: int | None,
    duration_ms: int,
    success: bool,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO api_call_logs
          (provider, endpoint, request_hash, status_code, duration_ms, success, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (provider, endpoint, request_hash, status_code, duration_ms, 1 if success else 0, error_message),
    )
