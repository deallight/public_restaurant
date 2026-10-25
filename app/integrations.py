from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .agents import (
    FOOD_CATEGORIES,
    NormalizedExpenseRow,
    PermitSnapshot,
    PlaceCandidate,
    category_from_text,
    franchise_search_name,
    generic_parenthetical_place_name,
    name_similarity_with_branch,
    parse_place_name,
    similarity,
    split_place_name_parts,
)
from .utils import normalize_address, normalize_text, strip_address_detail


class IntegrationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _get_json(url: str, headers: dict[str, str], timeout: int = 8) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            exc.read()
        finally:
            exc.close()
        status_code = int(exc.code)
        raise IntegrationError(
            "HTTP GET request failed",
            status_code=status_code,
            retryable=status_code == 429 or status_code >= 500,
        ) from exc
    except URLError as exc:
        raise IntegrationError("HTTP GET service is unavailable", retryable=True) from exc


def _get_json_with_retry(url: str, headers: dict[str, str], timeout: int = 8) -> dict[str, Any]:
    """Retry transient GET failures for rate-limited integrations only."""
    attempts = 3
    for attempt in range(attempts):
        try:
            return _get_json(url, headers, timeout)
        except IntegrationError as exc:
            if not exc.retryable or attempt >= attempts - 1:
                raise
            time.sleep(0.75 * (2**attempt))
    raise IntegrationError("HTTP GET request failed", retryable=True)


def _post_form_json(
    url: str,
    data: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout: int = 8,
) -> dict[str, Any]:
    encoded = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise IntegrationError(f"HTTP {exc.code}: {body[:300]}") from exc
    except URLError as exc:
        raise IntegrationError(str(exc)) from exc


