from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .agents import NormalizedExpenseRow, PermitSnapshot, PlaceCandidate, category_from_text, similarity
from .utils import normalize_address, normalize_text, strip_address_detail


class IntegrationError(RuntimeError):
    pass


def _get_json(url: str, headers: dict[str, str], timeout: int = 8) -> dict[str, Any]:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise IntegrationError(f"HTTP {exc.code}: {body[:300]}") from exc
    except URLError as exc:
        raise IntegrationError(str(exc)) from exc


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
    if any(token in text for token in ["카페", "커피", "디저트", "베이커리", "제과"]):
        return "cafe"
    if any(token in text for token in ["주점", "호프", "맥주", "포차", "술집", "바"]):
        return "bar"
    if any(token in text for token in ["음식점", "한식", "중식", "일식", "분식", "고기", "요리"]):
        return "restaurant"
    return category_from_text(value)


def _clean_place_query(value: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", value or "")
    text = re.sub(r"(주식회사|유한회사|㈜|\(주\))", " ", text)
    text = re.sub(r"\s*외\s*\d+\s*(?:개소|개|곳)?\s*.*$", " ", text)
    text = re.sub(r"\s*(일원|등)\s*$", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_place_query_parts(value: str) -> list[str]:
    parts = re.split(r"[,/_·]+", value or "")
    return [part.strip() for part in parts if part.strip()]


def _specific_place_aliases(value: str) -> list[str]:
    compact = normalize_text(value).replace(" ", "")
    aliases: list[str] = []
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
    return aliases


def _place_query_variants(value: str) -> list[str]:
    raw = (value or "").strip()
    cleaned = _clean_place_query(raw)
    variants: list[str] = []
    parenthetical_hints = [hint.strip() for hint in re.findall(r"\(([^)]*점)\)", raw) if hint.strip()]
    if parenthetical_hints and cleaned:
        for hint in parenthetical_hints:
            variants.append(f"{cleaned} {hint}")
            if hint == "명륜점":
                variants.append(f"{cleaned} 동래명륜점")
    variants.extend(_specific_place_aliases(raw))
    variants.extend(_specific_place_aliases(cleaned))
    split_parts: list[str] = []
    for source in [raw, cleaned]:
        for part in _split_place_query_parts(source):
            cleaned_part = _clean_place_query(part)
            if cleaned_part:
                split_parts.append(cleaned_part)
    split_parts.sort(
        key=lambda part: (
            0 if category_from_text(part) in {"restaurant", "cafe", "bar"} else 1,
            len(part),
        )
    )
    variants.extend(split_parts)
    variants.extend(value for value in [cleaned, raw] if value)
    if cleaned:
        words = cleaned.split()
        if len(words) >= 2:
            variants.append(words[-1])
            variants.append(" ".join(words[-2:]))
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
            queries.append(f"{place} 부산")
        else:
            queries.append(f"{place} 부산")
        queries.append(place)
    queries.extend(address_values)
    seen: set[str] = set()
    deduped: list[str] = []
    for query in queries:
        key = normalize_text(query)
        if key and key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped


def _place_rank(row: NormalizedExpenseRow, place: PlaceCandidate) -> tuple[float, int, float]:
    row_address = normalize_address(strip_address_detail(row.address or ""))
    place_address = normalize_address(place.road_address or place.address)
    address_score = similarity(row_address, place_address) if row_address else 0.0
    busan_bonus = 1 if "부산" in (place.road_address or place.address) else 0
    name_score = similarity(normalize_text(row.place_name), normalize_text(place.name))
    return (address_score, busan_bonus, name_score)


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
            payload = _get_json(
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
                    )
                )
        places.sort(key=lambda place: _place_rank(row, place), reverse=True)
        return places[:10]


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
class NaverLoginClient:
    client_id: str
    client_secret: str
    redirect_uri: str

    def exchange_code(self, code: str, state: str) -> OAuthProfile:
        payload = _get_json(
            "https://nid.naver.com/oauth2.0/token?"
            + urlencode(
                {
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "state": state,
                }
            ),
            headers={"Accept": "application/json"},
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
        return OAuthProfile(
            provider="naver",
            subject=subject,
            display_name=response.get("nickname") or response.get("name") or "Naver 사용자",
        )


@dataclass(frozen=True)
class GoogleOAuthClient:
    client_id: str
    client_secret: str
    redirect_uri: str

    def exchange_code(self, code: str) -> OAuthProfile:
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
        return OAuthProfile(
            provider="google",
            subject=subject,
            display_name=profile.get("name") or "Google 사용자",
        )


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
                )
            )
        return refined
