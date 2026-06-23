from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.pipeline import DailyPipeline
from app.agents import PlaceCandidate
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
            self.assertTrue(restaurant["naver_map_url"].startswith("https://map.naver.com/p/search/"))

    def test_naver_map_query_strips_floor_for_map_link(self) -> None:
        query = naver_map_query("토곡정", "부산광역시 연제구 토곡로 7 1층")

        self.assertEqual(query, "토곡정 부산광역시 연제구 토곡로 7")

    def test_naver_map_query_stops_after_road_or_lot_number(self) -> None:
        self.assertEqual(
            naver_map_query(
                "큐제",
                "부산광역시 연제구 중앙대로1043번길 34 시청역 sk뷰 근린생활시설 112호",
            ),
            "큐제 부산광역시 연제구 중앙대로1043번길 34",
        )
        self.assertEqual(
            naver_map_query(
                "빕스 부산서면점",
                "부산광역시 부산진구 중앙대로 654 서면 푸르지오시티시그니처 3층",
            ),
            "빕스 부산서면점 부산광역시 부산진구 중앙대로 654",
        )
        self.assertEqual(
            naver_map_query("큐제", "부산광역시 연제구 연산동 490-30 1층 큐제"),
            "큐제 부산광역시 연제구 연산동 490-30",
        )

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

    def test_manual_review_candidate_row_can_be_updated(self) -> None:
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("edit", "미락", 90000)

        updated = self.service.update_review_candidate(
            review_id,
            context,
            original_place_name="미락",
            original_address="부산광역시 연제구 거제천로 78",
            place_major_category="restaurant",
        )

        queue_item = next(row for row in self.service.admin_review_queue() if row["review_id"] == review_id)
        with self.db.session() as conn:
            candidate = conn.execute(
                """
                SELECT original_place_name, original_address, normalized_address,
                       review_place_name, review_address, review_normalized_address,
                       place_major_category, review_major_category, status, manual_review_status
                FROM restaurant_candidates
                WHERE id = ?
                """,
                (updated["candidate_id"],),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT reason_codes_json
                FROM decision_audit_logs
                WHERE target_type = 'restaurant_candidate'
                  AND target_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (updated["candidate_id"],),
            ).fetchone()

        self.assertEqual(updated["result"], "updated")
        self.assertEqual(candidate["original_address"], "")
        self.assertEqual(candidate["normalized_address"], "")
        self.assertEqual(candidate["review_place_name"], "미락")
        self.assertEqual(candidate["review_address"], "부산광역시 연제구 거제천로 78")
        self.assertEqual(candidate["review_normalized_address"], "부산광역시 연제구 거제천로 78")
        self.assertEqual(candidate["place_major_category"], "cafe")
        self.assertEqual(candidate["review_major_category"], "restaurant")
        self.assertEqual(candidate["status"], "needs_review")
        self.assertEqual(candidate["manual_review_status"], "pending")
        self.assertEqual(queue_item["original_address"], "")
        self.assertEqual(queue_item["review_address"], "부산광역시 연제구 거제천로 78")
        self.assertEqual(queue_item["effective_address"], "부산광역시 연제구 거제천로 78")
        self.assertIn("ADMIN_CANDIDATE_EDIT", audit["reason_codes_json"])

    def test_admin_candidate_groups_include_all_review_states(self) -> None:
        context = RequestContext(actor_id="admin")
        approved_review = self._insert_manual_review_with_provider("admin-approved", "승인식당", 30000)
        rejected_review = self._insert_manual_review_with_provider("admin-rejected", "반려식당", 40000)
        pending_review = self._insert_manual_review_with_provider("admin-pending", "검토식당", 50000)
        self.service.approve_new(approved_review, context)
        self.service.reject_candidate(rejected_review, context, reason="AMBIGUOUS_BRANCH")
        with self.db.session() as conn:
            pending_candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (pending_review,),
            ).fetchone()["candidate_id"]

        groups = self.service.admin_candidates(limit=100)["groups"]

        self.assertTrue(any(row["original_place_name"] == "승인식당" for row in groups["verified"]["items"]))
        self.assertTrue(any(row["original_place_name"] == "반려식당" for row in groups["rejected"]["items"]))
        self.assertTrue(any(row["candidate_id"] == pending_candidate_id for row in groups["needs_review"]["items"]))

    def test_admin_candidate_pagination_uses_status_offsets(self) -> None:
        context = RequestContext(actor_id="admin")
        first_review = self._insert_manual_review_with_provider("page-one", "페이지식당1", 30000)
        second_review = self._insert_manual_review_with_provider("page-two", "페이지식당2", 31000)
        self.service.approve_new(first_review, context)
        self.service.approve_new(second_review, context)

        first_page = self.service.admin_candidates(limit=1, offsets={"verified": 0})["groups"]["verified"]
        second_page = self.service.admin_candidates(limit=1, offsets={"verified": 1})["groups"]["verified"]

        self.assertEqual(len(first_page["items"]), 1)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertNotEqual(first_page["items"][0]["candidate_id"], second_page["items"][0]["candidate_id"])
        self.assertTrue(first_page["has_next"])
        self.assertTrue(second_page["has_prev"])

    def test_admin_candidate_update_keeps_stable_list_order(self) -> None:
        context = RequestContext(actor_id="admin")
        older_review = self._insert_manual_review_with_provider("stable-one", "안정식당1", 30000)
        self._insert_manual_review_with_provider("stable-two", "안정식당2", 31000)
        with self.db.session() as conn:
            older_candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (older_review,),
            ).fetchone()["candidate_id"]

        before = [
            row["candidate_id"]
            for row in self.service.admin_candidates(limit=100, q="안정식당")["groups"]["needs_review"]["items"]
        ]
        self.service.update_admin_candidate(
            older_candidate_id,
            context,
            review_place_name="안정식당2",
            review_address="부산 연제구 중앙대로 1001",
            review_major_category="restaurant",
            target_status="needs_review",
        )
        after = [
            row["candidate_id"]
            for row in self.service.admin_candidates(limit=100, q="안정식당")["groups"]["needs_review"]["items"]
        ]

        self.assertEqual(before, after)

    def test_admin_candidate_sort_can_be_selected(self) -> None:
        low_review = self._insert_manual_review_with_provider("sort-low", "정렬식당낮음", 10000)
        high_review = self._insert_manual_review_with_provider("sort-high", "정렬식당높음", 99000)
        with self.db.session() as conn:
            low_candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (low_review,),
            ).fetchone()["candidate_id"]
            high_candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (high_review,),
            ).fetchone()["candidate_id"]

        group = self.service.admin_candidates(
            limit=10,
            q="정렬식당",
            sort="amount_desc",
        )["groups"]["needs_review"]

        self.assertEqual(group["items"][0]["candidate_id"], high_candidate_id)
        self.assertEqual(group["items"][1]["candidate_id"], low_candidate_id)

    def test_admin_candidate_search_filters_database_rows(self) -> None:
        context = RequestContext(actor_id="admin")
        first_review = self._insert_manual_review_with_provider("search-target", "검색대상식당", 30000)
        second_review = self._insert_manual_review_with_provider("search-other", "다른식당", 31000)
        self.service.approve_new(first_review, context)
        self.service.approve_new(second_review, context)

        groups = self.service.admin_candidates(limit=100, q="검색대상")["groups"]
        names = [row["original_place_name"] for row in groups["verified"]["items"]]

        self.assertIn("검색대상식당", names)
        self.assertNotIn("다른식당", names)

    def test_provider_candidates_returns_up_to_ten(self) -> None:
        review_id = self._insert_manual_review_with_provider("ten-providers", "후보많은식당", 30000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]
            for index in range(10):
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
                    VALUES (?, 'naver', ?, ?, 'restaurant', '부산 주소', '부산 주소',
                            ?, '부산 주소', 129.0, 35.0, ?, 0.8, 1, 1, 1, 1,
                            'ambiguous', 'TEST', '{}')
                    """,
                    (
                        candidate_id,
                        f"naver-extra-{index}",
                        f"후보많은식당 {index}",
                        f"후보많은식당 {index}",
                        0.8 + index / 100,
                    ),
                )

        item = next(
            row
            for row in self.service.admin_candidates(limit=100)["groups"]["needs_review"]["items"]
            if row["candidate_id"] == candidate_id
        )

        self.assertEqual(len(item["provider_candidates"]), 10)

    def test_admin_candidate_direct_approval_uses_edited_db_row_address(self) -> None:
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("direct-approve", "고향보리밥", 36000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]

        result = self.service.update_admin_candidate(
            candidate_id,
            context,
            review_place_name="고향보리밥",
            review_address="부산 사하구 낙동대로 521-1",
            review_major_category="restaurant",
            target_status="verified",
            reviewer_note="DB 행 기준 승인",
        )

        with self.db.session() as conn:
            restaurant = conn.execute(
                """
                SELECT r.canonical_name, r.address, r.naver_place_id, r.map_exposure_status
                FROM restaurants r
                JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
                WHERE rel.candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            candidate = conn.execute(
                """
                SELECT original_address, review_address, status, manual_review_status
                FROM restaurant_candidates
                WHERE id = ?
                """,
                (candidate_id,),
            ).fetchone()

        self.assertEqual(result["result"], "verified")
        self.assertEqual(restaurant["canonical_name"], "고향보리밥")
        self.assertEqual(restaurant["address"], "부산 사하구 낙동대로 521-1")
        self.assertTrue(restaurant["naver_place_id"].startswith("manual-"))
        self.assertEqual(restaurant["map_exposure_status"], "visible")
        self.assertEqual(candidate["original_address"], "")
        self.assertEqual(candidate["review_address"], "부산 사하구 낙동대로 521-1")
        self.assertEqual(candidate["status"], "verified")
        self.assertEqual(candidate["manual_review_status"], "approved")

    def test_admin_candidate_direct_approval_uses_selected_provider_and_merges(self) -> None:
        context = RequestContext(actor_id="admin")
        first_review = self._insert_manual_review_with_provider("direct-provider-one", "스타벅스 외 2", 50000)
        second_review = self._insert_manual_review_with_provider("direct-provider-two", "스타벅스커피", 70000)
        with self.db.session() as conn:
            first = conn.execute(
                """
                SELECT mrt.candidate_id, pv.id AS verification_id
                FROM manual_review_tasks mrt
                JOIN place_verifications pv ON pv.candidate_id = mrt.candidate_id
                WHERE mrt.id = ?
                """,
                (first_review,),
            ).fetchone()
            second = conn.execute(
                """
                SELECT mrt.candidate_id, pv.id AS verification_id
                FROM manual_review_tasks mrt
                JOIN place_verifications pv ON pv.candidate_id = mrt.candidate_id
                WHERE mrt.id = ?
                """,
                (second_review,),
            ).fetchone()

        first_result = self.service.update_admin_candidate(
            int(first["candidate_id"]),
            context,
            review_place_name="스타벅스 외 2",
            review_address="",
            review_major_category="cafe",
            target_status="verified",
            verification_id=int(first["verification_id"]),
        )
        second_result = self.service.update_admin_candidate(
            int(second["candidate_id"]),
            context,
            review_place_name="스타벅스커피",
            review_address="",
            review_major_category="cafe",
            target_status="verified",
            verification_id=int(second["verification_id"]),
        )

        with self.db.session() as conn:
            restaurant = conn.execute(
                """
                SELECT canonical_name, address, road_address, naver_place_id, longitude, latitude
                FROM restaurants
                WHERE id = ?
                """,
                (first_result["restaurant_id"],),
            ).fetchone()
            link_count = conn.execute(
                "SELECT COUNT(*) AS c FROM restaurant_expense_links WHERE restaurant_id = ?",
                (first_result["restaurant_id"],),
            ).fetchone()["c"]
            first_candidate = conn.execute(
                """
                SELECT review_place_name, review_address, review_major_category
                FROM restaurant_candidates
                WHERE id = ?
                """,
                (first["candidate_id"],),
            ).fetchone()

        self.assertEqual(first_result["restaurant_id"], second_result["restaurant_id"])
        self.assertEqual(restaurant["canonical_name"], "스타벅스 부산시청점")
        self.assertEqual(restaurant["road_address"], "부산광역시 연제구 중앙대로 1001")
        self.assertEqual(restaurant["naver_place_id"], "naver-starbucks-001")
        self.assertAlmostEqual(restaurant["longitude"], 129.0756416)
        self.assertAlmostEqual(restaurant["latitude"], 35.1795543)
        self.assertEqual(link_count, 2)
        self.assertEqual(first_candidate["review_place_name"], "스타벅스 부산시청점")
        self.assertEqual(first_candidate["review_address"], "부산광역시 연제구 중앙대로 1001")
        self.assertEqual(first_candidate["review_major_category"], "cafe")

    def test_admin_candidate_geocode_button_updates_coordinates_for_later_approval(self) -> None:
        service = RestaurantService(self.db, geocoding_client=FakeGeocodingClient())
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("direct-geocode", "고향보리밥", 36000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]

        geocoded = service.geocode_admin_candidate(
            candidate_id,
            context,
            review_place_name="고향보리밥",
            review_address="부산 사하구 낙동대로 521-1",
            review_major_category="restaurant",
        )
        approved = service.update_admin_candidate(
            candidate_id,
            context,
            review_place_name="고향보리밥",
            review_address="부산 사하구 낙동대로 521-1",
            review_major_category="restaurant",
            target_status="verified",
        )

        with self.db.session() as conn:
            restaurant = conn.execute(
                """
                SELECT r.longitude, r.latitude, r.address
                FROM restaurants r
                JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
                WHERE rel.candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            verification = conn.execute(
                """
                SELECT provider, provider_road_address
                FROM place_verifications
                WHERE candidate_id = ? AND provider = 'naver_geocode'
                """,
                (candidate_id,),
            ).fetchone()

        self.assertEqual(geocoded["geocoding"]["status"], "success")
        self.assertEqual(approved["geocoding"]["status"], "skipped")
        self.assertEqual(approved["geocoding"]["reason"], "split_to_geocode_button")
        self.assertEqual(restaurant["address"], "부산 사하구 낙동대로 521-1")
        self.assertAlmostEqual(restaurant["longitude"], 128.966, places=3)
        self.assertAlmostEqual(restaurant["latitude"], 35.104, places=3)
        self.assertEqual(verification["provider_road_address"], "부산광역시 사하구 낙동대로 521-1")

    def test_admin_candidate_geocode_button_updates_existing_approved_restaurant_coordinates(self) -> None:
        service = RestaurantService(self.db, geocoding_client=FakeGeocodingClient())
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("approved-geocode", "고향보리밥", 36000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]

        service.update_admin_candidate(
            candidate_id,
            context,
            review_place_name="고향보리밥",
            review_address="부산 사하구 낙동대로 521-1",
            review_major_category="restaurant",
            target_status="verified",
        )
        geocoded = service.geocode_admin_candidate(
            candidate_id,
            context,
            review_place_name="고향보리밥",
            review_address="부산 사하구 낙동대로 521-1",
            review_major_category="restaurant",
        )

        with self.db.session() as conn:
            restaurant = conn.execute(
                """
                SELECT r.longitude, r.latitude, r.road_address
                FROM restaurants r
                JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
                WHERE rel.candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()

        self.assertEqual(geocoded["geocoding"]["status"], "success")
        self.assertEqual(geocoded["geocoding"]["updated_restaurants"], 1)
        self.assertAlmostEqual(restaurant["longitude"], 128.966, places=3)
        self.assertAlmostEqual(restaurant["latitude"], 35.104, places=3)
        self.assertEqual(restaurant["road_address"], "부산광역시 사하구 낙동대로 521-1")

    def test_admin_candidate_update_refreshes_naver_provider_candidates(self) -> None:
        service = RestaurantService(self.db, naver_client=FakeNaverProviderClient())
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("refresh-provider", "고향보리밥", 36000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]

        result = service.update_admin_candidate(
            candidate_id,
            context,
            review_place_name="고향보리밥",
            review_address="부산 사하구 낙동대로 521-1",
            review_major_category="restaurant",
            target_status="needs_review",
        )

        item = next(
            row
            for row in service.admin_candidates(limit=100, q="고향보리밥")["groups"]["needs_review"]["items"]
            if row["candidate_id"] == candidate_id
        )
        provider_names = [candidate["provider_place_name"] for candidate in item["provider_candidates"]]

        self.assertEqual(result["provider_refresh"]["status"], "success")
        self.assertIn("고향보리밥", provider_names)

    def test_admin_candidate_can_move_approved_item_to_rejected(self) -> None:
        context = RequestContext(actor_id="admin")
        review_id = self._insert_manual_review_with_provider("move-rejected", "상태변경식당", 61000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]
        self.service.update_admin_candidate(
            candidate_id,
            context,
            review_place_name="상태변경식당",
            review_address="부산 연제구 중앙대로 1001",
            review_major_category="restaurant",
            target_status="verified",
        )

        result = self.service.update_admin_candidate(
            candidate_id,
            context,
            review_place_name="상태변경식당",
            review_address="부산 연제구 중앙대로 1001",
            review_major_category="restaurant",
            target_status="rejected",
            rejection_reason="AMBIGUOUS_BRANCH",
        )

        with self.db.session() as conn:
            candidate = conn.execute(
                "SELECT status, manual_review_status, rejection_reason FROM restaurant_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            link_count = conn.execute(
                "SELECT COUNT(*) AS count FROM restaurant_expense_links WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()["count"]

        self.assertEqual(result["result"], "rejected")
        self.assertEqual(candidate["status"], "rejected")
        self.assertEqual(candidate["manual_review_status"], "rejected")
        self.assertEqual(candidate["rejection_reason"], "AMBIGUOUS_BRANCH")
        self.assertEqual(link_count, 0)

    def test_collection_progress_percent_excludes_failed_documents(self) -> None:
        item = self.service._collection_progress_item(
            "디지털경제실",
            {
                "total_count": 5,
                "pending_count": 0,
                "processing_count": 0,
                "collected_count": 1,
                "duplicate_count": 2,
                "failed_count": 2,
            },
        )

        self.assertEqual(item["processed"], 5)
        self.assertEqual(item["successful"], 3)
        self.assertEqual(item["stored"], 3)
        self.assertEqual(item["failed"], 2)
        self.assertEqual(item["percent"], 60.0)

    def test_admin_document_title_uses_expense_title_in_department_path(self) -> None:
        self.assertEqual(
            self.service._admin_document_title(
                "(방호조사과)2026년",
                "1분기 업무추진비 집행내역(방호조사과) 소방재난본부 > 방호조사과",
            ),
            "1분기 업무추진비 집행내역(방호조사과)",
        )

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


class FakeGeocodingClient:
    def geocode(self, address: str) -> dict:
        return {
            "roadAddress": "부산광역시 사하구 낙동대로 521-1",
            "x": "128.966",
            "y": "35.104",
        }


class FakeNaverProviderClient:
    def search_local(self, row) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-gohyang-busan",
                name="고향보리밥",
                category="restaurant",
                address="부산 사하구 하단동",
                road_address="부산 사하구 낙동대로 521-1",
                longitude=128.966,
                latitude=35.104,
            )
        ]


if __name__ == "__main__":
    unittest.main()
