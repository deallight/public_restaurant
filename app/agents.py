from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import re
from typing import Literal, Protocol

from .utils import normalize_address, normalize_text


Decision = Literal["approved", "rejected", "needs_review"]
ApprovedBy = Literal["rule", "ai", "manual", "none"]
FOOD_CATEGORIES = {"restaurant", "cafe", "bar"}
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


@dataclass(frozen=True)
class PlaceCandidate:
    provider_place_id: str
    name: str
    category: str
    address: str
    road_address: str
    longitude: float
    latitude: float


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
    return SequenceMatcher(None, normalize_text(left), normalize_text(right)).ratio()


def clean_place_name_for_matching(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"\b외\s*\d+.*$", " ", text)
    text = re.sub(r"\b(일원|등)$", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def category_from_text(value: str) -> str:
    text = normalize_text(value)
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


def is_distinctive_place_name(value: str) -> bool:
    compact = normalize_text(value).replace(" ", "")
    if any(token in compact for token in ["미상", "불명", "없음", "무기재"]):
        return False
    return len(compact) >= 4 and not compact.isdigit()


def compact_place_name(value: str) -> str:
    return normalize_text(value).replace(" ", "")


def is_addressless_exact_food_candidate(row: NormalizedExpenseRow, candidate: PlaceCandidate | None) -> bool:
    if candidate is None or row.normalized_address:
        return False
    row_name = compact_place_name(row.normalized_place_name or row.place_name)
    candidate_name = compact_place_name(candidate.name)
    provider_address = normalize_address(candidate.road_address or candidate.address)
    if any(token in row_name for token in ["미상", "불명", "없음", "무기재"]):
        return False
    return (
        len(row_name) >= 3
        and row_name == candidate_name
        and candidate.category in FOOD_CATEGORIES
        and "부산" in provider_address
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
    aliases = [normalize_text(value), clean_place_name_for_matching(value)]
    compact_aliases = [alias.replace(" ", "") for alias in aliases]
    seen: set[str] = set()
    result: list[str] = []
    for alias in aliases + compact_aliases:
        normalized = normalize_text(alias)
        if normalized and normalized not in seen and is_distinctive_place_name(normalized):
            seen.add(normalized)
            result.append(normalized)
    return result


def branch_hints(value: str) -> set[str]:
    compact = normalize_text(value).replace(" ", "")
    hints = {hint for hint in BRANCH_HINTS if hint in compact}
    hints.update(re.findall(r"[0-9]+호점", compact))
    return hints


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
    if left_hints and right_hints and any(left_hint in right_hint or right_hint in left_hint for left_hint in left_hints for right_hint in right_hints):
        score = max(score, 0.92)
    return min(score, 1.0)


def candidate_address_similarity(reference_address: str, candidate: PlaceCandidate) -> float:
    reference = normalize_address(reference_address)
    return max(
        similarity(reference, candidate.address),
        similarity(reference, candidate.road_address),
    )


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
            name_score = similarity(row.normalized_place_name, place.name)
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
            if similarity(row.normalized_place_name, name) >= 0.72:
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
        name_score = name_similarity_with_branch(row.normalized_place_name, best.name)
        address_score = similarity(row.normalized_address, best.address)
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

    def verify(self, row: NormalizedExpenseRow) -> VerificationDecision:
        purpose_decision = non_food_purpose_decision(row)
        if purpose_decision is not None:
            return purpose_decision

        candidates = self.naver_client.search_local(row)
        permit = self.permit_client.lookup(row)
        best = candidates[0] if candidates else None
        exact_addressless_food = is_addressless_exact_food_candidate(row, best)

        if permit and permit.business_status in {"closed", "moved", "non_food"}:
            name_score = name_similarity_with_branch(row.normalized_place_name, best.name) if best else 0.0
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
            name_score = name_similarity_with_branch(row.normalized_place_name, best.name)
            address_score = candidate_address_similarity(permit.address, best)
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
                and (is_distinctive_place_name(row.normalized_place_name) or exact_addressless_food)
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
            name_score = name_similarity_with_branch(row.normalized_place_name, best.name)
            provider_address = normalize_address(best.road_address or best.address)
            address_score = candidate_address_similarity(row.normalized_address, best)
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
                and best.category in FOOD_CATEGORIES
                and name_score >= 0.9
                and "부산" in provider_address
                and is_distinctive_place_name(row.normalized_place_name)
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.93, 0.64 + name_score * 0.24),
                    approved_by="rule",
                    selected_candidate=best,
                    category=best.category,
                    reason_codes=["NAVER_HIGH_CONFIDENCE_ADDRESSLESS", "BUSAN_PROVIDER_ADDRESS", "PERMIT_MISSING_ALLOWED"],
                    evidence={"name_similarity": name_score, "address_similarity": 0.0},
                )

        rule_score = 0.0
        if best:
            rule_score += name_similarity_with_branch(row.normalized_place_name, best.name) * 0.45
            rule_score += similarity(row.normalized_address, best.address) * 0.25
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
        return self.ai_reviewer.decide(row, candidates, permit, rule_score)
