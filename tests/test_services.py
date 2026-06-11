from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.pipeline import DailyPipeline
from app.services import AppError, RequestContext, RestaurantService, naver_map_query
from app.source_catalog import iter_source_catalog


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.db.initialize()
        DailyPipeline(self.db).run()
        self.service = RestaurantService(self.db, review_rate_limit_per_hour=3)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _insert_manual_review_with_provider(self, suffix: str, place_name: str, amount: int) -> int:
        with self.db.session() as conn:
            doc = conn.execute(
                """
                INSERT INTO raw_documents
                  (institution_id, source_url, source_title, published_at, collected_at, content_hash)
                VALUES (1, ?, '수동승인 테스트', '2026-06-05', CURRENT_TIMESTAMP, ?)
                """,
                (f"fixture://manual-provider/{suffix}", f"manual-provider-{suffix}"),
            )
            expense = conn.execute(
                """
                INSERT INTO expense_records
                  (raw_document_id, institution_id, region_id, source_row_number, department_name,
                   used_date, place_name, purpose, amount, participants, payment_method,
                   original_row_json, normalized_place_name, normalized_purpose,
                   is_food_candidate, candidate_reason, row_hash)
                VALUES (?, 1, 1, 1, '총무과', '2026-06-05', ?, '업무협의 간담회',
                        ?, '4명', 'card', '{}', ?, '업무협의 간담회', 1,
                        'manual_provider_test', ?)
                """,
                (doc.lastrowid, place_name, amount, place_name, f"manual-provider-row-{suffix}"),
            )
            candidate = conn.execute(
                """
                INSERT INTO restaurant_candidates
                  (expense_record_id, institution_id, region_id, original_place_name,
                   normalized_place_name, original_address, normalized_address, used_date,
                   amount, place_major_category, status, verification_status,
                   manual_review_status, extraction_reason)
                VALUES (?, 1, 1, ?, ?, '', '', '2026-06-05', ?, 'cafe',
                        'needs_review', 'ambiguous', 'pending', 'manual_provider_test')
                """,
                (expense.lastrowid, place_name, place_name, amount),
            )
            review = conn.execute(
                """
                INSERT INTO manual_review_tasks (candidate_id, status, reason)
                VALUES (?, 'pending', 'AI_BOUNDARY_SCORE')
                """,
                (candidate.lastrowid,),
            )
            conn.execute(
                """
                INSERT INTO place_verifications
                  (candidate_id, provider, provider_place_id, provider_place_name,
                   provider_category, provider_address, provider_road_address,
                   normalized_provider_name, normalized_provider_address,
                   longitude, latitude, name_similarity, address_similarity,
                   is_name_match, is_address_match, is_coordinate_valid,
                   is_category_valid, verification_status, verification_reason,
                   raw_response_json)
                VALUES (?, 'naver', 'naver-starbucks-001', '스타벅스 부산시청점',
                        'cafe', '부산광역시 연제구 연산동 1000',
                        '부산광역시 연제구 중앙대로 1001', '스타벅스 부산시청점',
                        '부산광역시 연제구 연산동 1000', 129.0756416, 35.1795543,
                        0.91, 0.0, 1, 0, 1, 1, 'ambiguous',
                        'AI_BOUNDARY_SCORE', '{}')
                """,
                (candidate.lastrowid,),
            )
            return int(review.lastrowid)

    def test_map_ranking_category_and_search(self) -> None:
        all_restaurants = self.service.list_map_restaurants()
        cafes = self.service.list_map_restaurants(category="cafe")
        ranking = self.service.rankings()
        search = self.service.search("광안리")

        self.assertEqual(len(all_restaurants), 3)
        self.assertEqual(len(cafes), 1)
        self.assertEqual(cafes[0]["category_label"], "카페")
        self.assertGreaterEqual(ranking[0]["visit_count"], ranking[-1]["visit_count"])
        self.assertEqual(search[0]["name"], "광안리커피")
        for restaurant in all_restaurants:
            self.assertIsNotNone(restaurant["latitude"])
            self.assertIsNotNone(restaurant["longitude"])
            self.assertTrue(restaurant["naver_map_query"])
            self.assertTrue(restaurant["naver_map_url"].startswith("nmap://search?query="))

    def test_naver_map_query_strips_floor_for_map_link(self) -> None:
        query = naver_map_query("토곡정", "부산광역시 연제구 토곡로 7 1층")

        self.assertEqual(query, "토곡정 부산광역시 연제구 토곡로 7")

    def test_manual_approval_uses_provider_place_and_merges_same_naver_place(self) -> None:
        context = RequestContext(actor_id="admin")
        first_review = self._insert_manual_review_with_provider("one", "스타벅스 외 2", 50000)
        second_review = self._insert_manual_review_with_provider("two", "스타벅스커피", 70000)

        first = self.service.approve_new(first_review, context)
        second = self.service.approve_new(second_review, context)

        self.assertEqual(first["restaurant_id"], second["restaurant_id"])
        with self.db.session() as conn:
            restaurant = conn.execute(
                """
                SELECT canonical_name, normalized_name, naver_place_id, major_category
                FROM restaurants
                WHERE id = ?
                """,
                (first["restaurant_id"],),
            ).fetchone()
            link_count = conn.execute(
                """
                SELECT COUNT(*) AS c, SUM(amount) AS total
                FROM restaurant_expense_links
                WHERE restaurant_id = ?
                """,
                (first["restaurant_id"],),
            ).fetchone()
            aliases = conn.execute(
                """
                SELECT normalized_alias
                FROM alias_memory
                WHERE restaurant_id = ?
                """,
                (first["restaurant_id"],),
            ).fetchall()

        self.assertEqual(restaurant["canonical_name"], "스타벅스 부산시청점")
        self.assertEqual(restaurant["normalized_name"], "스타벅스 부산시청점")
        self.assertEqual(restaurant["naver_place_id"], "naver-starbucks-001")
        self.assertEqual(restaurant["major_category"], "cafe")
        self.assertEqual(link_count["c"], 2)
        self.assertEqual(link_count["total"], 120000)
        self.assertIn("스타벅스", {row["normalized_alias"] for row in aliases})

    def test_manual_reject_stores_reason_and_reviewer_note(self) -> None:
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("reject", "미상식당", 51000)

        self.service.reject_candidate(
            review_id,
            context,
            reason="ADDRESS_MISMATCH",
            reviewer_note="원문 주소와 네이버 후보 주소가 다름",
        )

        with self.db.session() as conn:
            row = conn.execute(
                """
                SELECT c.status, c.rejection_reason, c.review_note,
                       mrt.status AS task_status, mrt.reason, mrt.reviewer_note
                FROM manual_review_tasks mrt
                JOIN restaurant_candidates c ON c.id = mrt.candidate_id
                WHERE mrt.id = ?
                """,
                (review_id,),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT reason_codes_json
                FROM decision_audit_logs
                WHERE target_type = 'manual_review_task' AND target_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (review_id,),
            ).fetchone()

        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["rejection_reason"], "ADDRESS_MISMATCH")
        self.assertEqual(row["review_note"], "원문 주소와 네이버 후보 주소가 다름")
        self.assertEqual(row["task_status"], "rejected")
        self.assertEqual(row["reason"], "ADDRESS_MISMATCH")
        self.assertEqual(row["reviewer_note"], "원문 주소와 네이버 후보 주소가 다름")
        self.assertIn("ADDRESS_MISMATCH", audit["reason_codes_json"])

    def test_admin_review_queue_can_approve_selected_provider_candidate(self) -> None:
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("select", "가온비", 45000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]
            second = conn.execute(
                """
                INSERT INTO place_verifications
                  (candidate_id, provider, provider_place_id, provider_place_name,
                   provider_category, provider_address, provider_road_address,
                   normalized_provider_name, normalized_provider_address,
                   longitude, latitude, name_similarity, address_similarity,
                   is_name_match, is_address_match, is_coordinate_valid,
                   is_category_valid, verification_status, verification_reason,
                   raw_response_json)
                VALUES (?, 'naver', 'naver-gaonbi-002', 'cafe가온비',
                        'cafe', '부산광역시 서구 토성동5가 56-4',
                        '부산광역시 서구 구덕로 127', 'cafe가온비',
                        '부산광역시 서구 구덕로 127', 129.022, 35.096,
                        0.95, 0.0, 1, 0, 1, 1, 'ambiguous',
                        'AI_BOUNDARY_SCORE', '{}')
                """,
                (candidate_id,),
            )

        queue = self.service.admin_review_queue()
        item = next(row for row in queue if row["review_id"] == review_id)
        self.assertGreaterEqual(len(item["provider_candidates"]), 2)

        approved = self.service.approve_new(
            review_id,
            context,
            reviewer_note="카페가온비 후보 선택 승인",
            verification_id=int(second.lastrowid),
        )

        with self.db.session() as conn:
            restaurant = conn.execute(
                "SELECT canonical_name, naver_place_id FROM restaurants WHERE id = ?",
                (approved["restaurant_id"],),
            ).fetchone()
            candidate = conn.execute(
                """
                SELECT status, review_note
                FROM restaurant_candidates
                WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()

        self.assertEqual(restaurant["canonical_name"], "cafe가온비")
        self.assertEqual(restaurant["naver_place_id"], "naver-gaonbi-002")
        self.assertEqual(candidate["status"], "verified")
        self.assertEqual(candidate["review_note"], "카페가온비 후보 선택 승인")

    def test_source_registry_groups_priority_and_collection_counts(self) -> None:
        payload = self.service.source_registry()

        self.assertEqual(payload["summary"]["source_count"], len(iter_source_catalog()))
        self.assertGreaterEqual(payload["summary"]["by_status"]["crawl_target_ready"], 1)
        self.assertEqual(payload["groups"][0]["priority"], 1)
        self.assertEqual(payload["groups"][0]["group_key"], "busan_city_core")
        self.assertEqual(payload["groups"][0]["documents_collected"], 1)

    def test_review_rate_limit_report_and_admin_report_list(self) -> None:
        restaurant_id = self.service.list_map_restaurants()[0]["id"]
        context = RequestContext(ip="203.0.113.10")

        created = [
            self.service.add_review(
                restaurant_id,
                rating=5,
                body=f"좋았습니다 {index}",
                reviewer_label="테스터",
                context=context,
            )
            for index in range(3)
        ]
        with self.assertRaises(AppError) as raised:
            self.service.add_review(
                restaurant_id,
                rating=4,
                body="반복 리뷰",
                reviewer_label="테스터",
                context=context,
            )
        self.assertEqual(raised.exception.status, 429)

        report = self.service.report_review(created[0]["id"], "spam_or_abuse", context)
        self.assertEqual(report["status"], "open")
        self.assertEqual(len(self.service.review_reports()), 1)

    def test_sso_account_admin_merge(self) -> None:
        google = self.service.upsert_oauth_account("google", "google-sub-1", "사용자")
        naver = self.service.upsert_oauth_account("naver", "naver-sub-1", "사용자")
        source_id = naver["user"]["id"]
        target_id = google["user"]["id"]

        request = self.service.request_account_merge(
            source_id,
            target_id,
            "same human suspected",
            RequestContext(actor_id="admin"),
        )
        result = self.service.merge_account(source_id, target_id, RequestContext(actor_id="admin"))

        self.assertEqual(request["status"], "pending")
        self.assertEqual(result["result"], "merged")
        with self.db.session() as conn:
            source = conn.execute("SELECT status FROM users WHERE id = ?", (source_id,)).fetchone()
            accounts = conn.execute(
                "SELECT COUNT(*) AS c FROM oauth_accounts WHERE user_id = ?", (target_id,)
            ).fetchone()
        self.assertEqual(source["status"], "merged")
        self.assertEqual(accounts["c"], 2)


if __name__ == "__main__":
    unittest.main()
