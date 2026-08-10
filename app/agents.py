from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Callable, Literal, Protocol

from .utils import normalize_address, normalize_text, normalized_address_similarity, structured_address_match

try:  # rapidfuzz is optional; keep the app runnable without extra installs.
    from rapidfuzz import fuzz as rapidfuzz_fuzz
except ImportError:  # pragma: no cover - depends on local environment
    rapidfuzz_fuzz = None


Decision = Literal["approved", "rejected", "needs_review"]
ApprovedBy = Literal["rule", "ai", "manual", "none"]
FOOD_CATEGORIES = {"restaurant", "cafe", "bar"}
ADDRESSLESS_FUZZY_NAME_THRESHOLD = 0.70
FRANCHISE_BRANDS: dict[str, dict[str, object]] = {
    "starbucks": {
        "category": "cafe",
        "aliases": ("스타벅스", "스타벅스커피", "스타벅스코리아", "starbucks"),
    },
    "vips": {
        "category": "restaurant",
        "aliases": ("빕스", "vips"),
    },
    "twosome": {
        "category": "cafe",
        "aliases": ("투썸", "투썸플레이스"),
    },
    "ediya": {
        "category": "cafe",
        "aliases": ("이디야", "이디야커피"),
    },
    "paikdabang": {
        "category": "cafe",
        "aliases": ("빽다방",),
    },
    "mega": {
        "category": "cafe",
        "aliases": ("메가커피", "메가mgc커피", "mega"),
    },
    "compose": {
        "category": "cafe",
        "aliases": ("컴포즈커피", "컴포즈"),
    },
    "mcdonalds": {
        "category": "restaurant",
        "aliases": ("맥도날드", "mcdonald", "mcdonalds"),
    },
    "lotteria": {
        "category": "restaurant",
        "aliases": ("롯데리아",),
    },
    "subway": {
        "category": "restaurant",
        "aliases": ("써브웨이", "서브웨이", "subway"),
    },
    "paris_baguette": {
        "category": "cafe",
        "aliases": ("파리바게트", "파리바게뜨"),
    },
    "bon_dosirak": {
        "category": "restaurant",
        "aliases": ("본도시락",),
    },
}
FRANCHISE_NON_BRANCH_TOKENS = {
    "코리아",
    "korea",
    "본사",
    "본부",
    "주식회사",
    "유한회사",
    "법인",
}
NON_FOOD_PURPOSE_RULES = (
    ("기념품", "NON_FOOD_PURPOSE_GIFT"),
    ("사무용품", "NON_FOOD_PURPOSE_OFFICE_SUPPLY"),
    ("문구", "NON_FOOD_PURPOSE_OFFICE_SUPPLY"),
    ("비품", "NON_FOOD_PURPOSE_SUPPLY"),
    ("소모품", "NON_FOOD_PURPOSE_SUPPLY"),
    ("홍보물품", "NON_FOOD_PURPOSE_PROMOTIONAL_ITEM"),
    ("격려물품", "NON_FOOD_PURPOSE_SUPPORT_ITEM"),
    ("필요물품", "NON_FOOD_PURPOSE_SUPPLY"),
    ("특산품", "NON_FOOD_PURPOSE_PRODUCT_PURCHASE"),
)
HARD_REJECT_PLACE_RULES = (
    ("구입", "PURCHASE_WORD_IN_PLACE_NAME"),
    ("쿠팡", "COUPANG_PLACE_NAME"),
    ("매점", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
    ("편의점", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
    ("슈퍼마켓", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
)
HARD_REJECT_PURPOSE_RULES = (
    ("경조사", "CEREMONIAL_EVENT_EXPENSE"),
)
FOOD_PURCHASE_PURPOSE_TOKENS = (
    "간담",
    "오찬",
    "만찬",
    "식사",
    "다과",
    "간식",
    "음료",
    "커피",
    "도시락",
    "식품",
    "급식",
)
MANUAL_FEEDBACK_REJECT_PATTERNS = (
    ("선과홍티하우스", "MANUAL_FEEDBACK_CLOSED_OR_NOT_OPERATING"),
    ("포도나무면가", "MANUAL_FEEDBACK_CLOSED_OR_NOT_OPERATING"),
    ("연제지역자활센터", "MANUAL_FEEDBACK_NON_RESTAURANT_ORGANIZATION"),
    ("연제지역자할센터", "MANUAL_FEEDBACK_NON_RESTAURANT_ORGANIZATION"),
)
PAYMENT_PROCESSOR_PLACE_TOKENS = (
    "부산동백전",
    "kicc",
    "코페이",
    "코웨이",
)
UNKNOWN_PLACE_TOKENS = ("미상", "불명", "없음", "무기재")
BRANCH_HINTS = (
    "부산시청점",
    "시청점",
    "광안점",
    "해운대점",
    "센텀점",
    "다대포점",
    "서면점",
    "연산점",
    "대연점",
    "남포점",
    "동래점",
    "온천점",
    "사직점",
    "기장점",
    "영도점",
    "하단점",
    "본점",
    "직영점",
)
BRANCH_SUFFIXES = ("본점", "직영점", "점")
NON_BRANCH_HINTS = {
    "일반음식점",
    "휴게음식점",
    "음식점",
    "전문점",
    "판매점",
    "백화점",
}
BRAND_ALIAS_REPLACEMENTS = (
    ("스타벅스커피", "스타벅스"),
    ("이디야커피", "이디야"),
    ("투썸플레이스", "투썸"),
    ("파리바게뜨", "파리바게트"),
)
GENERIC_PLACE_PREFIXES = ("카페", "cafe", "커피")


@dataclass(frozen=True)
class PlaceNameParts:
    original: str
    base_name: str
    branch_name: str
    combined_name: str
    is_generic: bool


@dataclass(frozen=True)
class PlaceCandidate:
    provider_place_id: str
    name: str
    category: str
    address: str
    road_address: str
    longitude: float
    latitude: float
    provider_category_raw: str = ""


@dataclass(frozen=True)
class PermitSnapshot:
    permit_id: str | None
    category: str
    business_status: str
    address: str
    longitude: float | None = None
    latitude: float | None = None
    raw_response_json: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedExpenseRow:
    row_number: int
    department_name: str
    used_date: str
    place_name: str
    address: str
    purpose: str
    amount: int
    normalized_place_name: str
    normalized_address: str


@dataclass(frozen=True)
class VerificationDecision:
    decision: Decision
    confidence: float
    approved_by: ApprovedBy
    selected_candidate: PlaceCandidate | None
    category: str
    reason_codes: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


class NaverClient(Protocol):
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        ...


class PermitClient(Protocol):
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        ...


class AiReviewer(Protocol):
    def decide(
        self,
        row: NormalizedExpenseRow,
        candidates: list[PlaceCandidate],
        permit: PermitSnapshot | None,
        rule_score: float,
    ) -> VerificationDecision:
        ...


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_normalized = normalize_text(left)
    right_normalized = normalize_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    if rapidfuzz_fuzz is not None:
        return rapidfuzz_fuzz.ratio(left_normalized, right_normalized) / 100
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def parse_place_name(value: str) -> PlaceNameParts:
    original = str(value or "").strip()
    parenthetical = [
        re.sub(r"\s+", " ", hint).strip()
        for hint in re.findall(r"\(([^)]{1,40})\)", original)
    ]
    branch_name = next(
        (
            hint
            for hint in parenthetical
            if re.search(r"(?:본점|직영점|\d+호점|점)$", hint, flags=re.IGNORECASE)
            and normalize_text(hint) not in NON_BRANCH_HINTS
        ),
        "",
    )
    without_parenthetical = re.sub(r"\([^)]*\)", " ", original)
    without_company = re.sub(r"(주식회사|유한회사|㈜|\(주\))", " ", without_parenthetical)
    without_companion = re.sub(r"\s*외\s*\d+\s*(?:개소|개|곳)?\s*.*$", " ", without_company)
    without_companion = re.sub(r"\s*(?:일원|등)\s*$", " ", without_companion)
    base_name = re.sub(r"\s+", " ", without_companion).strip(" ,/_·-")
    combined_name = " ".join(part for part in [base_name, branch_name] if part).strip()
    generic_key = normalize_text(base_name).replace(" ", "")
    generic_names = {
        normalize_text(name).replace(" ", "")
        for name in (*NON_BRANCH_HINTS, "카페", "커피숍", "주점", "식당")
    }
    return PlaceNameParts(
        original=original,
        base_name=base_name,
        branch_name=branch_name,
        combined_name=combined_name,
        is_generic=bool(generic_key and generic_key in generic_names),
    )


def generic_parenthetical_place_name(value: str) -> str:
    """Extract a usable merchant name from labels such as ``음식점 (이레옥)``.

    A parenthetical value that only looks like a branch (for example
    ``광화문점``) is intentionally not treated as a complete merchant name.
    It is still searched, but it cannot by itself unlock auto approval.
    """
    parsed = parse_place_name(value)
    if not parsed.is_generic:
        return ""
    generic_names = {
        normalize_text(name).replace(" ", "")
        for name in (*NON_BRANCH_HINTS, "카페", "커피숍", "주점", "식당")
    }
    for hint in re.findall(r"\(([^)]{1,80})\)", value or ""):
        cleaned = re.sub(r"\s+", " ", hint).strip(" ,/_·-")
        compact = normalize_text(cleaned).replace(" ", "")
        if len(compact) < 3 or compact in generic_names:
            continue
        branch_only = bool(re.search(r"(?:본점|직영점|\d+호점|점)$", cleaned, flags=re.IGNORECASE))
        if branch_only and " " not in cleaned and len(compact) < 9 and not franchise_brand(cleaned):
            continue
        return cleaned
    return ""


def clean_place_name_for_matching(value: str) -> str:
    parsed = parse_place_name(value)
    text = generic_parenthetical_place_name(value) or parsed.combined_name or normalize_text(value)
    text = re.sub(r"\s*외\s*\d+.*$", " ", text)
    text = re.sub(r"\b(일원|등)$", " ", text)
    return normalize_text(re.sub(r"\s+", " ", text).strip())


def brand_alias_variants(value: str) -> list[str]:
    variants = [normalize_text(value)]
    for source, target in BRAND_ALIAS_REPLACEMENTS:
        current = list(variants)
        for variant in current:
            compact = variant.replace(" ", "")
            if source in compact:
                variants.append(compact.replace(source, target))
            if target in compact:
                variants.append(compact.replace(target, source))
    seen: set[str] = set()
    result: list[str] = []
    for variant in variants:
        normalized = normalize_text(variant)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def generic_place_alias_variants(value: str) -> list[str]:
    compact = compact_place_name(value)
    variants: list[str] = []
    for prefix in GENERIC_PLACE_PREFIXES:
        normalized_prefix = compact_place_name(prefix)
        if compact.startswith(normalized_prefix) and len(compact) > len(normalized_prefix) + 1:
            variants.append(compact[len(normalized_prefix):])
    return variants


def category_from_text(value: str) -> str:
    text = normalize_text(value)
    brand = franchise_brand(value)
    if brand:
        category = franchise_category(brand)
        if category in FOOD_CATEGORIES:
            return category
    if any(token in text for token in ["카페", "커피", "디저트", "베이커리", "제과", "스타벅스"]):
        return "cafe"
    if any(token in text for token in ["포차", "주점", "호프", "맥주", "바"]):
        return "bar"
    if any(
        token in text
        for token in [
            "식당",
            "국밥",
            "횟집",
            "고기",
            "음식점",
            "분식",
            "초밥",
            "국수",
            "만두",
            "삼계탕",
            "복국",
            "곰탕",
            "갈비",
            "버거",
        ]
    ):
        return "restaurant"
    return "other"


def non_food_purpose_reason(value: str) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    compact = text.replace(" ", "")
    for token, reason in NON_FOOD_PURPOSE_RULES:
        if token in compact:
            if token in {"비품", "소모품", "필요물품"} and any(food in compact for food in FOOD_PURCHASE_PURPOSE_TOKENS):
                continue
            return reason
    return None


def is_food_context_purpose(value: str) -> bool:
    compact = normalize_text(value).replace(" ", "")
    return any(token in compact for token in FOOD_PURCHASE_PURPOSE_TOKENS)


def non_food_purpose_decision(row: NormalizedExpenseRow) -> VerificationDecision | None:
    reason = non_food_purpose_reason(row.purpose)
    if reason is None:
        return None
    return VerificationDecision(
        decision="rejected",
        confidence=0.99,
        approved_by="rule",
        selected_candidate=None,
        category="other",
        reason_codes=[reason],
        evidence={"purpose": row.purpose},
    )


def expense_scope_reject_decision(row: NormalizedExpenseRow) -> VerificationDecision | None:
    raw_name = row.place_name or row.normalized_place_name
    compact_name = normalize_text(raw_name).replace(" ", "")
    compact_purpose = normalize_text(row.purpose).replace(" ", "")
    place_parts = [
        normalize_text(part)
        for part in re.split(r"[,;]+", row.place_name or "")
        if normalize_text(part)
    ]
    if len(place_parts) >= 2 and len({part.replace(" ", "") for part in place_parts}) >= 2:
        return VerificationDecision(
            decision="rejected",
            confidence=0.99,
            approved_by="rule",
            selected_candidate=None,
            category="other",
            reason_codes=["MULTIPLE_PLACE_NAMES_UNSUPPORTED"],
            evidence={"place_count": len(place_parts), "place_name": row.place_name},
        )
    for token, reason in HARD_REJECT_PLACE_RULES:
        if token in compact_name:
            return VerificationDecision(
                decision="rejected",
                confidence=0.99,
                approved_by="rule",
                selected_candidate=None,
                category="other",
                reason_codes=[reason],
                evidence={"place_name": row.place_name},
            )
    for token, reason in HARD_REJECT_PURPOSE_RULES:
        if token in compact_purpose:
            return VerificationDecision(
                decision="rejected",
                confidence=0.99,
                approved_by="rule",
                selected_candidate=None,
                category="other",
                reason_codes=[reason],
                evidence={"purpose": row.purpose},
            )
    return None


def manual_feedback_reject_decision(row: NormalizedExpenseRow) -> VerificationDecision | None:
    compact_name = compact_place_name(row.place_name or row.normalized_place_name)
    if not compact_name:
        return None
    for pattern, reason in MANUAL_FEEDBACK_REJECT_PATTERNS:
        if compact_place_name(pattern) in compact_name:
            return VerificationDecision(
                decision="rejected",
                confidence=0.98,
                approved_by="rule",
                selected_candidate=None,
                category=category_from_text(row.place_name),
                reason_codes=[reason],
                evidence={"place_name": row.place_name},
            )
    if any(compact_place_name(token) in compact_name for token in PAYMENT_PROCESSOR_PLACE_TOKENS):
        return VerificationDecision(
            decision="rejected",
            confidence=0.99,
            approved_by="rule",
            selected_candidate=None,
            category="other",
            reason_codes=["PAYMENT_PROCESSOR_OR_CARD_PLACE_NAME"],
            evidence={"place_name": row.place_name},
        )
    return None


def is_distinctive_place_name(value: str) -> bool:
    compact = normalize_text(value).replace(" ", "")
    if any(token in compact for token in ["미상", "불명", "없음", "무기재"]):
        return False
    return len(compact) >= 4 and not compact.isdigit()


def is_verifiable_place_name(value: str) -> bool:
    compact = normalize_text(value).replace(" ", "")
    if any(token in compact for token in ["미상", "불명", "없음", "무기재"]):
        return False
    return len(compact) >= 2 and not compact.isdigit()


def compact_place_name(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def franchise_brand(value: str) -> str | None:
    compact = compact_place_name(value)
    if not compact:
        return None
    for brand, config in FRANCHISE_BRANDS.items():
        aliases = config["aliases"]
        assert isinstance(aliases, tuple)
        if any(compact_place_name(alias) in compact for alias in aliases):
            return brand
    return None


def franchise_category(brand: str | None) -> str:
    if not brand:
        return "other"
    category = FRANCHISE_BRANDS.get(brand, {}).get("category", "other")
    return str(category)


def same_franchise_brand(left: str, right: str) -> bool:
    left_brand = franchise_brand(left)
    return bool(left_brand and left_brand == franchise_brand(right))


def franchise_branch_hint(value: str) -> str:
    brand = franchise_brand(value)
    if not brand:
        return ""
    text = re.sub(r"\s*외\s*\d+.*$", " ", value or "")
    parenthetical = [hint.strip() for hint in re.findall(r"\(([^)]{1,20}(?:점|dt|DT))\)", text) if hint.strip()]
    if parenthetical:
        return parenthetical[0]
    compact = compact_place_name(text)
    aliases = FRANCHISE_BRANDS[brand]["aliases"]
    assert isinstance(aliases, tuple)
    for alias in sorted((compact_place_name(alias) for alias in aliases), key=len, reverse=True):
        compact = compact.replace(alias, "")
    for token in FRANCHISE_NON_BRANCH_TOKENS:
        compact = compact.replace(compact_place_name(token), "")
    compact = re.sub(r"^\d+$", "", compact)
    if len(compact) < 2:
        return ""
    if compact in {compact_place_name(token) for token in FRANCHISE_NON_BRANCH_TOKENS}:
        return ""
    return compact


def has_franchise_branch_hint(value: str) -> bool:
    return bool(franchise_branch_hint(value))


def franchise_addressless_without_branch_decision(
    row: NormalizedExpenseRow,
    candidates: list[PlaceCandidate],
) -> VerificationDecision | None:
    brand = franchise_brand(row.place_name or row.normalized_place_name)
    if not brand:
        return None
    if has_franchise_branch_hint(row.place_name) or has_franchise_branch_hint(row.normalized_place_name):
        return None
    if row.normalized_address:
        return None
    matching = [
        candidate
        for candidate in candidates
        if same_franchise_brand(row.place_name or row.normalized_place_name, candidate.name)
        and candidate.category in FOOD_CATEGORIES
        and has_valid_provider_coordinates(candidate)
    ]
    return VerificationDecision(
        decision="needs_review",
        confidence=0.84 if matching else 0.62,
        approved_by="rule",
        selected_candidate=matching[0] if matching else None,
        category=franchise_category(brand),
        reason_codes=["FRANCHISE_ADDRESSLESS_NO_BRANCH"],
        evidence={
            "brand": brand,
            "place_name": row.place_name,
            "candidate_evidence": candidate_evidence_payload(row, matching or candidates),
        },
    )


def provider_address_in_busan(candidate: PlaceCandidate) -> bool:
    return "부산" in normalize_address(candidate.road_address or candidate.address)


def unspecified_companion_place_decision(
    row: NormalizedExpenseRow,
    candidates: list[PlaceCandidate],
) -> VerificationDecision | None:
    if row.normalized_address:
        return None
    raw_name = row.place_name or row.normalized_place_name
    if not re.search(r"(?:외\s*\d+\s*(?:개소|개|곳)?|등)\s*$", raw_name or ""):
        return None
    best = candidates[0] if candidates else None
    return VerificationDecision(
        decision="needs_review",
        confidence=0.82 if best else 0.6,
        approved_by="rule",
        selected_candidate=best,
        category=best.category if best else category_from_text(raw_name),
        reason_codes=["UNSPECIFIED_COMPANION_PLACES"],
        evidence={"candidate_evidence": candidate_evidence_payload(row, candidates)},
    )


def is_addressless_exact_food_candidate(row: NormalizedExpenseRow, candidate: PlaceCandidate | None) -> bool:
    if candidate is None or row.normalized_address:
        return False
    parsed = parse_place_name(row.place_name or row.normalized_place_name)
    generic_identity = generic_parenthetical_place_name(row.place_name or row.normalized_place_name)
    if parsed.is_generic and not generic_identity:
        return False
    row_name = compact_place_name(generic_identity or clean_place_name_for_matching(row.place_name or row.normalized_place_name))
    candidate_name = compact_place_name(candidate.name)
    if any(token in row_name for token in ["미상", "불명", "없음", "무기재"]):
        return False
    return (
        len(row_name) >= 3
        and row_name == candidate_name
        and candidate.category in FOOD_CATEGORIES
        and bool(candidate.longitude)
        and bool(candidate.latitude)
        and is_food_context_purpose(row.purpose)
    )


def unique_addressless_exact_food_decision(
    row: NormalizedExpenseRow,
    candidates: list[PlaceCandidate],
    permit: PermitSnapshot | None,
) -> VerificationDecision | None:
    if row.normalized_address or len(candidates) < 2:
        return None
    if permit is not None and permit.business_status in {"closed", "moved", "non_food"}:
        return None
    exact = [candidate for candidate in candidates if is_addressless_exact_food_candidate(row, candidate)]
    exact_addresses = {
        normalize_address(candidate.road_address or candidate.address)
        for candidate in exact
        if normalize_address(candidate.road_address or candidate.address)
    }
    if len(exact_addresses) != 1:
        return None
    selected = exact[0]
    return VerificationDecision(
        decision="approved",
        confidence=0.94,
        approved_by="rule",
        selected_candidate=selected,
        category=selected.category,
        reason_codes=[
            "NAVER_UNIQUE_EXACT_AMONG_MULTIPLE",
            "PROVIDER_ADDRESS_AVAILABLE",
            *(["PERMIT_ACTIVE"] if permit is not None and permit.is_active_food_business else []),
        ],
        evidence={
            "exact_candidate_count": len(exact),
            "total_candidate_count": len(candidates),
            "selected_address": selected.road_address or selected.address,
            "candidate_evidence": candidate_evidence_payload(row, candidates),
        },
    )


def has_competing_addressless_food_candidate(
    row: NormalizedExpenseRow,
    candidates: list[PlaceCandidate],
) -> bool:
    if row.normalized_address:
        return False
    strong: list[PlaceCandidate] = []
    for candidate in candidates:
        if candidate.category not in FOOD_CATEGORIES or not has_valid_provider_coordinates(candidate):
            continue
        if has_conflicting_branch_hint(row.place_name, candidate.name):
            continue
        if name_similarity_with_branch(row_name_for_matching(row), candidate.name) >= 0.9:
            strong.append(candidate)
    distinct_addresses = {
        normalize_address(candidate.road_address or candidate.address)
        for candidate in strong
        if normalize_address(candidate.road_address or candidate.address)
    }
    return len(distinct_addresses) > 1


def addressless_too_many_matches_decision(
    row: NormalizedExpenseRow,
    candidates: list[PlaceCandidate],
    permit: PermitSnapshot | None,
) -> VerificationDecision | None:
    if row.normalized_address or parse_place_name(row.place_name).branch_name:
        return None
    strong = [
        candidate
        for candidate in candidates
        if candidate.category in FOOD_CATEGORIES
        and has_valid_provider_coordinates(candidate)
        and name_similarity_with_branch(row_name_for_matching(row), candidate.name) >= 0.90
    ]
    distinct_addresses = {
        normalize_address(candidate.road_address or candidate.address)
        for candidate in strong
        if normalize_address(candidate.road_address or candidate.address)
    }
    if len(distinct_addresses) < 3:
        return None
    if permit is not None and permit.is_active_food_business:
        permit_matches = [
            candidate
            for candidate in strong
            if candidate_address_similarity(permit.address, candidate) >= 0.72
        ]
        if len(permit_matches) == 1:
            return None
    return VerificationDecision(
        decision="needs_review",
        confidence=0.86,
        approved_by="rule",
        selected_candidate=strong[0],
        category=strong[0].category,
        reason_codes=["ADDRESSLESS_TOO_MANY_MATCHES"],
        evidence={
            "strong_candidate_count": len(strong),
            "distinct_address_count": len(distinct_addresses),
            "candidate_evidence": candidate_evidence_payload(row, strong),
        },
    )


def is_addressless_base_food_candidate(row: NormalizedExpenseRow, candidate: PlaceCandidate | None) -> bool:
    if candidate is None or row.normalized_address:
        return False
    row_name = compact_place_name(clean_place_name_for_matching(row.normalized_place_name or row.place_name))
    candidate_name = compact_place_name(clean_place_name_for_matching(candidate.name))
    if len(row_name) < 2 or not row_name or row_name == candidate_name:
        return False
    if any(token in row_name for token in ["미상", "불명", "없음", "무기재"]):
        return False
    suffix = candidate_name.removeprefix(row_name)
    return (
        candidate_name.startswith(row_name)
        and suffix in BRANCH_SUFFIXES
        and candidate.category in FOOD_CATEGORIES
        and provider_address_in_busan(candidate)
        and bool(candidate.longitude)
        and bool(candidate.latitude)
        and is_food_context_purpose(row.purpose)
    )


def road_names(value: str) -> set[str]:
    text = normalize_address(value)
    return set(re.findall(r"[가-힣0-9]+(?:대로|번길|길|로)", text))


def permit_address_conflicts_with_candidate(permit: PermitSnapshot, candidate: PlaceCandidate | None) -> bool:
    if candidate is None or not permit.address:
        return False
    permit_address = normalize_address(permit.address)
    provider_address = normalize_address(candidate.road_address or candidate.address)
    if not permit_address or not provider_address:
        return False
    if "부산" in provider_address and "부산" not in permit_address:
        return True
    permit_roads = road_names(permit_address)
    provider_roads = road_names(provider_address)
    if permit_roads and provider_roads and permit_roads.isdisjoint(provider_roads):
        return True
    return candidate_address_similarity(permit.address, candidate) < 0.62


def alias_keys_for_place(value: str) -> list[str]:
    parsed = parse_place_name(value)
    aliases = [
        normalize_text(value),
        clean_place_name_for_matching(value),
        normalize_text(parsed.base_name),
        normalize_text(parsed.combined_name),
    ]
    forced_aliases: set[str] = set()
    for alias in list(aliases):
        aliases.extend(brand_alias_variants(alias))
        generic_aliases = generic_place_alias_variants(alias)
        aliases.extend(generic_aliases)
        forced_aliases.update(normalize_text(generic_alias) for generic_alias in generic_aliases)
    compact_aliases = [alias.replace(" ", "") for alias in aliases]
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases + compact_aliases:
        normalized = normalize_text(alias)
        if (
            normalized
            and normalized not in seen
            and (is_distinctive_place_name(normalized) or normalized in forced_aliases)
        ):
            seen.add(normalized)
            result.append(normalized)
    return result


def branch_hints(value: str) -> set[str]:
    text = re.sub(r"\s+", " ", (value or "").lower()).strip()
    compact = re.sub(r"[^0-9a-z가-힣]+", "", text)
    hints = {hint for hint in BRANCH_HINTS if hint in compact}
    parsed_branch = parse_place_name(value).branch_name
    if parsed_branch:
        hints.add(parsed_branch)
    hints.update(re.findall(r"\(([^)]{1,40}(?:본점|직영점|호점|점))\)", text))
    hints.update(re.findall(r"(?:^|\s)([가-힣A-Za-z0-9]{1,40}(?:본점|직영점|호점|점))(?=$|\s)", text))
    hints.update(re.findall(r"[0-9]+호점", compact))
    return {hint for hint in hints if hint not in NON_BRANCH_HINTS}


def has_conflicting_branch_hint(left: str, right: str) -> bool:
    left_hints = branch_hints(left)
    right_hints = branch_hints(right)
    if not left_hints or not right_hints:
        return False
    return not any(
        left_hint in right_hint or right_hint in left_hint
        for left_hint in left_hints
        for right_hint in right_hints
    )


def name_similarity_with_branch(left: str, right: str) -> float:
    left_aliases = alias_keys_for_place(left) or [normalize_text(left)]
    right_aliases = alias_keys_for_place(right) or [normalize_text(right)]
    score = max((similarity(left_alias, right_alias) for left_alias in left_aliases for right_alias in right_aliases), default=0.0)
    for left_alias in left_aliases:
        for right_alias in right_aliases:
            left_compact = left_alias.replace(" ", "")
            right_compact = right_alias.replace(" ", "")
            if left_compact and right_compact and (left_compact in right_compact or right_compact in left_compact):
                score = max(score, 0.95)
    left_hints = branch_hints(left)
    right_hints = branch_hints(right)
    if has_conflicting_branch_hint(left, right):
        return min(score, ADDRESSLESS_FUZZY_NAME_THRESHOLD - 0.01)
    if left_hints and right_hints and any(left_hint in right_hint or right_hint in left_hint for left_hint in left_hints for right_hint in right_hints):
        score = max(score, 0.92)
    return min(score, 1.0)


def has_valid_provider_coordinates(candidate: PlaceCandidate) -> bool:
    return candidate.longitude is not None and candidate.latitude is not None


def has_unknown_placeholder_name(value: str) -> bool:
    compact = compact_place_name(value)
    return any(token in compact for token in UNKNOWN_PLACE_TOKENS)


def unknown_place_address_mismatch_decision(
    row: NormalizedExpenseRow,
    candidate: PlaceCandidate | None,
) -> VerificationDecision | None:
    if candidate is None or not row.normalized_address:
        return None
    if not has_unknown_placeholder_name(row.place_name or row.normalized_place_name):
        return None
    address_score = candidate_address_similarity(row.normalized_address, candidate)
    if address_score >= 0.72:
        return None
    return VerificationDecision(
        decision="rejected",
        confidence=0.94,
        approved_by="rule",
        selected_candidate=candidate,
        category=category_from_text(row.place_name),
        reason_codes=["UNKNOWN_PLACE_ADDRESS_MISMATCH"],
        evidence={
            "name_similarity": name_similarity_with_branch(row_name_for_matching(row), candidate.name),
            "address_similarity": address_score,
            "provider_category": candidate.category,
        },
    )


def is_addressless_fuzzy_food_candidate(
    row: NormalizedExpenseRow,
    candidate: PlaceCandidate | None,
    name_score: float,
) -> bool:
    if candidate is None or row.normalized_address:
        return False
    if name_score < ADDRESSLESS_FUZZY_NAME_THRESHOLD:
        return False
    if candidate.category not in FOOD_CATEGORIES:
        return False
    if not provider_address_in_busan(candidate):
        return False
    if not has_valid_provider_coordinates(candidate):
        return False
    if not is_verifiable_place_name(row.normalized_place_name or row.place_name):
        return False
    source_name = row.place_name or row.normalized_place_name
    parsed = parse_place_name(source_name)
    if parsed.is_generic and not generic_parenthetical_place_name(source_name):
        return False
    row_name = compact_place_name(clean_place_name_for_matching(source_name))
    candidate_name = compact_place_name(clean_place_name_for_matching(candidate.name))
    if len(row_name) < 3:
        return False
    if len(row_name) < 4 and row_name not in candidate_name:
        return False
    if has_conflicting_branch_hint(source_name, candidate.name):
        return False
    return is_food_context_purpose(row.purpose) or category_from_text(row.place_name) in FOOD_CATEGORIES


def row_name_for_matching(row: NormalizedExpenseRow) -> str:
    source_name = row.place_name if branch_hints(row.place_name) else row.normalized_place_name or row.place_name
    return clean_place_name_for_matching(source_name)


def candidate_address_similarity(reference_address: str, candidate: PlaceCandidate) -> float:
    return max(
        normalized_address_similarity(reference_address, candidate.address),
        normalized_address_similarity(reference_address, candidate.road_address),
    )


def candidate_structured_address_match(reference_address: str, candidate: PlaceCandidate) -> bool:
    return (
        structured_address_match(reference_address, candidate.address)
        or structured_address_match(reference_address, candidate.road_address)
    )


def candidate_evidence_payload(row: NormalizedExpenseRow, candidates: list[PlaceCandidate]) -> list[dict]:
    payload: list[dict] = []
    for candidate in candidates[:5]:
        payload.append(
            {
                "provider_place_id": candidate.provider_place_id,
                "name": candidate.name,
                "category": candidate.category,
                "provider_category_raw": candidate.provider_category_raw,
                "address": candidate.address,
                "road_address": candidate.road_address,
                "longitude": candidate.longitude,
                "latitude": candidate.latitude,
                "name_similarity": name_similarity_with_branch(row_name_for_matching(row), candidate.name),
                "address_similarity": candidate_address_similarity(row.normalized_address, candidate)
                if row.normalized_address
                else 0.0,
            }
        )
    return payload


class FakeNaverClient:
    """Deterministic local stand-in for Naver Search/Maps clients."""

    def __init__(self) -> None:
        self._places = [
            PlaceCandidate(
                provider_place_id="naver-busan-001",
                name="부산돼지국밥 시청점",
                category="restaurant",
                address="부산광역시 연제구 중앙대로 1001",
                road_address="부산광역시 연제구 중앙대로 1001",
                longitude=129.0756416,
                latitude=35.1795543,
            ),
            PlaceCandidate(
                provider_place_id="naver-busan-002",
                name="광안리커피",
                category="cafe",
                address="부산광역시 수영구 광안해변로 219",
                road_address="부산광역시 수영구 광안해변로 219",
                longitude=129.118619,
                latitude=35.153169,
            ),
            PlaceCandidate(
                provider_place_id="naver-busan-003",
                name="해운대포차",
                category="bar",
                address="부산광역시 해운대구 구남로 29",
                road_address="부산광역시 해운대구 구남로 29",
                longitude=129.160384,
                latitude=35.162922,
            ),
            PlaceCandidate(
                provider_place_id="naver-busan-004",
                name="부산컨벤션센터 회의실",
                category="other",
                address="부산광역시 해운대구 APEC로 55",
                road_address="부산광역시 해운대구 APEC로 55",
                longitude=129.135545,
                latitude=35.16908,
            ),
            PlaceCandidate(
                provider_place_id="naver-busan-005",
                name="미상식당 본점",
                category="restaurant",
                address="부산광역시 부산진구 중앙대로 730",
                road_address="부산광역시 부산진구 중앙대로 730",
                longitude=129.059163,
                latitude=35.157669,
            ),
        ]

    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        scored: list[tuple[float, PlaceCandidate]] = []
        for place in self._places:
            name_score = similarity(row_name_for_matching(row), place.name)
            address_score = similarity(row.normalized_address, place.address)
            if name_score >= 0.35 or address_score >= 0.45:
                scored.append((name_score + address_score * 0.4, place))
        return [place for _, place in sorted(scored, key=lambda item: item[0], reverse=True)[:5]]


class FakePermitClient:
    """Deterministic local stand-in for public permit API data."""

    def __init__(self) -> None:
        self._permits = {
            "부산돼지국밥 시청점": PermitSnapshot(
                permit_id="permit-001",
                category="restaurant",
                business_status="active",
                address="부산광역시 연제구 중앙대로 1001",
                longitude=129.0756416,
                latitude=35.1795543,
            ),
            "광안리커피": PermitSnapshot(
                permit_id="permit-002",
                category="cafe",
                business_status="active",
                address="부산광역시 수영구 광안해변로 219",
                longitude=129.118619,
                latitude=35.153169,
            ),
            "해운대포차": PermitSnapshot(
                permit_id="permit-003",
                category="bar",
                business_status="active",
                address="부산광역시 해운대구 구남로 29",
                longitude=129.160384,
                latitude=35.162922,
            ),
            "부산컨벤션센터 회의실": PermitSnapshot(
                permit_id="permit-004",
                category="other",
                business_status="non_food",
                address="부산광역시 해운대구 APEC로 55",
            ),
        }

    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        for name, permit in self._permits.items():
            if similarity(row_name_for_matching(row), name) >= 0.72:
                return permit
        return None


class RuleBasedAiReviewer:
    """A conservative local AI substitute that never writes to the DB."""

    def decide(
        self,
        row: NormalizedExpenseRow,
        candidates: list[PlaceCandidate],
        permit: PermitSnapshot | None,
        rule_score: float,
    ) -> VerificationDecision:
        if not candidates:
            return VerificationDecision(
                decision="needs_review",
                confidence=0.5,
                approved_by="ai",
                selected_candidate=None,
                category=category_from_text(row.place_name),
                reason_codes=["AI_NO_MAP_CANDIDATE"],
                evidence={"rule_score": rule_score},
            )
        best = candidates[0]
        name_score = name_similarity_with_branch(row_name_for_matching(row), best.name)
        address_score = candidate_address_similarity(row.normalized_address, best)
        if permit is None and name_score >= 0.82 and address_score >= 0.75:
            return VerificationDecision(
                decision="needs_review",
                confidence=0.74,
                approved_by="ai",
                selected_candidate=best,
                category=best.category,
                reason_codes=["AI_GOOD_NAVER_MATCH_PERMIT_MISSING"],
                evidence={"name_similarity": name_score, "address_similarity": address_score},
            )
        return VerificationDecision(
            decision="needs_review",
            confidence=0.58,
            approved_by="ai",
            selected_candidate=best,
            category=best.category,
            reason_codes=["AI_BOUNDARY_SCORE"],
            evidence={"name_similarity": name_score, "address_similarity": address_score},
        )


class VerifierAgent:
    def __init__(
        self,
        naver_client: NaverClient | None = None,
        permit_client: PermitClient | None = None,
        ai_reviewer: AiReviewer | None = None,
    ) -> None:
        self.naver_client = naver_client or FakeNaverClient()
        self.permit_client = permit_client or FakePermitClient()
        self.ai_reviewer = ai_reviewer or RuleBasedAiReviewer()

    def verify(
        self,
        row: NormalizedExpenseRow,
        progress: Callable[[str, str, int], None] | None = None,
    ) -> VerificationDecision:
        scope_reject_decision = expense_scope_reject_decision(row)
        if scope_reject_decision is not None:
            return scope_reject_decision
        purpose_decision = non_food_purpose_decision(row)
        if purpose_decision is not None:
            return purpose_decision
        feedback_reject_decision = manual_feedback_reject_decision(row)
        if feedback_reject_decision is not None:
            return feedback_reject_decision
        if progress:
            progress("naver_search", "네이버 장소 검색 API", 82)
        candidates = self.naver_client.search_local(row)
        if progress:
            progress("permit_lookup", "인허가 조회 API", 88)
        permit = self.permit_client.lookup(row)
        if progress:
            progress("rule_decision", "검색 결과 조건 판단", 92)
        companion_decision = unspecified_companion_place_decision(row, candidates)
        if companion_decision is not None:
            return companion_decision
        franchise_decision = franchise_addressless_without_branch_decision(row, candidates)
        if franchise_decision is not None:
            return franchise_decision
        unique_exact_decision = unique_addressless_exact_food_decision(row, candidates, permit)
        if unique_exact_decision is not None:
            return unique_exact_decision
        too_many_matches = addressless_too_many_matches_decision(row, candidates, permit)
        if too_many_matches is not None:
            return too_many_matches
        best = candidates[0] if candidates else None
        unknown_mismatch_decision = unknown_place_address_mismatch_decision(row, best)
        if unknown_mismatch_decision is not None:
            return unknown_mismatch_decision
        if (
            best
            and best.category == "other"
            and category_from_text(row.place_name) == "other"
            and not is_food_context_purpose(row.purpose)
        ):
            if any(
                token in compact_place_name(row.place_name or row.normalized_place_name)
                for token in ("호텔", "리조트", "대사관", "영사관")
            ):
                return VerificationDecision(
                    decision="needs_review",
                    confidence=0.78,
                    approved_by="rule",
                    selected_candidate=best,
                    category="other",
                    reason_codes=["NAVER_OTHER_AMBIGUOUS_VENUE"],
                    evidence={"candidate_evidence": candidate_evidence_payload(row, candidates)},
                )
            return VerificationDecision(
                decision="rejected",
                confidence=0.9,
                approved_by="rule",
                selected_candidate=best,
                category="other",
                reason_codes=["NAVER_OTHER_CATEGORY"],
                evidence={
                    "name_similarity": name_similarity_with_branch(row_name_for_matching(row), best.name),
                    "address_similarity": candidate_address_similarity(row.normalized_address, best)
                    if row.normalized_address
                    else 0.0,
                    "provider_category": best.category,
                    "purpose": row.purpose,
                    "permit_status": permit.business_status if permit else None,
                },
            )
        has_competing_addressless_food = has_competing_addressless_food_candidate(row, candidates)
        exact_addressless_food = (
            is_addressless_exact_food_candidate(row, best)
            and not has_competing_addressless_food
        )
        base_addressless_food = (
            is_addressless_base_food_candidate(row, best)
            and not has_competing_addressless_food
        )

        if permit and permit.business_status in {"closed", "moved", "non_food"}:
            name_score = name_similarity_with_branch(row_name_for_matching(row), best.name) if best else 0.0
            address_score = candidate_address_similarity(permit.address, best) if best else 0.0
            if (
                exact_addressless_food
                and permit.business_status in {"closed", "moved"}
                and permit_address_conflicts_with_candidate(permit, best)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.92, 0.68 + name_score * 0.22),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category if best else permit.category,
                    reason_codes=[
                        "NAVER_EXACT_ADDRESSLESS",
                        "BUSAN_PROVIDER_ADDRESS",
                        "PERMIT_NOT_ACTIVE_DIFFERENT_ADDRESS_IGNORED",
                    ],
                    evidence={
                        "permit_id": permit.permit_id,
                        "permit_status": permit.business_status,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )
            if not row.normalized_address:
                return VerificationDecision(
                    decision="needs_review",
                    confidence=0.72,
                    approved_by="rule",
                    selected_candidate=best,
                    category=permit.category,
                    reason_codes=["PERMIT_NOT_ACTIVE_ADDRESSLESS"],
                    evidence={
                        "permit_status": permit.business_status,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )
            return VerificationDecision(
                decision="rejected",
                confidence=0.94,
                approved_by="rule",
                selected_candidate=best,
                category=permit.category,
                reason_codes=["PERMIT_NOT_ACTIVE_FOOD"],
                evidence={"permit_status": permit.business_status},
            )

        if permit and best:
            name_score = name_similarity_with_branch(row_name_for_matching(row), best.name)
            address_score = candidate_address_similarity(permit.address, best)
            row_address_score = candidate_address_similarity(row.normalized_address, best) if row.normalized_address else 0.0
            row_structured_address_match = (
                candidate_structured_address_match(row.normalized_address, best) if row.normalized_address else False
            )
            provider_address = normalize_address(best.road_address or best.address)
            if (
                exact_addressless_food
                and permit_address_conflicts_with_candidate(permit, best)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.92, 0.68 + name_score * 0.22),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["NAVER_EXACT_ADDRESSLESS", "BUSAN_PROVIDER_ADDRESS", "PERMIT_ADDRESS_CONFLICT_IGNORED"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "permit_status": permit.business_status,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )
            if (
                not row.normalized_address
                and permit.business_status == "active"
                and permit.category in FOOD_CATEGORIES
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.9
                and "부산" in provider_address
                and (
                    is_distinctive_place_name(row.normalized_place_name)
                    or exact_addressless_food
                    or base_addressless_food
                )
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.97, 0.63 + name_score * 0.26 + min(address_score, 1.0) * 0.08),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["PERMIT_ACTIVE", "NAVER_FOOD_CATEGORY", "ADDRESSLESS_NAME_MATCH"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )
            if (
                permit.business_status == "active"
                and permit.category in FOOD_CATEGORIES
                and is_addressless_fuzzy_food_candidate(row, best, name_score)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.9, 0.58 + name_score * 0.24 + min(address_score, 1.0) * 0.1),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["PERMIT_ACTIVE", "NAVER_FOOD_CATEGORY", "ADDRESSLESS_FUZZY_NAME_MATCH"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )
            if (
                row.normalized_address
                and permit.business_status == "active"
                and permit.category in FOOD_CATEGORIES
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.88
                and row_structured_address_match
                and is_verifiable_place_name(row.normalized_place_name)
            ):
                category = best.category if best.category in FOOD_CATEGORIES else permit.category
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.98, 0.55 + name_score * 0.27 + row_address_score * 0.16),
                    approved_by="rule",
                    selected_candidate=best,
                    category=category,
                    reason_codes=["PERMIT_ACTIVE", "NAVER_FOOD_CATEGORY", "NORMALIZED_ADDRESS_MATCH"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "name_similarity": name_score,
                        "address_similarity": max(address_score, row_address_score),
                        "row_address_similarity": row_address_score,
                    },
                )
            if (
                row.normalized_address
                and permit.business_status == "active"
                and permit.category in FOOD_CATEGORIES
                and best.category in FOOD_CATEGORIES
                and same_franchise_brand(row.place_name or row.normalized_place_name, best.name)
                and row_structured_address_match
                and provider_address_in_busan(best)
            ):
                category = best.category if best.category in FOOD_CATEGORIES else permit.category
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.97, 0.62 + row_address_score * 0.2),
                    approved_by="rule",
                    selected_candidate=best,
                    category=category,
                    reason_codes=["PERMIT_ACTIVE", "NAVER_FOOD_CATEGORY", "FRANCHISE_ADDRESS_MATCH"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "name_similarity": name_score,
                        "address_similarity": max(address_score, row_address_score),
                        "row_address_similarity": row_address_score,
                    },
                )
            if (
                permit.business_status == "active"
                and permit.category in FOOD_CATEGORIES
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.78
                and address_score >= 0.72
            ):
                category = best.category if best.category in FOOD_CATEGORIES else permit.category
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.99, 0.55 + name_score * 0.25 + address_score * 0.2),
                    approved_by="rule",
                    selected_candidate=best,
                    category=category,
                    reason_codes=["PERMIT_ACTIVE", "NAVER_FOOD_CATEGORY", "ADDRESS_MATCH"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )

        if best and permit is None:
            name_score = name_similarity_with_branch(row_name_for_matching(row), best.name)
            provider_address = normalize_address(best.road_address or best.address)
            address_score = candidate_address_similarity(row.normalized_address, best)
            row_structured_address_match = (
                candidate_structured_address_match(row.normalized_address, best) if row.normalized_address else False
            )
            if (
                row.normalized_address
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.88
                and row_structured_address_match
                and is_verifiable_place_name(row.normalized_place_name)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.96, 0.5 + name_score * 0.28 + address_score * 0.18),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["NAVER_HIGH_CONFIDENCE", "NAVER_NORMALIZED_ADDRESS_MATCH", "PERMIT_MISSING_ALLOWED"],
                    evidence={"name_similarity": name_score, "address_similarity": address_score},
                )
            if (
                row.normalized_address
                and best.category in FOOD_CATEGORIES
                and same_franchise_brand(row.place_name or row.normalized_place_name, best.name)
                and row_structured_address_match
                and provider_address_in_busan(best)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.95, 0.61 + address_score * 0.24),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["NAVER_FRANCHISE_ADDRESS_MATCH", "BUSAN_PROVIDER_ADDRESS", "PERMIT_MISSING_ALLOWED"],
                    evidence={"name_similarity": name_score, "address_similarity": address_score},
                )
            if (
                row.normalized_address
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.9
                and address_score >= 0.82
                and is_distinctive_place_name(row.normalized_place_name)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.96, 0.5 + name_score * 0.28 + address_score * 0.18),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["NAVER_HIGH_CONFIDENCE", "NAVER_ADDRESS_MATCH", "PERMIT_MISSING_ALLOWED"],
                    evidence={"name_similarity": name_score, "address_similarity": address_score},
                )
            if (
                not row.normalized_address
                and not has_competing_addressless_food
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.9
                and (
                    exact_addressless_food
                    or (
                        "부산" in provider_address
                        and (
                            is_distinctive_place_name(row.normalized_place_name)
                            or base_addressless_food
                        )
                    )
                )
            ):
                reason = (
                    "NAVER_EXACT_ADDRESSLESS"
                    if exact_addressless_food
                    else "NAVER_BASE_NAME_ADDRESSLESS"
                    if base_addressless_food
                    else "NAVER_HIGH_CONFIDENCE_ADDRESSLESS"
                )
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.93, 0.64 + name_score * 0.24),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=[
                        reason,
                        "BUSAN_PROVIDER_ADDRESS"
                        if "부산" in provider_address
                        else "PROVIDER_ADDRESS_AVAILABLE",
                        "PERMIT_MISSING_ALLOWED",
                    ],
                    evidence={"name_similarity": name_score, "address_similarity": 0.0},
                )
            if (
                not has_competing_addressless_food
                and is_addressless_fuzzy_food_candidate(row, best, name_score)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.88, 0.56 + name_score * 0.28),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["NAVER_FUZZY_ADDRESSLESS", "BUSAN_PROVIDER_ADDRESS", "PERMIT_MISSING_ALLOWED"],
                    evidence={"name_similarity": name_score, "address_similarity": 0.0},
                )

        rule_score = 0.0
        if best:
            rule_score += name_similarity_with_branch(row_name_for_matching(row), best.name) * 0.45
            rule_score += candidate_address_similarity(row.normalized_address, best) * 0.25
            rule_score += 0.15 if best.category in FOOD_CATEGORIES else -0.2
        if permit and permit.business_status == "active":
            rule_score += 0.2
        if rule_score <= 0.18 and permit is None and row.normalized_address:
            return VerificationDecision(
                decision="rejected",
                confidence=0.83,
                approved_by="rule",
                selected_candidate=best,
                category=category_from_text(row.place_name),
                reason_codes=["LOW_SCORE_NO_PERMIT"],
                evidence={"rule_score": rule_score},
            )
        if progress:
            progress("ai_review", "경계값 최종 판단", 94)
        decision = self.ai_reviewer.decide(row, candidates, permit, rule_score)
        if candidates:
            decision.evidence["candidate_evidence"] = candidate_evidence_payload(row, candidates)
        return decision
