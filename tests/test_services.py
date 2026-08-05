from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.database import Database
from app.pipeline import DailyPipeline
from app.agents import PlaceCandidate
from app.services import AppError, RequestContext, RestaurantService, naver_map_query
from app.source_catalog import iter_source_catalog


class FakeReviewSummaryClient:
    provider = "fake"
    model = "fake-review-summary"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict] = []

    def summarize(self, restaurant_name: str, reviews: list[dict]) -> str:
        self.calls.append(
            {
                "restaurant_name": restaurant_name,
                "review_count": len(reviews),
            }
        )
        if self.fail:
            raise RuntimeError("temporary provider failure")
        return f"{restaurant_name}의 공개 리뷰 {len(reviews)}개를 요약했습니다."


class FakeRestaurantImageClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    def search_restaurant_images(
        self,
        name: str,
        address: str,
        limit: int = 4,
    ) -> list[dict]:
        self.calls.append({"name": name, "address": address})
        if self.fail:
            raise RuntimeError("temporary image provider failure")
        return [
            {
                "thumbnail_url": f"https://search.pstatic.net/example-{index}.jpg",
                "source_url": f"https://example.com/example-{index}.jpg",
                "title": f"{name} 사진 {index}",
                "provider": "naver_image_search",
                "is_naver_place_image": index == 1,
            }
            for index in range(1, limit + 1)
        ]


