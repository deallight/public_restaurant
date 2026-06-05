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
)
from app.database import Database
from app.integrations import DataGoKrPermitClient
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

    def test_needs_review_persists_naver_candidate_evidence(self) -> None:
        result = DailyPipeline(
            self.db,
            adapter=AddresslessAdapter(),
            verifier=VerifierAgent(ExactNaverClient(), EmptyPermitClient()),
        ).run()

        self.assertEqual(result["summary"]["needs_review"], 1)
        with self.db.session() as conn:
            verification = conn.execute(
                """
                SELECT provider_place_name, verification_status, name_similarity, address_similarity
                FROM place_verifications
                """
            ).fetchone()

        self.assertEqual(verification["provider_place_name"], "두레박국밥")
        self.assertEqual(verification["verification_status"], "ambiguous")
        self.assertEqual(round(verification["name_similarity"], 2), 1.0)
        self.assertEqual(round(verification["address_similarity"], 2), 0.0)

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


class EmptyNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return []


class EmptyPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return None


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


class CountingPermitClient(ExactPermitClient):
    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        self.calls += 1
        return super().lookup(row)


if __name__ == "__main__":
    unittest.main()
