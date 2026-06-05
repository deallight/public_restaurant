from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents import (
    FakeNaverClient,
    FakePermitClient,
    NormalizedExpenseRow,
    PermitSnapshot,
    PlaceCandidate,
    VerifierAgent,
    non_food_purpose_reason,
)
from app.database import Database
from app.integrations import DataGoKrPermitClient, _search_queries
from app.pipeline import CachedPermitClient, DailyPipeline, RawExpenseRow, SourceDocument
from app.source_catalog import iter_source_catalog


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_daily_pipeline_is_idempotent_for_service_data(self) -> None:
        first = DailyPipeline(self.db).run()
        second = DailyPipeline(self.db).run()

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        self.assertEqual(second["summary"]["rows_inserted"], 0)
        self.assertEqual(
            self.db.counts(
                [
                    "raw_documents",
                    "expense_records",
                    "restaurant_candidates",
                    "restaurants",
                    "restaurant_expense_links",
                    "manual_review_tasks",
                ]
            ),
            {
                "raw_documents": 1,
                "expense_records": 5,
                "restaurant_candidates": 5,
                "restaurants": 3,
                "restaurant_expense_links": 3,
                "manual_review_tasks": 1,
            },
        )

    def test_sqlite_schema_apply_and_development_rollback(self) -> None:
        self.assertGreater(self.db.count("regions"), 0)
        self.assertEqual(self.db.count("source_registry"), len(iter_source_catalog()))
        self.db.rollback_schema()
        with self.db.session() as conn:
            tables = conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = 'regions'
                """
            ).fetchall()
        self.assertEqual(tables, [])
        self.db.initialize()
        self.assertGreater(self.db.count("regions"), 0)

    def test_verifier_uses_rule_reject_and_ai_review_boundaries(self) -> None:
        verifier = VerifierAgent(FakeNaverClient(), FakePermitClient())

        approved = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="기획담당관",
                used_date="2026-01-14",
                place_name="부산돼지국밥 시청점",
                address="부산광역시 연제구 중앙대로 1001",
                purpose="간담",
                amount=68000,
                normalized_place_name="부산돼지국밥 시청점",
                normalized_address="부산광역시 연제구 중앙대로 1001",
            )
        )
        rejected = verifier.verify(
            NormalizedExpenseRow(
                row_number=2,
                department_name="국제협력과",
                used_date="2026-03-18",
                place_name="부산컨벤션센터 회의실",
                address="부산광역시 해운대구 APEC로 55",
                purpose="대관",
                amount=250000,
                normalized_place_name="부산컨벤션센터 회의실",
                normalized_address="부산광역시 해운대구 APEC로 55",
            )
        )
        needs_review = verifier.verify(
            NormalizedExpenseRow(
                row_number=3,
                department_name="교통혁신과",
                used_date="2026-03-23",
                place_name="미상식당",
                address="부산광역시 부산진구 중앙대로 730",
                purpose="간담",
                amount=51000,
                normalized_place_name="미상식당",
                normalized_address="부산광역시 부산진구 중앙대로 730",
            )
        )

        self.assertEqual(approved.decision, "approved")
        self.assertEqual(approved.approved_by, "rule")
        self.assertEqual(rejected.decision, "rejected")
        self.assertEqual(needs_review.decision, "needs_review")
        self.assertEqual(needs_review.approved_by, "ai")

    def test_verifier_sends_addressless_live_rows_to_review(self) -> None:
        verifier = VerifierAgent(FakeNaverClient(), FakePermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="방호조사과",
                used_date="2023-05-23",
                place_name="두레박국밥",
                address="",
                purpose="간담",
                amount=150000,
                normalized_place_name="두레박국밥",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertEqual(decision.approved_by, "ai")

    def test_verifier_does_not_reject_addressless_rows_on_closed_permit_only(self) -> None:
        verifier = VerifierAgent(EmptyNaverClient(), ClosedPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="방호조사과",
                used_date="2023-05-23",
                place_name="두레박국밥",
                address="",
                purpose="간담",
                amount=150000,
                normalized_place_name="두레박국밥",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertEqual(decision.reason_codes, ["PERMIT_NOT_ACTIVE_ADDRESSLESS"])

    def test_data_go_kr_permit_client_maps_active_food_status(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="기획담당관",
            used_date="2026-01-14",
            place_name="부산돼지국밥 시청점",
            address="부산광역시 연제구 중앙대로 1001",
            purpose="간담",
            amount=68000,
            normalized_place_name="부산돼지국밥 시청점",
            normalized_address="부산광역시 연제구 중앙대로 1001",
        )
        payload = {
            "response": {
                "header": {"resultCode": "0", "resultMsg": "NORMAL SERVICE"},
                "body": {
                    "items": {
                        "item": [
                            {
                                "MNG_NO": "permit-live-1",
                                "BPLC_NM": "부산돼지국밥 시청점",
                                "ROAD_NM_ADDR": "부산광역시 연제구 중앙대로 1001",
                                "LOTNO_ADDR": "부산광역시 연제구 연산동",
                                "SALS_STTS_NM": "영업/정상",
                                "DTL_SALS_STTS_NM": "영업",
                                "CLSBIZ_YMD": "",
                            }
                        ]
                    }
                },
            }
        }
        requested_urls: list[str] = []

        def fake_get_json(url, headers):
            requested_urls.append(url)
            return payload

        with patch("app.integrations._get_json", side_effect=fake_get_json):
            permit = DataGoKrPermitClient("test-key").lookup(row)

        self.assertIsNotNone(permit)
        self.assertEqual(permit.permit_id, "permit-live-1")
        self.assertEqual(permit.category, "restaurant")
        self.assertEqual(permit.business_status, "active")
        self.assertIn("cond%5BROAD_NM_ADDR%3A%3ALIKE%5D=", requested_urls[0])
        self.assertNotIn("OPN_ATMY_GRP_CD", requested_urls[0])

    def test_verify_pending_can_auto_approve_addressless_live_candidate(self) -> None:
        DailyPipeline(
            self.db,
            adapter=AddresslessAdapter(),
            verifier=VerifierAgent(EmptyNaverClient(), EmptyPermitClient()),
        ).run()

        result = DailyPipeline(
            self.db,
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
        ).verify_pending(limit=10)

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["needs_review"], 0)
        with self.db.session() as conn:
            restaurant_count = conn.execute("SELECT COUNT(*) AS c FROM restaurants").fetchone()["c"]
            pending_count = conn.execute(
                "SELECT COUNT(*) AS c FROM manual_review_tasks WHERE status = 'pending'"
            ).fetchone()["c"]
            resolved = conn.execute("SELECT status FROM manual_review_tasks").fetchone()["status"]
        self.assertEqual(restaurant_count, 1)
        self.assertEqual(pending_count, 0)
        self.assertEqual(resolved, "auto_approved")

    def test_high_confidence_naver_only_can_auto_approve_addressless_candidate(self) -> None:
        result = DailyPipeline(
            self.db,
            adapter=AddresslessAdapter(),
            verifier=VerifierAgent(ExactNaverClient(), EmptyPermitClient()),
        ).run()

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["needs_review"], 0)
        with self.db.session() as conn:
            verification = conn.execute(
                """
                SELECT provider_place_name, verification_status, name_similarity, address_similarity
                FROM place_verifications
                """
            ).fetchone()
            pending_count = conn.execute(
                "SELECT COUNT(*) AS c FROM manual_review_tasks WHERE status = 'pending'"
            ).fetchone()["c"]
            restaurant_count = conn.execute("SELECT COUNT(*) AS c FROM restaurants").fetchone()["c"]
            visible_map_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM restaurants
                WHERE verification_status = 'success'
                  AND map_exposure_status = 'visible'
                """
            ).fetchone()["c"]

        self.assertEqual(verification["provider_place_name"], "두레박국밥")
        self.assertEqual(verification["verification_status"], "success")
        self.assertEqual(round(verification["name_similarity"], 2), 1.0)
        self.assertEqual(round(verification["address_similarity"], 2), 0.0)
        self.assertEqual(pending_count, 0)
        self.assertEqual(restaurant_count, 1)
        self.assertEqual(visible_map_count, 1)

    def test_generic_addressless_naver_candidate_stays_manual_review(self) -> None:
        result = DailyPipeline(
            self.db,
            adapter=GenericAddresslessAdapter(),
            verifier=VerifierAgent(GenericNaverClient(), EmptyPermitClient()),
        ).run()

        self.assertEqual(result["summary"]["approved"], 0)
        self.assertEqual(result["summary"]["needs_review"], 1)
        with self.db.session() as conn:
            verification = conn.execute(
                """
                SELECT provider_place_name, verification_status, name_similarity, address_similarity
                FROM place_verifications
                """
            ).fetchone()
            pending_count = conn.execute(
                "SELECT COUNT(*) AS c FROM manual_review_tasks WHERE status = 'pending'"
            ).fetchone()["c"]

        self.assertEqual(verification["provider_place_name"], "씨드")
        self.assertEqual(verification["verification_status"], "ambiguous")
        self.assertEqual(round(verification["name_similarity"], 2), 1.0)
        self.assertEqual(round(verification["address_similarity"], 2), 0.0)
        self.assertEqual(pending_count, 1)

    def test_reverification_updates_ambiguous_evidence_to_success_and_visible_map(self) -> None:
        DailyPipeline(
            self.db,
            adapter=GenericAddresslessAdapter(),
            verifier=VerifierAgent(GenericNaverClient(), EmptyPermitClient()),
        ).run()

        result = DailyPipeline(
            self.db,
            verifier=VerifierAgent(GenericNaverClient(), GenericPermitClient()),
        ).verify_pending(limit=10)

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["needs_review"], 0)
        with self.db.session() as conn:
            verification = conn.execute(
                """
                SELECT verification_status, verification_reason, address_similarity
                FROM place_verifications
                """
            ).fetchone()
            visible_map_count = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM restaurants
                WHERE verification_status = 'success'
                  AND map_exposure_status = 'visible'
                """
            ).fetchone()["c"]
            resolved = conn.execute("SELECT status FROM manual_review_tasks").fetchone()["status"]

        self.assertEqual(verification["verification_status"], "success")
        self.assertIn("PERMIT_ACTIVE", verification["verification_reason"])
        self.assertGreaterEqual(verification["address_similarity"], 0.72)
        self.assertEqual(visible_map_count, 1)
        self.assertEqual(resolved, "auto_approved")

    def test_alias_memory_auto_approves_repeated_place_and_links_existing_map_item(self) -> None:
        DailyPipeline(
            self.db,
            adapter=AddresslessAdapter(),
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
        ).run()

        result = DailyPipeline(
            self.db,
            adapter=RepeatedAliasAdapter(),
            verifier=VerifierAgent(EmptyNaverClient(), EmptyPermitClient()),
        ).run()

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["needs_review"], 0)
        with self.db.session() as conn:
            restaurant_count = conn.execute("SELECT COUNT(*) AS c FROM restaurants").fetchone()["c"]
            link_count = conn.execute("SELECT COUNT(*) AS c FROM restaurant_expense_links").fetchone()["c"]
            alias_count = conn.execute("SELECT COUNT(*) AS c FROM alias_memory").fetchone()["c"]
            latest_reason = conn.execute(
                """
                SELECT reason_codes_json
                FROM decision_audit_logs
                WHERE action = 'approved'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()["reason_codes_json"]

        self.assertEqual(restaurant_count, 1)
        self.assertEqual(link_count, 2)
        self.assertGreaterEqual(alias_count, 1)
        self.assertIn("ALIAS_MEMORY_MATCH", latest_reason)

    def test_non_food_purchase_purpose_rejects_before_alias_memory(self) -> None:
        DailyPipeline(
            self.db,
            adapter=AddresslessAdapter(),
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
        ).run()

        result = DailyPipeline(
            self.db,
            adapter=GiftAliasAdapter(),
            verifier=VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient()),
        ).run()

        self.assertEqual(result["summary"]["approved"], 0)
        self.assertEqual(result["summary"]["rejected"], 1)
        self.assertEqual(result["summary"]["dlq"], 0)
        with self.db.session() as conn:
            restaurant_count = conn.execute("SELECT COUNT(*) AS c FROM restaurants").fetchone()["c"]
            link_count = conn.execute("SELECT COUNT(*) AS c FROM restaurant_expense_links").fetchone()["c"]
            rejected = conn.execute(
                """
                SELECT c.status, c.rejection_reason
                FROM restaurant_candidates c
                ORDER BY c.id DESC
                LIMIT 1
                """
            ).fetchone()
            pending_count = conn.execute(
                "SELECT COUNT(*) AS c FROM manual_review_tasks WHERE status = 'pending'"
            ).fetchone()["c"]

        self.assertEqual(restaurant_count, 1)
        self.assertEqual(link_count, 1)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["rejection_reason"], "NON_FOOD_PURPOSE_GIFT")
        self.assertEqual(pending_count, 0)

    def test_branch_hint_match_can_auto_approve_permit_and_naver_food_candidate(self) -> None:
        verifier = VerifierAgent(BranchNaverClient(), BranchPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="본도시락 시청점외 1",
                address="",
                purpose="간담",
                amount=88000,
                normalized_place_name="본도시락 시청점외 1",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("PERMIT_ACTIVE", decision.reason_codes)
        self.assertGreaterEqual(decision.evidence["name_similarity"], 0.78)

    def test_addressless_strong_naver_branch_match_can_approve_without_permit(self) -> None:
        verifier = VerifierAgent(BranchNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="본도시락 시청점외 1",
                address="",
                purpose="업무협의 간담",
                amount=88000,
                normalized_place_name="본도시락 시청점외 1",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_HIGH_CONFIDENCE_ADDRESSLESS", decision.reason_codes)

    def test_short_exact_addressless_food_name_ignores_conflicting_active_permit(self) -> None:
        verifier = VerifierAgent(ShortExactNaverClient(), OutOfRegionActivePermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="방호조사과",
                used_date="2026-06-05",
                place_name="오복정",
                address="",
                purpose="업무추진 간담회",
                amount=175000,
                normalized_place_name="오복정",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_EXACT_ADDRESSLESS", decision.reason_codes)
        self.assertIn("PERMIT_ADDRESS_CONFLICT_IGNORED", decision.reason_codes)
        self.assertEqual(decision.selected_candidate.name, "오복정")

    def test_short_exact_addressless_food_name_ignores_closed_different_address_permit(self) -> None:
        verifier = VerifierAgent(TogokNaverClient(), ClosedDifferentAddressPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="방호조사과",
                used_date="2026-06-05",
                place_name="토곡정",
                address="",
                purpose="경호안전 관련 간담회",
                amount=153000,
                normalized_place_name="토곡정",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_EXACT_ADDRESSLESS", decision.reason_codes)
        self.assertIn("PERMIT_NOT_ACTIVE_DIFFERENT_ADDRESS_IGNORED", decision.reason_codes)
        self.assertEqual(decision.selected_candidate.name, "토곡정")

    def test_non_food_purpose_rules_keep_snack_purchases_reviewable(self) -> None:
        self.assertEqual(non_food_purpose_reason("시정홍보 방문기념품 구입"), "NON_FOOD_PURPOSE_GIFT")
        self.assertEqual(non_food_purpose_reason("사무용품 구입"), "NON_FOOD_PURPOSE_OFFICE_SUPPLY")
        self.assertIsNone(non_food_purpose_reason("회의 다과 구입"))
        self.assertIsNone(non_food_purpose_reason("직원 격려 간식 구입"))

    def test_naver_search_queries_strip_companion_suffix(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="감사담당관",
            used_date="2026-06-05",
            place_name="19버거테이블 외 1",
            address="",
            purpose="간담",
            amount=70000,
            normalized_place_name="19버거테이블 외 1",
            normalized_address="",
        )

        self.assertEqual(_search_queries(row)[:2], ["19버거테이블 부산", "19버거테이블"])

    def test_cached_permit_client_stores_snapshot_and_api_log(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="방호조사과",
            used_date="2023-05-23",
            place_name="두레박국밥",
            address="",
            purpose="간담",
            amount=150000,
            normalized_place_name="두레박국밥",
            normalized_address="",
        )
        client = CountingPermitClient()

        with self.db.session() as conn:
            cached = CachedPermitClient(conn, client)
            first = cached.lookup(row)
            second = cached.lookup(row)
            api_logs = conn.execute("SELECT COUNT(*) AS c FROM api_call_logs").fetchone()["c"]
            snapshots = conn.execute("SELECT COUNT(*) AS c FROM permit_snapshots").fetchone()["c"]

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(client.calls, 1)
        self.assertEqual(api_logs, 1)
        self.assertEqual(snapshots, 1)


class AddresslessAdapter:
    source_key = "busan_city_expense_v1"

    def discover(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                source_url="fixture://busan/addressless",
                source_title="주소 없는 업무추진비",
                published_at="2026-06-05",
                content="addressless-live-fixture",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        return [
            RawExpenseRow(
                row_number=1,
                department_name="방호조사과",
                used_date="2023-05-23",
                place_name="두레박국밥",
                address="",
                purpose="간담",
                amount=150000,
            )
        ]


class GenericAddresslessAdapter:
    source_key = "busan_city_expense_v1"

    def discover(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                source_url="fixture://busan/generic-addressless",
                source_title="짧은 상호 업무추진비",
                published_at="2026-06-05",
                content="generic-addressless-fixture",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        return [
            RawExpenseRow(
                row_number=1,
                department_name="기획담당관",
                used_date="2026-06-05",
                place_name="씨드",
                address="",
                purpose="간담",
                amount=45000,
            )
        ]


class RepeatedAliasAdapter:
    source_key = "busan_city_expense_v1"

    def discover(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                source_url="fixture://busan/repeated-alias",
                source_title="반복 상호 업무추진비",
                published_at="2026-07-05",
                content="repeated-alias-fixture",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        return [
            RawExpenseRow(
                row_number=1,
                department_name="방호조사과",
                used_date="2026-07-01",
                place_name="두레박국밥",
                address="",
                purpose="간담",
                amount=123000,
            )
        ]


class GiftAliasAdapter:
    source_key = "busan_city_expense_v1"

    def discover(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                source_url="fixture://busan/gift-alias",
                source_title="비식품 기념품 구입",
                published_at="2026-08-05",
                content="gift-alias-fixture",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        return [
            RawExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-08-01",
                place_name="두레박국밥",
                address="",
                purpose="시정홍보 방문기념품 구입",
                amount=90000,
            )
        ]


class EmptyNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return []


class EmptyPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return None


class ExplodingNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        raise AssertionError("Naver client should not be called for non-food purpose rows")


class ExplodingPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        raise AssertionError("Permit client should not be called for non-food purpose rows")


class ExactNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-live-001",
                name="두레박국밥",
                category="restaurant",
                address="부산광역시 연제구 중앙대로 1001",
                road_address="부산광역시 연제구 중앙대로 1001",
                longitude=129.0756416,
                latitude=35.1795543,
            )
        ]


class GenericNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-generic-001",
                name="씨드",
                category="cafe",
                address="부산광역시 해운대구 우동 123",
                road_address="부산광역시 해운대구 센텀중앙로 97",
                longitude=129.129,
                latitude=35.173,
            )
        ]


class BranchNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-branch-001",
                name="본도시락 부산시청점",
                category="restaurant",
                address="부산광역시 연제구 연산동 1000",
                road_address="부산광역시 연제구 중앙대로 1001",
                longitude=129.0756416,
                latitude=35.1795543,
            )
        ]


class ShortExactNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-short-001",
                name="오복정",
                category="restaurant",
                address="부산광역시 연제구 연산동 482-10",
                road_address="부산광역시 연제구 고분로242번길 9",
                longitude=129.093,
                latitude=35.185,
            )
        ]


class TogokNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-togok-001",
                name="토곡정",
                category="restaurant",
                address="부산광역시 연제구 연산동 490-30 1층",
                road_address="부산광역시 연제구 토곡로 7 1층",
                longitude=129.101,
                latitude=35.186,
            )
        ]


class ExactPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-live-001",
            category="restaurant",
            business_status="active",
            address="부산광역시 연제구 중앙대로 1001",
            raw_response_json={"BPLC_NM": "두레박국밥"},
        )


class ClosedPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-closed-001",
            category="restaurant",
            business_status="closed",
            address="부산광역시 연제구 과정로 207",
            raw_response_json={"BPLC_NM": "두레박국밥"},
        )


class GenericPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-generic-001",
            category="restaurant",
            business_status="active",
            address="부산광역시 해운대구 센텀중앙로 97",
            raw_response_json={"BPLC_NM": "씨드"},
        )


class OutOfRegionActivePermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-active-other-region",
            category="restaurant",
            business_status="active",
            address="대전광역시 대덕구 석봉로58번길 74",
            raw_response_json={"BPLC_NM": "오복정"},
        )


class ClosedDifferentAddressPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-closed-different-address",
            category="restaurant",
            business_status="closed",
            address="부산광역시 연제구 과정로 112-1",
            raw_response_json={"BPLC_NM": "토곡정"},
        )


class BranchPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-branch-001",
            category="restaurant",
            business_status="active",
            address="부산광역시 연제구 중앙대로 1001",
            raw_response_json={"BPLC_NM": "본도시락 부산시청점"},
        )


class CountingPermitClient(ExactPermitClient):
    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        self.calls += 1
        return super().lookup(row)


if __name__ == "__main__":
    unittest.main()