class ServiceTests(unittest.TestCase):
    PNG_1X1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
    )

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
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO restaurants
                  (region_id, canonical_name, normalized_name, major_category, address,
                   road_address, normalized_address, longitude, latitude,
                   verification_status, map_exposure_status)
                VALUES (1, '서울테스트식당', '서울테스트식당', 'restaurant',
                        '서울특별시 중구 세종대로 110', '서울특별시 중구 세종대로 110',
                        '서울특별시 중구 세종대로 110', 126.978, 37.5665,
                        'success', 'visible')
                """
            )
            conn.execute(
                """
                INSERT INTO restaurants
                  (region_id, canonical_name, normalized_name, major_category, address,
                   road_address, normalized_address, longitude, latitude,
                   verification_status, map_exposure_status)
                VALUES (1, '서울이름부산식당', '서울이름부산식당', 'restaurant',
                        '부산광역시 연제구 중앙대로 1001', '부산광역시 연제구 중앙대로 1001',
                        '부산광역시 연제구 중앙대로 1001', 129.0756416, 35.1795543,
                        'success', 'visible')
                """
            )
        all_restaurants = self.service.list_map_restaurants()
        cafes = self.service.list_map_restaurants(category="cafe")
        ranking = self.service.rankings()
        search = self.service.search("광안리")
        region_search = self.service.search("수영구")
        bounded_region_search = self.service.list_map_restaurants(
            q="부산광역시 수영구",
            search_mode="address",
            bounds="35.145,129.108,35.162,129.13",
        )
        city_search = self.service.list_map_restaurants(q="부산광역시")
        default_seoul_search = self.service.list_map_restaurants(q="서울")
        outside_city_search = self.service.list_map_restaurants(q="서울", search_mode="address")

        self.assertEqual(len(all_restaurants), 5)
        self.assertEqual(len(cafes), 1)
        self.assertEqual(cafes[0]["category_label"], "카페")
        self.assertGreaterEqual(ranking[0]["visit_count"], ranking[-1]["visit_count"])
        self.assertEqual(search[0]["name"], "광안리커피")
        self.assertEqual(region_search[0]["name"], "광안리커피")
        self.assertEqual([restaurant["name"] for restaurant in bounded_region_search], ["광안리커피"])
        self.assertNotIn("서울테스트식당", [restaurant["name"] for restaurant in city_search])
        self.assertIn("서울이름부산식당", [restaurant["name"] for restaurant in default_seoul_search])
        self.assertEqual([restaurant["name"] for restaurant in outside_city_search], ["서울테스트식당"])
        fixture_restaurant = next(
            restaurant for restaurant in all_restaurants
            if restaurant["name"] == "부산돼지국밥 시청점"
        )
        detail = self.service.get_restaurant(int(fixture_restaurant["id"]))
        self.assertEqual(
            detail["visits"],
            [
                {
                    "visited_at": "2026-01-14",
                    "institution_name": "부산광역시청",
                    "purpose": "현안 업무 협의 간담",
                }
            ],
        )
        self.assertNotIn("department_name", detail["visits"][0])
        for restaurant in all_restaurants:
            self.assertIsNotNone(restaurant["latitude"])
            self.assertIsNotNone(restaurant["longitude"])
            self.assertTrue(restaurant["naver_map_query"])
            self.assertTrue(restaurant["naver_map_url"].startswith("https://map.naver.com/p/search/"))

    def test_map_region_search_excludes_name_only_matches(self) -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO restaurants
                  (region_id, canonical_name, normalized_name, major_category, address,
                   road_address, normalized_address, longitude, latitude,
                   verification_status, map_exposure_status)
                VALUES (1, '기장꼼장어', '기장꼼장어', 'restaurant',
                        '부산광역시 연제구 중앙대로 1001', '부산광역시 연제구 중앙대로 1001',
                        '부산광역시 연제구 중앙대로 1001', 129.0756416, 35.1795543,
                        'success', 'visible')
                """
            )
            conn.execute(
                """
                INSERT INTO restaurants
                  (region_id, canonical_name, normalized_name, major_category, address,
                   road_address, normalized_address, longitude, latitude,
                   verification_status, map_exposure_status)
                VALUES (1, '기장바다식당', '기장바다식당', 'restaurant',
                        '부산광역시 기장군 기장읍 기장해안로 100',
                        '부산광역시 기장군 기장읍 기장해안로 100',
                        '부산광역시 기장군 기장읍 기장해안로 100', 129.222312, 35.244498,
                        'success', 'visible')
                """
            )

        default_search_names = [
            restaurant["name"] for restaurant in self.service.list_map_restaurants(q="기장")
        ]
        region_search_names = [
            restaurant["name"]
            for restaurant in self.service.list_map_restaurants(
                q="부산광역시 기장군",
                search_mode="address",
                bounds="35.14,129.11,35.39,129.35",
            )
        ]

        self.assertIn("기장꼼장어", default_search_names)
        self.assertIn("기장바다식당", default_search_names)
        self.assertNotIn("기장꼼장어", region_search_names)
        self.assertEqual(region_search_names, ["기장바다식당"])

    def test_map_restaurants_filters_by_ten_unit_visit_count(self) -> None:
        with self.db.session() as conn:
            restaurant_id = conn.execute(
                "SELECT id FROM restaurants WHERE canonical_name = ?",
                ("부산돼지국밥 시청점",),
            ).fetchone()["id"]
            for index in range(9):
                doc = conn.execute(
                    """
                    INSERT INTO raw_documents
                      (institution_id, source_url, source_title, published_at, collected_at, content_hash)
                    VALUES (1, ?, '방문횟수 필터 테스트', '2026-06-05', CURRENT_TIMESTAMP, ?)
                    """,
                    (f"fixture://visit-filter/{index}", f"visit-filter-{index}"),
                )
                expense = conn.execute(
                    """
                    INSERT INTO expense_records
                      (raw_document_id, institution_id, region_id, source_row_number, department_name,
                       used_date, place_name, purpose, amount, participants, payment_method,
                       original_row_json, normalized_place_name, normalized_purpose,
                       is_food_candidate, candidate_reason, row_hash)
                    VALUES (?, 1, 1, ?, '총무과', '2026-06-05', '부산돼지국밥 시청점',
                            '업무협의 간담회', 10000, '4명', 'card', '{}',
                            '부산돼지국밥 시청점', '업무협의 간담회', 1,
                            'visit_filter_test', ?)
                    """,
                    (doc.lastrowid, index + 10, f"visit-filter-row-{index}"),
                )
                candidate = conn.execute(
                    """
                    INSERT INTO restaurant_candidates
                      (expense_record_id, institution_id, region_id, original_place_name,
                       normalized_place_name, original_address, normalized_address, used_date,
                       amount, place_major_category, status, verification_status,
                       manual_review_status, extraction_reason)
                    VALUES (?, 1, 1, '부산돼지국밥 시청점', '부산돼지국밥 시청점',
                            '부산광역시 연제구 중앙대로 1001',
                            '부산광역시 연제구 중앙대로 1001', '2026-06-05',
                            10000, 'restaurant', 'verified', 'success',
                            'approved', 'visit_filter_test')
                    """,
                    (expense.lastrowid,),
                )
                conn.execute(
                    """
                    INSERT INTO restaurant_expense_links
                      (restaurant_id, expense_record_id, candidate_id, used_date, amount, link_reason)
                    VALUES (?, ?, ?, '2026-06-05', 10000, 'visit_filter_test')
                    """,
                    (restaurant_id, expense.lastrowid, candidate.lastrowid),
                )

        filtered = self.service.list_map_restaurants(min_visit_count=10)
        names = [restaurant["name"] for restaurant in filtered]

        self.assertEqual(names, ["부산돼지국밥 시청점"])
        self.assertEqual(filtered[0]["visit_count"], 10)
        with self.assertRaises(AppError):
            self.service.list_map_restaurants(min_visit_count=15)

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

    def test_admin_candidates_pending_status_selects_unverified_candidates(self) -> None:
        review_id = self._insert_manual_review_with_provider("pending-select", "검증대기식당", 30000)
        with self.db.session() as conn:
            candidate_id = conn.execute(
                "SELECT candidate_id FROM manual_review_tasks WHERE id = ?",
                (review_id,),
            ).fetchone()["candidate_id"]
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET verification_status = 'not_requested',
                    review_note = 'PENDING_VERIFICATION'
                WHERE id = ?
                """,
                (candidate_id,),
            )
            conn.execute(
                """
                UPDATE manual_review_tasks
                SET reason = 'PENDING_VERIFICATION'
                WHERE candidate_id = ?
                """,
                (candidate_id,),
            )

        payload = self.service.admin_candidates(
            status="pending",
            start_date="2026-06-01",
            end_date="2026-06-30",
            limit=10,
        )
        selected = payload["selected"]

        self.assertEqual(selected["label"], "검증 대기")
        self.assertEqual(selected["total"], 1)
        self.assertEqual(selected["items"][0]["candidate_id"], candidate_id)
        self.assertEqual(selected["items"][0]["verification_status"], "not_requested")
        self.assertEqual(payload["groups"]["needs_review"]["pending_total"], 1)
        self.assertEqual(payload["groups"]["needs_review"]["manual_total"], 0)
        self.assertEqual(payload["groups"]["needs_review"]["total"], 0)
        self.assertFalse(payload["groups"]["needs_review"]["items"])

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

    def test_ai_summary_first_at_five_then_every_ten_reviews_after_one_hour(self) -> None:
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        client = FakeReviewSummaryClient()
        current_time = [datetime(2026, 7, 24, 1, 0, tzinfo=timezone.utc)]
        service = RestaurantService(
            self.db,
            review_rate_limit_per_hour=100,
            ai_summary_client=client,
            ai_summary_now=lambda: current_time[0],
        )

        for index in range(4):
            service.add_review(
                restaurant_id,
                5,
                f"첫 요약 전 리뷰 {index}",
                "테스터",
                RequestContext(ip=f"203.0.113.{index + 1}"),
            )
        self.assertEqual(client.calls, [])

        service.add_review(
            restaurant_id,
            4,
            "다섯 번째 공개 리뷰",
            "테스터",
            RequestContext(ip="203.0.113.5"),
        )
        self.assertEqual([call["review_count"] for call in client.calls], [5])
        first_detail = service.get_restaurant(restaurant_id)
        self.assertEqual(first_detail["visit_count"], 1)
        self.assertEqual(first_detail["ai_summary"]["summarized_review_count"], 5)
        self.assertEqual(first_detail["ai_summary"]["next_summary_review_count"], 15)

        for index in range(5, 15):
            service.add_review(
                restaurant_id,
                4,
                f"추가 공개 리뷰 {index}",
                "테스터",
                RequestContext(ip=f"198.51.100.{index + 1}"),
            )
        self.assertEqual(len(client.calls), 1)
        pending_detail = service.get_restaurant(restaurant_id)
        self.assertTrue(pending_detail["ai_summary"]["refresh_pending"])

        current_time[0] += timedelta(hours=1)
        refreshed_detail = service.get_restaurant(restaurant_id)
        self.assertEqual([call["review_count"] for call in client.calls], [5, 15])
        self.assertEqual(refreshed_detail["ai_summary"]["summarized_review_count"], 15)
        self.assertFalse(refreshed_detail["ai_summary"]["refresh_pending"])

        service.get_restaurant(restaurant_id)
        self.assertEqual(len(client.calls), 2)
        with self.db.session() as conn:
            groq_logs = conn.execute(
                "SELECT success FROM api_call_logs WHERE provider = 'groq' ORDER BY id"
            ).fetchall()
        self.assertEqual([int(row["success"]) for row in groq_logs], [1, 1])

    def test_restaurant_detail_omits_images_and_skips_search_provider(self) -> None:
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        client = FakeRestaurantImageClient()
        service = RestaurantService(self.db, restaurant_image_client=client)

        detail = service.get_restaurant(restaurant_id)

        self.assertEqual(detail["restaurant_images"], [])
        self.assertIsNone(detail["restaurant_image"])
        self.assertEqual(client.calls, [])
        with self.db.session() as conn:
            image_log = conn.execute(
                "SELECT success FROM api_call_logs WHERE provider = 'naver_image_search'"
            ).fetchone()
        self.assertIsNone(image_log)

    def test_api_usage_metrics_uses_latest_log_per_provider(self) -> None:
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO api_call_logs
                  (provider, endpoint, request_hash, success, status_code,
                   error_message, called_at)
                VALUES ('provider-a', 'lookup', 'provider-a-old', 0, 503,
                        'temporary failure', '2026-07-26T23:59:00')
                """
            )
            conn.execute(
                """
                INSERT INTO api_call_logs
                  (provider, endpoint, request_hash, success, status_code,
                   error_message, called_at)
                VALUES ('provider-b', 'lookup', 'provider-b-latest', 0, 429,
                        'quota exceeded', '2026-07-27T00:02:00')
                """
            )
            conn.execute(
                """
                INSERT INTO api_call_logs
                  (provider, endpoint, request_hash, success, status_code,
                   error_message, called_at)
                VALUES ('provider-a', 'lookup', 'provider-a-latest', 1, 200,
                        NULL, '2026-07-27T00:03:00')
                """
            )

        metrics = self.service.api_usage_metrics("2026-07-27", "2026-07")

        self.assertEqual(metrics["provider-a"]["day_count"], 1)
        self.assertEqual(metrics["provider-a"]["month_count"], 2)
        self.assertTrue(metrics["provider-a"]["last_success"])
        self.assertEqual(metrics["provider-a"]["last_status_code"], 200)
        self.assertIsNone(metrics["provider-a"]["last_error"])
        self.assertFalse(metrics["provider-b"]["last_success"])
        self.assertEqual(metrics["provider-b"]["last_status_code"], 429)
        self.assertEqual(metrics["provider-b"]["last_error"], "quota exceeded")

    def test_admin_accounts_excludes_oauth_subject_and_updates_access(self) -> None:
        with self.db.session() as conn:
            admin = conn.execute(
                "INSERT INTO users (display_name, role) VALUES ('운영 관리자', 'admin')"
            )
            member = conn.execute(
                "INSERT INTO users (display_name) VALUES ('가입 회원')"
            )
            conn.execute(
                """
                INSERT INTO oauth_accounts
                  (user_id, provider, provider_subject, display_name, last_login_at)
                VALUES (?, 'naver', 'private-provider-subject', '가입 회원', CURRENT_TIMESTAMP)
                """,
                (member.lastrowid,),
            )

        result = self.service.admin_accounts(q="가입", role="user", status="active")

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["accounts"][0]["provider"], "naver")
        self.assertNotIn("provider_subject", result["accounts"][0])
        updated = self.service.admin_update_account(
            int(member.lastrowid),
            role="admin",
            status="active",
            actor_user_id=int(admin.lastrowid),
        )
        self.assertEqual(updated["role"], "admin")
        with self.db.session() as conn:
            audit = conn.execute(
                """
                SELECT action FROM decision_audit_logs
                WHERE target_type = 'user' AND target_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (member.lastrowid,),
            ).fetchone()
        self.assertEqual(audit["action"], "account_access_update")

    def test_admin_cannot_remove_own_access_or_reactivate_closed_account(self) -> None:
        with self.db.session() as conn:
            admin = conn.execute(
                "INSERT INTO users (display_name, role) VALUES ('운영 관리자', 'admin')"
            )
            deleted = conn.execute(
                "INSERT INTO users (display_name, status) VALUES ('탈퇴 회원', 'deleted')"
            )

        with self.assertRaises(AppError) as own_access_error:
            self.service.admin_update_account(
                int(admin.lastrowid),
                role="user",
                status="active",
                actor_user_id=int(admin.lastrowid),
            )
        self.assertEqual(own_access_error.exception.status, 409)
        with self.assertRaises(AppError) as closed_account_error:
            self.service.admin_update_account(
                int(deleted.lastrowid),
                role="user",
                status="active",
                actor_user_id=int(admin.lastrowid),
            )
        self.assertEqual(closed_account_error.exception.status, 409)

    def test_admin_delete_account_removes_links_and_anonymizes_activity(self) -> None:
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        with self.db.session() as conn:
            admin = conn.execute(
                "INSERT INTO users (display_name, role) VALUES ('운영 관리자', 'admin')"
            )
            member = conn.execute(
                "INSERT INTO users (display_name, status) VALUES ('삭제 대상', 'suspended')"
            )
            member_id = int(member.lastrowid)
            conn.execute(
                """
                INSERT INTO oauth_accounts (user_id, provider, provider_subject, display_name)
                VALUES (?, 'naver', 'delete-target-subject', '삭제 대상')
                """,
                (member_id,),
            )
            conn.execute(
                "INSERT INTO user_saved_restaurants (user_id, restaurant_id) VALUES (?, ?)",
                (member_id, restaurant_id),
            )
            review = conn.execute(
                """
                INSERT INTO restaurant_reviews
                  (restaurant_id, user_id, rating, body, reviewer_label)
                VALUES (?, ?, 5, '삭제 전 리뷰', '삭제 대상')
                """,
                (restaurant_id, member_id),
            )

        result = self.service.admin_delete_account(
            member_id,
            actor_user_id=int(admin.lastrowid),
        )

        self.assertEqual(result["result"], "deleted")
        self.assertEqual(result["oauth_accounts_deleted"], 1)
        self.assertEqual(result["saved_restaurants_deleted"], 1)
        self.assertEqual(result["reviews_anonymized"], 1)
        with self.db.session() as conn:
            deleted_user = conn.execute(
                "SELECT display_name, role, status FROM users WHERE id = ?",
                (member_id,),
            ).fetchone()
            oauth_count = conn.execute(
                "SELECT COUNT(*) AS count FROM oauth_accounts WHERE user_id = ?",
                (member_id,),
            ).fetchone()["count"]
            saved_count = conn.execute(
                "SELECT COUNT(*) AS count FROM user_saved_restaurants WHERE user_id = ?",
                (member_id,),
            ).fetchone()["count"]
            anonymized_review = conn.execute(
                "SELECT user_id, reviewer_label FROM restaurant_reviews WHERE id = ?",
                (review.lastrowid,),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT action FROM decision_audit_logs
                WHERE target_type = 'user' AND target_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (member_id,),
            ).fetchone()
        self.assertEqual(dict(deleted_user), {
            "display_name": "탈퇴한 사용자",
            "role": "user",
            "status": "deleted",
        })
        self.assertEqual(int(oauth_count), 0)
        self.assertEqual(int(saved_count), 0)
        self.assertIsNone(anonymized_review["user_id"])
        self.assertEqual(anonymized_review["reviewer_label"], "탈퇴한 사용자")
        self.assertEqual(audit["action"], "admin_account_delete")

        with self.assertRaises(AppError) as self_delete_error:
            self.service.admin_delete_account(
                int(admin.lastrowid),
                actor_user_id=int(admin.lastrowid),
            )
        self.assertEqual(self_delete_error.exception.status, 409)

    def test_admin_image_can_be_managed_without_public_exposure(self) -> None:
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        client = FakeRestaurantImageClient()
        service = RestaurantService(self.db, restaurant_image_client=client)

        saved = service.save_admin_restaurant_image(
            restaurant_id=restaurant_id,
            filename="front.png",
            image_bytes=self.PNG_1X1,
            context=RequestContext(actor_id="local-admin"),
            alt_text="매장 외관",
            sort_order=1,
        )

        self.assertEqual(len(saved["images"]), 1)
        admin_image = saved["images"][0]
        self.assertEqual(admin_image["provider"], "admin_upload")
        self.assertTrue(admin_image["is_admin_image"])
        self.assertTrue(service._admin_image_file_path(
            admin_image["source_url"].removeprefix("/media/restaurant-images/")
        ).exists())

        detail = service.get_restaurant(restaurant_id)
        self.assertEqual(detail["restaurant_images"], [])
        self.assertIsNone(detail["restaurant_image"])
        self.assertEqual(client.calls, [])

        updated = service.update_admin_restaurant_image(
            restaurant_id,
            admin_image["id"],
            alt_text="대표 출입구",
            sort_order=2,
        )
        self.assertEqual(updated["images"][0]["alt_text"], "대표 출입구")
        self.assertEqual(updated["images"][0]["sort_order"], 2)

        removed = service.delete_admin_restaurant_image(
            restaurant_id,
            admin_image["id"],
        )
        self.assertEqual(removed["images"], [])

    def test_admin_image_rejects_unsupported_file_content(self) -> None:
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])

        with self.assertRaisesRegex(AppError, "PNG, JPEG, and WebP"):
            self.service.save_admin_restaurant_image(
                restaurant_id=restaurant_id,
                filename="not-an-image.txt",
                image_bytes=b"not an image",
                context=RequestContext(actor_id="local-admin"),
            )

    def test_ai_summary_failure_is_also_rate_limited_for_one_hour(self) -> None:
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        client = FakeReviewSummaryClient(fail=True)
        current_time = [datetime(2026, 7, 24, 2, 0, tzinfo=timezone.utc)]
        service = RestaurantService(
            self.db,
            review_rate_limit_per_hour=100,
            ai_summary_client=client,
            ai_summary_now=lambda: current_time[0],
        )

        for index in range(5):
            service.add_review(
                restaurant_id,
                5,
                f"실패 재시도 제한 리뷰 {index}",
                "테스터",
                RequestContext(ip=f"192.0.2.{index + 1}"),
            )
        self.assertEqual(len(client.calls), 1)

        current_time[0] += timedelta(minutes=59, seconds=59)
        service.get_restaurant(restaurant_id)
        self.assertEqual(len(client.calls), 1)

        current_time[0] += timedelta(seconds=1)
        service.get_restaurant(restaurant_id)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(service.get_restaurant(restaurant_id)["ai_summary"]["text"], "")

    def test_ai_summary_cooldown_starts_when_successful_generation_finishes(self) -> None:
        service = RestaurantService(self.db, ai_summary_client=FakeReviewSummaryClient())
        cached = {
            "summary_text": "기존 요약",
            "summarized_review_count": 5,
            "last_attempted_at": "2026-07-24T01:00:00+00:00",
            "last_generated_at": "2026-07-24T01:00:30+00:00",
        }

        self.assertFalse(
            service._ai_summary_is_due(
                cached,
                15,
                datetime(2026, 7, 24, 2, 0, 29, tzinfo=timezone.utc),
            )
        )
        self.assertTrue(
            service._ai_summary_is_due(
                cached,
                15,
                datetime(2026, 7, 24, 2, 0, 30, tzinfo=timezone.utc),
            )
        )

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

    def test_my_page_manages_owned_reviews_and_saved_restaurants(self) -> None:
        account = self.service.upsert_oauth_account("naver", "mypage-user", "마이페이지 사용자")
        user_id = int(account["user"]["id"])
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        context = RequestContext(user_id=user_id, ip="203.0.113.31", actor_id=f"user:{user_id}")

        saved = self.service.save_restaurant(user_id, restaurant_id)
        review = self.service.add_review(
            restaurant_id,
            5,
            "마이페이지에서 관리할 리뷰입니다.",
            "마이페이지 사용자",
            context,
        )
        page = self.service.my_page(user_id)

        self.assertTrue(saved["is_saved"])
        self.assertTrue(self.service.get_restaurant(restaurant_id, user_id=user_id)["is_saved"])
        self.assertEqual(page["counts"], {"reviews": 1, "saved_restaurants": 1})
        self.assertEqual(page["reviews"][0]["id"], review["id"])
        self.assertEqual(page["saved_restaurants"][0]["id"], restaurant_id)

        other = self.service.upsert_oauth_account("naver", "other-mypage-user", "다른 사용자")
        with self.assertRaisesRegex(AppError, "review not found"):
            self.service.delete_own_review(int(other["user"]["id"]), int(review["id"]))

        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO restaurant_ai_summaries
                  (restaurant_id, summary_text, summarized_review_count, status)
                VALUES (?, '삭제할 리뷰를 포함한 요약', 5, 'ready')
                """,
                (restaurant_id,),
            )
        self.service.delete_own_review(user_id, int(review["id"]))
        self.service.unsave_restaurant(user_id, restaurant_id)
        emptied = self.service.my_page(user_id)
        self.assertEqual(emptied["counts"], {"reviews": 0, "saved_restaurants": 0})
        with self.db.session() as conn:
            summary = conn.execute(
                "SELECT * FROM restaurant_ai_summaries WHERE restaurant_id = ?",
                (restaurant_id,),
            ).fetchone()
        self.assertEqual(summary["summary_text"], "")
        self.assertEqual(summary["summarized_review_count"], 0)
        self.assertEqual(summary["status"], "idle")
        with self.db.session() as conn:
            deleted_review = conn.execute(
                "SELECT * FROM restaurant_reviews WHERE id = ?", (review["id"],)
            ).fetchone()
        self.assertIsNone(deleted_review["user_id"])
        self.assertEqual(deleted_review["body"], "")
        self.assertIsNone(deleted_review["ip_hash"])

    def test_account_delete_removes_links_and_anonymizes_retained_activity(self) -> None:
        account = self.service.upsert_oauth_account(
            "naver", "delete-service-user", "탈퇴 사용자"
        )
        user_id = int(account["user"]["id"])
        restaurant_id = int(self.service.list_map_restaurants()[0]["id"])
        context = RequestContext(
            user_id=user_id,
            ip="203.0.113.81",
            actor_id=f"user:{user_id}",
        )
        self.service.save_restaurant(user_id, restaurant_id)
        review = self.service.add_review(
            restaurant_id,
            5,
            "탈퇴 후에도 익명으로 남을 리뷰",
            "탈퇴 사용자",
            context,
        )
        report = self.service.report_review(int(review["id"]), "spam_or_abuse", context)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO review_moderation_logs
                  (review_id, action, before_json, after_json)
                VALUES (?, 'inspect', ?, ?)
                """,
                (review["id"], '{"reviewer_label":"탈퇴 사용자"}', '{"status":"visible"}'),
            )

        result = self.service.delete_account(user_id)

        self.assertEqual(
            result,
            {
                "result": "deleted",
                "oauth_accounts_deleted": 1,
                "saved_restaurants_deleted": 1,
                "reviews_anonymized": 1,
                "reports_anonymized": 1,
            },
        )
        self.assertIsNone(self.service.get_active_user(user_id))
        with self.db.session() as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            linked_accounts = conn.execute(
                "SELECT COUNT(*) AS count FROM oauth_accounts WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            saved = conn.execute(
                "SELECT COUNT(*) AS count FROM user_saved_restaurants WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]
            retained_review = conn.execute(
                "SELECT * FROM restaurant_reviews WHERE id = ?", (review["id"],)
            ).fetchone()
            anonymized_report = conn.execute(
                "SELECT * FROM review_reports WHERE id = ?", (report["id"],)
            ).fetchone()
            review_audits = conn.execute(
                """
                SELECT before_json, after_json FROM decision_audit_logs
                WHERE target_type = 'restaurant_review' AND target_id = ?
                """,
                (review["id"],),
            ).fetchall()
            moderation = conn.execute(
                "SELECT before_json, after_json FROM review_moderation_logs WHERE review_id = ?",
                (review["id"],),
            ).fetchone()

        self.assertEqual(user["display_name"], "탈퇴한 사용자")
        self.assertEqual(user["status"], "deleted")
        self.assertEqual(linked_accounts, 0)
        self.assertEqual(saved, 0)
        self.assertIsNone(retained_review["user_id"])
        self.assertEqual(retained_review["reviewer_label"], "탈퇴한 사용자")
        self.assertIsNone(retained_review["ip_hash"])
        self.assertIsNone(anonymized_report["reporter_user_id"])
        self.assertIsNone(anonymized_report["reporter_ip_hash"])
        self.assertTrue(review_audits)
        self.assertTrue(
            all(row["before_json"] == "{}" and row["after_json"] == "{}" for row in review_audits)
        )
        self.assertEqual(moderation["before_json"], "{}")
        self.assertEqual(moderation["after_json"], "{}")

        new_account = self.service.upsert_oauth_account(
            "naver", "delete-service-user", "재가입 사용자"
        )
        self.assertTrue(new_account["created"])
        self.assertNotEqual(int(new_account["user"]["id"]), user_id)

    def test_saved_restaurant_map_filter_is_scoped_to_logged_in_user(self) -> None:
        first = self.service.upsert_oauth_account("naver", "saved-filter-one", "첫 사용자")
        second = self.service.upsert_oauth_account("naver", "saved-filter-two", "둘째 사용자")
        first_user_id = int(first["user"]["id"])
        second_user_id = int(second["user"]["id"])
        restaurants = self.service.list_map_restaurants()
        saved_ids = [int(restaurants[0]["id"]), int(restaurants[1]["id"])]
        for restaurant_id in saved_ids:
            self.service.save_restaurant(first_user_id, restaurant_id)

        filtered = self.service.list_map_restaurants(
            user_id=first_user_id,
            saved_only=True,
        )

        self.assertEqual({int(item["id"]) for item in filtered}, set(saved_ids))
        self.assertEqual(
            self.service.list_map_restaurants(user_id=second_user_id, saved_only="1"),
            [],
        )
        with self.assertRaisesRegex(AppError, "login required"):
            self.service.list_map_restaurants(saved_only=True)


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