def _post_form_status(
    url: str,
    data: dict[str, str],
    *,
    timeout: int = 8,
) -> int:
    request = Request(
        url,
        data=urlencode(data).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
            return int(response.status)
    except HTTPError as exc:
        exc.read()
        raise IntegrationError(
            "Naver token revocation failed",
            status_code=int(exc.code),
            retryable=int(exc.code) == 503,
        ) from exc
    except URLError as exc:
        raise IntegrationError(
            "Naver token revocation is temporarily unavailable",
            retryable=True,
        ) from exc


def _post_json(
    url: str,
    data: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 12,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "public-restaurant/1.0",
            **(headers or {}),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise IntegrationError(f"HTTP {exc.code}: {body[:300]}") from exc
    except (URLError, json.JSONDecodeError) as exc:
        raise IntegrationError(str(exc)) from exc


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").replace("&amp;", "&").strip()


def _naver_map_coord(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    # Naver Search local returns WGS84 coordinates scaled by 1e7.
    if abs(number) > 1000:
        number = number / 10_000_000
    return number


def _category_from_naver(value: str) -> str:
    text = normalize_text(value)
    if any(
        token in text
        for token in ["카페", "커피", "디저트", "베이커리", "제과", "아이스크림", "떡", "한과"]
    ):
        return "cafe"
    if any(token in text for token in ["주점", "호프", "맥주", "포차", "술집", "바", "이자카야"]):
        return "bar"
    if any(
        token in text
        for token in [
            "음식점",
            "한식",
            "한정식",
            "중식",
            "일식",
            "분식",
            "김밥",
            "스파게티",
            "파스타",
            "고기",
            "곱창",
            "막창",
            "족발",
            "보쌈",
            "덮밥",
            "요리",
            "양식",
            "멕시코",
            "남미음식",
            "패밀리레스토랑",
            "뷔페",
            "샤브샤브",
            "해산물",
            "쌈밥",
        ]
    ):
        return "restaurant"
    return category_from_text(value)


def _clean_place_query(value: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", value or "")
    text = re.sub(r"(주식회사|유한회사|㈜|\(주\))", " ", text)
    text = re.sub(
        r"\s*(?:외\s*\d+|등\s*\d+)\s*(?:개소|개|곳|명|인|건)?\s*.*$",
        " ",
        text,
    )
    text = re.sub(r"\s*(일원|등)\s*$", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_place_query_parts(value: str) -> list[str]:
    return split_place_name_parts(value)


# 수동검토에서 확인된 원문 상호와 네이버 등록 상호의 차이를 검색에 반영합니다.
MANUAL_REVIEW_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "우적해운대점": ("우적 센텀점",),
    "장박사양곱창전문점": ("장박사양곱창 센텀점",),
    "마파람해물짐해물탕시청직영점": ("마파람해물찜해물탕 시청직영점",),
    "문화의물결까사부사노부산근현대역사관점": ("까사부사노 부산근현대역사관점",),
    "도너츠윤부산서구점": ("도너츠윤 부산점",),
    "명지일삼육고기": ("일삼육고기집 명지본점",),
    "첸차이나금곡점": ("첸차이나",),
    "백구당외": ("백구당",),
    "순사이쿠보": ("슌사이쿠보 화명",),
    "풀무원푸드한국공항공사부산지역본부": (
        "풀무원푸드앤컬처 한국공항공사김해공항카페",
    ),
    "메가엠지씨커피포레나점": ("메가MGC커피 포레나점",),
    "장군성숯불갈비": ("장군성",),
    "활스시": ("스시활",),
    "후스시청점": ("후스",),
    "맛이슈": ("맛이슈 부산북구점",),
    "글로벌양상국": ("양상국 해운대점",),
    "나폴레옹베이커리여의도아이에프씨몰점": ("나폴레옹제과점 IFC몰점",),
    "학산테라로사커피세종지점": ("테라로사 세종점",),
    "빅바이트컴퍼니잠바주스서울역점": ("잠바주스 서울역점",),
    "꿈앤카페지방자치회관점": ("꿈엔카페 지방자치회관점",),
    "농업회사법인동백1917": ("동백1917우리술양조장",),
    "더젬코리아": ("제이엠커피로스터스 부산 본점",),
    "언양불고기부산집본": ("언양불고기 부산집본가",),
    "뚜레쥬르부산센텀대로점": ("뚜레쥬르 부산센텀점",),
    "와카바서부산유통단지점": ("와카바 부산",),
    "푸드카페삐뽀랑냠냠": ("삐뽀랑냠냠 부산",),
    "정동각": ("정동각 여의도",),
    "천해로207호": ("cafe천해로207",),
    "꽃가람한식당": ("꽃가람 본점",),
    "더소울규니꾸": ("규니꾸 우마카세",),
    "영진인삼어탕국수": ("영진인삼추어탕",),
    "육관소양재역본점": ("육즙관리소 양재역점",),
    "해씨라래": ("해싸라래 커피로스터리",),
    "lateliermarron": ("아뜰리에마롱",),
    "우수미회센타별관": ("덕승식당 제주 연동",),
}


def _specific_place_aliases(value: str) -> list[str]:
    compact = normalize_text(value).replace(" ", "")
    raw_compact = re.sub(r"\s+", "", value or "")
    aliases: list[str] = list(MANUAL_REVIEW_SEARCH_ALIASES.get(compact, ()))
    if re.search(r"이비가짬뽕\(센텀2로\s*29\)", raw_compact):
        aliases.append("이비가짬뽕 부산 해운대 센텀점")
    if re.search(r"로터리집\(민락동소재\)", raw_compact):
        aliases.append("로타리집")
    if compact == "오발탄에프에스":
        aliases.append("오발탄")
    if compact == "연제제일식당":
        aliases.append("연제제일식당닭한마리")
    if compact == "식당3선덕척점":
        aliases.append("식당3선 덕천점")
    if compact in {"심길선총송양곱창", "심길선청송양곱창"}:
        aliases.append("청송양곱창")
    if compact in {"백만김밥도시락", "백만김밥부산본점"}:
        aliases.append("백만김밥 부산본점")
        aliases.append("백만김밥")
    if compact in {"303화덕온천", "303화덕동래온천장본점"}:
        aliases.append("303화덕 동래점")
    if compact in {"고더커피부산대학교병원점", "고더커피부산대학병원점"}:
        aliases.append("고더커피 부산대학병원점")
    if "엠즈씨드" in compact and "폴바셋" in compact:
        aliases.append("폴 바셋")
    if "세일기업" in compact and "수안커피컴퍼니" in compact:
        aliases.append("수안커피컴퍼니 커피하우스")
    # 운영사명과 실제 음식점명이 함께 적힌 반복 상호는 음식점명도 검색합니다.
    if compact == "푸드웨이목장원":
        aliases.append("목장원")
    if compact == "만수산돼지국밥ii":
        aliases.append("만수산돼지국밥")
    if "오현프로젝트강경불고기센텀시티점" in compact:
        aliases.append("강경불고기 센텀시티점")
        aliases.append("고담현불고기 해운대센텀시티 본점")
    if "커넥트현대부산점고디바베이커리" in compact:
        aliases.append("고디바베이커리 커넥트현대점")
    if compact == "서면린선다이닝":
        aliases.append("린선다이닝 서면본점")
    if "칠암사계" in compact:
        aliases.append("칠암사계")
    if "카페가온비" in compact or "cafe가온비" in compact:
        aliases.append("카페가온비")
        aliases.append("cafe가온비")
    if "벌교궁꼬막한정식" in compact:
        aliases.append("궁꼬막한정식")
    if "푸짐한마을실비식당" in compact:
        aliases.append("푸짐한실비식당")
        aliases.append("조방푸짐한마을실비식당")
    if "에이제신관" in compact:
        aliases.append("예이제 신관")
        aliases.append("예이제")
    if "폴바셋연산점" in compact:
        aliases.append("폴 바셋 부산 연산역점")
        aliases.append("폴 바셋")
    if compact == "제일옥곰탕":
        aliases.append("제일옥")
    if compact == "이재모피자광복점":
        aliases.append("이재모피자 광복")
    if compact == "보리에더베이101":
        aliases.append("보리에 베이커리 앤 델리")
        aliases.append("보리에")
    if compact == "솥솥해운대점":
        aliases.append("솔솥 해운대 해리단길점")
    if compact == "금수복국센텀벡스코점":
        aliases.append("금수복국 센텀점")
    if "씨네드쉐프" in compact:
        aliases.append("씨네드쉐프")
    if compact == "베지나랑광안점":
        aliases.append("베지나랑 광안리점")
        aliases.append("베지나랑")
    if compact == "명지첫집오션시티점":
        aliases.append("명지첫집 오션시티직영점")
        aliases.append("명지첫집")
    if compact == "모모스커피온천장점":
        aliases.append("모모스커피 본점")
        aliases.append("모모스커피")
    if compact == "가시리":
        aliases.append("가시리 식당")
    if compact == "명품용수떡집":
        aliases.append("용수떡집")
    return aliases


def _place_query_variants(value: str) -> list[str]:
    raw = (value or "").strip()
    cleaned = _clean_place_query(raw)
    variants: list[str] = []
    raw_parts = _split_place_query_parts(raw)

    # 다중 상호는 합쳐진 전체 문자열이 아니라 각 상호를 별도 검색합니다.
    if len(raw_parts) >= 2:
        for part in sorted(
            raw_parts,
            key=lambda item: category_from_text(_clean_place_query(item)) not in FOOD_CATEGORIES,
        ):
            cleaned_part = _clean_place_query(part)
            spaced_part = franchise_search_name(part)
            variants.extend(_specific_place_aliases(part))
            variants.extend(value for value in [spaced_part, cleaned_part, part] if value)
        seen: set[str] = set()
        deduped: list[str] = []
        for variant in variants:
            key = normalize_text(variant)
            if key and key not in seen:
                seen.add(key)
                deduped.append(variant)
        return deduped

    parsed = parse_place_name(raw)
    spaced_franchise = franchise_search_name(raw)
    if spaced_franchise:
        variants.append(spaced_franchise)
    outside_parentheses = re.sub(r"\([^)]*\)", "", raw)
    if normalize_text(outside_parentheses).replace(" ", "").isdigit():
        variants.extend(
            re.sub(r"\s+", " ", hint).strip()
            for hint in re.findall(r"\(([^)]{1,80})\)", raw)
            if re.sub(r"\s+", " ", hint).strip()
        )
    if parsed.is_generic:
        variants.extend(
            re.sub(r"\s+", " ", hint).strip()
            for hint in re.findall(r"\(([^)]{1,80})\)", raw)
            if re.sub(r"\s+", " ", hint).strip()
        )
        identity = generic_parenthetical_place_name(raw)
        if identity:
            variants.insert(0, identity)
    if parsed.branch_name and parsed.base_name:
        variants.append(parsed.combined_name)
        if parsed.branch_name == "명륜점":
            variants.append(f"{parsed.base_name} 동래명륜점")
    variants.extend(_specific_place_aliases(raw))
    variants.extend(_specific_place_aliases(cleaned))
    split_parts: list[str] = []
    for source in [raw, cleaned]:
        for part in _split_place_query_parts(source):
            cleaned_part = _clean_place_query(part)
            compact_part = normalize_text(cleaned_part).replace(" ", "")
            if (
                cleaned_part
                and len(compact_part) >= 2
                and compact_part not in {"오프라인", "온라인"}
            ):
                split_parts.append(cleaned_part)
    split_parts.sort(
        key=lambda part: (
            0 if category_from_text(part) in {"restaurant", "cafe", "bar"} else 1,
            len(part),
        )
    )
    variants.extend(split_parts)
    has_companion_suffix = bool(
        re.search(r"(?:외|등)\s*\d+\s*(?:개소|개|곳|명|인|건)?\s*$", raw)
    )
    variants.extend(value for value in [cleaned, *([] if has_companion_suffix else [raw])] if value)
    seen: set[str] = set()
    deduped: list[str] = []
    for variant in variants:
        key = normalize_text(variant)
        if key and key not in seen:
            seen.add(key)
            deduped.append(variant)
    return deduped


def _search_queries(row: NormalizedExpenseRow) -> list[str]:
    raw_place = (row.place_name or "").strip()
    place_values = _place_query_variants(raw_place)
    address_values: list[str] = []
    if row.address:
        cleaned_address = strip_address_detail(row.address)
        address_values.extend(value for value in [cleaned_address, row.address] if value)
    queries: list[str] = []
    for place in place_values:
        if address_values:
            queries.extend(f"{place} {address}" for address in address_values)
            queries.append(place)
            if not any("부산" in normalize_address(address) for address in address_values):
                queries.append(f"{place} 부산")
        else:
            queries.append(place)
            queries.append(f"{place} 부산")
    queries.extend(address_values)
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        key = normalize_text(query)
        if key and key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped


def _place_rank(row: NormalizedExpenseRow, place: PlaceCandidate) -> tuple[float, float, int, int]:
    row_address = normalize_address(strip_address_detail(row.address or ""))
    place_address = normalize_address(place.road_address or place.address)
    address_score = similarity(row_address, place_address) if row_address else 0.0
    name_score = name_similarity_with_branch(row.place_name, place.name)
    food_category = 1 if place.category in FOOD_CATEGORIES else 0
    busan_tiebreaker = 1 if "부산" in (place.road_address or place.address) else 0
    return (address_score, name_score, food_category, busan_tiebreaker)


@dataclass(frozen=True)
class NaverSearchLocalClient:
    client_id: str
    client_secret: str
    endpoint: str = "https://openapi.naver.com/v1/search/local.json"

    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        if not self.client_id or not self.client_secret:
            raise IntegrationError("Naver Search credentials are missing")
        seen: set[str] = set()
        places: list[PlaceCandidate] = []
        for query in _search_queries(row):
            params = urlencode({"query": query, "display": 10, "start": 1, "sort": "random"})
            payload = _get_json_with_retry(
                f"{self.endpoint}?{params}",
                headers={
                    "X-Naver-Client-Id": self.client_id,
                    "X-Naver-Client-Secret": self.client_secret,
                    "Accept": "application/json",
                },
            )
            for item in payload.get("items", []):
                title = _strip_html(item.get("title", ""))
                address = item.get("address", "") or item.get("roadAddress", "")
                road_address = item.get("roadAddress", "") or address
                longitude = _naver_map_coord(item.get("mapx"))
                latitude = _naver_map_coord(item.get("mapy"))
                if not title or longitude is None or latitude is None:
                    continue
                provider_place_id = f"naver-search:{normalize_text(title)}:{normalize_address(road_address)}"
                if provider_place_id in seen:
                    continue
                seen.add(provider_place_id)
                places.append(
                    PlaceCandidate(
                        provider_place_id=provider_place_id,
                        name=title,
                        category=_category_from_naver(item.get("category", "")),
                        address=address,
                        road_address=road_address,
                        longitude=longitude,
                        latitude=latitude,
                        provider_category_raw=str(item.get("category", "")),
                    )
                )
        places.sort(key=lambda place: _place_rank(row, place), reverse=True)
        return places[:10]


def _safe_http_url(value: Any) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return candidate


@dataclass(frozen=True)
class NaverImageSearchClient:
    client_id: str
    client_secret: str
    api_hub: bool = False

    @property
    def endpoint(self) -> str:
        if self.api_hub:
            return "https://naverapihub.apigw.ntruss.com/search/v1/image"
        return "https://openapi.naver.com/v1/search/image.json"

    @property
    def headers(self) -> dict[str, str]:
        if self.api_hub:
            return {
                "X-NCP-APIGW-API-KEY-ID": self.client_id,
                "X-NCP-APIGW-API-KEY": self.client_secret,
                "Accept": "application/json",
            }
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
            "Accept": "application/json",
        }

    def search_restaurant_images(
        self,
        name: str,
        address: str = "",
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        if not self.client_id or not self.client_secret:
            raise IntegrationError("Naver Image Search credentials are missing")
        restaurant_name = str(name or "").strip()
        normalized_name = normalize_text(restaurant_name).replace(" ", "")
        if not normalized_name:
            return []
        image_limit = max(1, min(int(limit), 10))
        clean_address = strip_address_detail(address or "")
        query = " ".join(value for value in [restaurant_name, clean_address, "음식점"] if value)
        params = urlencode(
            {
                "query": query,
                "display": max(10, image_limit * 5),
                "start": 1,
                "sort": "sim",
            }
        )
        payload = _get_json(
            f"{self.endpoint}?{params}",
            headers=self.headers,
            timeout=4,
        )
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        seen_urls: set[str] = set()
        for result_index, item in enumerate(payload.get("items", [])):
            title = _strip_html(item.get("title", ""))
            normalized_title = normalize_text(title).replace(" ", "")
            if normalized_name not in normalized_title:
                continue
            thumbnail_url = _safe_http_url(item.get("thumbnail"))
            source_url = _safe_http_url(item.get("link"))
            if not thumbnail_url and not source_url:
                continue
            deduplication_url = source_url or thumbnail_url
            if deduplication_url in seen_urls:
                continue
            seen_urls.add(deduplication_url)
            source_host = (urlparse(source_url).hostname or "").lower()
            is_naver_place_image = source_host == "ldb-phinf.pstatic.net"
            candidates.append(
                (
                    0 if is_naver_place_image else 1,
                    result_index,
                    {
                        "thumbnail_url": thumbnail_url or source_url,
                        "source_url": source_url or thumbnail_url,
                        "title": title or restaurant_name,
                        "provider": "naver_image_search",
                        "is_naver_place_image": is_naver_place_image,
                    },
                )
            )
        candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
        return [candidate[2] for candidate in candidates[:image_limit]]

    def search_restaurant_image(self, name: str, address: str = "") -> dict[str, Any] | None:
        images = self.search_restaurant_images(name, address, limit=1)
        return images[0] if images else None


@dataclass(frozen=True)
class NaverMapsGeocodingClient:
    client_id: str
    client_secret: str
    endpoint: str = "https://naveropenapi.apigw.ntruss.com/map-geocode/v2/geocode"

    def geocode(self, address: str) -> dict[str, Any] | None:
        if not self.client_id or not self.client_secret:
            raise IntegrationError("Naver Maps credentials are missing")
        params = urlencode({"query": address, "count": 1})
        payload = _get_json(
            f"{self.endpoint}?{params}",
            headers={
                "X-NCP-APIGW-API-KEY-ID": self.client_id,
                "X-NCP-APIGW-API-KEY": self.client_secret,
                "Accept": "application/json",
            },
        )
        addresses = payload.get("addresses") or []
        return addresses[0] if addresses else None


@dataclass(frozen=True)
class DataGoKrPermitClient:
    service_key: str
    endpoints: tuple[tuple[str, str], ...] = (
        ("restaurant", "https://apis.data.go.kr/1741000/general_restaurants/info"),
        ("cafe", "https://apis.data.go.kr/1741000/rest_cafes/info"),
        ("cafe", "https://apis.data.go.kr/1741000/bakeries/info"),
    )

    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        if not self.service_key:
            return None
        best: tuple[float, PermitSnapshot] | None = None
        for category, endpoint in self.endpoints:
            for item in self._query_endpoint(endpoint, row):
                snapshot = self._snapshot_from_item(category, item)
                name_score = similarity(row.normalized_place_name, snapshot.raw_response_json.get("BPLC_NM", ""))
                road_score = similarity(row.normalized_address, snapshot.address)
                lot_score = similarity(row.normalized_address, snapshot.raw_response_json.get("LOTNO_ADDR", ""))
                score = name_score * 0.72 + max(road_score, lot_score) * 0.28
                if row.address and "부산" in normalize_address(row.address):
                    if "부산" not in normalize_address(snapshot.address):
                        score -= 0.2
                if best is None or score > best[0]:
                    best = (score, snapshot)
        if best and best[0] >= 0.42:
            return best[1]
        return None

    def _query_endpoint(self, endpoint: str, row: NormalizedExpenseRow) -> list[dict[str, Any]]:
        query = row.place_name.strip()
        if not query:
            return []
        params = {
            "serviceKey": self.service_key,
            "pageNo": "1",
            "numOfRows": "20",
            "returnType": "json",
            "cond[BPLC_NM::LIKE]": query,
        }
        if "부산" in normalize_address(row.address):
            params["cond[ROAD_NM_ADDR::LIKE]"] = "부산"
        try:
            payload = _get_json(f"{endpoint}?{urlencode(params)}", headers={"Accept": "application/json"})
        except IntegrationError:
            return []
        response = payload.get("response") or {}
        header = response.get("header") or {}
        result_code = str(header.get("resultCode", "0"))
        if result_code not in {"0", "00"}:
            return []
        body = response.get("body") or {}
        items = ((body.get("items") or {}).get("item")) or []
        if isinstance(items, dict):
            return [items]
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []

    def _snapshot_from_item(self, category: str, item: dict[str, Any]) -> PermitSnapshot:
        status_name = f"{item.get('SALS_STTS_NM', '')} {item.get('DTL_SALS_STTS_NM', '')}"
        business_status = "active" if self._is_active_status(status_name, item.get("CLSBIZ_YMD", "")) else "closed"
        address = item.get("ROAD_NM_ADDR") or item.get("LOTNO_ADDR") or ""
        return PermitSnapshot(
            permit_id=item.get("MNG_NO") or item.get("BPLC_NM"),
            category=category,
            business_status=business_status,
            address=address,
            longitude=None,
            latitude=None,
            raw_response_json=item,
        )

    def _is_active_status(self, status_name: str, closed_date: str | None) -> bool:
        text = normalize_text(status_name)
        if closed_date:
            return False
        if any(token in text for token in ["폐업", "취소", "말소", "직권", "휴업"]):
            return False
        return any(token in text for token in ["영업", "정상"])


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str
    display_name: str


@dataclass(frozen=True)
class OAuthLoginResult:
    profile: OAuthProfile
    access_token: str
    refresh_token: str = ""
    expires_in: int = 0


@dataclass(frozen=True)
class NaverLoginClient:
    client_id: str
    client_secret: str
    redirect_uri: str

    def exchange_code(self, code: str, state: str) -> OAuthLoginResult:
        payload = _post_form_json(
            "https://nid.naver.com/oauth2.0/token",
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "state": state,
            },
        )
        access_token = payload.get("access_token")
        if not access_token:
            raise IntegrationError("Naver Login did not return an access token")
        profile = _get_json(
            "https://openapi.naver.com/v1/nid/me",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        response = profile.get("response") or {}
        subject = response.get("id")
        if not subject:
            raise IntegrationError("Naver profile did not return a subject")
        return OAuthLoginResult(
            profile=OAuthProfile(
                provider="naver",
                subject=subject,
                display_name=response.get("nickname") or response.get("name") or "Naver 사용자",
            ),
            access_token=str(access_token),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=int(payload.get("expires_in") or 0),
        )

    def revoke_token(self, token: str, token_type_hint: str = "refresh_token") -> None:
        if not token:
            raise IntegrationError("Naver token revocation requires a token")
        status = _post_form_status(
            "https://nid.naver.com/oauth2.0/revoke",
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "token": token,
                "token_type_hint": token_type_hint,
            },
        )
        if status != 200:
            raise IntegrationError(
                "Naver token revocation failed",
                status_code=status,
                retryable=status == 503,
            )


@dataclass(frozen=True)
class GoogleOAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str

    def exchange_code(self, code: str) -> OAuthLoginResult:
        payload = _post_form_json(
            "https://oauth2.googleapis.com/token",
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
        )
        access_token = payload.get("access_token")
        if not access_token:
            raise IntegrationError("Google OAuth did not return an access token")
        profile = _get_json(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        subject = profile.get("sub")
        if not subject:
            raise IntegrationError("Google userinfo did not return a subject")
        return OAuthLoginResult(
            profile=OAuthProfile(
                provider="google",
                subject=subject,
                display_name=profile.get("name") or "Google 사용자",
            ),
            access_token=str(access_token),
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=int(payload.get("expires_in") or 0),
        )


@dataclass(frozen=True)
class GroqReviewSummaryClient:
    api_key: str
    model: str = "llama-3.3-70b-versatile"
    endpoint: str = "https://api.groq.com/openai/v1/chat/completions"
    provider: str = "groq"

    def summarize(self, restaurant_name: str, reviews: list[dict[str, Any]]) -> str:
        if not self.api_key:
            raise IntegrationError("Groq API key is missing")
        review_lines = [
            f"- 별점 {int(review.get('rating') or 0)}점: {str(review.get('body') or '').strip()}"
            for review in reviews
            if str(review.get("body") or "").strip()
        ]
        payload = _post_json(
            self.endpoint,
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "당신은 음식점의 공개 방문 리뷰를 요약하는 도우미입니다. "
                            "리뷰에 명시된 사실만 사용하고 메뉴, 가격, 서비스 등을 추측하지 마세요. "
                            "반복되는 장점과 주의점을 균형 있게 담아 한국어 2~3문장, 220자 이내로 작성하세요. "
                            "리뷰 근거가 부족하면 부족하다고 명확히 말하세요."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"가게명: {restaurant_name}\n"
                            f"공개 리뷰 수: {len(review_lines)}\n"
                            "공개 리뷰:\n"
                            + "\n".join(review_lines)
                        ),
                    },
                ],
                "temperature": 0.2,
                "max_completion_tokens": 220,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        choices = payload.get("choices") or []
        if not choices:
            raise IntegrationError("Groq response did not contain a summary")
        content = str((choices[0].get("message") or {}).get("content") or "").strip()
        if not content:
            raise IntegrationError("Groq response summary was empty")
        return content[:600]


class GeocodingNaverClient:
    """Naver local search client with optional Geocoding address refinement."""

    def __init__(self, search_client: NaverSearchLocalClient, geocoding_client: NaverMapsGeocodingClient | None):
        self.search_client = search_client
        self.geocoding_client = geocoding_client

    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        places = self.search_client.search_local(row)
        if not self.geocoding_client:
            return places
        refined: list[PlaceCandidate] = []
        for place in places:
            try:
                geocoded = self.geocoding_client.geocode(place.road_address or place.address)
            except IntegrationError:
                geocoded = None
            if not geocoded:
                refined.append(place)
                continue
            x = geocoded.get("x")
            y = geocoded.get("y")
            refined.append(
                PlaceCandidate(
                    provider_place_id=place.provider_place_id,
                    name=place.name,
                    category=place.category,
                    address=place.address,
                    road_address=geocoded.get("roadAddress") or place.road_address,
                    longitude=float(x) if x else place.longitude,
                    latitude=float(y) if y else place.latitude,
                    provider_category_raw=place.provider_category_raw,
                )
            )
        return refined
