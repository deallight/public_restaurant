from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal, Protocol

from .utils import normalize_address, normalize_text


Decision = Literal["approved", "rejected", "needs_review"]
ApprovedBy = Literal["rule", "ai", "manual", "none"]


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


def category_from_text(value: str) -> str:
    text = normalize_text(value)
    if any(token in text for token in ["카페", "커피", "디저트", "베이커리"]):
        return "cafe"
    if any(token in text for token in ["포차", "주점", "호프", "맥주", "바"]):
        return "bar"
    if any(token in text for token in ["식당", "국밥", "횟집", "고기", "음식점", "분식"]):
        return "restaurant"
    return "other"


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
        name_score = similarity(row.normalized_place_name, best.name)
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
        candidates = self.naver_client.search_local(row)
        permit = self.permit_client.lookup(row)
        best = candidates[0] if candidates else None

        if permit and permit.business_status in {"closed", "moved", "non_food"}:
            if not row.normalized_address:
                return VerificationDecision(
                    decision="needs_review",
                    confidence=0.72,
                    approved_by="rule",
                    selected_candidate=best,
                    category=permit.category,
                    reason_codes=["PERMIT_NOT_ACTIVE_ADDRESSLESS"],
                    evidence={"permit_status": permit.business_status},
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
            name_score = similarity(row.normalized_place_name, best.name)
            address_score = similarity(normalize_address(permit.address), best.address)
            if (
                permit.business_status == "active"
                and permit.category in {"restaurant", "cafe", "bar"}
                and best.category == permit.category
                and name_score >= 0.78
                and address_score >= 0.72
            ):
                return VerificationDecision(
                    decision="approved",
                    confidence=min(0.99, 0.55 + name_score * 0.25 + address_score * 0.2),
                    approved_by="rule",
                    selected_candidate=best,
                    category=permit.category,
                    reason_codes=["PERMIT_ACTIVE", "NAVER_CATEGORY_MATCH", "ADDRESS_MATCH"],
                    evidence={
                        "permit_id": permit.permit_id,
                        "name_similarity": name_score,
                        "address_similarity": address_score,
                    },
                )

        rule_score = 0.0
        if best:
            rule_score += similarity(row.normalized_place_name, best.name) * 0.45
            rule_score += similarity(row.normalized_address, best.address) * 0.25
            rule_score += 0.15 if best.category in {"restaurant", "cafe", "bar"} else -0.2
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
