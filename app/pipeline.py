from __future__ import annotations

import re
import time
from dataclasses import dataclass, replace
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urljoin, urlparse
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
    candidate_address_similarity,
    candidate_structured_address_match,
    expense_scope_reject_decision,
    has_valid_provider_coordinates,
    is_food_context_purpose,
    name_similarity_with_branch,
    non_food_purpose_decision,
    row_name_for_matching,
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


COLLECTION_SCAN_SAFETY_MAX_PAGES = 500
COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS = 10000
VerificationProgressCallback = Callable[[dict[str, Any]], None]


def source_document_identity(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.path.rstrip("/").endswith("/ghopen12/view"):
        document_ids = parse_qs(parsed.query).get("schIndx") or []
        if document_ids and str(document_ids[0]).isdigit():
            return f"{parsed.netloc.lower()}{parsed.path.rstrip('/')}?schIndx={document_ids[0]}"
    return url


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


@dataclass(frozen=True)
class CollectionTarget:
    source_url: str
    source_title: str
    published_at: str
    department_name: str = ""


class UnsupportedDocumentError(RuntimeError):
    pass


def _parse_iso_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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
    split_from_place_name: str = ""
    cleaned_from_place_name: str = ""
    split_index: int = 0
    split_count: int = 1
    source_amount: int | None = None


def clean_parsed_place_name(value: str) -> str:
    original = re.sub(r"\s+", " ", str(value or "")).strip()
    cleaned = original
    suffix_pattern = re.compile(r"(?:\s*외\s*\d+\s*(?:개소|개|곳|명)?|\s*등)\s*$")
    while cleaned:
        without_suffix = suffix_pattern.sub("", cleaned).strip(" ,;，；/_·-")
        if without_suffix == cleaned or not without_suffix:
            break
        cleaned = without_suffix
    return cleaned or original


def split_raw_expense_row_places(raw_row: RawExpenseRow) -> list[RawExpenseRow]:
    if raw_row.split_count > 1:
        return [raw_row]
    parts: list[str] = []
    original_parts: dict[str, str] = {}
    seen: set[str] = set()
    for value in re.split(r"[,;，；]+", raw_row.place_name or ""):
        original_part = re.sub(r"\s+", " ", value).strip()
        part = clean_parsed_place_name(original_part)
        key = normalize_text(part).replace(" ", "")
        if not key or key in seen:
            continue
        seen.add(key)
        parts.append(part)
        original_parts[key] = original_part
    if len(parts) < 2:
        cleaned_name = clean_parsed_place_name(raw_row.place_name)
        if cleaned_name == raw_row.place_name:
            return [raw_row]
        return [
            replace(
                raw_row,
                place_name=cleaned_name,
                cleaned_from_place_name=raw_row.place_name,
            )
        ]
    return [
        replace(
            raw_row,
            place_name=place_name,
            amount=0,
            split_from_place_name=raw_row.place_name,
            cleaned_from_place_name=(
                original_parts[normalize_text(place_name).replace(" ", "")]
                if original_parts[normalize_text(place_name).replace(" ", "")] != place_name
                else ""
            ),
            split_index=index,
            split_count=len(parts),
            source_amount=raw_row.amount,
        )
        for index, place_name in enumerate(parts, start=1)
    ]


class ExpenseAdapter(Protocol):
    source_key: str

    def discover(self) -> list[SourceDocument]:
        ...

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        ...


class LoggedNaverClient:
    def __init__(self, conn: Any, client: NaverClient):
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
    def __init__(self, conn: Any, client: PermitClient):
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
    def __init__(self, candidates: PlaceCandidate | list[PlaceCandidate]):
        self.candidates = candidates if isinstance(candidates, list) else [candidates]

    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return self.candidates


class NoPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return None


class StoredPermitClient:
    def __init__(self, permit: PermitSnapshot):
        self.permit = permit

    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return self.permit


def existing_success_verification_decision(
    conn: Any,
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


def stored_provider_evidence_review_decision(
    conn: Any,
    candidate_id: int,
    row: NormalizedExpenseRow,
) -> VerificationDecision | None:
    verifications = conn.execute(
        """
        SELECT *
        FROM place_verifications
        WHERE candidate_id = ?
          AND provider_place_id IS NOT NULL
          AND is_coordinate_valid = 1
        ORDER BY
          CASE WHEN provider_category IN ('restaurant', 'cafe', 'bar') THEN 0 ELSE 1 END,
          name_similarity DESC,
          address_similarity DESC,
          verified_at DESC,
          id DESC
        LIMIT 10
        """,
        (candidate_id,),
    ).fetchall()
    if not verifications:
        return None
    candidates = [
        PlaceCandidate(
            provider_place_id=verification["provider_place_id"],
            name=verification["provider_place_name"],
            category=verification["provider_category"],
            address=verification["provider_address"] or verification["provider_road_address"] or "",
            road_address=verification["provider_road_address"] or verification["provider_address"] or "",
            longitude=float(verification["longitude"]),
            latitude=float(verification["latitude"]),
            provider_category_raw=str(
                safe_json_loads(verification["raw_response_json"], {}).get("provider_category_raw") or ""
            ),
        )
        for verification in verifications
    ]
    return VerifierAgent(StoredProviderNaverClient(candidates), NoPermitClient()).verify(row)


def existing_provider_evidence_decision(
    conn: Any,
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
    decision = stored_provider_evidence_review_decision(conn, candidate_id, row)
    if decision is None:
        return None
    if decision.decision in {"approved", "rejected"}:
        return decision
    return None


def advisory_permit_resolution_decision(
    row: NormalizedExpenseRow,
    decision: VerificationDecision,
    permit: PermitSnapshot,
) -> VerificationDecision:
    if (
        decision.decision != "needs_review"
        or permit.business_status != "active"
        or permit.category not in FOOD_CATEGORIES
        or not permit.address
        or not is_food_context_purpose(row.purpose)
    ):
        return decision
    matches: list[tuple[float, float, PlaceCandidate]] = []
    for evidence in decision.evidence.get("candidate_evidence", []):
        if not isinstance(evidence, dict):
            continue
        candidate = PlaceCandidate(
            provider_place_id=str(evidence.get("provider_place_id") or ""),
            name=str(evidence.get("name") or ""),
            category=str(evidence.get("category") or "other"),
            address=str(evidence.get("address") or ""),
            road_address=str(evidence.get("road_address") or evidence.get("address") or ""),
            longitude=float(evidence.get("longitude") or 0.0),
            latitude=float(evidence.get("latitude") or 0.0),
            provider_category_raw=str(evidence.get("provider_category_raw") or ""),
        )
        if (
            not candidate.provider_place_id
            or candidate.category not in FOOD_CATEGORIES
            or not has_valid_provider_coordinates(candidate)
        ):
            continue
        name_score = name_similarity_with_branch(row_name_for_matching(row), candidate.name)
        address_score = candidate_address_similarity(permit.address, candidate)
        if (
            name_score < 0.55
            or address_score < 0.72
            or not candidate_structured_address_match(permit.address, candidate)
        ):
            continue
        matches.append((address_score, name_score, candidate))
    if not matches:
        return decision
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_address_score, best_name_score, best = matches[0]
    competing_addresses = {
        normalize_address(candidate.road_address or candidate.address)
        for address_score, _, candidate in matches
        if address_score >= best_address_score - 0.05
    }
    if len(competing_addresses) > 1:
        return decision
    evidence = dict(decision.evidence)
    evidence.update(
        {
            "name_similarity": best_name_score,
            "address_similarity": best_address_score,
            "permit_id": permit.permit_id,
            "permit_status": permit.business_status,
            "permit_address": permit.address,
        }
    )
    return VerificationDecision(
        decision="approved",
        confidence=min(0.98, 0.68 + best_name_score * 0.15 + best_address_score * 0.15),
        approved_by="rule",
        selected_candidate=best,
        category=best.category,
        reason_codes=[*decision.reason_codes, "PERMIT_ADVISORY_ACTIVE_ADDRESS_MATCH"],
        evidence=evidence,
    )


def advisory_permit_conflict_decision(
    row: NormalizedExpenseRow,
    decision: VerificationDecision,
    permit: PermitSnapshot,
) -> VerificationDecision:
    if (
        decision.decision != "approved"
        or row.normalized_address
        or "NAVER_UNIQUE_EXACT_AMONG_MULTIPLE" not in decision.reason_codes
        or permit.business_status != "active"
        or permit.category not in FOOD_CATEGORIES
        or not permit.address
        or decision.selected_candidate is None
        or candidate_structured_address_match(permit.address, decision.selected_candidate)
    ):
        return decision
    alternatives: list[PlaceCandidate] = []
    for item in decision.evidence.get("candidate_evidence", []):
        if not isinstance(item, dict):
            continue
        candidate = PlaceCandidate(
            provider_place_id=str(item.get("provider_place_id") or ""),
            name=str(item.get("name") or ""),
            category=str(item.get("category") or "other"),
            address=str(item.get("address") or ""),
            road_address=str(item.get("road_address") or item.get("address") or ""),
            longitude=float(item.get("longitude") or 0.0),
            latitude=float(item.get("latitude") or 0.0),
            provider_category_raw=str(item.get("provider_category_raw") or ""),
        )
        if (
            candidate.provider_place_id
            and candidate.category in FOOD_CATEGORIES
            and candidate_structured_address_match(permit.address, candidate)
        ):
            alternatives.append(candidate)
    if not alternatives:
        return decision
    evidence = dict(decision.evidence)
    evidence.update(
        {
            "permit_id": permit.permit_id,
            "permit_status": permit.business_status,
            "permit_address": permit.address,
            "conflicting_candidate_ids": [candidate.provider_place_id for candidate in alternatives],
        }
    )
    return VerificationDecision(
        decision="needs_review",
        confidence=0.88,
        approved_by="rule",
        selected_candidate=decision.selected_candidate,
        category=decision.category,
        reason_codes=[
            *(
                reason
                for reason in decision.reason_codes
                if reason
                not in {
                    "NAVER_UNIQUE_EXACT_AMONG_MULTIPLE",
                    "PROVIDER_ADDRESS_AVAILABLE",
                    "PERMIT_ACTIVE",
                }
            ),
            "PERMIT_ADVISORY_CONFLICTING_CANDIDATE",
        ],
        evidence=evidence,
    )


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
        start_date: str = "",
        end_date: str = "",
        raw_dir: Path | None = None,
        filter_rows_by_used_date: bool = True,
    ) -> None:
        self.max_pages = max_pages
        self.max_documents = max_documents
        self.start_date = _parse_iso_date(start_date)
        self.end_date = _parse_iso_date(end_date)
        self.raw_dir = raw_dir or BASE_DIR / "var" / "raw" / "busan_city"
        self.filter_rows_by_used_date = filter_rows_by_used_date

    def discover(self) -> list[SourceDocument]:
        documents: list[SourceDocument] = []
        for target in self.discover_targets():
            try:
                documents.append(self.fetch_document(target))
            except Exception:
                continue
        return documents

    def discover_targets(self) -> list[CollectionTarget]:
        targets: list[CollectionTarget] = []
        seen: set[str] = set()
        for page in range(1, self.max_pages + 1):
            list_url = f"{self.base_url}{self.list_path}?curPage={page}"
            try:
                list_html = self._get_text(list_url)
            except Exception:
                continue
            items = self._parse_list(list_html, list_url)
            if not items:
                break
            dated_items = 0
            items_before_start = 0
            new_items = 0
            for item in items:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                new_items += 1
                published = _parse_iso_date(item.get("published_at"))
                if published:
                    dated_items += 1
                    if self.start_date and published < self.start_date:
                        items_before_start += 1
                        continue
                if self.end_date and published and published > self.end_date:
                    continue
                targets.append(
                    CollectionTarget(
                        source_url=item["url"],
                        source_title=item.get("title", ""),
                        published_at=item.get("published_at", ""),
                        department_name=item.get("department", ""),
                    )
                )
                if len(targets) >= self.max_documents:
                    return targets
            if new_items == 0:
                break
            if self.start_date and dated_items and items_before_start == dated_items:
                break
        return targets

    def fetch_document(self, target: CollectionTarget) -> SourceDocument:
        item = {
            "url": target.source_url,
            "title": target.source_title,
            "department": target.department_name,
            "published_at": target.published_at,
        }
        detail_html = self._get_text(target.source_url)
        return self._parse_detail(item, detail_html)

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        supported = [
            attachment
            for attachment in document.attachments
            if attachment.filename.lower().endswith((".xlsx", ".xls", ".html", ".htm"))
        ]
        if not supported:
            raise UnsupportedDocumentError("no supported XLSX attachment found")
        rows: list[RawExpenseRow] = []
        parse_errors: list[str] = []
        parsed_any = False
        for attachment in supported:
            try:
                parsed_rows = parse_expense_xlsx(Path(attachment.content_path).read_bytes())
                parsed_any = True
            except Exception as exc:
                parse_errors.append(f"{attachment.filename}: {exc}")
                continue
            for parsed in parsed_rows:
                if self.filter_rows_by_used_date and not self._is_within_date_range(parsed.used_date):
                    continue
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
        if not parsed_any:
            raise UnsupportedDocumentError("all attachments failed: " + " | ".join(parse_errors))
        return rows

    def _is_within_date_range(self, used_date: str) -> bool:
        parsed_date = _parse_iso_date(used_date)
        if parsed_date is None:
            return True
        if self.start_date and parsed_date < self.start_date:
            return False
        if self.end_date and parsed_date > self.end_date:
            return False
        return True

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
            try:
                payload = self._get_bytes(url, referer=referer)
            except Exception:
                continue
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
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urlopen(Request(url, headers=headers), timeout=12) as response:
                    return response.read()
            except Exception as exc:
                last_error = exc
                if attempt < 1:
                    time.sleep(0.4 * (attempt + 1))
        raise last_error or TimeoutError("request failed")


class DailyPipeline:
    def __init__(
        self,
        database: Database,
        adapter: ExpenseAdapter | None = None,
        verifier: VerifierAgent | None = None,
        settings: Settings | None = None,
        verify_new_rows: bool = True,
        verification_progress_callback: VerificationProgressCallback | None = None,
    ) -> None:
        self.database = database
        self.adapter = adapter or BusanFixtureAdapter()
        self.verifier = verifier
        self.settings = settings
        self.verify_new_rows = verify_new_rows
        self.verification_progress_callback = verification_progress_callback

    def _active_verifier(self, conn: Any) -> VerifierAgent:
        return self.verifier or self._build_verifier(self.settings, conn)

    def _build_verifier(self, settings: Settings | None, conn: Any | None = None) -> VerifierAgent:
        if not settings:
            return VerifierAgent()
        naver_client = None
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
        return VerifierAgent(naver_client=naver_client, permit_client=NoPermitClient())

    def _build_advisory_permit_client(
        self,
        settings: Settings | None,
        conn: Any | None = None,
    ) -> PermitClient | None:
        if not settings or not settings.data_go_kr_service_key:
            return None
        permit_client: PermitClient = DataGoKrPermitClient(settings.data_go_kr_service_key)
        if settings.data_go_kr_service_key:
            if conn is not None:
                permit_client = CachedPermitClient(conn, permit_client)
        return permit_client

    def _annotate_advisory_permit(
        self,
        conn: Any,
        row: NormalizedExpenseRow,
        decision: VerificationDecision,
    ) -> tuple[str, VerificationDecision]:
        if self._should_skip_advisory_permit(decision):
            return "skipped", decision
        permit_client = self._build_advisory_permit_client(self.settings, conn)
        if permit_client is None:
            return "skipped", decision
        try:
            permit = permit_client.lookup(row)
        except Exception as exc:
            decision.reason_codes.append("PERMIT_ADVISORY_UNAVAILABLE")
            decision.evidence["permit_advisory"] = {
                "status": "unavailable",
                "error": str(exc)[:300],
            }
            return "unavailable", decision
        if permit is None:
            decision.reason_codes.append("PERMIT_ADVISORY_MISSING")
            decision.evidence["permit_advisory"] = {"status": "missing"}
            return "missing", decision
        advisory_payload = {
            "status": "found",
            "permit_id": permit.permit_id,
            "business_status": permit.business_status,
            "category": permit.category,
            "address": permit.address,
        }
        if permit.business_status == "active" and permit.category in FOOD_CATEGORIES:
            reason = "PERMIT_ADVISORY_ACTIVE_FOOD"
            result = "active_food"
        elif permit.business_status in {"closed", "moved", "non_food"}:
            reason = "PERMIT_ADVISORY_NOT_ACTIVE"
            result = "not_active"
        elif permit.category not in FOOD_CATEGORIES:
            reason = "PERMIT_ADVISORY_NON_FOOD_CATEGORY"
            result = "non_food_category"
        else:
            reason = "PERMIT_ADVISORY_FOUND"
            result = "found"
        decision.reason_codes.append(reason)
        decision.evidence["permit_advisory"] = advisory_payload
        decision = advisory_permit_conflict_decision(row, decision, permit)
        resolved = advisory_permit_resolution_decision(row, decision, permit)
        if resolved.decision == "approved":
            return result, resolved
        if (
            decision.decision == "needs_review"
            and permit.business_status == "active"
            and permit.category in FOOD_CATEGORIES
            and permit.address
        ):
            resolved = self._resolve_with_permit_address(conn, row, decision, permit)
        return result, resolved

    def _resolve_with_permit_address(
        self,
        conn: Any,
        row: NormalizedExpenseRow,
        decision: VerificationDecision,
        permit: PermitSnapshot,
    ) -> VerificationDecision:
        enriched_row = replace(
            row,
            address=permit.address,
            normalized_address=normalize_address(permit.address),
        )
        try:
            naver_client = self._active_verifier(conn).naver_client
            enriched = VerifierAgent(
                naver_client=naver_client,
                permit_client=StoredPermitClient(permit),
            ).verify(enriched_row)
        except Exception as exc:
            decision.reason_codes.append("PERMIT_ADDRESS_SEARCH_UNAVAILABLE")
            decision.evidence["permit_address_search_error"] = str(exc)[:300]
            return decision
        if enriched.decision != "approved":
            enriched = advisory_permit_resolution_decision(enriched_row, enriched, permit)
        if enriched.decision != "approved":
            return decision
        evidence = dict(enriched.evidence)
        evidence["permit_advisory"] = decision.evidence.get("permit_advisory", {})
        return VerificationDecision(
            decision="approved",
            confidence=enriched.confidence,
            approved_by="rule",
            selected_candidate=enriched.selected_candidate,
            category=enriched.category,
            reason_codes=[*enriched.reason_codes, "PERMIT_ADVISORY_ADDRESS_ENRICHED"],
            evidence=evidence,
        )

    def _should_skip_advisory_permit(self, decision: VerificationDecision) -> bool:
        if decision.selected_candidate is not None:
            return False
        pre_api_prefixes = (
            "NON_FOOD_PURPOSE_",
            "FRANCHISE_ADDRESSLESS_NO_BRANCH",
            "MANUAL_FEEDBACK_",
            "PAYMENT_PROCESSOR_OR_CARD_PLACE_NAME",
            "UNKNOWN_PLACE_ADDRESS_MISMATCH",
        )
        return any(
            reason == prefix or reason.startswith(prefix)
            for reason in decision.reason_codes
            for prefix in pre_api_prefixes
        )

    def _track_advisory_permit_summary(
        self,
        summary: dict[str, Any],
        result: str,
    ) -> None:
        if result == "skipped":
            return
        if result == "unavailable":
            summary["permit_advisory_unavailable"] = summary.get("permit_advisory_unavailable", 0) + 1
            return
        summary["permit_advisory_checked"] = summary.get("permit_advisory_checked", 0) + 1

    def run(self) -> dict[str, Any]:
        self.database.prepare()
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
                "permit_advisory_checked": 0,
                "permit_advisory_unavailable": 0,
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
                        self._mark_document_parse_failed(
                            conn,
                            raw_document_id,
                            self._parse_error_status(exc),
                            str(exc),
                        )
                        continue
                    document_rows_seen = 0
                    document_rows_inserted = 0
                    for extracted_row in extracted_rows:
                        for raw_row in split_raw_expense_row_places(extracted_row):
                            summary["rows_seen"] += 1
                            document_rows_seen += 1
                            try:
                                normalized = self._normalize(raw_row)
                                expense_id, inserted_row = self._upsert_expense(
                                    conn, source, raw_document_id, raw_row, normalized
                                )
                                if inserted_row:
                                    summary["rows_inserted"] += 1
                                    document_rows_inserted += 1
                                candidate_id = self._upsert_candidate(
                                    conn, source, expense_id, raw_row, normalized
                                )
                                if self._candidate_is_resolved(conn, candidate_id):
                                    continue
                                if not self.verify_new_rows:
                                    self._defer_candidate_verification(conn, candidate_id)
                                    summary["needs_review"] += 1
                                    continue
                                decision = (
                                    expense_scope_reject_decision(normalized)
                                    or non_food_purpose_decision(normalized)
                                    or alias_memory_decision(conn, normalized)
                                    or verifier.verify(normalized)
                                )
                                permit_result, decision = self._annotate_advisory_permit(
                                    conn, normalized, decision
                                )
                                self._track_advisory_permit_summary(summary, permit_result)
                                self._persist_decision(conn, source, expense_id, candidate_id, decision)
                                summary[decision.decision] += 1
                            except Exception as exc:  # pragma: no cover - defensive DLQ guard
                                summary["dlq"] += 1
                                self._insert_dlq(conn, batch_id, "row", raw_row.__dict__, str(exc))
                    self._mark_document_parsed(
                        conn,
                        raw_document_id,
                        "parsed" if document_rows_seen > 0 else "empty",
                        document_rows_seen,
                        document_rows_inserted,
                    )
                self._finish_batch(conn, batch_id, "success", summary)
                return {"batch_id": batch_id, "status": "success", "summary": summary}
            except Exception as exc:
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                raise

    def verify_pending(self, limit: int = 100, sort: str = "verification_oldest") -> dict[str, Any]:
        return self._verify_pending(
            limit=limit,
            job_name="verify_pending",
            initial_only=False,
            sort=sort,
        )

    def verify_collected(
        self,
        limit: int = 100,
        sort: str = "verification_oldest",
        plan_id: int | None = None,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        return self._verify_pending(
            limit=limit,
            job_name="verify_collected",
            initial_only=True,
            sort=sort,
            plan_id=plan_id,
            start_date=start_date,
            end_date=end_date,
        )

    def create_collection_plan(
        self,
        start_date: str,
        end_date: str,
        max_pages: int = COLLECTION_SCAN_SAFETY_MAX_PAGES,
        max_documents: int = COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS,
        batch_size: int = 20,
    ) -> dict[str, Any]:
        self.database.prepare()
        if not hasattr(self.adapter, "discover_targets"):
            raise RuntimeError("adapter does not support collection planning")
        self.adapter.max_pages = max(1, min(int(max_pages or COLLECTION_SCAN_SAFETY_MAX_PAGES), COLLECTION_SCAN_SAFETY_MAX_PAGES))  # type: ignore[attr-defined]
        self.adapter.max_documents = max(1, min(int(max_documents or COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS), COLLECTION_SCAN_SAFETY_MAX_DOCUMENTS))  # type: ignore[attr-defined]
        self.adapter.start_date = _parse_iso_date(start_date)  # type: ignore[attr-defined]
        self.adapter.end_date = _parse_iso_date(end_date)  # type: ignore[attr-defined]
        capped_batch_size = max(1, min(int(batch_size or 20), 200))
        with self.database.session() as conn:
            batch_id = self._create_batch(conn, "collection_plan_discover")
            summary = {
                "start_date": start_date,
                "end_date": end_date,
                "scan_max_pages": self.adapter.max_pages,  # type: ignore[attr-defined]
                "scan_max_documents": self.adapter.max_documents,  # type: ignore[attr-defined]
                "batch_size": capped_batch_size,
                "documents_seen": 0,
                "documents_inserted": 0,
                "documents_skipped_collected": 0,
                "dlq": 0,
            }
            try:
                source = self._load_source(conn)
                cur = conn.execute(
                    """
                    INSERT INTO collection_plans
                      (source_key, start_date, end_date, status, scan_max_pages,
                       scan_max_documents, batch_size, created_batch_job_id, summary_json,
                       updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.adapter.source_key,
                        start_date,
                        end_date,
                        "discovering",
                        self.adapter.max_pages,  # type: ignore[attr-defined]
                        self.adapter.max_documents,  # type: ignore[attr-defined]
                        capped_batch_size,
                        batch_id,
                        safe_json_dumps(summary),
                        utc_now(),
                    ),
                )
                plan_id = int(cur.lastrowid)
                targets = self.adapter.discover_targets()  # type: ignore[attr-defined]
                for target in targets:
                    summary["documents_seen"] += 1
                    existing_document = self._existing_document_for_target(
                        conn,
                        source,
                        target,
                    )
                    existing_raw_document_id = (
                        int(existing_document["id"]) if existing_document is not None else None
                    )
                    existing_parse_status = (
                        str(existing_document["parse_status"] or "not_requested")
                        if existing_document is not None
                        else "not_requested"
                    )
                    existing_rows_seen = (
                        int(existing_document["expense_count"] or 0)
                        if existing_document is not None
                        else 0
                    )
                    parse_complete = existing_parse_status in {"parsed", "empty"} or existing_rows_seen > 0
                    status = "duplicate" if parse_complete else "collected" if existing_raw_document_id else "pending"
                    inserted = conn.execute(
                        """
                        INSERT OR IGNORE INTO collection_plan_documents
                          (plan_id, source_url, source_title, department_name, published_at,
                           status, raw_document_id, rows_seen, parse_status, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            plan_id,
                            target.source_url,
                            target.source_title,
                            target.department_name,
                            target.published_at,
                            status,
                            existing_raw_document_id,
                            existing_rows_seen,
                            existing_parse_status if existing_raw_document_id else "not_requested",
                            safe_json_dumps(
                                {"skip_reason": "already_collected"}
                                if parse_complete
                                else {"skip_reason": "already_downloaded_pending_parse"}
                                if existing_raw_document_id
                                else {}
                            ),
                        ),
                    ).rowcount
                    if inserted:
                        summary["documents_inserted"] += 1
                        if parse_complete:
                            summary["documents_skipped_collected"] += 1
                self._refresh_collection_plan_counts(conn, plan_id, extra_summary=summary)
                self._finish_batch(conn, batch_id, "success", summary)
                return {
                    "batch_id": batch_id,
                    "plan_id": plan_id,
                    "status": "success",
                    "summary": summary,
                }
            except Exception as exc:
                summary["dlq"] += 1
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                raise

    def run_collection_plan_batch(self, plan_id: int, batch_size: int | None = None) -> dict[str, Any]:
        self.database.prepare()
        with self.database.session() as conn:
            plan = conn.execute("SELECT * FROM collection_plans WHERE id = ?", (plan_id,)).fetchone()
            if plan is None:
                raise RuntimeError(f"collection plan not found: {plan_id}")
            if hasattr(self.adapter, "start_date"):
                self.adapter.start_date = _parse_iso_date(plan["start_date"])  # type: ignore[attr-defined]
            if hasattr(self.adapter, "end_date"):
                self.adapter.end_date = _parse_iso_date(plan["end_date"])  # type: ignore[attr-defined]
            source = self._load_source(conn)
            capped_batch_size = max(1, min(int(batch_size or plan["batch_size"] or 20), 200))
            batch_id = self._create_batch(conn, "collection_plan_batch")
            summary = {
                "plan_id": plan_id,
                "batch_size": capped_batch_size,
                "documents_seen": 0,
                "documents_inserted": 0,
                "documents_duplicate": 0,
                "rows_seen": 0,
                "rows_inserted": 0,
                "approved": 0,
                "rejected": 0,
                "needs_review": 0,
                "dlq": 0,
                "permit_advisory_checked": 0,
                "permit_advisory_unavailable": 0,
            }
            try:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM collection_plan_documents
                    WHERE plan_id = ? AND status = 'pending'
                    ORDER BY published_at DESC, id ASC
                    LIMIT ?
                    """,
                    (plan_id, capped_batch_size),
                ).fetchall()
                now = utc_now()
                conn.execute(
                    """
                    UPDATE collection_plans
                    SET status = 'collecting', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, plan_id),
                )
                for target_row in rows:
                    summary["documents_seen"] += 1
                    conn.execute(
                        """
                        UPDATE collection_plan_documents
                        SET status = 'processing',
                            batch_job_id = ?,
                            attempts = attempts + 1,
                            error_message = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (batch_id, utc_now(), target_row["id"]),
                    )
                    document: SourceDocument | None = None
                    try:
                        document = self.adapter.fetch_document(
                            CollectionTarget(
                                source_url=target_row["source_url"],
                                source_title=target_row["source_title"] or "",
                                published_at=target_row["published_at"] or "",
                                department_name=target_row["department_name"] or "",
                            )
                        )
                        raw_document_id, inserted_doc = self._upsert_document(conn, source, document)
                        if inserted_doc:
                            summary["documents_inserted"] += 1
                        else:
                            summary["documents_duplicate"] += 1
                        conn.execute(
                            """
                            UPDATE collection_plan_documents
                            SET status = 'collected',
                                raw_document_id = ?,
                                parse_status = CASE
                                  WHEN parse_status IN ('parsed', 'empty') THEN parse_status
                                  ELSE 'not_requested'
                                END,
                                parse_error_message = NULL,
                                error_message = NULL,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                raw_document_id,
                                utc_now(),
                                target_row["id"],
                            ),
                        )
                    except Exception as exc:
                        summary["dlq"] += 1
                        self._insert_dlq(
                            conn,
                            batch_id,
                            "collection_plan_document",
                            {
                                "plan_id": plan_id,
                                "source_url": target_row["source_url"],
                                "source_title": target_row["source_title"],
                                "attachment_diagnostics": _attachment_diagnostics(document.attachments)
                                if document is not None
                                else [],
                            },
                            str(exc),
                        )
                        conn.execute(
                            """
                            UPDATE collection_plan_documents
                            SET status = 'failed',
                                rows_seen = ?,
                                rows_inserted = ?,
                                error_message = ?,
                                metadata_json = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                0,
                                0,
                                str(exc),
                                safe_json_dumps(
                                    {
                                        "attachment_diagnostics": _attachment_diagnostics(document.attachments)
                                        if document is not None
                                        else []
                                    }
                                ),
                                utc_now(),
                                target_row["id"],
                            ),
                        )
                self._refresh_collection_plan_counts(conn, plan_id, extra_summary=summary)
                self._finish_batch(conn, batch_id, "success", summary)
                return {
                    "batch_id": batch_id,
                    "plan_id": plan_id,
                    "status": "success",
                    "summary": summary,
                }
            except Exception as exc:
                self._refresh_collection_plan_counts(conn, plan_id, extra_summary=summary)
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                raise

    def run_collection_plan_batches(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict[str, Any]:
        self.database.prepare()
        capped_max_batches = max(1, min(int(max_batches or 100), 500))
        pending_before = self._collection_plan_pending_count(plan_id)
        summary: dict[str, Any] = {
            "plan_id": plan_id,
            "batches_run": 0,
            "batch_ids": [],
            "pending_before": pending_before,
            "pending_after": pending_before,
            "documents_seen": 0,
            "documents_inserted": 0,
            "documents_duplicate": 0,
            "rows_seen": 0,
            "rows_inserted": 0,
            "approved": 0,
            "rejected": 0,
            "needs_review": 0,
            "dlq": 0,
        }
        last_batch_id: int | None = None
        for _ in range(capped_max_batches):
            if self._collection_plan_pending_count(plan_id) <= 0:
                break
            result = self.run_collection_plan_batch(plan_id=plan_id, batch_size=batch_size)
            batch_summary = result.get("summary", {})
            if int(batch_summary.get("documents_seen") or 0) <= 0:
                break
            last_batch_id = int(result["batch_id"])
            summary["batches_run"] += 1
            summary["batch_ids"].append(last_batch_id)
            for key in [
                "documents_seen",
                "documents_inserted",
                "documents_duplicate",
                "rows_seen",
                "rows_inserted",
                "approved",
                "rejected",
                "needs_review",
                "dlq",
            ]:
                summary[key] += int(batch_summary.get(key) or 0)
            summary["pending_after"] = self._collection_plan_pending_count(plan_id)
            if summary["pending_after"] <= 0:
                break
        return {
            "batch_id": last_batch_id,
            "plan_id": plan_id,
            "status": "success",
            "summary": summary,
        }

    def parse_collection_plan_batch(self, plan_id: int, batch_size: int | None = None) -> dict[str, Any]:
        self.database.prepare()
        with self.database.session() as conn:
            plan = conn.execute("SELECT * FROM collection_plans WHERE id = ?", (plan_id,)).fetchone()
            if plan is None:
                raise RuntimeError(f"collection plan not found: {plan_id}")
            if hasattr(self.adapter, "start_date"):
                self.adapter.start_date = _parse_iso_date(plan["start_date"])  # type: ignore[attr-defined]
            if hasattr(self.adapter, "end_date"):
                self.adapter.end_date = _parse_iso_date(plan["end_date"])  # type: ignore[attr-defined]
            if hasattr(self.adapter, "filter_rows_by_used_date"):
                self.adapter.filter_rows_by_used_date = False  # type: ignore[attr-defined]
            source = self._load_source(conn)
            capped_batch_size = max(1, min(int(batch_size or plan["batch_size"] or 20), 200))
            batch_id = self._create_batch(conn, "collection_plan_parse")
            summary = {
                "plan_id": plan_id,
                "batch_size": capped_batch_size,
                "documents_seen": 0,
                "documents_parsed": 0,
                "documents_empty": 0,
                "rows_seen": 0,
                "rows_inserted": 0,
                "approved": 0,
                "rejected": 0,
                "needs_review": 0,
                "dlq": 0,
            }
            try:
                rows = conn.execute(
                    """
                    SELECT
                      cpd.id AS collection_document_id,
                      cpd.raw_document_id,
                      cpd.metadata_json AS collection_metadata_json,
                      rd.source_url,
                      rd.source_title,
                      rd.published_at,
                      rd.raw_content_path,
                      rd.metadata_json AS raw_metadata_json
                    FROM collection_plan_documents cpd
                    JOIN raw_documents rd ON rd.id = cpd.raw_document_id
                    WHERE cpd.plan_id = ?
                      AND cpd.status IN ('collected', 'duplicate')
                      AND cpd.parse_status = 'not_requested'
                    ORDER BY cpd.published_at DESC, cpd.id ASC
                    LIMIT ?
                    """,
                    (plan_id, capped_batch_size),
                ).fetchall()
                for row in rows:
                    summary["documents_seen"] += 1
                    document = self._source_document_from_raw_row(row)
                    now = utc_now()
                    conn.execute(
                        """
                        UPDATE collection_plan_documents
                        SET parse_status = 'parsing',
                            parse_attempts = parse_attempts + 1,
                            parse_error_message = NULL,
                            batch_job_id = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (batch_id, now, row["collection_document_id"]),
                    )
                    conn.execute(
                        """
                        UPDATE raw_documents
                        SET parse_status = 'parsing',
                            parse_error_message = NULL
                        WHERE id = ?
                        """,
                        (row["raw_document_id"],),
                    )
                    doc_rows_seen = 0
                    doc_rows_inserted = 0
                    try:
                        extracted_rows = self.adapter.extract(document)
                        for extracted_row in extracted_rows:
                            for raw_row in split_raw_expense_row_places(extracted_row):
                                doc_rows_seen += 1
                                summary["rows_seen"] += 1
                                normalized = self._normalize(raw_row)
                                expense_id, inserted_row = self._upsert_expense(
                                    conn, source, int(row["raw_document_id"]), raw_row, normalized
                                )
                                candidate_id = self._upsert_candidate(
                                    conn, source, expense_id, raw_row, normalized
                                )
                                if inserted_row:
                                    doc_rows_inserted += 1
                                    summary["rows_inserted"] += 1
                                    if not self._candidate_is_resolved(conn, candidate_id):
                                        self._defer_candidate_verification(conn, candidate_id)
                                        summary["needs_review"] += 1
                        parse_status = "parsed" if doc_rows_seen > 0 else "empty"
                        if parse_status == "parsed":
                            summary["documents_parsed"] += 1
                        else:
                            summary["documents_empty"] += 1
                        self._mark_document_parsed(
                            conn,
                            int(row["raw_document_id"]),
                            parse_status,
                            doc_rows_seen,
                            doc_rows_inserted,
                        )
                        conn.execute(
                            """
                            UPDATE collection_plan_documents
                            SET parse_status = ?,
                                rows_seen = ?,
                                rows_inserted = ?,
                                parse_error_message = NULL,
                                parsed_at = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                parse_status,
                                doc_rows_seen,
                                doc_rows_inserted,
                                utc_now(),
                                utc_now(),
                                row["collection_document_id"],
                            ),
                        )
                    except Exception as exc:
                        parse_status = self._parse_error_status(exc)
                        summary["dlq"] += 1
                        self._insert_dlq(
                            conn,
                            batch_id,
                            "collection_plan_parse",
                            {
                                "plan_id": plan_id,
                                "raw_document_id": row["raw_document_id"],
                                "source_url": row["source_url"],
                                "source_title": row["source_title"],
                                "attachment_diagnostics": _attachment_diagnostics(document.attachments),
                            },
                            str(exc),
                        )
                        self._mark_document_parse_failed(
                            conn,
                            int(row["raw_document_id"]),
                            parse_status,
                            str(exc),
                        )
                        conn.execute(
                            """
                            UPDATE collection_plan_documents
                            SET parse_status = ?,
                                rows_seen = ?,
                                rows_inserted = ?,
                                parse_error_message = ?,
                                metadata_json = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                parse_status,
                                doc_rows_seen,
                                doc_rows_inserted,
                                str(exc),
                                safe_json_dumps(
                                    {
                                        **safe_json_loads(row["collection_metadata_json"], {}),
                                        "attachment_diagnostics": _attachment_diagnostics(document.attachments),
                                    }
                                ),
                                utc_now(),
                                row["collection_document_id"],
                            ),
                        )
                self._refresh_collection_plan_counts(conn, plan_id, extra_summary=summary)
                self._finish_batch(conn, batch_id, "success", summary)
                return {
                    "batch_id": batch_id,
                    "plan_id": plan_id,
                    "status": "success",
                    "summary": summary,
                }
            except Exception as exc:
                self._refresh_collection_plan_counts(conn, plan_id, extra_summary=summary)
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                raise

    def parse_collection_plan_batches(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict[str, Any]:
        self.database.prepare()
        capped_max_batches = max(1, min(int(max_batches or 100), 500))
        pending_before = self._collection_plan_parse_pending_count(plan_id)
        summary: dict[str, Any] = {
            "plan_id": plan_id,
            "batches_run": 0,
            "batch_ids": [],
            "pending_before": pending_before,
            "pending_after": pending_before,
            "documents_seen": 0,
            "documents_parsed": 0,
            "documents_empty": 0,
            "rows_seen": 0,
            "rows_inserted": 0,
            "approved": 0,
            "rejected": 0,
            "needs_review": 0,
            "dlq": 0,
        }
        last_batch_id: int | None = None
        previous_pending = pending_before
        for _ in range(capped_max_batches):
            if self._collection_plan_parse_pending_count(plan_id) <= 0:
                break
            result = self.parse_collection_plan_batch(plan_id=plan_id, batch_size=batch_size)
            batch_summary = result.get("summary", {})
            if int(batch_summary.get("documents_seen") or 0) <= 0:
                break
            last_batch_id = int(result["batch_id"])
            summary["batches_run"] += 1
            summary["batch_ids"].append(last_batch_id)
            for key in [
                "documents_seen",
                "documents_parsed",
                "documents_empty",
                "rows_seen",
                "rows_inserted",
                "approved",
                "rejected",
                "needs_review",
                "dlq",
            ]:
                summary[key] += int(batch_summary.get(key) or 0)
            summary["pending_after"] = self._collection_plan_parse_pending_count(plan_id)
            if summary["pending_after"] <= 0:
                break
            if summary["pending_after"] >= previous_pending:
                break
            previous_pending = summary["pending_after"]
        return {
            "batch_id": last_batch_id,
            "plan_id": plan_id,
            "status": "success",
            "summary": summary,
        }

    def retry_collection_plan_parse_failures(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict[str, Any]:
        self.database.prepare()
        with self.database.session() as conn:
            failed_before = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM collection_plan_documents
                    WHERE plan_id = ?
                      AND status IN ('collected', 'duplicate')
                      AND raw_document_id IS NOT NULL
                      AND parse_status = 'failed'
                    """,
                    (plan_id,),
                ).fetchone()["count"]
                or 0
            )
            if failed_before <= 0:
                pending = self._collection_plan_parse_pending_count(plan_id)
                return {
                    "batch_id": None,
                    "plan_id": plan_id,
                    "status": "success",
                    "summary": {
                        "plan_id": plan_id,
                        "retried_parse_failed": 0,
                        "batches_run": 0,
                        "pending_before": pending,
                        "pending_after": pending,
                        "documents_seen": 0,
                        "documents_parsed": 0,
                        "documents_empty": 0,
                        "rows_seen": 0,
                        "rows_inserted": 0,
                        "approved": 0,
                        "rejected": 0,
                        "needs_review": 0,
                        "dlq": 0,
                    },
                }
            now = utc_now()
            conn.execute(
                """
                UPDATE raw_documents
                SET parse_status = 'not_requested',
                    parse_error_message = NULL
                WHERE id IN (
                  SELECT raw_document_id
                  FROM collection_plan_documents
                  WHERE plan_id = ?
                    AND status IN ('collected', 'duplicate')
                    AND raw_document_id IS NOT NULL
                    AND parse_status = 'failed'
                )
                """,
                (plan_id,),
            )
            conn.execute(
                """
                UPDATE collection_plan_documents
                SET parse_status = 'not_requested',
                    parse_error_message = NULL,
                    updated_at = ?
                WHERE plan_id = ?
                  AND status IN ('collected', 'duplicate')
                  AND raw_document_id IS NOT NULL
                  AND parse_status = 'failed'
                """,
                (now, plan_id),
            )
        result = self.parse_collection_plan_batches(
            plan_id=plan_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )
        result["summary"]["retried_parse_failed"] = failed_before
        return result

    def retry_collection_plan_failures(
        self,
        plan_id: int,
        batch_size: int | None = None,
        max_batches: int = 100,
    ) -> dict[str, Any]:
        self.database.prepare()
        with self.database.session() as conn:
            failed_before = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM collection_plan_documents
                    WHERE plan_id = ? AND status = 'failed'
                    """,
                    (plan_id,),
                ).fetchone()["count"]
                or 0
            )
            if failed_before <= 0:
                return {
                    "batch_id": None,
                    "plan_id": plan_id,
                    "status": "success",
                    "summary": {
                        "plan_id": plan_id,
                        "retried_failed": 0,
                        "batches_run": 0,
                        "pending_before": self._collection_plan_pending_count(plan_id),
                        "pending_after": self._collection_plan_pending_count(plan_id),
                        "documents_seen": 0,
                        "documents_inserted": 0,
                        "documents_duplicate": 0,
                        "rows_seen": 0,
                        "rows_inserted": 0,
                        "approved": 0,
                        "rejected": 0,
                        "needs_review": 0,
                        "dlq": 0,
                    },
                }
            conn.execute(
                """
                UPDATE collection_plan_documents
                SET status = 'pending',
                    error_message = NULL,
                    metadata_json = '{}',
                    updated_at = ?
                WHERE plan_id = ? AND status = 'failed'
                """,
                (utc_now(), plan_id),
            )
            self._refresh_collection_plan_counts(
                conn,
                plan_id,
                extra_summary={"retry_failed_documents": failed_before},
            )
        result = self.run_collection_plan_batches(
            plan_id=plan_id,
            batch_size=batch_size,
            max_batches=max_batches,
        )
        result["summary"]["retried_failed"] = failed_before
        return result

    def _collection_plan_pending_count(self, plan_id: int) -> int:
        with self.database.session() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM collection_plan_documents
                WHERE plan_id = ? AND status = 'pending'
                """,
                (plan_id,),
            ).fetchone()
            return int(row["count"] or 0)

    def _collection_plan_parse_pending_count(self, plan_id: int) -> int:
        with self.database.session() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM collection_plan_documents
                WHERE plan_id = ?
                  AND status IN ('collected', 'duplicate')
                  AND raw_document_id IS NOT NULL
                  AND parse_status = 'not_requested'
                """,
                (plan_id,),
            ).fetchone()
            return int(row["count"] or 0)

    def _emit_verification_progress(self, event: dict[str, Any]) -> None:
        if self.verification_progress_callback is None:
            return
        try:
            self.verification_progress_callback({**event, "updated_at": utc_now()})
        except Exception:
            return

    def _verification_order_sql(self, sort: str, initial_only: bool) -> str:
        fallback = (
            "COALESCE(mrt.created_at, c.updated_at) ASC, c.id ASC"
            if initial_only
            else "mrt.created_at ASC, c.id ASC"
        )
        return {
            "verification_oldest": fallback,
            "used_date_desc": "COALESCE(c.used_date, '') DESC, c.id DESC",
            "used_date_asc": "COALESCE(c.used_date, '') ASC, c.id ASC",
            "source_published_desc": "COALESCE(rd.published_at, '') DESC, c.id DESC",
            "source_published_asc": "COALESCE(rd.published_at, '') ASC, c.id ASC",
            "amount_desc": "COALESCE(c.amount, 0) DESC, c.id DESC",
            "name_asc": "COALESCE(c.normalized_place_name, c.original_place_name) ASC, c.id ASC",
            "id_desc": "c.id DESC",
            "id_asc": "c.id ASC",
        }.get(str(sort or "verification_oldest"), fallback)

    def _verify_pending(
        self,
        limit: int,
        job_name: str,
        initial_only: bool,
        sort: str,
        plan_id: int | None = None,
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        self.database.prepare()
        capped_limit = max(1, min(limit, 500))
        with self.database.session() as conn:
            batch_id = self._create_batch(conn, job_name)
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
                summary["pending_before"] = self._verification_pending_count(
                    conn,
                    initial_only,
                    plan_id=plan_id,
                    start_date=start_date,
                    end_date=end_date,
                )
                verifier = self._active_verifier(conn)
                pending_filter = (
                    """
                    c.verification_status = 'not_requested'
                    AND c.review_note = 'PENDING_VERIFICATION'
                    AND c.status = 'needs_review'
                    AND c.manual_review_status = 'pending'
                    AND (mrt.id IS NULL OR mrt.status = 'pending')
                    """
                    if initial_only
                    else "mrt.status = 'pending'"
                )
                order_sql = self._verification_order_sql(sort, initial_only)
                scope_clauses: list[str] = []
                scope_params: list[Any] = []
                if plan_id is not None:
                    scope_clauses.append(
                        """
                        EXISTS (
                          SELECT 1
                          FROM collection_plan_documents cpd
                          WHERE cpd.plan_id = ?
                            AND cpd.raw_document_id = rd.id
                            AND cpd.status = 'collected'
                        )
                        """
                    )
                    scope_params.append(int(plan_id))
                if start_date:
                    scope_clauses.append("COALESCE(c.used_date, '') >= ?")
                    scope_params.append(start_date)
                if end_date:
                    scope_clauses.append("COALESCE(c.used_date, '') <= ?")
                    scope_params.append(end_date)
                scope_sql = "".join(f" AND ({clause})" for clause in scope_clauses)
                rows = conn.execute(
                    f"""
                    SELECT
                      c.id AS candidate_id,
                      c.expense_record_id,
                      c.region_id,
                      c.original_place_name,
                      c.original_address,
                      c.normalized_place_name,
                      c.normalized_address,
                      c.review_place_name,
                      c.review_address,
                      c.review_normalized_place_name,
                      c.review_normalized_address,
                      c.used_date,
                      c.amount,
                      er.source_row_number,
                      er.department_name,
                      er.purpose
                    FROM restaurant_candidates c
                    LEFT JOIN manual_review_tasks mrt ON mrt.candidate_id = c.id
                    JOIN expense_records er ON er.id = c.expense_record_id
                    JOIN raw_documents rd ON rd.id = er.raw_document_id
                    WHERE {pending_filter}
                    {scope_sql}
                    ORDER BY {order_sql}
                    LIMIT ?
                    """,
                    (*scope_params, capped_limit),
                ).fetchall()
                self._emit_verification_progress(
                    {
                        "event": "batch_started",
                        "batch_id": batch_id,
                        "job_name": job_name,
                        "total": len(rows),
                        "limit": capped_limit,
                    }
                )
                for order, row in enumerate(rows, start=1):
                    self._emit_verification_progress(
                        {
                            "event": "candidate_queued",
                            "batch_id": batch_id,
                            "candidate_id": int(row["candidate_id"]),
                            "order": order,
                            "stage": "queued",
                            "label": "검증 대기",
                            "percent": 4,
                            "status": "queued",
                            "place_name": row["original_place_name"] or "",
                        }
                    )
                for order, row in enumerate(rows, start=1):
                    summary["rows_seen"] += 1
                    candidate_id = int(row["candidate_id"])

                    def progress(stage: str, label: str, percent: int, status: str = "running") -> None:
                        self._emit_verification_progress(
                            {
                                "event": "candidate_step",
                                "batch_id": batch_id,
                                "candidate_id": candidate_id,
                                "order": order,
                                "stage": stage,
                                "label": label,
                                "percent": percent,
                                "status": status,
                                "place_name": row["original_place_name"] or "",
                            }
                        )

                    try:
                        progress("normalize", "원문 정규화", 8)
                        normalized = NormalizedExpenseRow(
                            row_number=int(row["source_row_number"] or 0),
                            department_name=row["department_name"] or "",
                            used_date=row["used_date"] or "",
                            place_name=row["review_place_name"] or row["original_place_name"] or "",
                            address=row["review_address"] or row["original_address"] or "",
                            purpose=row["purpose"] or "",
                            amount=int(row["amount"] or 0),
                            normalized_place_name=row["review_normalized_place_name"]
                            or row["normalized_place_name"]
                            or normalize_text(row["review_place_name"] or row["original_place_name"]),
                            normalized_address=row["review_normalized_address"]
                            or row["normalized_address"]
                            or normalize_address(row["review_address"] or row["original_address"]),
                        )
                        progress("expense_scope_rule", "업무추진비 범위 조건", 18)
                        decision = expense_scope_reject_decision(normalized)
                        if decision is None:
                            progress("purpose_rule", "음식점 목적 조건", 30)
                            decision = non_food_purpose_decision(normalized)
                        if decision is None:
                            progress("existing_success", "기존 승인 근거 확인", 42)
                            decision = existing_success_verification_decision(conn, candidate_id, normalized)
                        if decision is None:
                            progress("alias_memory", "별칭 기억 확인", 54)
                            decision = alias_memory_decision(conn, normalized)
                        if decision is None:
                            progress("provider_evidence", "기존 검색 근거 확인", 66)
                            decision = existing_provider_evidence_decision(conn, candidate_id, normalized)
                        if decision is None:
                            progress("external_verification", "외부 API 검증", 78)
                            decision = verifier.verify(
                                normalized,
                                progress=lambda stage, label, percent: progress(stage, label, percent),
                            )
                        progress("persist_decision", "검증 결과 저장", 96)
                        permit_result, decision = self._annotate_advisory_permit(
                            conn, normalized, decision
                        )
                        self._track_advisory_permit_summary(summary, permit_result)
                        self._persist_decision(
                            conn,
                            {"region_id": row["region_id"]},
                            int(row["expense_record_id"]),
                            candidate_id,
                            decision,
                        )
                        summary[decision.decision] += 1
                        self._emit_verification_progress(
                            {
                                "event": "candidate_done",
                                "batch_id": batch_id,
                                "candidate_id": candidate_id,
                                "order": order,
                                "stage": "done",
                                "label": {
                                    "approved": "승인 완료",
                                    "needs_review": "수동검토 이동",
                                    "rejected": "반려 완료",
                                }.get(decision.decision, "검증 완료"),
                                "percent": 100,
                                "status": "completed",
                                "decision": decision.decision,
                                "place_name": row["original_place_name"] or "",
                            }
                        )
                    except Exception as exc:  # pragma: no cover - defensive DLQ guard
                        summary["dlq"] += 1
                        self._emit_verification_progress(
                            {
                                "event": "candidate_done",
                                "batch_id": batch_id,
                                "candidate_id": candidate_id,
                                "order": order,
                                "stage": "failed",
                                "label": "검증 실패",
                                "percent": 100,
                                "status": "failed",
                                "place_name": row["original_place_name"] or "",
                            }
                        )
                        self._insert_dlq(
                            conn,
                            batch_id,
                            "pending_verification",
                            dict(row),
                            str(exc),
                        )
                summary["rows_processed"] = summary["rows_seen"]
                summary["pending_after"] = self._verification_pending_count(
                    conn,
                    initial_only,
                    plan_id=plan_id,
                    start_date=start_date,
                    end_date=end_date,
                )
                self._finish_batch(conn, batch_id, "success", summary)
                self._emit_verification_progress(
                    {
                        "event": "batch_finished",
                        "batch_id": batch_id,
                        "job_name": job_name,
                        "processed": summary["rows_processed"],
                        "summary": summary,
                    }
                )
                return {"batch_id": batch_id, "status": "success", "summary": summary}
            except Exception as exc:
                summary["pending_after"] = self._verification_pending_count(
                    conn,
                    initial_only,
                    plan_id=plan_id,
                    start_date=start_date,
                    end_date=end_date,
                )
                self._finish_batch(conn, batch_id, "failed", summary, str(exc))
                self._emit_verification_progress(
                    {
                        "event": "batch_finished",
                        "batch_id": batch_id,
                        "job_name": job_name,
                        "processed": summary["rows_seen"],
                        "summary": summary,
                    }
                )
                raise

    def _verification_pending_count(
        self,
        conn: Any,
        initial_only: bool,
        plan_id: int | None = None,
        start_date: str = "",
        end_date: str = "",
    ) -> int:
        if initial_only:
            scope_clauses: list[str] = []
            scope_params: list[Any] = []
            if plan_id is not None:
                scope_clauses.append(
                    """
                    EXISTS (
                      SELECT 1
                      FROM collection_plan_documents cpd
                      WHERE cpd.plan_id = ?
                        AND cpd.raw_document_id = rd.id
                        AND cpd.status = 'collected'
                    )
                    """
                )
                scope_params.append(int(plan_id))
            if start_date:
                scope_clauses.append("COALESCE(c.used_date, '') >= ?")
                scope_params.append(start_date)
            if end_date:
                scope_clauses.append("COALESCE(c.used_date, '') <= ?")
                scope_params.append(end_date)
            scope_sql = "".join(f" AND ({clause})" for clause in scope_clauses)
            return int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM restaurant_candidates c
                    JOIN expense_records er ON er.id = c.expense_record_id
                    JOIN raw_documents rd ON rd.id = er.raw_document_id
                    LEFT JOIN manual_review_tasks mrt ON mrt.candidate_id = c.id
                    WHERE c.verification_status = 'not_requested'
                      AND c.review_note = 'PENDING_VERIFICATION'
                      AND c.status = 'needs_review'
                      AND c.manual_review_status = 'pending'
                      AND (mrt.id IS NULL OR mrt.status = 'pending')
                      {scope_sql}
                    """,
                    scope_params,
                ).fetchone()["count"]
            )
        return int(
            conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM manual_review_tasks
                WHERE status = 'pending'
                """
            ).fetchone()["count"]
        )

    def _refresh_collection_plan_counts(
        self,
        conn: Any,
        plan_id: int,
        extra_summary: dict[str, Any] | None = None,
    ) -> None:
        counts = conn.execute(
            """
            SELECT
              COUNT(*) AS discovered_count,
              SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
              SUM(CASE WHEN status = 'collected' THEN 1 ELSE 0 END) AS collected_count,
              SUM(CASE WHEN status = 'duplicate' THEN 1 ELSE 0 END) AS duplicate_count,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
              SUM(rows_seen) AS rows_seen,
              SUM(rows_inserted) AS rows_inserted
            FROM collection_plan_documents
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        pending_count = int(counts["pending_count"] or 0)
        failed_count = int(counts["failed_count"] or 0)
        discovered_count = int(counts["discovered_count"] or 0)
        processed_count = (
            int(counts["collected_count"] or 0)
            + int(counts["duplicate_count"] or 0)
            + failed_count
        )
        if discovered_count == 0:
            status = "empty"
        elif pending_count > 0 and processed_count > 0:
            status = "collecting"
        elif pending_count > 0:
            status = "ready"
        elif failed_count > 0:
            status = "completed_with_errors"
        else:
            status = "completed"
        now = utc_now()
        conn.execute(
            """
            UPDATE collection_plans
            SET status = ?,
                discovered_count = ?,
                pending_count = ?,
                collected_count = ?,
                duplicate_count = ?,
                failed_count = ?,
                rows_seen = ?,
                rows_inserted = ?,
                summary_json = ?,
                updated_at = ?,
                completed_at = CASE WHEN ? IN ('completed', 'completed_with_errors', 'empty') THEN ? ELSE completed_at END
            WHERE id = ?
            """,
            (
                status,
                discovered_count,
                pending_count,
                int(counts["collected_count"] or 0),
                int(counts["duplicate_count"] or 0),
                failed_count,
                int(counts["rows_seen"] or 0),
                int(counts["rows_inserted"] or 0),
                safe_json_dumps(extra_summary or {}),
                now,
                status,
                now,
                plan_id,
            ),
        )

    def _create_batch(self, conn: Any, job_name: str | None = None) -> int:
        cur = conn.execute(
            "INSERT INTO batch_jobs (job_name, status, started_at) VALUES (?, ?, ?)",
            (job_name or f"daily_{self.adapter.source_key}", "running", utc_now()),
        )
        return int(cur.lastrowid)

    def _load_source(self, conn: Any) -> Any:
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

    def _existing_document_for_target(
        self,
        conn: Any,
        source: Any,
        target: CollectionTarget,
    ) -> Any | None:
        identity = source_document_identity(target.source_url)
        parsed = urlparse(target.source_url)
        document_ids = parse_qs(parsed.query).get("schIndx") or []
        if document_ids and str(document_ids[0]).isdigit():
            url_clause = "(rd.source_url = ? OR rd.source_url LIKE ?)"
            url_params: list[Any] = [target.source_url, f"%schIndx={document_ids[0]}%"]
        else:
            url_clause = "rd.source_url = ?"
            url_params = [target.source_url]
        rows = conn.execute(
            f"""
            SELECT
              rd.id,
              rd.source_url,
              rd.parse_status,
              COUNT(er.id) AS expense_count
            FROM raw_documents rd
            LEFT JOIN expense_records er ON er.raw_document_id = rd.id
            WHERE rd.institution_id = ?
              AND {url_clause}
            GROUP BY rd.id, rd.source_url, rd.parse_status
            ORDER BY rd.id DESC
            """,
            (source["institution_id"], *url_params),
        ).fetchall()
        return next(
            (
                row
                for row in rows
                if source_document_identity(row["source_url"]) == identity
            ),
            None,
        )

    def _upsert_document(
        self, conn: Any, source: Any, document: SourceDocument
    ) -> tuple[int, bool]:
        existing_by_url = self._existing_document_for_target(
            conn,
            source,
            CollectionTarget(
                source_url=document.source_url,
                source_title=document.source_title,
                published_at=document.published_at,
                department_name=document.department_name,
            ),
        )
        if existing_by_url is not None:
            return int(existing_by_url["id"]), False
        content_hash = stable_hash(document.content)
        existing = conn.execute(
            """
            SELECT id FROM raw_documents
            WHERE institution_id = ? AND content_hash = ?
            """,
            (source["institution_id"], content_hash),
        ).fetchone()
        if existing:
            return int(existing["id"]), False
        cur = conn.execute(
            """
            INSERT INTO raw_documents
              (institution_id, source_registry_id, source_url, source_title, published_at,
               collected_at, content_hash, raw_content_path, status, parse_status, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "collected",
                "not_requested",
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

    def _source_document_from_raw_row(self, row: Any) -> SourceDocument:
        metadata = safe_json_loads(row["raw_metadata_json"], {})
        attachments: list[SourceAttachment] = []
        for item in metadata.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            attachments.append(
                SourceAttachment(
                    filename=str(item.get("filename") or ""),
                    url=str(item.get("url") or ""),
                    content_path=str(item.get("content_path") or ""),
                    content_hash=str(item.get("content_hash") or ""),
                    media_type=str(item.get("media_type") or ""),
                )
            )
        return SourceDocument(
            source_url=str(row["source_url"] or ""),
            source_title=str(row["source_title"] or ""),
            published_at=str(row["published_at"] or ""),
            content=safe_json_dumps(
                {
                    "url": row["source_url"],
                    "title": row["source_title"],
                    "department": metadata.get("department_name") or "",
                    "attachment_hashes": [attachment.content_hash for attachment in attachments],
                }
            ),
            department_name=str(metadata.get("department_name") or ""),
            raw_content_path=row["raw_content_path"],
            attachments=tuple(attachments),
        )

    def _parse_error_status(self, exc: Exception) -> str:
        message = str(exc).lower()
        return "unsupported" if "unsupported" in message or isinstance(exc, UnsupportedDocumentError) else "failed"

    def _mark_document_parsed(
        self,
        conn: Any,
        raw_document_id: int,
        parse_status: str,
        rows_seen: int,
        rows_inserted: int,
    ) -> None:
        metadata_row = conn.execute(
            "SELECT metadata_json FROM raw_documents WHERE id = ?", (raw_document_id,)
        ).fetchone()
        metadata = safe_json_loads(metadata_row["metadata_json"], {}) if metadata_row else {}
        metadata["parse_summary"] = {
            "rows_seen": rows_seen,
            "rows_inserted": rows_inserted,
        }
        conn.execute(
            """
            UPDATE raw_documents
            SET status = 'parsed',
                parse_status = ?,
                parsed_at = ?,
                parse_error_message = NULL,
                metadata_json = ?
            WHERE id = ?
            """,
            (parse_status, utc_now(), safe_json_dumps(metadata), raw_document_id),
        )

    def _mark_document_parse_failed(
        self,
        conn: Any,
        raw_document_id: int,
        parse_status: str,
        error_message: str,
    ) -> None:
        conn.execute(
            """
            UPDATE raw_documents
            SET parse_status = ?,
                parse_error_message = ?
            WHERE id = ?
            """,
            (parse_status, error_message, raw_document_id),
        )

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
        conn: Any,
        source: Any,
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
        conn: Any,
        source: Any,
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
                (
                    f"{self.adapter.source_key}:multi_place_split"
                    if raw_row.split_count > 1
                    else (
                        f"{self.adapter.source_key}:place_suffix_cleaned"
                        if raw_row.cleaned_from_place_name
                        else self.adapter.source_key
                    )
                ),
            ),
        )
        return int(cur.lastrowid)

    def _candidate_is_resolved(self, conn: Any, candidate_id: int) -> bool:
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

    def _defer_candidate_verification(self, conn: Any, candidate_id: int) -> None:
        conn.execute(
            """
            UPDATE restaurant_candidates
            SET status = 'needs_review',
                verification_status = 'not_requested',
                manual_review_status = 'pending',
                review_note = 'PENDING_VERIFICATION',
                updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), candidate_id),
        )
        conn.execute(
            """
            INSERT INTO manual_review_tasks (candidate_id, status, reason)
            VALUES (?, 'pending', 'PENDING_VERIFICATION')
            ON CONFLICT(candidate_id) DO UPDATE SET
              status = 'pending',
              reason = 'PENDING_VERIFICATION',
              reviewer_note = NULL,
              reviewed_by = NULL,
              reviewed_at = NULL
            """,
            (candidate_id,),
        )

    def _persist_decision(
        self,
        conn: Any,
        source: Any,
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
        conn: Any,
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
                provider_category_raw=str(evidence.get("provider_category_raw") or ""),
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
        conn: Any,
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
        conn: Any,
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
        raw_evidence = dict(decision.evidence)
        if candidate.provider_category_raw:
            raw_evidence["provider_category_raw"] = candidate.provider_category_raw
        raw_response_json = safe_json_dumps(raw_evidence)
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
        conn: Any,
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
        self, conn: Any, restaurant_id: int, expense_id: int, candidate_id: int
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
        conn: Any,
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
        conn: Any,
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


def _attachment_diagnostics(attachments: tuple[SourceAttachment, ...]) -> list[dict[str, Any]]:
    return [_attachment_diagnostic(attachment) for attachment in attachments]


def _attachment_diagnostic(attachment: SourceAttachment) -> dict[str, Any]:
    path = Path(attachment.content_path)
    payload = path.read_bytes() if path.exists() else b""
    head = payload[:64]
    return {
        "filename": attachment.filename,
        "media_type": attachment.media_type,
        "url": attachment.url,
        "content_path": attachment.content_path,
        "content_hash": attachment.content_hash,
        "size_bytes": len(payload),
        "head_hex": head[:16].hex(),
        "detected_type": _detect_attachment_type(payload),
    }


def _detect_attachment_type(payload: bytes) -> str:
    head = payload[:512].lstrip().lower()
    if not payload:
        return "empty"
    if payload.startswith(b"PK\x03\x04"):
        return "xlsx_zip"
    if payload.startswith(b"\x9b DRMONE") or b"DRMONE" in payload[:64]:
        return "encrypted_drm"
    if payload.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "legacy_xls_binary"
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<table" in head:
        return "html_table"
    return "unknown"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _insert_api_call_log(
    conn: Any,
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
