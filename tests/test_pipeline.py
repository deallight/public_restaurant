from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.agents import (
    FakeNaverClient,
    FakePermitClient,
    NormalizedExpenseRow,
    PermitSnapshot,
    PlaceCandidate,
    VerificationDecision,
    VerifierAgent,
    alias_keys_for_place,
    expense_scope_reject_decision,
    franchise_brand,
    has_conflicting_branch_hint,
    has_franchise_branch_hint,
    has_multiple_distinct_place_names,
    manual_feedback_reject_decision,
    name_similarity_with_branch,
    non_food_purpose_reason,
    parse_place_name,
    prefer_unique_busan_candidate,
)
from app.config import Settings
from app.alias_memory import alias_memory_decision
from app.integrations import (
    DataGoKrPermitClient,
    _category_from_naver,
    _place_query_variants,
    _place_rank,
    _search_queries,
)
from app.pipeline import (
    CachedPermitClient,
    CollectionTarget,
    DailyPipeline,
    RawExpenseRow,
    SourceDocument,
    advisory_permit_conflict_decision,
    advisory_permit_resolution_decision,
    clean_parsed_place_name,
    existing_provider_evidence_decision,
    split_raw_expense_row_places,
)
from app.services import RestaurantService
from app.source_catalog import iter_source_catalog
from app.utils import normalize_text, normalized_address_similarity, strip_address_detail, structured_address_match
from tests.postgres_test import fresh_postgres_database, postgres_test_url


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = fresh_postgres_database()

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

    def test_live_collection_can_defer_verification_for_new_rows(self) -> None:
        result = DailyPipeline(self.db, verify_new_rows=False).run()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["summary"]["rows_inserted"], 5)
        self.assertEqual(result["summary"]["needs_review"], 5)
        with self.db.session() as conn:
            pending = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM manual_review_tasks
                WHERE status = 'pending' AND reason = 'PENDING_VERIFICATION'
                """
            ).fetchone()["c"]
            candidates = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM restaurant_candidates
                WHERE status = 'needs_review'
                  AND verification_status = 'not_requested'
                  AND manual_review_status = 'pending'
                """
            ).fetchone()["c"]

        self.assertEqual(pending, 5)
        self.assertEqual(candidates, 5)

    def test_verify_collected_only_processes_initial_unverified_rows(self) -> None:
        DailyPipeline(self.db, verify_new_rows=False).run()
        with self.db.session() as conn:
            manual = conn.execute("SELECT candidate_id FROM manual_review_tasks LIMIT 1").fetchone()
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET verification_status = 'ambiguous',
                    review_note = 'AI_BOUNDARY_SCORE'
                WHERE id = ?
                """,
                (manual["candidate_id"],),
            )
            conn.execute(
                "UPDATE manual_review_tasks SET reason = 'AI_BOUNDARY_SCORE' WHERE candidate_id = ?",
                (manual["candidate_id"],),
            )

        result = DailyPipeline(
            self.db,
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
        ).verify_collected(limit=10)

        self.assertEqual(result["summary"]["rows_seen"], 4)
        with self.db.session() as conn:
            untouched = conn.execute(
                """
                SELECT c.status, c.verification_status, mrt.status AS task_status
                FROM restaurant_candidates c
                JOIN manual_review_tasks mrt ON mrt.candidate_id = c.id
                WHERE c.id = ?
                """,
                (manual["candidate_id"],),
            ).fetchone()

        self.assertEqual(untouched["status"], "needs_review")
        self.assertEqual(untouched["verification_status"], "ambiguous")

    def test_verify_collected_processes_initial_candidate_without_review_task(self) -> None:
        DailyPipeline(self.db, verify_new_rows=False).run()
        with self.db.session() as conn:
            candidate_id = conn.execute(
                """
                SELECT candidate_id
                FROM manual_review_tasks
                WHERE status = 'pending'
                LIMIT 1
                """
            ).fetchone()["candidate_id"]
            conn.execute("DELETE FROM manual_review_tasks WHERE candidate_id = ?", (candidate_id,))

        result = DailyPipeline(
            self.db,
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
        ).verify_collected(limit=10)

        self.assertEqual(result["summary"]["pending_before"], 5)
        self.assertEqual(result["summary"]["rows_processed"], 5)
        with self.db.session() as conn:
            candidate = conn.execute(
                "SELECT status, verification_status FROM restaurant_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()

        self.assertNotEqual(candidate["verification_status"], "not_requested")

    def test_verify_collected_emits_candidate_progress_events(self) -> None:
        DailyPipeline(self.db, verify_new_rows=False).run()
        events: list[dict] = []

        result = DailyPipeline(
            self.db,
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
            verification_progress_callback=events.append,
        ).verify_collected(limit=1)

        event_names = [event["event"] for event in events]
        step_stages = [event.get("stage") for event in events if event["event"] == "candidate_step"]
        done = [event for event in events if event["event"] == "candidate_done"]
        finished = [event for event in events if event["event"] == "batch_finished"]

        self.assertEqual(result["summary"]["rows_processed"], 1)
        self.assertIn("batch_started", event_names)
        self.assertIn("candidate_queued", event_names)
        self.assertIn("normalize", step_stages)
        self.assertIn("expense_scope_rule", step_stages)
        self.assertIn("persist_decision", step_stages)
        self.assertEqual(done[-1]["percent"], 100)
        self.assertEqual(done[-1]["status"], "completed")
        self.assertEqual(finished[-1]["processed"], 1)

    def test_verify_collected_sort_can_prioritize_latest_used_date(self) -> None:
        DailyPipeline(self.db, verify_new_rows=False).run()
        with self.db.session() as conn:
            latest_candidate_id = conn.execute(
                """
                SELECT c.id
                FROM restaurant_candidates c
                JOIN expense_records er ON er.id = c.expense_record_id
                WHERE c.verification_status = 'not_requested'
                ORDER BY COALESCE(c.used_date, '') DESC, c.id DESC
                LIMIT 1
                """
            ).fetchone()["id"]

        DailyPipeline(
            self.db,
            verifier=VerifierAgent(ExactNaverClient(), ExactPermitClient()),
        ).verify_collected(limit=1, sort="used_date_desc")

        with self.db.session() as conn:
            latest_status = conn.execute(
                "SELECT verification_status FROM restaurant_candidates WHERE id = ?",
                (latest_candidate_id,),
            ).fetchone()["verification_status"]
            remaining_pending = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM restaurant_candidates
                    WHERE verification_status = 'not_requested'
                    """
                ).fetchone()["count"]
            )

        self.assertNotEqual(latest_status, "not_requested")
        self.assertEqual(remaining_pending, 4)

    def test_collection_plan_discovers_targets_and_batches_collection(self) -> None:
        adapter = PlanningAdapter()
        pipeline = DailyPipeline(self.db, adapter=adapter, verify_new_rows=False)

        plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=2,
            max_documents=10,
            batch_size=1,
        )
        first = pipeline.run_collection_plan_batch(plan["plan_id"], batch_size=1)
        first_parse = pipeline.parse_collection_plan_batch(plan["plan_id"], batch_size=1)
        second = pipeline.run_collection_plan_batch(plan["plan_id"], batch_size=1)
        second_parse = pipeline.parse_collection_plan_batch(plan["plan_id"], batch_size=1)

        self.assertEqual(plan["summary"]["documents_seen"], 2)
        self.assertEqual(first["summary"]["documents_seen"], 1)
        self.assertEqual(first["summary"]["rows_inserted"], 0)
        self.assertEqual(first_parse["summary"]["rows_inserted"], 1)
        self.assertEqual(second["summary"]["documents_seen"], 1)
        self.assertEqual(second_parse["summary"]["rows_inserted"], 1)
        with self.db.session() as conn:
            plan_row = conn.execute(
                "SELECT * FROM collection_plans WHERE id = ?",
                (plan["plan_id"],),
            ).fetchone()
            documents = conn.execute(
                """
                SELECT status, parse_status, rows_inserted
                FROM collection_plan_documents
                WHERE plan_id = ?
                ORDER BY id ASC
                """,
                (plan["plan_id"],),
            ).fetchall()
            pending = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM restaurant_candidates
                WHERE verification_status = 'not_requested'
                  AND review_note = 'PENDING_VERIFICATION'
                """
            ).fetchone()["c"]

        self.assertEqual(plan_row["status"], "completed")
        self.assertEqual([row["status"] for row in documents], ["collected", "collected"])
        self.assertEqual([row["parse_status"] for row in documents], ["parsed", "parsed"])
        self.assertEqual([row["rows_inserted"] for row in documents], [1, 1])
        self.assertEqual(pending, 2)
        progress = RestaurantService(self.db).ops_logs(plan_id=plan["plan_id"], limit=10)["progress"]
        self.assertEqual(len(progress["by_institution"]), 2)
        self.assertTrue(all(item["percent"] == 100.0 for item in progress["by_institution"]))
        self.assertEqual(progress["by_priority"][0]["percent"], 100.0)
        period_progress = RestaurantService(self.db).collection_progress(
            "2026-01-01",
            "2026-12-31",
        )
        self.assertEqual(period_progress["document_count"], 2)
        self.assertEqual(period_progress["plan_count"], 1)
        self.assertEqual(len(period_progress["by_institution"]), 2)
        self.assertEqual(
            RestaurantService(self.db).collection_progress(
                "2025-01-01",
                "2025-12-31",
            )["document_count"],
            0,
        )
        dashboard = RestaurantService(self.db).collection_dashboard(
            "2026-01-01",
            "2026-12-31",
        )
        self.assertEqual(dashboard["city"]["name"], "부산광역시")
        self.assertEqual(dashboard["city"]["document_count"], 2)
        self.assertEqual(len(dashboard["priorities"]), 8)
        self.assertEqual(dashboard["priorities"][0]["priority"], 1)
        self.assertEqual(len(dashboard["priorities"][0]["institutions"]), 2)
        self.assertEqual(dashboard["priorities"][0]["verification"]["total"], 2)
        self.assertEqual(dashboard["priorities"][0]["verification"]["pending"], 2)
        self.assertEqual(dashboard["priorities"][0]["verification"]["completed"], 0)
        self.assertTrue(
            all(
                item["verification"]["total"] == 1
                for item in dashboard["priorities"][0]["institutions"]
            )
        )
        document_board = RestaurantService(self.db).admin_documents(
            "2026-01-01",
            "2026-12-31",
            limit=10,
        )
        self.assertEqual(document_board["total"], 2)
        self.assertEqual(document_board["summary"]["candidates"], 2)
        self.assertEqual(len(document_board["items"]), 2)
        selected_document = document_board["items"][0]
        institution_board = RestaurantService(self.db).admin_documents(
            "2026-01-01",
            "2026-12-31",
            institution=selected_document["institution_label"],
            limit=10,
        )
        self.assertEqual(institution_board["total"], 1)
        document_detail = RestaurantService(self.db).admin_document_detail(
            int(selected_document["id"]),
        )
        self.assertEqual(document_detail["document"]["id"], selected_document["id"])
        self.assertEqual(document_detail["total"], 1)
        self.assertEqual(len(document_detail["items"]), 1)
        self.assertIn("effective_place_name", document_detail["items"][0])
        candidate_board = RestaurantService(self.db).admin_candidates(
            start_date="2026-01-01",
            end_date="2026-12-31",
            limit=10,
        )
        self.assertEqual(candidate_board["groups"]["needs_review"]["total"], 0)
        self.assertEqual(candidate_board["groups"]["needs_review"]["pending_total"], 2)
        self.assertEqual(candidate_board["groups"]["needs_review"]["manual_total"], 0)
        self.assertEqual(candidate_board["groups"]["needs_review"]["items"], [])
        self.assertEqual(candidate_board["selected"]["total"], 2)
        pending_candidate_page = RestaurantService(self.db).admin_candidates(
            start_date="2026-01-01",
            end_date="2026-12-31",
            status="pending",
            limit=1,
            offset=0,
        )
        self.assertEqual(pending_candidate_page["selected"]["label"], "검증 대기")
        self.assertEqual(pending_candidate_page["selected"]["total"], 2)
        self.assertEqual(len(pending_candidate_page["selected"]["items"]), 1)
        self.assertTrue(pending_candidate_page["selected"]["has_next"])
        empty_candidate_board = RestaurantService(self.db).admin_candidates(
            start_date="2025-01-01",
            end_date="2025-12-31",
            limit=10,
        )
        self.assertEqual(empty_candidate_board["groups"]["needs_review"]["total"], 0)

    def test_collection_plan_batches_repeat_until_no_pending_documents(self) -> None:
        adapter = PlanningAdapter()
        pipeline = DailyPipeline(self.db, adapter=adapter, verify_new_rows=False)

        plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=2,
            max_documents=10,
            batch_size=1,
        )
        result = pipeline.run_collection_plan_batches(plan["plan_id"], batch_size=1)
        parse_result = pipeline.parse_collection_plan_batches(plan["plan_id"], batch_size=1)

        self.assertEqual(result["summary"]["batches_run"], 2)
        self.assertEqual(result["summary"]["pending_before"], 2)
        self.assertEqual(result["summary"]["pending_after"], 0)
        self.assertEqual(result["summary"]["rows_inserted"], 0)
        self.assertEqual(parse_result["summary"]["batches_run"], 2)
        self.assertEqual(parse_result["summary"]["pending_before"], 2)
        self.assertEqual(parse_result["summary"]["pending_after"], 0)
        self.assertEqual(parse_result["summary"]["rows_inserted"], 2)
        with self.db.session() as conn:
            remaining = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM collection_plan_documents
                WHERE plan_id = ? AND status = 'pending'
                """,
                (plan["plan_id"],),
            ).fetchone()["c"]
            remaining_parse = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM collection_plan_documents
                WHERE plan_id = ?
                  AND parse_status IN ('not_requested', 'failed', 'unsupported')
                """,
                (plan["plan_id"],),
            ).fetchone()["c"]

        self.assertEqual(remaining, 0)
        self.assertEqual(remaining_parse, 0)

    def test_collection_parse_splits_multiple_place_names_into_verification_candidates(self) -> None:
        class MultiplePlaceAdapter(PlanningAdapter):
            def discover_targets(self) -> list[CollectionTarget]:
                return super().discover_targets()[:1]

            def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
                return [
                    RawExpenseRow(
                        row_number=1,
                        department_name=document.department_name,
                        used_date="2026-02-01",
                        place_name="만객당 외 1, 히얼이즈커피 등",
                        address="",
                        purpose="다과 구입",
                        amount=100000,
                    )
                ]

        pipeline = DailyPipeline(self.db, adapter=MultiplePlaceAdapter(), verify_new_rows=False)
        plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_documents=1,
            batch_size=1,
        )

        collection = pipeline.run_collection_plan_batch(plan["plan_id"], batch_size=1)
        parsing = pipeline.parse_collection_plan_batch(plan["plan_id"], batch_size=1)

        with self.db.session() as conn:
            rows = conn.execute(
                """
                SELECT er.place_name, er.amount, er.original_row_json,
                       c.extraction_reason, c.verification_status, c.review_note
                FROM expense_records er
                JOIN restaurant_candidates c ON c.expense_record_id = er.id
                ORDER BY er.place_name ASC
                """
            ).fetchall()

        self.assertEqual(collection["summary"]["rows_inserted"], 0)
        self.assertEqual(parsing["summary"]["rows_seen"], 2)
        self.assertEqual(parsing["summary"]["rows_inserted"], 2)
        self.assertEqual([row["place_name"] for row in rows], ["만객당", "히얼이즈커피"])
        self.assertEqual([row["amount"] for row in rows], [0, 0])
        self.assertTrue(all(row["extraction_reason"].endswith(":multi_place_split") for row in rows))
        self.assertTrue(all(row["verification_status"] == "not_requested" for row in rows))
        self.assertTrue(all(row["review_note"] == "PENDING_VERIFICATION" for row in rows))
        original_payloads = [json.loads(row["original_row_json"]) for row in rows]
        self.assertTrue(
            all(
                payload["split_from_place_name"] == "만객당 외 1, 히얼이즈커피 등"
                for payload in original_payloads
            )
        )
        self.assertEqual(
            [payload["cleaned_from_place_name"] for payload in original_payloads],
            ["만객당 외 1", "히얼이즈커피 등"],
        )
        self.assertEqual([payload["split_index"] for payload in original_payloads], [1, 2])
        self.assertTrue(all(payload["split_count"] == 2 for payload in original_payloads))
        self.assertTrue(all(payload["source_amount"] == 100000 for payload in original_payloads))

    def test_split_place_names_deduplicates_delimited_values(self) -> None:
        raw_row = RawExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-02-01",
            place_name="만객당; 만객당，히얼이즈커피",
            address="",
            purpose="다과 구입",
            amount=100000,
        )

        split_rows = split_raw_expense_row_places(raw_row)

        self.assertEqual([row.place_name for row in split_rows], ["만객당", "히얼이즈커피"])
        self.assertEqual([row.split_index for row in split_rows], [1, 2])
        self.assertTrue(all(row.amount == 0 for row in split_rows))
        self.assertTrue(all(row.source_amount == 100000 for row in split_rows))

    def test_split_place_names_handles_multiline_expense_cells(self) -> None:
        raw_row = RawExpenseRow(
            row_number=1,
            department_name="관광마이스산업과",
            used_date="2026-03-03",
            place_name=(
                "유가네닭갈비 하단역점\n"
                "북창동순두부 부산서면점\n"
                "스시리\n"
                "나정순할매쭈꾸미볶음"
            ),
            address="",
            purpose="현장 실사 관련 간담회",
            amount=201900,
        )

        split_rows = split_raw_expense_row_places(raw_row)

        self.assertEqual(
            [row.place_name for row in split_rows],
            [
                "유가네닭갈비 하단역점",
                "북창동순두부 부산서면점",
                "스시리",
                "나정순할매쭈꾸미볶음",
            ],
        )
        self.assertTrue(all(row.amount == 0 for row in split_rows))
        self.assertTrue(all(row.source_amount == 201900 for row in split_rows))
        self.assertTrue(has_multiple_distinct_place_names(raw_row.place_name))
        self.assertTrue(
            {
                "유가네닭갈비 하단역점",
                "북창동순두부 부산서면점",
                "스시리",
                "나정순할매쭈꾸미볶음",
            }.issubset(set(_place_query_variants(raw_row.place_name)))
        )
        self.assertNotIn(
            "유가네닭갈비 하단역점 북창동순두부 부산서면점 스시리 나정순할매쭈꾸미볶음",
            _place_query_variants(raw_row.place_name),
        )
        self.assertEqual(_place_query_variants("6692(육육구이)")[0], "육육구이")

    def test_parse_removes_only_trailing_companion_suffixes(self) -> None:
        cases = {
            "해운대식당 등": "해운대식당",
            "해운대식당등": "해운대식당",
            "해운대식당 외 1": "해운대식당",
            "해운대식당외1개소": "해운대식당",
            "해운대식당 외 1건": "해운대식당",
            "해운대식당 외1인": "해운대식당",
            "해운대식당 등 2개소": "해운대식당",
            "뚜레쥬르(외1)": "뚜레쥬르",
            "놀유니버스티켓(문화비외1)": "놀유니버스티켓",
            "해운대식당 외 2곳 등": "해운대식당",
            "등촌샤브칼국수": "등촌샤브칼국수",
            "해운대등대횟집": "해운대등대횟집",
        }

        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(clean_parsed_place_name(original), expected)

    def test_baskin_robbins_spelling_variants_share_franchise_rule(self) -> None:
        self.assertEqual(franchise_brand("스탁벅스"), "starbucks")
        self.assertEqual(franchise_brand("배스킨라빈스 부산시청점"), "baskin_robbins")
        self.assertEqual(franchise_brand("베스킨라빈스"), "baskin_robbins")
        self.assertEqual(franchise_brand("명랑핫도그"), "myungrang_hotdog")
        self.assertEqual(franchise_brand("김종구식맛치킨"), "kim_jonggu_chicken")
        self.assertEqual(franchise_brand("모모스커피"), "momos_coffee")
        self.assertEqual(franchise_brand("외식1번가 서부산점"), "dining_first")
        self.assertEqual(franchise_brand("양산도"), "yangsando")
        self.assertEqual(franchise_brand("히얼이즈커피"), "here_is_coffee")
        self.assertEqual(franchise_brand("히스피(HUSFFEE)장림점"), "husffee")
        self.assertEqual(franchise_brand("히스피커피&디저트 장림점"), "husffee")
        self.assertEqual(franchise_brand("오발탄 부산해운대점"), "obaltan")
        self.assertEqual(franchise_brand("㈜오발탄에프에스"), "obaltan")
        self.assertEqual(franchise_brand("오막집"), "omakjip")
        self.assertEqual(franchise_brand("제주옥탑"), "jeju_rooftop")
        self.assertEqual(franchise_brand("삿뽀로"), "sapporo")
        self.assertEqual(franchise_brand("블루샥"), "blueshaak")
        self.assertEqual(franchise_brand("맛찬들왕소금구이"), "matchandeul")
        self.assertEqual(franchise_brand("버거킹"), "burger_king")
        self.assertEqual(franchise_brand("㈜부산어묵고래사"), "goraesa")
        self.assertEqual(franchise_brand("아웃백스테이크하우스"), "outback")
        self.assertEqual(franchise_brand("옵스"), "ops_bakery")
        self.assertEqual(franchise_brand("교동면옥"), "gyodong_myeonok")
        self.assertEqual(franchise_brand("도미노피자"), "dominos_pizza")
        self.assertEqual(franchise_brand("크리스털제이드"), "crystal_jade")
        self.assertEqual(
            franchise_brand("크리스탈제이드 롯데백화점 부산본점"),
            "crystal_jade",
        )
        self.assertEqual(franchise_brand("엔제리너스"), "angelinus")
        self.assertEqual(franchise_brand("김가네김밥 부산시청점"), "kimgane")
        self.assertEqual(franchise_brand("샐러디"), "salady")
        self.assertEqual(franchise_brand("샤브20 부산광안리점"), "shabu_20")
        self.assertEqual(franchise_brand("피자에땅"), "pizza_etang")
        self.assertEqual(franchise_brand("고디바베이커리 커넥트현대점"), "godiva")
        self.assertEqual(franchise_brand("더벤티"), "the_venti")
        self.assertEqual(franchise_brand("청오디피케이㈜연산점"), "dominos_pizza")
        self.assertEqual(franchise_brand("금수복국 해운대본점"), "geumsu_bokguk")
        self.assertEqual(franchise_brand("수제국수 부산시청점"), "handmade_noodles")
        self.assertEqual(franchise_brand("이재모피자"), "lee_jaemo_pizza")
        self.assertEqual(franchise_brand("취원"), "chwiwon")
        self.assertEqual(franchise_brand("루시앙 베이커리 부산시청점"), "lucien_bakery")
        self.assertEqual(franchise_brand("루시앙"), "lucien_bakery")
        self.assertEqual(franchise_brand("읍천리382"), "eupcheonri_382")
        self.assertEqual(franchise_brand("장산다방 부산시청점"), "jangsan_dabang")
        self.assertEqual(franchise_brand("부자회관 부산시청점"), "buja_hoegwan")
        self.assertEqual(franchise_brand("가마치통닭"), "gamachi_chicken")
        self.assertEqual(franchise_brand("뚜레쥬르"), "tous_les_jours")
        self.assertEqual(franchise_brand("이흥용베이커리"), "lee_heung_yong_bakery")
        self.assertEqual(franchise_brand("경복궁앤도시락"), "gyeongbokgung_dosirak")
        self.assertEqual(franchise_brand("주식회사 더블유더파티 시청지점"), "the_party")
        self.assertEqual(franchise_brand("하이오 커피"), "hio_coffee")
        self.assertEqual(franchise_brand("비비큐 부산아델라호텔점"), "bbq_chicken")
        self.assertEqual(franchise_brand("BBQ치킨 부산중앙점"), "bbq_chicken")
        self.assertEqual(franchise_brand("백년불고기물갈비"), "baeknyeon_bulgogi")
        self.assertEqual(franchise_brand("샤브마니아만덕점"), "shabu_mania")
        self.assertEqual(franchise_brand("처갓집양념치킨"), "cheogatjip_chicken")
        self.assertEqual(franchise_brand("동키치킨"), "donkey_chicken")
        self.assertEqual(franchise_brand("이화수육개장"), "ihwasu_yukgaejang")
        self.assertEqual(franchise_brand("현풍닭칼국수"), "hyeonpung_chicken_noodles")
        self.assertEqual(franchise_brand("커피베이"), "coffee_bay")
        self.assertEqual(franchise_brand("이우철삼계탕"), "lee_woocheol_samgyetang")
        self.assertEqual(franchise_brand("월남면반"), "wolnam_myeonban")
        self.assertEqual(franchise_brand("삼동소바"), "samdong_soba")
        self.assertEqual(franchise_brand("베지나랑 광안점"), "vegenarang")
        self.assertEqual(franchise_brand("와플대학"), "waffle_university")
        self.assertEqual(franchise_brand("프레드피자"), "fred_pizza")
        self.assertEqual(franchise_brand("떡보의하루"), "tteokbo_haru")
        self.assertEqual(franchise_brand("명지첫집 오션시티점"), "myeongji_cheotjip")
        self.assertEqual(franchise_brand("블루보틀커피코리아 유한회사"), "blue_bottle")
        self.assertFalse(has_franchise_branch_hint("블루샥 커피"))
        self.assertEqual(franchise_brand("솔솥"), "solsot")

    def test_naver_hanjeongsik_category_is_food(self) -> None:
        self.assertEqual(_category_from_naver("한정식"), "restaurant")
        self.assertEqual(_category_from_naver("아이스크림"), "cafe")
        self.assertEqual(_category_from_naver("곱창,막창,양"), "restaurant")
        self.assertEqual(_category_from_naver("떡,한과"), "cafe")
        self.assertEqual(_category_from_naver("족발,보쌈"), "restaurant")
        self.assertEqual(_category_from_naver("덮밥"), "restaurant")
        self.assertEqual(_category_from_naver("김밥"), "restaurant")
        self.assertEqual(_category_from_naver("스파게티,파스타전문"), "restaurant")
        self.assertEqual(_category_from_naver("이자카야"), "bar")
        self.assertEqual(_category_from_naver("쌈밥"), "restaurant")

    def test_compact_franchise_branch_search_uses_readable_spacing(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="폴바셋연산점",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="폴바셋연산점",
            normalized_address="",
        )

        self.assertEqual(_search_queries(row)[:6], [
            "폴 바셋 연산점",
            "폴 바셋 연산점 부산",
            "폴 바셋 부산 연산역점",
            "폴 바셋 부산 연산역점 부산",
            "폴 바셋",
            "폴 바셋 부산",
        ])
        self.assertIn("폴바셋연산점", _search_queries(row))

    def test_franchise_station_wording_is_the_same_branch(self) -> None:
        self.assertFalse(has_conflicting_branch_hint(
            "폴바셋연산점",
            "폴 바셋 부산 연산역점",
        ))
        self.assertGreaterEqual(
            name_similarity_with_branch("폴바셋연산점", "폴 바셋 부산 연산역점"),
            0.96,
        )
        self.assertFalse(has_conflicting_branch_hint(
            "히스피(HUSFFEE)장림점",
            "히스피커피&디저트 장림점",
        ))
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "히스피(HUSFFEE)장림점",
                "히스피커피&디저트 장림점",
            ),
            0.96,
        )
        self.assertFalse(has_conflicting_branch_hint(
            "부산토곡 파리바게트",
            "파리바게뜨 부산토곡점",
        ))
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "부산토곡 파리바게트",
                "파리바게뜨 부산토곡점",
            ),
            0.96,
        )

    def test_repeated_suffix_merchant_uses_learned_short_name(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="제일옥곰탕",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="제일옥곰탕",
            normalized_address="",
        )

        self.assertIn("제일옥", _search_queries(row))

    def test_renamed_gwangbok_branch_uses_current_naver_name(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="이재모피자광복점",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="이재모피자광복점",
            normalized_address="",
        )

        self.assertIn("이재모피자 광복", _search_queries(row))

        self.assertFalse(has_conflicting_branch_hint(
            "이재모피자광복점",
            "이재모피자 본점 2",
        ))
        self.assertGreaterEqual(
            name_similarity_with_branch("이재모피자광복점", "이재모피자 본점 2"),
            0.96,
        )
        self.assertFalse(has_conflicting_branch_hint(
            "장덕풍천산삼민물장어",
            "장덕풍천장어 광안본점",
        ))
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "장덕풍천산삼민물장어",
                "장덕풍천장어 광안본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("웰빙선생오리", "웰빙선유황생오리"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("더83비프", "83비프 동래본점"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "고고365대게좋은날",
                "외식365 대게좋은날 부산본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "피아크 카페앤베이커리",
                "피아크 카페&베이커리",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "솥솥 해운대점",
                "솔솥 해운대 해리단길점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "금수복국 센텀벡스코점",
                "금수복국 센텀점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "샤브마니아만덕점",
                "샤브마니아 부산만덕점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "베지나랑 광안점",
                "베지나랑 광안리점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "명지첫집 오션시티점",
                "명지첫집 오션시티직영점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "모모스커피 온천장점",
                "모모스커피 본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "로터리집",
                "로타리집",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "연제 제일식당",
                "연제제일식당닭한마리",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "식당3선 덕척점",
                "식당3선 덕천점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "심길선 총송양곱창",
                "심길선청송양곱창",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "백만김밥도시락",
                "백만김밥 부산본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "303화덕온천",
                "303화덕 동래온천장본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "고더커피 부산대학교병원점",
                "고더커피 부산대학병원점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "세일기업(주) 수안커피컴퍼니",
                "수안커피컴퍼니 커피하우스",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "명지 일삼육고기집",
                "일삼육고기집 명지본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("다은나루", "다온나루"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("다은나루 외 1", "다온나루"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("만호갈미조개", "만호갈미샤브샤브"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "주차장산꼼장어3호점",
                "주차장산꼼장어 본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("진상주물럭", "진상 W스퀘어점"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch("투쉐프", "2Chef 중앙동 본점"),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "오현프로젝트 강경불고기 센텀시티점",
                "고담현불고기 해운대센텀시티 본점",
            ),
            0.96,
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "강경불고기 해운대센텀시티점",
                "고담현불고기 해운대센텀시티 본점",
            ),
            0.96,
        )

    def test_connect_hyundai_godiva_uses_current_naver_name(self) -> None:
        self.assertIn(
            "고디바베이커리 커넥트현대점",
            _place_query_variants("커넥트현대 부산점 고디바베이커리"),
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "커넥트현대 부산점 고디바베이커리",
                "고디바베이커리 커넥트현대점",
            ),
            0.96,
        )

    def test_seomyeon_rinseon_dining_uses_current_naver_name(self) -> None:
        self.assertIn(
            "린선다이닝 서면본점",
            _place_query_variants("서면린선다이닝"),
        )
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "서면린선다이닝",
                "린선다이닝 서면본점",
            ),
            0.96,
        )

    def test_geographic_place_name_uses_restaurant_query_fallback(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="가시리",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="가시리",
            normalized_address="",
        )

        self.assertIn("가시리 식당", _search_queries(row))

    def test_repeated_prefix_merchant_uses_learned_short_name(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="명품용수떡집",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="명품용수떡집",
            normalized_address="",
        )

        self.assertIn("용수떡집", _search_queries(row))

    def test_branch_word_alone_is_not_used_as_a_search_query(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="지엔지 시청점",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="지엔지 시청점",
            normalized_address="",
        )

        self.assertNotIn("시청점", _search_queries(row))

    def test_parenthetical_brand_ampersand_is_not_split_as_multiple_places(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="",
            used_date="2026-01-01",
            place_name="지엔지(G&G)시청점",
            address="",
            purpose="간담회",
            amount=10000,
            normalized_place_name="지엔지(G&G)시청점",
            normalized_address="",
        )

        self.assertFalse(has_multiple_distinct_place_names(row.place_name))
        self.assertNotIn("지엔지(G", _search_queries(row))
        self.assertNotIn("G)시청점", _search_queries(row))

    def test_venue_label_and_current_store_name_are_equivalent(self) -> None:
        self.assertGreaterEqual(
            name_similarity_with_branch(
                "㈜보리에 더베이101",
                "보리에 베이커리 앤 델리",
            ),
            0.96,
        )

    def test_unique_busan_match_is_preferred_among_multiple_regions(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-05",
            place_name="온천정",
            address="",
            purpose="업무협의 오찬",
            amount=70000,
            normalized_place_name="온천정",
            normalized_address="",
        )
        candidates = [
            PlaceCandidate(
                provider_place_id="seoul-1",
                name="온천정",
                category="restaurant",
                address="서울특별시 종로구 종로 1",
                road_address="서울특별시 종로구 종로 1",
                longitude=126.978,
                latitude=37.566,
            ),
            PlaceCandidate(
                provider_place_id="busan-1",
                name="온천정",
                category="restaurant",
                address="부산광역시 동래구 온천장로 1",
                road_address="부산광역시 동래구 온천장로 1",
                longitude=129.081,
                latitude=35.221,
            ),
        ]

        selected = prefer_unique_busan_candidate(row, candidates)

        self.assertEqual([candidate.provider_place_id for candidate in selected], ["busan-1"])

    def test_collection_plan_skips_previously_collected_documents(self) -> None:
        adapter = PlanningAdapter()
        pipeline = DailyPipeline(self.db, adapter=adapter, verify_new_rows=False)

        first_plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=2,
            max_documents=10,
            batch_size=1,
        )
        first_batch = pipeline.run_collection_plan_batch(first_plan["plan_id"], batch_size=1)
        first_parse = pipeline.parse_collection_plan_batch(first_plan["plan_id"], batch_size=1)
        second_plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=2,
            max_documents=10,
            batch_size=10,
        )
        second_batch = pipeline.run_collection_plan_batch(second_plan["plan_id"], batch_size=10)

        self.assertEqual(first_batch["summary"]["documents_seen"], 1)
        self.assertEqual(first_parse["summary"]["rows_inserted"], 1)
        self.assertEqual(second_plan["summary"]["documents_seen"], 2)
        self.assertEqual(second_plan["summary"]["documents_skipped_collected"], 1)
        self.assertEqual(second_batch["summary"]["documents_seen"], 1)
        with self.db.session() as conn:
            plan_row = conn.execute(
                "SELECT pending_count, duplicate_count, collected_count FROM collection_plans WHERE id = ?",
                (second_plan["plan_id"],),
            ).fetchone()
            documents = conn.execute(
                """
                SELECT source_url, status, parse_status, raw_document_id
                FROM collection_plan_documents
                WHERE plan_id = ?
                ORDER BY source_url ASC
                """,
                (second_plan["plan_id"],),
            ).fetchall()

        self.assertEqual(plan_row["pending_count"], 0)
        self.assertEqual(plan_row["duplicate_count"], 1)
        self.assertEqual(plan_row["collected_count"], 1)
        self.assertEqual(
            [(row["source_url"], row["status"]) for row in documents],
            [
                ("fixture://busan/planning/1", "collected"),
                ("fixture://busan/planning/2", "duplicate"),
            ],
        )
        self.assertEqual([row["parse_status"] for row in documents], ["not_requested", "parsed"])
        self.assertIsNotNone(documents[1]["raw_document_id"])
        service = RestaurantService(self.db)
        first_plan_pending = service.admin_documents(
            "2026-01-01",
            "2026-12-31",
            parse_status="not_requested",
            plan_id=first_plan["plan_id"],
            limit=10,
        )
        second_plan_pending = service.admin_documents(
            "2026-01-01",
            "2026-12-31",
            parse_status="not_requested",
            plan_id=second_plan["plan_id"],
            limit=10,
        )
        self.assertEqual(first_plan_pending["total"], 0)
        self.assertEqual(second_plan_pending["total"], 1)
        self.assertEqual(second_plan_pending["items"][0]["plan_id"], second_plan["plan_id"])

    def test_collection_plan_skips_same_busan_document_when_cur_page_changes(self) -> None:
        class PageVariantAdapter(PlanningAdapter):
            def __init__(self, page: int) -> None:
                super().__init__()
                self.page = page

            def discover_targets(self) -> list[CollectionTarget]:
                return [
                    CollectionTarget(
                        source_url=(
                            "https://www.busan.go.kr/ghopen12/view?"
                            f"schCommand=Expense&schIndx=21753&curPage={self.page}&"
                        ),
                        source_title="2026년 2분기 업무추진비 내역(생활체육과)",
                        published_at="2026-07-12",
                        department_name="생활체육과",
                    )
                ]

            def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
                return [
                    RawExpenseRow(
                        row_number=1,
                        department_name="생활체육과",
                        used_date="2026-06-05",
                        place_name="중복검증식당",
                        address="",
                        purpose="간담회",
                        amount=30000,
                    )
                ]

        first_pipeline = DailyPipeline(
            self.db,
            adapter=PageVariantAdapter(1),
            verify_new_rows=False,
        )
        first_plan = first_pipeline.create_collection_plan(
            "2026-05-05",
            "2026-08-05",
        )
        first_pipeline.run_collection_plan_batch(first_plan["plan_id"])
        first_pipeline.parse_collection_plan_batch(first_plan["plan_id"])

        second_pipeline = DailyPipeline(
            self.db,
            adapter=PageVariantAdapter(6),
            verify_new_rows=False,
        )
        second_plan = second_pipeline.create_collection_plan("2026-05-05", "2026-08-05")
        duplicate_scope_verification = second_pipeline.verify_collected(
            limit=10,
            plan_id=second_plan["plan_id"],
            start_date="2026-05-05",
            end_date="2026-08-05",
        )

        with self.db.session() as conn:
            planned = conn.execute(
                """
                SELECT status, parse_status, raw_document_id
                FROM collection_plan_documents
                WHERE plan_id = ?
                """,
                (second_plan["plan_id"],),
            ).fetchone()

        self.assertEqual(second_plan["summary"]["documents_skipped_collected"], 1)
        self.assertEqual(planned["status"], "duplicate")
        self.assertEqual(planned["parse_status"], "parsed")
        self.assertIsNotNone(planned["raw_document_id"])
        self.assertEqual(duplicate_scope_verification["summary"]["rows_seen"], 0)
        self.assertEqual(duplicate_scope_verification["summary"]["pending_before"], 0)

    def test_verify_collected_can_limit_to_plan_and_used_date_range(self) -> None:
        pipeline = DailyPipeline(
            self.db,
            adapter=PlanningAdapter(),
            verifier=VerifierAgent(),
            verify_new_rows=False,
        )
        plan = pipeline.create_collection_plan(
            start_date="2026-05-05",
            end_date="2026-08-05",
            max_pages=1,
            max_documents=1,
        )
        pipeline.run_collection_plan_batch(plan["plan_id"])
        pipeline.parse_collection_plan_batch(plan["plan_id"])

        scoped = pipeline.verify_collected(
            limit=10,
            plan_id=plan["plan_id"],
            start_date="2026-05-05",
            end_date="2026-08-05",
        )
        unscoped = pipeline.verify_collected(limit=10, plan_id=plan["plan_id"])

        self.assertEqual(scoped["summary"]["rows_seen"], 0)
        self.assertEqual(scoped["summary"]["pending_before"], 0)
        self.assertEqual(unscoped["summary"]["rows_seen"], 1)

    def test_parse_failed_document_is_not_treated_as_completed_duplicate(self) -> None:
        adapter = FailingParsePlanningAdapter()
        pipeline = DailyPipeline(self.db, adapter=adapter, verify_new_rows=False)

        first_plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=1,
            max_documents=1,
            batch_size=1,
        )
        collection = pipeline.run_collection_plan_batch(first_plan["plan_id"], batch_size=1)
        parsing = pipeline.parse_collection_plan_batch(first_plan["plan_id"], batch_size=1)
        second_plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=1,
            max_documents=1,
            batch_size=1,
        )

        self.assertEqual(collection["summary"]["documents_seen"], 1)
        self.assertEqual(parsing["summary"]["dlq"], 1)
        self.assertEqual(parsing["summary"]["rows_inserted"], 0)
        self.assertEqual(second_plan["summary"]["documents_skipped_collected"], 0)
        with self.db.session() as conn:
            first_document = conn.execute(
                """
                SELECT status, parse_status, parse_error_message
                FROM collection_plan_documents
                WHERE plan_id = ?
                """,
                (first_plan["plan_id"],),
            ).fetchone()
            second_document = conn.execute(
                """
                SELECT status, parse_status, raw_document_id
                FROM collection_plan_documents
                WHERE plan_id = ?
                """,
                (second_plan["plan_id"],),
            ).fetchone()

        self.assertEqual(first_document["status"], "collected")
        self.assertEqual(first_document["parse_status"], "unsupported")
        self.assertIn("unsupported encrypted DRM file", first_document["parse_error_message"])
        self.assertEqual(second_document["status"], "collected")
        self.assertEqual(second_document["parse_status"], "unsupported")
        self.assertIsNotNone(second_document["raw_document_id"])

    def test_unsupported_parse_documents_are_not_retried_automatically(self) -> None:
        adapter = FailingParsePlanningAdapter()
        pipeline = DailyPipeline(self.db, adapter=adapter, verify_new_rows=False)

        plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=1,
            max_documents=1,
            batch_size=1,
        )
        pipeline.run_collection_plan_batch(plan["plan_id"], batch_size=1)
        first_parse = pipeline.parse_collection_plan_batch(plan["plan_id"], batch_size=1)
        automatic_retry = pipeline.parse_collection_plan_batches(plan["plan_id"], batch_size=1)
        failed_retry = pipeline.retry_collection_plan_parse_failures(
            plan["plan_id"],
            batch_size=1,
        )

        self.assertEqual(first_parse["summary"]["dlq"], 1)
        self.assertEqual(automatic_retry["summary"]["pending_before"], 0)
        self.assertEqual(automatic_retry["summary"]["documents_seen"], 0)
        self.assertEqual(failed_retry["summary"]["retried_parse_failed"], 0)
        with self.db.session() as conn:
            document = conn.execute(
                """
                SELECT parse_status, parse_attempts
                FROM collection_plan_documents
                WHERE plan_id = ?
                """,
                (plan["plan_id"],),
            ).fetchone()

        self.assertEqual(document["parse_status"], "unsupported")
        self.assertEqual(document["parse_attempts"], 1)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO collection_plan_documents
                  (plan_id, source_url, source_title, department_name, published_at,
                   status, parse_status, error_message)
                VALUES (?, 'fixture://busan/planning/failed-collection',
                        '수집 실패 업무추진비', '총무과', '2026-06-29',
                        'failed', 'not_requested', 'download failed')
                """,
                (plan["plan_id"],),
            )
        document_board = RestaurantService(self.db).admin_documents(
            "2026-01-01",
            "2026-12-31",
            limit=10,
        )
        unsupported_board = RestaurantService(self.db).admin_documents(
            "2026-01-01",
            "2026-12-31",
            parse_status="unsupported",
            limit=10,
        )
        pending_board = RestaurantService(self.db).admin_documents(
            "2026-01-01",
            "2026-12-31",
            parse_status="not_requested",
            limit=10,
        )
        self.assertEqual(document_board["summary"]["parse_pending"], 0)
        self.assertEqual(document_board["summary"]["parse_failed"], 0)
        self.assertEqual(document_board["summary"]["parse_unsupported"], 1)
        self.assertEqual(unsupported_board["total"], 1)
        self.assertEqual(pending_board["total"], 0)

    def test_failed_parse_documents_retry_only_through_retry_action(self) -> None:
        adapter = FlakyParsePlanningAdapter()
        pipeline = DailyPipeline(self.db, adapter=adapter, verify_new_rows=False)

        plan = pipeline.create_collection_plan(
            start_date="2026-01-01",
            end_date="2026-12-31",
            max_pages=1,
            max_documents=1,
            batch_size=1,
        )
        pipeline.run_collection_plan_batch(plan["plan_id"], batch_size=1)
        first_parse = pipeline.parse_collection_plan_batch(plan["plan_id"], batch_size=1)
        automatic_retry = pipeline.parse_collection_plan_batches(plan["plan_id"], batch_size=1)
        failed_retry = pipeline.retry_collection_plan_parse_failures(
            plan["plan_id"],
            batch_size=1,
        )

        self.assertEqual(first_parse["summary"]["dlq"], 1)
        self.assertEqual(automatic_retry["summary"]["pending_before"], 0)
        self.assertEqual(automatic_retry["summary"]["documents_seen"], 0)
        self.assertEqual(failed_retry["summary"]["retried_parse_failed"], 1)
        self.assertEqual(failed_retry["summary"]["documents_parsed"], 1)
        self.assertEqual(failed_retry["summary"]["rows_inserted"], 1)
        with self.db.session() as conn:
            document = conn.execute(
                """
                SELECT parse_status, parse_attempts, rows_inserted
                FROM collection_plan_documents
                WHERE plan_id = ?
                """,
                (plan["plan_id"],),
            ).fetchone()

        self.assertEqual(document["parse_status"], "parsed")
        self.assertEqual(document["parse_attempts"], 2)
        self.assertEqual(document["rows_inserted"], 1)

    def test_postgres_schema_is_initialized_and_seeded(self) -> None:
        self.assertGreater(self.db.count("regions"), 0)
        self.assertEqual(self.db.count("source_registry"), len(iter_source_catalog()))
        with self.db.session() as conn:
            tables = conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = current_schema() AND table_name = 'regions'
                """
            ).fetchall()
        self.assertEqual([row["table_name"] for row in tables], ["regions"])

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

    def test_structured_address_matching_ignores_floor_and_room_details(self) -> None:
        self.assertEqual(strip_address_detail("부산광역시 연제구 토곡로 7 1층"), "부산광역시 연제구 토곡로 7")
        self.assertTrue(
            structured_address_match(
                "부산광역시 연제구 토곡로 7",
                "부산광역시 연제구 토곡로 7 201호",
            )
        )
        self.assertTrue(
            structured_address_match(
                "부산광역시 연제구 연산동 490-30 1층",
                "부산광역시 연제구 연산동 490-30",
            )
        )
        self.assertFalse(
            structured_address_match(
                "부산광역시 연제구 토곡로 7",
                "부산광역시 연제구 토곡로 9",
            )
        )
        self.assertEqual(
            normalized_address_similarity("부산광역시 연제구 토곡로 7 1층", "부산광역시 연제구 토곡로 7"),
            1.0,
        )

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

    def test_settings_permit_timeout_is_advisory_and_does_not_dlq(self) -> None:
        settings = Settings(
            database_url=postgres_test_url(),
            data_go_kr_service_key="test-key",
        )

        with patch("app.pipeline.DataGoKrPermitClient", return_value=TimeoutPermitClient()):
            result = DailyPipeline(
                self.db,
                adapter=AddresslessAdapter(),
                verifier=VerifierAgent(ExactNaverClient(), EmptyPermitClient()),
                settings=settings,
            ).run()

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["dlq"], 0)
        self.assertEqual(result["summary"]["permit_advisory_unavailable"], 1)
        with self.db.session() as conn:
            audit = conn.execute(
                """
                SELECT reason_codes_json
                FROM decision_audit_logs
                WHERE action = 'approved'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()["reason_codes_json"]
            api_error = conn.execute(
                """
                SELECT error_message
                FROM api_call_logs
                WHERE provider = 'data_go_kr'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()["error_message"]

        self.assertIn("PERMIT_ADVISORY_UNAVAILABLE", audit)
        self.assertIn("read operation timed out", api_error)

    def test_settings_permit_not_active_is_advisory_and_does_not_override_naver_approval(self) -> None:
        settings = Settings(
            database_url=postgres_test_url(),
            data_go_kr_service_key="test-key",
        )

        with patch("app.pipeline.DataGoKrPermitClient", return_value=ClosedPermitClient()):
            result = DailyPipeline(
                self.db,
                adapter=AddresslessAdapter(),
                verifier=VerifierAgent(ExactNaverClient(), EmptyPermitClient()),
                settings=settings,
            ).run()

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["needs_review"], 0)
        self.assertEqual(result["summary"]["dlq"], 0)
        self.assertEqual(result["summary"]["permit_advisory_checked"], 1)
        with self.db.session() as conn:
            verification = conn.execute(
                """
                SELECT verification_status, verification_reason
                FROM place_verifications
                """
            ).fetchone()

        self.assertEqual(verification["verification_status"], "success")
        self.assertIn("PERMIT_ADVISORY_NOT_ACTIVE", verification["verification_reason"])

    def test_active_permit_address_resolves_one_matching_food_candidate(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="공원도시과",
            used_date="2026-04-21",
            place_name="메이리",
            address="",
            purpose="관계기관 간담회 개최",
            amount=100000,
            normalized_place_name="메이리",
            normalized_address="",
        )
        decision = VerificationDecision(
            decision="needs_review",
            confidence=0.58,
            approved_by="ai",
            selected_candidate=None,
            category="restaurant",
            reason_codes=["AI_BOUNDARY_SCORE", "PERMIT_ADVISORY_ACTIVE_FOOD"],
            evidence={
                "candidate_evidence": [
                    {
                        "provider_place_id": "matching",
                        "name": "메이리 대한민국 조리기능장의집",
                        "category": "restaurant",
                        "address": "부산광역시 연제구 연산동 579-64",
                        "road_address": "부산광역시 연제구 거제시장로 3",
                        "longitude": 129.08,
                        "latitude": 35.18,
                    },
                    {
                        "provider_place_id": "different",
                        "name": "메이리연 대한민국 조리기능장의집",
                        "category": "restaurant",
                        "address": "부산광역시 연제구 거제동 1",
                        "road_address": "부산광역시 연제구 거제천로 93",
                        "longitude": 129.07,
                        "latitude": 35.19,
                    },
                ]
            },
        )
        permit = PermitSnapshot(
            permit_id="permit-meiri",
            category="restaurant",
            business_status="active",
            address="부산광역시 연제구 거제시장로 3, 1,2층",
        )

        resolved = advisory_permit_resolution_decision(row, decision, permit)

        self.assertEqual(resolved.decision, "approved")
        self.assertEqual(resolved.selected_candidate.provider_place_id, "matching")
        self.assertIn("PERMIT_ADVISORY_ACTIVE_ADDRESS_MATCH", resolved.reason_codes)

    def test_active_permit_requires_same_structured_address_not_similar_district(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-05",
            place_name="도미노피자",
            address="",
            purpose="노고 격려 간식 구입",
            amount=100000,
            normalized_place_name="도미노피자",
            normalized_address="",
        )
        candidate = PlaceCandidate(
            provider_place_id="domino-busandae",
            name="도미노피자 부산대점",
            category="restaurant",
            address="부산광역시 금정구 금정로 54",
            road_address="부산광역시 금정구 금정로 54",
            longitude=129.08,
            latitude=35.23,
        )
        decision = VerificationDecision(
            decision="needs_review",
            confidence=0.8,
            approved_by="rule",
            selected_candidate=candidate,
            category="restaurant",
            reason_codes=["ADDRESSLESS_TOO_MANY_MATCHES"],
            evidence={
                "candidate_evidence": [
                    {
                        "provider_place_id": candidate.provider_place_id,
                        "name": candidate.name,
                        "category": candidate.category,
                        "address": candidate.address,
                        "road_address": candidate.road_address,
                        "longitude": candidate.longitude,
                        "latitude": candidate.latitude,
                    }
                ]
            },
        )
        permit = PermitSnapshot(
            permit_id="permit-domino-different-road",
            category="restaurant",
            business_status="active",
            address="부산광역시 금정구 금샘로 377",
        )

        resolved = advisory_permit_resolution_decision(row, decision, permit)

        self.assertEqual(resolved.decision, "needs_review")

    def test_active_permit_matching_alternative_downgrades_unique_exact_approval(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-05",
            place_name="꽃다림",
            address="",
            purpose="만찬 간담회",
            amount=100000,
            normalized_place_name="꽃다림",
            normalized_address="",
        )
        candidates = [
            PlaceCandidate(
                provider_place_id="exact",
                name="꽃다림",
                category="restaurant",
                address="부산광역시 동래구 중앙대로1367번길 26",
                road_address="부산광역시 동래구 중앙대로1367번길 26",
                longitude=129.08,
                latitude=35.2,
            ),
            PlaceCandidate(
                provider_place_id="permit-match",
                name="꽃다림 국밥전문점",
                category="restaurant",
                address="부산광역시 해운대구 반송로 812",
                road_address="부산광역시 해운대구 반송로 812",
                longitude=129.15,
                latitude=35.23,
            ),
        ]
        approved = VerifierAgent(
            type("StaticNaver", (), {"search_local": lambda self, ignored: candidates})(),
            EmptyPermitClient(),
        ).verify(row)
        permit = PermitSnapshot(
            permit_id="permit-flower",
            category="restaurant",
            business_status="active",
            address="부산광역시 해운대구 반송로 812, 1층",
        )

        resolved = advisory_permit_conflict_decision(row, approved, permit)
        permit_selected = advisory_permit_resolution_decision(row, resolved, permit)

        self.assertEqual(approved.decision, "approved")
        self.assertEqual(resolved.decision, "needs_review")
        self.assertIn("PERMIT_ADVISORY_CONFLICTING_CANDIDATE", resolved.reason_codes)
        self.assertEqual(permit_selected.decision, "approved")
        self.assertEqual(permit_selected.selected_candidate.provider_place_id, "permit-match")

    def test_active_permit_address_can_trigger_one_enriched_naver_search(self) -> None:
        class PermitAddressNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                if not row.address:
                    return []
                return [
                    PlaceCandidate(
                        provider_place_id="naver-permit-address",
                        name="제일옥곰탕",
                        category="restaurant",
                        address="부산광역시 연제구 연산동 1",
                        road_address="부산광역시 연제구 진연로 30-9",
                        longitude=129.09,
                        latitude=35.18,
                    )
                ]

        row = NormalizedExpenseRow(
            row_number=1,
            department_name="맑은물정책과",
            used_date="2026-05-22",
            place_name="제일옥곰탕",
            address="",
            purpose="수질개선 관련 간담회 개최",
            amount=100000,
            normalized_place_name="제일옥곰탕",
            normalized_address="",
        )
        decision = VerificationDecision(
            decision="needs_review",
            confidence=0.5,
            approved_by="ai",
            selected_candidate=None,
            category="restaurant",
            reason_codes=["AI_NO_MAP_CANDIDATE", "PERMIT_ADVISORY_ACTIVE_FOOD"],
            evidence={"candidate_evidence": []},
        )
        permit = PermitSnapshot(
            permit_id="permit-jeilok",
            category="restaurant",
            business_status="active",
            address="부산광역시 연제구 진연로 30-9, 1층",
        )
        pipeline = DailyPipeline(
            self.db,
            verifier=VerifierAgent(PermitAddressNaverClient(), EmptyPermitClient()),
        )

        with self.db.session() as conn:
            resolved = pipeline._resolve_with_permit_address(conn, row, decision, permit)

        self.assertEqual(resolved.decision, "approved")
        self.assertEqual(resolved.selected_candidate.name, "제일옥곰탕")
        self.assertIn("PERMIT_ADVISORY_ADDRESS_ENRICHED", resolved.reason_codes)

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

    def test_manual_review_stores_multiple_naver_candidates(self) -> None:
        result = DailyPipeline(
            self.db,
            adapter=GenericAddresslessAdapter(),
            verifier=VerifierAgent(MultiCandidateNaverClient(), EmptyPermitClient()),
        ).run()

        self.assertEqual(result["summary"]["approved"], 0)
        self.assertEqual(result["summary"]["needs_review"], 1)
        with self.db.session() as conn:
            verifications = conn.execute(
                """
                SELECT provider_place_name, verification_status
                FROM place_verifications
                ORDER BY name_similarity DESC, id ASC
                """
            ).fetchall()

        self.assertEqual([row["provider_place_name"] for row in verifications], ["씨드", "씨드커피"])
        self.assertEqual({row["verification_status"] for row in verifications}, {"ambiguous"})

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

    def test_verify_pending_auto_approves_pending_task_with_existing_success_evidence(self) -> None:
        DailyPipeline(self.db).run()
        with self.db.session() as conn:
            conn.execute("UPDATE manual_review_tasks SET status = 'test_ignored'")
            candidate = conn.execute(
                """
                SELECT id
                FROM restaurant_candidates
                WHERE original_place_name = '부산돼지국밥 시청점'
                """
            ).fetchone()
            conn.execute(
                """
                UPDATE restaurant_candidates
                SET status = 'needs_review',
                    verification_status = 'ambiguous',
                    manual_review_status = 'pending'
                WHERE id = ?
                """,
                (candidate["id"],),
            )
            conn.execute(
                """
                INSERT INTO manual_review_tasks (candidate_id, status, reason)
                VALUES (?, 'pending', 'reset_from_test_approval')
                ON CONFLICT(candidate_id) DO UPDATE SET status = 'pending', reason = excluded.reason
                """,
                (candidate["id"],),
            )

        result = DailyPipeline(
            self.db,
            verifier=VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient()),
        ).verify_pending(limit=10)

        self.assertEqual(result["summary"]["approved"], 1)
        self.assertEqual(result["summary"]["dlq"], 0)
        with self.db.session() as conn:
            task = conn.execute(
                "SELECT status, reason FROM manual_review_tasks WHERE candidate_id = ?",
                (candidate["id"],),
            ).fetchone()
            latest_reason = conn.execute(
                """
                SELECT reason_codes_json
                FROM decision_audit_logs
                WHERE target_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (candidate["id"],),
            ).fetchone()["reason_codes_json"]

        self.assertEqual(task["status"], "auto_approved")
        self.assertIn("EXISTING_SUCCESS_VERIFICATION", task["reason"])
        self.assertIn("EXISTING_SUCCESS_VERIFICATION", latest_reason)

    def test_existing_provider_evidence_does_not_override_not_active_permit_reason(self) -> None:
        with self.db.session() as conn:
            doc = conn.execute(
                """
                INSERT INTO raw_documents
                  (institution_id, source_url, source_title, published_at, collected_at, content_hash)
                VALUES (1, 'fixture://not-active-provider', '인허가 비활성 후보', '2026-06-05',
                        CURRENT_TIMESTAMP, 'not-active-provider')
                """
            )
            expense = conn.execute(
                """
                INSERT INTO expense_records
                  (raw_document_id, institution_id, region_id, source_row_number, department_name,
                   used_date, place_name, purpose, amount, participants, payment_method,
                   original_row_json, normalized_place_name, normalized_purpose,
                   is_food_candidate, candidate_reason, row_hash)
                VALUES (?, 1, 1, 1, '총무과', '2026-06-05', '제주복국', '업무협의 간담회',
                        100000, '4명', 'card', '{}', '제주복국', '업무협의 간담회', 1,
                        'not_active_provider_test', 'not-active-provider-row')
                """,
                (doc.lastrowid,),
            )
            candidate = conn.execute(
                """
                INSERT INTO restaurant_candidates
                  (expense_record_id, institution_id, region_id, original_place_name,
                   normalized_place_name, original_address, normalized_address, used_date,
                   amount, place_major_category, status, verification_status,
                   manual_review_status, extraction_reason)
                VALUES (?, 1, 1, '제주복국', '제주복국', '', '', '2026-06-05', 100000,
                        'restaurant', 'needs_review', 'ambiguous', 'pending',
                        'not_active_provider_test')
                """,
                (expense.lastrowid,),
            )
            conn.execute(
                """
                INSERT INTO manual_review_tasks (candidate_id, status, reason)
                VALUES (?, 'pending', 'PERMIT_NOT_ACTIVE_ADDRESSLESS')
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
                VALUES (?, 'naver', 'naver-jeju-001', '제주복국',
                        'restaurant', '부산광역시 연제구 연산동 1',
                        '부산광역시 연제구 중앙대로 1', '제주복국',
                        '부산광역시 연제구 연산동 1', 129.075, 35.18,
                        1.0, 0.0, 1, 0, 1, 1, 'ambiguous',
                        'PERMIT_NOT_ACTIVE_ADDRESSLESS', '{}')
                """,
                (candidate.lastrowid,),
            )
            decision = existing_provider_evidence_decision(
                conn,
                int(candidate.lastrowid),
                NormalizedExpenseRow(
                    row_number=1,
                    department_name="총무과",
                    used_date="2026-06-05",
                    place_name="제주복국",
                    address="",
                    purpose="업무협의 간담회",
                    amount=100000,
                    normalized_place_name="제주복국",
                    normalized_address="",
                ),
            )

        self.assertIsNone(decision)

    def test_branch_hint_with_unspecified_companion_auto_rejects(self) -> None:
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

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["UNSPECIFIED_COMPANION_PLACES"])
        self.assertEqual(decision.evidence["candidate_evidence"], [])

    def test_addressless_strong_naver_branch_with_companion_auto_rejects(self) -> None:
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

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["UNSPECIFIED_COMPANION_PLACES"])

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

    def test_cleaned_address_match_can_auto_approve_food_candidate(self) -> None:
        verifier = VerifierAgent(FloorAddressNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="토곡정",
                address="부산광역시 연제구 토곡로 7 201호",
                purpose="업무협의 간담회",
                amount=100000,
                normalized_place_name="토곡정",
                normalized_address="부산광역시 연제구 토곡로 7 201호",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_NORMALIZED_ADDRESS_MATCH", decision.reason_codes)
        self.assertEqual(decision.evidence["address_similarity"], 1.0)

    def test_cleaned_address_mismatch_stays_manual_review(self) -> None:
        verifier = VerifierAgent(WrongAddressNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="토곡정",
                address="부산광역시 연제구 토곡로 7 1층",
                purpose="업무협의 간담회",
                amount=100000,
                normalized_place_name="토곡정",
                normalized_address="부산광역시 연제구 토곡로 7 1층",
            )
        )

        self.assertEqual(decision.decision, "needs_review")

    def test_cleaned_address_match_keeps_other_category_for_food_context_in_review(self) -> None:
        verifier = VerifierAgent(OtherAddressNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="토곡정",
                address="부산광역시 연제구 토곡로 7 1층",
                purpose="업무협의 간담회",
                amount=100000,
                normalized_place_name="토곡정",
                normalized_address="부산광역시 연제구 토곡로 7 1층",
            )
        )

        self.assertEqual(decision.decision, "needs_review")

    def test_cleaned_address_match_does_not_override_closed_permit(self) -> None:
        verifier = VerifierAgent(FloorAddressNaverClient(), ClosedDifferentAddressPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="토곡정",
                address="부산광역시 연제구 토곡로 7 1층",
                purpose="업무협의 간담회",
                amount=100000,
                normalized_place_name="토곡정",
                normalized_address="부산광역시 연제구 토곡로 7 1층",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertIn("PERMIT_NOT_ACTIVE_FOOD", decision.reason_codes)

    def test_other_category_without_food_context_is_auto_rejected(self) -> None:
        verifier = VerifierAgent(OtherNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="동자상회",
                address="",
                purpose="보훈업무 추진협조를 위한 보훈복지회관 등 방문계획",
                amount=1200000,
                normalized_place_name="동자상회",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["NAVER_OTHER_CATEGORY"])

    def test_hotel_other_category_stays_manual_for_banquet_or_lodging_ambiguity(self) -> None:
        class HotelNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                return [
                    PlaceCandidate(
                        provider_place_id="lotte-hotel-busan",
                        name="롯데호텔 부산",
                        category="other",
                        address="부산광역시 부산진구 부전동 503-15",
                        road_address="부산광역시 부산진구 가야대로 772",
                        longitude=129.055,
                        latitude=35.156,
                    )
                ]

        decision = VerifierAgent(HotelNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="㈜부산롯데호텔",
                address="",
                purpose="행사 운영",
                amount=1200000,
                normalized_place_name="㈜부산롯데호텔",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertEqual(decision.reason_codes, ["NAVER_OTHER_AMBIGUOUS_VENUE"])

    def test_brand_and_companion_suffix_auto_rejects_without_companion_identity(self) -> None:
        verifier = VerifierAgent(EdiyaNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="이디야 김해국제공항국제선점외 2",
                address="",
                purpose="공무국외출장 의전 관련 주요기관 관계자 간담회",
                amount=125300,
                normalized_place_name="이디야 김해국제공항국제선점외 2",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["UNSPECIFIED_COMPANION_PLACES"])

    def test_addressless_franchise_without_branch_is_rejected_even_with_one_candidate(self) -> None:
        verifier = VerifierAgent(BaseSuffixNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="취원",
                address="",
                purpose="국내외 주요인사 의전 관련 논의를 위한 간담회",
                amount=480000,
                normalized_place_name="취원",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["FRANCHISE_ADDRESSLESS_NO_BRANCH"])

    def test_addressless_fuzzy_food_name_can_auto_approve_without_permit(self) -> None:
        verifier = VerifierAgent(YeyijeNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="예이제",
                address="",
                purpose="국제 컨퍼런스 개최에 따른 간담회 개최",
                amount=798000,
                normalized_place_name="예이제",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_FUZZY_ADDRESSLESS", decision.reason_codes)
        self.assertGreaterEqual(decision.evidence["name_similarity"], 0.7)

    def test_addressless_fuzzy_70_percent_food_name_can_auto_approve_without_permit(self) -> None:
        verifier = VerifierAgent(DongjingaNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="동진가셀프식당",
                address="",
                purpose="업무협의 간담회",
                amount=95000,
                normalized_place_name="동진가셀프식당",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_FUZZY_ADDRESSLESS", decision.reason_codes)
        self.assertGreaterEqual(decision.evidence["name_similarity"], 0.7)

    def test_addressless_fuzzy_name_does_not_auto_approve_branch_conflict(self) -> None:
        verifier = VerifierAgent(BranchConflictNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="대박통(명륜점)",
                address="",
                purpose="업무협의 간담회",
                amount=110000,
                normalized_place_name="대박통",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertLess(decision.evidence["name_similarity"], 0.7)

    def test_addressless_exact_branch_can_auto_approve_non_busan_candidate(self) -> None:
        verifier = VerifierAgent(NonBusanFuzzyNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="강남면옥 정동점",
                address="",
                purpose="업무협의 간담회",
                amount=120000,
                normalized_place_name="강남면옥 정동점",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_EXACT_ADDRESSLESS", decision.reason_codes)
        self.assertIn("PROVIDER_ADDRESS_AVAILABLE", decision.reason_codes)

    def test_addressless_duplicate_exact_names_stay_manual_review(self) -> None:
        class DuplicateExactNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                return [
                    PlaceCandidate(
                        provider_place_id="first",
                        name="송이네밥상",
                        category="restaurant",
                        address="서울특별시 도봉구 방학로2길 12",
                        road_address="서울특별시 도봉구 방학로2길 12",
                        longitude=127.0,
                        latitude=37.6,
                    ),
                    PlaceCandidate(
                        provider_place_id="second",
                        name="송이네밥상",
                        category="restaurant",
                        address="경상북도 문경시 문경읍 새재로 660",
                        road_address="경상북도 문경시 문경읍 새재로 660",
                        longitude=128.1,
                        latitude=36.7,
                    ),
                ]

        decision = VerifierAgent(DuplicateExactNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="송이네밥상",
                address="",
                purpose="오찬 간담회",
                amount=120000,
                normalized_place_name="송이네밥상",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")

    def test_addressless_three_exact_names_stay_selectable_for_manual_review(self) -> None:
        class ThreeExactNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                return [
                    PlaceCandidate(
                        provider_place_id=f"place-{index}",
                        name="송이네밥상",
                        category="restaurant",
                        address=address,
                        road_address=address,
                        longitude=127.0 + index,
                        latitude=35.0 + index,
                    )
                    for index, address in enumerate(
                        [
                            "서울특별시 도봉구 방학로2길 12",
                            "경상북도 문경시 문경읍 새재로 660",
                            "전라남도 여수시 중앙로 10",
                        ]
                    )
                ]

        decision = VerifierAgent(ThreeExactNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="송이네밥상",
                address="",
                purpose="오찬 간담회",
                amount=120000,
                normalized_place_name="송이네밥상",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertEqual(decision.reason_codes, ["ADDRESSLESS_TOO_MANY_MATCHES"])
        self.assertEqual(decision.selected_candidate.name, "송이네밥상")
        self.assertEqual(len(decision.evidence["candidate_evidence"]), 3)
        self.assertEqual(
            {item["provider_place_id"] for item in decision.evidence["candidate_evidence"]},
            {"place-0", "place-1", "place-2"},
        )

    def test_unique_exact_name_auto_approves_despite_multiple_fuzzy_candidates(self) -> None:
        class ExactAmongFuzzyNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                names = ["해운대바다정식당", "해운대바다정식당A", "해운대바다정식당B"]
                return [
                    PlaceCandidate(
                        provider_place_id=f"fuzzy-{index}",
                        name=name,
                        category="restaurant",
                        address=address,
                        road_address=address,
                        longitude=129.1 + index * 0.01,
                        latitude=35.1 + index * 0.01,
                    )
                    for index, (name, address) in enumerate(
                        zip(
                            names,
                            [
                                "부산광역시 해운대구 해운대로 1",
                                "부산광역시 수영구 광안해변로 2",
                                "부산광역시 동구 중앙대로 3",
                            ],
                            strict=True,
                        )
                    )
                ]

        decision = VerifierAgent(ExactAmongFuzzyNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="해운대바다정식당",
                address="",
                purpose="업무협의 오찬",
                amount=120000,
                normalized_place_name="해운대바다정식당",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertEqual(decision.selected_candidate.name, "해운대바다정식당")
        self.assertIn("NAVER_UNIQUE_EXACT_AMONG_MULTIPLE", decision.reason_codes)

    def test_addressless_fuzzy_name_does_not_override_closed_same_address_permit(self) -> None:
        verifier = VerifierAgent(YeyijeNaverClient(), ClosedYeyijePermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="예이제",
                address="",
                purpose="국제 컨퍼런스 개최에 따른 간담회 개최",
                amount=798000,
                normalized_place_name="예이제",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertEqual(decision.reason_codes, ["PERMIT_NOT_ACTIVE_ADDRESSLESS"])

    def test_famous_franchise_addressless_with_companion_auto_rejects(self) -> None:
        verifier = VerifierAgent(StarbucksNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="스타벅스 코리아 외 2",
                address="",
                purpose="업무협의 간담회",
                amount=120000,
                normalized_place_name="스타벅스 코리아 외 2",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["UNSPECIFIED_COMPANION_PLACES"])
        self.assertEqual(decision.category, "cafe")
        self.assertIsNone(decision.selected_candidate)
        self.assertEqual(decision.evidence["candidate_evidence"], [])

    def test_vips_addressless_without_branch_without_result_auto_rejects(self) -> None:
        verifier = VerifierAgent(EmptyNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="빕스",
                address="",
                purpose="업무협의 간담회",
                amount=180000,
                normalized_place_name="빕스",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["FRANCHISE_ADDRESSLESS_NO_BRANCH"])
        self.assertEqual(decision.category, "restaurant")
        self.assertIsNone(decision.selected_candidate)

    def test_famous_franchise_with_branch_continues_and_can_approve(self) -> None:
        verifier = VerifierAgent(StarbucksNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="스타벅스 경성대점",
                address="",
                purpose="업무협의 간담회",
                amount=90000,
                normalized_place_name="스타벅스 경성대점",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("PERMIT_MISSING_ALLOWED", decision.reason_codes)
        self.assertEqual(decision.selected_candidate.name, "스타벅스 경성대점")

    def test_famous_franchise_without_branch_can_approve_by_address(self) -> None:
        verifier = VerifierAgent(StarbucksNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="스타벅스 코리아",
                address="부산광역시 남구 수영로 312",
                purpose="업무협의 간담회",
                amount=90000,
                normalized_place_name="스타벅스 코리아",
                normalized_address="부산광역시 남구 수영로 312",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_FRANCHISE_ADDRESS_MATCH", decision.reason_codes)
        self.assertEqual(decision.selected_candidate.name, "스타벅스 경성대점")

    def test_learned_legal_entity_without_search_result_is_rejected(self) -> None:
        verifier = VerifierAgent(EmptyNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="(주)신화케이푸드",
                address="",
                purpose="중앙부처 관계자와의 간담회 개최",
                amount=120000,
                normalized_place_name="(주)신화케이푸드",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["MANUAL_FEEDBACK_ADDRESSLESS_NO_MAP_MATCH"])

    def test_payment_processor_place_name_auto_rejects_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())

        for place_name in (
            "부산동백전_KICC_C외 3",
            "네이버파이낸셜",
            "다날",
            "에스에스지페이먼츠",
            "에스에스지페이",
            "한국결제_B",
            "이니시스",
            "(주)플래티넘페이먼츠",
            "헥토파이낸셜_이지피쥐",
            "KCP",
            "BC카드(부산근현대역사관)",
        ):
            with self.subTest(place_name=place_name):
                decision = verifier.verify(
                    NormalizedExpenseRow(
                        row_number=1,
                        department_name="총무과",
                        used_date="2026-06-05",
                        place_name=place_name,
                        address="",
                        purpose="행사 관계자 간담회",
                        amount=45000,
                        normalized_place_name=place_name,
                        normalized_address="",
                    )
                )

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(decision.reason_codes, ["PAYMENT_PROCESSOR_OR_CARD_PLACE_NAME"])

    def test_anonymized_place_name_auto_rejects_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())

        for place_name in (
            "김OO",
            "황OO",
            "김*우",
            "ㅇㅇ장례식장",
            "ㅇㅇ결혼식장",
            "00장례식장",
            "00결혼식장",
            "00웨딩홀",
            "OO결혼식장",
        ):
            with self.subTest(place_name=place_name):
                decision = verifier.verify(
                    NormalizedExpenseRow(
                        row_number=1,
                        department_name="총무과",
                        used_date="2026-06-05",
                        place_name=place_name,
                        address="",
                        purpose="간담회",
                        amount=45000,
                        normalized_place_name=place_name,
                        normalized_address="",
                    )
                )

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(decision.reason_codes, ["ANONYMIZED_PLACE_NAME"])

    def test_manual_feedback_closed_place_auto_rejects_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="포도나무면가",
                address="",
                purpose="민관합동TF팀 간담회",
                amount=100000,
                normalized_place_name="포도나무면가",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["MANUAL_FEEDBACK_CLOSED_OR_NOT_OPERATING"])

    def test_manual_feedback_non_restaurant_organization_auto_rejects_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())

        for place_name in (
            "연제지역자활센터",
            "연제지역자할센터",
            "부산수영지역자활센터",
            "주식회사 큐매니지먼트",
        ):
            with self.subTest(place_name=place_name):
                decision = verifier.verify(
                    NormalizedExpenseRow(
                        row_number=1,
                        department_name="총무과",
                        used_date="2026-06-26",
                        place_name=place_name,
                        address="",
                        purpose="여성정책분야 추진방향 논의 간담회 개최",
                        amount=0,
                        normalized_place_name=place_name,
                        normalized_address="",
                    )
                )

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(
                    decision.reason_codes,
                    ["MANUAL_FEEDBACK_NON_RESTAURANT_ORGANIZATION"],
                )

    def test_repeated_unlisted_airport_store_auto_rejects_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())
        place_name = "풀무원푸드앤컬처 김해공항 말똥도넛"

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-26",
                place_name=place_name,
                address="",
                purpose="회의 다과 구입",
                amount=30000,
                normalized_place_name=place_name,
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["MANUAL_FEEDBACK_ADDRESSLESS_NO_MAP_MATCH"])

    def test_manual_feedback_non_restaurant_retail_auto_rejects_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())

        for place_name in ("다드림마트", "(주)메가마트덕천점"):
            with self.subTest(place_name=place_name):
                decision = verifier.verify(
                    NormalizedExpenseRow(
                        row_number=1,
                        department_name="총무과",
                        used_date="2026-06-26",
                        place_name=place_name,
                        address="",
                        purpose="간담회",
                        amount=50000,
                        normalized_place_name=place_name,
                        normalized_address="",
                    )
                )

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(
                    decision.reason_codes,
                    ["MANUAL_FEEDBACK_NON_RESTAURANT_RETAIL"],
                )

    def test_repeated_addressless_manual_rejections_apply_before_api(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())

        for place_name in (
            "풍미",
            "픙미",
            "지앤지 시청점",
            "지엔지 시청점",
            "비스트로",
            "미락",
            "삼봉맥주",
            "바로테이스티",
            "북경",
            "토종먹거리",
            "한화커넥트",
            "한화케넥트㈜",
            "화담",
            "영남식육식당",
            "토곡식육식당",
            "반상",
            "명소",
            "온정",
            "이도식당",
            "수정궁",
            "고래",
            "한화호텔앤드리조트",
            "이랜드파크 켄싱턴호텔 여의도",
            "에이치엘비",
            "댄싱컵 부산시청점",
            "(주)신세계프라퍼티 코엑스몰",
            "비원 옛 해운대",
            "삼환축산 해운대직영점",
            "승승집 양정점",
            "홍미당 광안리점",
            "유가네한우곰탕 명지점",
            "원조지구촌암소숯불생고기",
            "자갈치옛날과자점",
            "떡의궁전",
            "풍어참가자미횟집",
            "바다원횟집",
            "㈜신화케이푸드",
            "남지강변횟집",
            "화지회관 식육식당",
            "묘묘당",
            "아키노히",
            "구일비 시청점",
            "몫몫",
            "주식회사 월링워커스 몫몫",
            "백마김씨네",
            "울진후포리청솔물회",
            "영광미소네집",
            "박수 에프엔비",
            "주식회사 갓솥비바에이",
            "소잡은어부",
            "고반식당",
            "카페젤라떼리아",
            "청춘횟집",
            "주식회사 우리의맛남 정성순대",
            "소제이연산점",
            "풍문 2층",
            "연화리해녀집장씨해녀집",
            "정초밥 솔티라이엇",
            "수제국수 부산시청점 블루샥 부산시청점",
            "규가 카페에쯔",
            "메이리 웁스",
            "주식회사 탑월드후레쉬",
            "주식회사 대상크리스탈시상품",
            "라온그루홈",
            "(주)라온사람들 서울안심정육",
            "마키노차야 더가우",
            "부용당 로우라이즈",
            "주식회사 에프지케이",
            "㈜아르프스튜디오",
            "도프커피 서여의도점",
            "모리 호수공원점",
            "주식회사 디케이씨에스",
            "카페드롭탑국제여객터미널",
            "㈜조선호텔앤리조트 그래드조선",
            "주식회사 아난티 오시리아 제1지점",
            "마당쇠민속보쌈",
            "130 팔선생",
            "주식회사 별무리애프앤비",
            "주식회사 뚜리삼",
            "Albert Hejin 3138",
            "BAVET ANTWERPEN",
            "De Blaauwe Kamer Wageningen NL",
            "RADISSON BLU OS OSLO",
            "A377 NO OSL Olearys int AGardemoen",
            "MITSUIFUDOUSANGURU-PUU Tokyo JPN",
            "NURSET STEAKHOUSE SAND",
            "TAVERNA DEL TEATRE",
            "CANALCITYPOA",
            "BLACK TAP",
        ):
            with self.subTest(place_name=place_name):
                decision = verifier.verify(
                    NormalizedExpenseRow(
                        row_number=1,
                        department_name="총무과",
                        used_date="2026-06-26",
                        place_name=place_name,
                        address="",
                        purpose="업무협의 간담회",
                        amount=50000,
                        normalized_place_name=place_name,
                        normalized_address="",
                    )
                )

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(decision.evidence["manual_review_passes"], 3)

    def test_reapproved_jeilok_is_not_blocked_by_old_manual_feedback(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-26",
            place_name="제일옥곰탕",
            address="",
            purpose="업무협의 간담회",
            amount=50000,
            normalized_place_name="제일옥곰탕",
            normalized_address="",
        )

        self.assertIsNone(manual_feedback_reject_decision(row))

    def test_repeated_manual_name_with_address_is_not_overgeneralized(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-26",
            place_name="풍미",
            address="부산광역시 연제구 중앙대로 1001",
            purpose="업무협의 간담회",
            amount=50000,
            normalized_place_name="풍미",
            normalized_address="부산광역시 연제구 중앙대로 1001",
        )

        self.assertIsNone(manual_feedback_reject_decision(row))

    def test_tenpercent_addressless_without_branch_auto_rejects(self) -> None:
        decision = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-26",
                place_name="텐퍼센트",
                address="",
                purpose="회의 다과 구입",
                amount=25000,
                normalized_place_name="텐퍼센트",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["FRANCHISE_ADDRESSLESS_NO_BRANCH"])

    def test_unknown_place_address_mismatch_auto_rejects(self) -> None:
        verifier = VerifierAgent(UnknownMismatchNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="미상식당",
                address="부산광역시 부산진구 중앙대로 730",
                purpose="교통 현안 간담",
                amount=51000,
                normalized_place_name="미상식당",
                normalized_address="부산광역시 부산진구 중앙대로 730",
            )
        )

        self.assertEqual(decision.decision, "rejected")
        self.assertEqual(decision.reason_codes, ["UNKNOWN_PLACE_ADDRESS_MISMATCH"])

    def test_non_food_purpose_rules_keep_snack_purchases_reviewable(self) -> None:
        self.assertEqual(non_food_purpose_reason("시정홍보 방문기념품 구입"), "NON_FOOD_PURPOSE_GIFT")
        self.assertEqual(non_food_purpose_reason("사무용품 구입"), "NON_FOOD_PURPOSE_OFFICE_SUPPLY")
        self.assertIsNone(non_food_purpose_reason("회의 다과 구입"))
        self.assertIsNone(non_food_purpose_reason("직원 격려 간식 구입"))

    def test_scope_reject_rules_run_before_external_verification(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())
        cases = [
            ("사무용품 구입", "업무협의", "PURCHASE_WORD_IN_PLACE_NAME"),
            ("쿠팡", "업무협의", "COUPANG_PLACE_NAME"),
            ("11번가", "업무협의", "ONLINE_MARKETPLACE_PLACE_NAME"),
            ("홈플러스㈜", "업무협의", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
            ("롯데마트 사상점", "내방객 차류 구입", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
            ("지에스25 뉴부산시청점", "다과류 구입", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
            ("빅 식자재마트", "업무협의", "NON_RESTAURANT_RETAIL_PLACE_NAME"),
            ("인터넷상거래", "업무협의", "ONLINE_COMMERCE_PLACE_NAME"),
            ("CJ CGV_예매(문화비)_오프라인 제휴", "업무협의", "NON_RESTAURANT_CULTURE_EXPENSE"),
            ("놀유니버스티켓(문화비외1)", "업무협의", "NON_RESTAURANT_CULTURE_EXPENSE"),
            ("동래소방서", "업무협의", "NON_RESTAURANT_PUBLIC_FACILITY"),
            ("덕산정수사업소", "업무협의", "NON_RESTAURANT_PUBLIC_FACILITY"),
            ("라온그룹홈", "업무협의", "NON_RESTAURANT_SOCIAL_WELFARE_FACILITY"),
            ("HDC현대산업개발㈜", "업무협의", "NON_RESTAURANT_CORPORATE_PLACE_NAME"),
            ("덕산정수사업소 인근 식당", "업무협의", "NON_RESTAURANT_PUBLIC_FACILITY"),
            ("관내 음식점", "업무협의", "GENERIC_LOCAL_RESTAURANT_PLACE_NAME"),
            ("토곡로 7", "업무협의", "ADDRESS_ONLY_PLACE_NAME"),
            ("고분로 13번길 43", "업무협의", "ADDRESS_ONLY_PLACE_NAME"),
            ("연제구 월드컵대로145번길 19", "업무협의", "ADDRESS_ONLY_PLACE_NAME"),
            ("6692", "업무협의", "NUMERIC_ONLY_PLACE_NAME"),
            ("부산식당", "직원 경조사 지원", "CEREMONIAL_EVENT_EXPENSE"),
            ("김혜성", "경조금 지출", "CEREMONIAL_EVENT_EXPENSE"),
            ("대한적십자사", "특별회비 납부", "NON_RESTAURANT_MEMBERSHIP_FEE"),
            (
                "RADISSON BLU OSLO SCANDIN",
                "북극항로 선도도시 전략 마련을 위한 현지간담회",
                "MANUAL_FEEDBACK_OUT_OF_REGION_VENUE",
            ),
            ("제용기", "행사 격려금 지급", "PERSONAL_RECIPIENT_PLACE_NAME"),
            (
                "제용기",
                "중부소방서 러닝크루 동호회 활동비 지급 요청",
                "PERSONAL_RECIPIENT_PLACE_NAME",
            ),
        ]

        for place_name, purpose, expected_reason in cases:
            with self.subTest(place_name=place_name, purpose=purpose):
                row = NormalizedExpenseRow(
                    row_number=1,
                    department_name="총무과",
                    used_date="2026-06-20",
                    place_name=place_name,
                    address="",
                    purpose=purpose,
                    amount=10000,
                    normalized_place_name=place_name,
                    normalized_address="",
                )
                decision = verifier.verify(row)

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(decision.reason_codes, [expected_reason])
                self.assertEqual(expense_scope_reject_decision(row), decision)

    def test_scope_rejects_retail_and_multiple_place_names(self) -> None:
        verifier = VerifierAgent(ExplodingNaverClient(), ExplodingPermitClient())
        cases = {
            "공무원매점": "NON_RESTAURANT_RETAIL_PLACE_NAME",
            "킹할인마트": "NON_RESTAURANT_RETAIL_PLACE_NAME",
            "만객당, 히얼이즈커피": "MULTIPLE_PLACE_NAMES_UNSUPPORTED",
            "샐러드와을숙도/창원식자재마트": "MULTIPLE_PLACE_NAMES_UNSUPPORTED",
            "미를담다&미담삼계탕": "MULTIPLE_PLACE_NAMES_UNSUPPORTED",
        }
        for place_name, expected_reason in cases.items():
            with self.subTest(place_name=place_name):
                row = NormalizedExpenseRow(
                    row_number=1,
                    department_name="공원도시과",
                    used_date="2026-05-12",
                    place_name=place_name,
                    address="",
                    purpose="다과 구입",
                    amount=100000,
                    normalized_place_name=normalize_text(place_name),
                    normalized_address="",
                )

                decision = verifier.verify(row)

                self.assertEqual(decision.decision, "rejected")
                self.assertEqual(decision.reason_codes, [expected_reason])

        duplicate_legal_name = NormalizedExpenseRow(
            row_number=1,
            department_name="공원도시과",
            used_date="2026-05-12",
            place_name="소소수산/주식회사소소수산",
            address="",
            purpose="간담회",
            amount=100000,
            normalized_place_name="소소수산/주식회사소소수산",
            normalized_address="",
        )
        self.assertIsNone(expense_scope_reject_decision(duplicate_legal_name))

    def test_addressless_legal_entity_without_storefront_info_is_searched(self) -> None:
        verifier = VerifierAgent(EmptyNaverClient(), EmptyPermitClient())

        decision = verifier.verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-05-12",
                place_name="주식회사 예시푸드컴퍼니",
                address="",
                purpose="업무협의 간담회",
                amount=100000,
                normalized_place_name="주식회사 예시푸드컴퍼니",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")
        self.assertEqual(decision.reason_codes, ["AI_NO_MAP_CANDIDATE"])

    def test_addressless_legal_entity_can_approve_exact_food_place(self) -> None:
        class LegalRestaurantNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                return [
                    PlaceCandidate(
                        provider_place_id="ireok-1",
                        name="이레옥",
                        category="restaurant",
                        address="부산광역시 해운대구 마린시티3로 51",
                        road_address="부산광역시 해운대구 마린시티3로 51",
                        longitude=129.143,
                        latitude=35.157,
                    )
                ]

        decision = VerifierAgent(LegalRestaurantNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-05-12",
                place_name="주식회사 이레옥",
                address="",
                purpose="업무협의 오찬",
                amount=100000,
                normalized_place_name="주식회사 이레옥",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertEqual(decision.selected_candidate.name, "이레옥")
        self.assertIn("NAVER_EXACT_ADDRESSLESS", decision.reason_codes)

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

        self.assertEqual(_search_queries(row)[:2], ["19버거테이블", "19버거테이블 부산"])

    def test_naver_search_queries_preserve_parenthetical_branch_hint(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="감사담당관",
            used_date="2026-06-05",
            place_name="대박통(명륜점)",
            address="",
            purpose="간담",
            amount=70000,
            normalized_place_name="대박통(명륜점)",
            normalized_address="",
        )

        self.assertEqual(
            _search_queries(row)[:3],
            ["대박통 명륜점", "대박통 명륜점 부산", "대박통 동래명륜점"],
        )

    def test_naver_search_queries_try_cleaned_address_before_raw_address(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-05",
            place_name="토곡정",
            address="부산광역시 연제구 토곡로 7 1층",
            purpose="간담",
            amount=100000,
            normalized_place_name="토곡정",
            normalized_address="부산광역시 연제구 토곡로 7 1층",
        )

        self.assertEqual(_search_queries(row)[:3], [
            "토곡정 부산광역시 연제구 토곡로 7",
            "토곡정 부산광역시 연제구 토곡로 7 1층",
            "토곡정",
        ])

    def test_naver_search_queries_expand_manual_review_aliases(self) -> None:
        cases = {
            "세종 오이시외 1개소": "세종 오이시",
            "주식회사 이흥용과자점 칠암사계": "칠암사계",
            "벌교궁꼬막한정식외 1": "궁꼬막한정식",
            "연제지역자활센터, 카페가온비": "카페가온비",
            "트레이더스 연산점 등 2개소": "트레이더스 연산점",
            "(주)보리에 더베이101": "보리에",
            "주식회사 더블유더파티 시청지점": "더파티 시청점",
            "솥솥 해운대점": "솔솥 해운대 해리단길점",
            "금수복국 센텀벡스코점": "금수복국 센텀점",
            "CJ CGV_씨네드쉐프_오프라인": "씨네드쉐프",
            "한국결제_B": "한국결제",
            "베지나랑 광안점": "베지나랑",
            "명지첫집 오션시티점": "명지첫집",
            "모모스커피 온천장점": "모모스커피",
            "이비가짬뽕(센텀2로29)": "이비가짬뽕 부산 해운대 센텀점",
            "로터리집(민락동 소재)": "로타리집",
            "청오디피케이㈜연산점": "도미노피자 연산점",
            "㈜오발탄에프에스": "오발탄",
            "연제 제일식당": "연제제일식당닭한마리",
            "식당3선 덕척점": "식당3선 덕천점",
            "심길선 총송양곱창": "청송양곱창",
            "백만김밥도시락": "백만김밥",
            "백만김밥 부산본점": "백만김밥",
            "주식회사303화덕온천": "303화덕 동래점",
            "주식회사 봉림 루시앙 부산시청점": "루시앙베이커리 부산시청점",
            "고더커피 부산대학교병원점": "고더커피 부산대학병원점",
            "엠즈씨드(주)폴바셋": "폴 바셋",
            "세일기업(주) 수안커피컴퍼니": "수안커피컴퍼니 커피하우스",
            "푸드웨이 목장원": "목장원",
            "만수산돼지국밥II": "만수산돼지국밥",
            "오현프로젝트 강경불고기 센텀시티점": "고담현불고기 해운대센텀시티 본점",
            "우적해운대점": "우적 센텀점",
            "장박사양곱창전문점": "장박사양곱창 센텀점",
            "마파람 해물짐 해물탕 시청직영점": "마파람해물찜해물탕 시청직영점",
            "문화의물결까사부사노부산근현대역사관점": "까사부사노 부산근현대역사관점",
            "도너츠윤 부산서구점": "도너츠윤 부산점",
            "명지일삼육고기": "일삼육고기집 명지본점",
            "첸차이나 금곡점": "첸차이나",
            "백구당외": "백구당",
            "순사이쿠보": "슌사이쿠보 화명",
            "풀무원푸드 한국공항공사부산지역본부": "풀무원푸드앤컬처 한국공항공사김해공항카페",
            "메가엠지씨커피포레나점": "메가MGC커피 포레나점",
            "장군성숯불갈비": "장군성",
            "활스시": "스시활",
            "후스 시청점": "후스",
            "맛!이슈": "맛이슈 부산북구점",
            "주식회사 글로벌양상국": "양상국 해운대점",
            "나폴레옹베이커리 여의도 아이에프씨몰점": "나폴레옹제과점 IFC몰점",
            "(주)학산 테라로사커피 세종지점": "테라로사 세종점",
            "㈜빅바이트컴퍼니 잠바주스 서울역점": "잠바주스 서울역점",
            "꿈앤카페지방자치회관점": "꿈엔카페 지방자치회관점",
            "농업회사법인 (주)동백1917": "동백1917우리술양조장",
            "주식회사 더젬코리아": "제이엠커피로스터스 부산 본점",
            "언양불고기 부산집본": "언양불고기 부산집본가",
            "뚜레쥬르 부산센텀대로점": "뚜레쥬르 부산센텀점",
            "와카바 서부산유통단지점": "와카바 부산",
            "푸드카페삐뽀랑냠냠": "삐뽀랑냠냠 부산",
            "정동각": "정동각 여의도",
            "천해로207호": "cafe천해로207",
            "꽃가람한식당": "꽃가람 본점",
            "더소울규니꾸": "규니꾸 우마카세",
            "영진인삼어탕국수": "영진인삼추어탕",
            "육관소양재역본점": "육즙관리소 양재역점",
            "해씨라래": "해싸라래 커피로스터리",
            "LATELIER MARRON": "아뜰리에마롱",
            "우수미회센타별관": "덕승식당 제주 연동",
        }
        for place_name, expected in cases.items():
            with self.subTest(place_name=place_name):
                row = NormalizedExpenseRow(
                    row_number=1,
                    department_name="총무과",
                    used_date="2026-06-05",
                    place_name=place_name,
                    address="",
                    purpose="간담",
                    amount=100000,
                    normalized_place_name=place_name,
                    normalized_address="",
                )

                self.assertIn(f"{expected} 부산", _search_queries(row))

    def test_addressless_multibrand_legal_name_does_not_use_alias_memory(self) -> None:
        class UnexpectedQuery:
            def execute(self, *args, **kwargs):
                raise AssertionError("addressless legal name must not query alias memory")

        for place_name in ("(주)비안 에프엔비", "주식회사 이에이치엠에스"):
            with self.subTest(place_name=place_name):
                row = NormalizedExpenseRow(
                    row_number=1,
                    department_name="총무과",
                    used_date="2026-06-05",
                    place_name=place_name,
                    address="",
                    purpose="간담",
                    amount=100000,
                    normalized_place_name=normalize_text(place_name),
                    normalized_address="",
                )
                self.assertIsNone(alias_memory_decision(UnexpectedQuery(), row))

    def test_naver_search_queries_prioritize_food_part_from_mixed_org_name(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-05",
            place_name="연제지역자활센터, 카페가온비",
            address="",
            purpose="다과 구입",
            amount=100000,
            normalized_place_name="연제지역자활센터, 카페가온비",
            normalized_address="",
        )

        self.assertEqual(_search_queries(row)[:2], ["카페가온비", "카페가온비 부산"])

    def test_parenthetical_branch_is_preserved_for_matching(self) -> None:
        parsed = parse_place_name("온더보더 (광화문D타워점)")

        self.assertEqual(parsed.base_name, "온더보더")
        self.assertEqual(parsed.branch_name, "광화문D타워점")
        self.assertEqual(parsed.combined_name, "온더보더 광화문D타워점")
        self.assertIn("온더보더 광화문d타워점", alias_keys_for_place(parsed.original))

    def test_generic_name_with_parenthetical_branch_is_identified(self) -> None:
        parsed = parse_place_name("음식점 (광화문점)")

        self.assertTrue(parsed.is_generic)
        self.assertEqual(parsed.branch_name, "광화문점")

    def test_generic_name_searches_parenthetical_merchant_before_generic_label(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-06-05",
            place_name="음식점 (온더보더 광화문D타워점)",
            address="",
            purpose="오찬 간담회",
            amount=83000,
            normalized_place_name="음식점 (온더보더 광화문d타워점)",
            normalized_address="",
        )

        self.assertEqual(
            _search_queries(row)[:2],
            ["온더보더 광화문D타워점", "온더보더 광화문D타워점 부산"],
        )

    def test_generic_parenthetical_full_merchant_can_auto_approve_exact_match(self) -> None:
        class ParentheticalMerchantNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                return [
                    PlaceCandidate(
                        provider_place_id="ontheborder-gwanghwamun",
                        name="온더보더 광화문D타워점",
                        category="restaurant",
                        address="서울특별시 종로구 청진동 246",
                        road_address="서울특별시 종로구 종로3길 17",
                        longitude=126.978,
                        latitude=37.571,
                    )
                ]

        decision = VerifierAgent(ParentheticalMerchantNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="음식점 (온더보더 광화문D타워점)",
                address="",
                purpose="오찬 간담회",
                amount=83000,
                normalized_place_name="음식점 (온더보더 광화문d타워점)",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "approved")
        self.assertIn("NAVER_EXACT_ADDRESSLESS", decision.reason_codes)

    def test_generic_parenthetical_branch_only_does_not_auto_approve(self) -> None:
        class BranchOnlyNaverClient:
            def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
                return [
                    PlaceCandidate(
                        provider_place_id="branch-only",
                        name="광화문점",
                        category="restaurant",
                        address="서울특별시 종로구 종로3길 17",
                        road_address="서울특별시 종로구 종로3길 17",
                        longitude=126.978,
                        latitude=37.571,
                    )
                ]

        decision = VerifierAgent(BranchOnlyNaverClient(), EmptyPermitClient()).verify(
            NormalizedExpenseRow(
                row_number=1,
                department_name="총무과",
                used_date="2026-06-05",
                place_name="음식점 (광화문점)",
                address="",
                purpose="오찬 간담회",
                amount=83000,
                normalized_place_name="음식점 (광화문점)",
                normalized_address="",
            )
        )

        self.assertEqual(decision.decision, "needs_review")

    def test_naver_food_taxonomy_maps_mexican_and_family_restaurant(self) -> None:
        self.assertEqual(_category_from_naver("멕시코,남미음식"), "restaurant")
        self.assertEqual(_category_from_naver("음식점>양식>패밀리레스토랑"), "restaurant")

    def test_naver_rank_prefers_exact_non_busan_name_over_unrelated_busan_place(self) -> None:
        row = NormalizedExpenseRow(
            row_number=1,
            department_name="총무과",
            used_date="2026-04-10",
            place_name="온더보더 광화문D타워점",
            address="",
            purpose="오찬 간담회",
            amount=83000,
            normalized_place_name="온더보더 광화문d타워점",
            normalized_address="",
        )
        exact = PlaceCandidate(
            provider_place_id="exact",
            name="온더보더 광화문D타워점",
            category="restaurant",
            address="서울특별시 종로구 청진동 246",
            road_address="서울특별시 종로구 종로3길 17",
            longitude=126.978,
            latitude=37.571,
        )
        unrelated = PlaceCandidate(
            provider_place_id="unrelated",
            name="부산문화카페",
            category="cafe",
            address="부산광역시 연제구 중앙대로 1001",
            road_address="부산광역시 연제구 중앙대로 1001",
            longitude=129.075,
            latitude=35.18,
        )

        self.assertGreater(_place_rank(row, exact), _place_rank(row, unrelated))

    def test_alias_keys_strip_generic_cafe_prefix(self) -> None:
        self.assertIn("가온비", alias_keys_for_place("카페 가온비"))
        self.assertIn("가온비", alias_keys_for_place("cafe가온비"))

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


class PlanningAdapter:
    source_key = "busan_city_expense_v1"

    def __init__(self) -> None:
        self.max_pages = 1
        self.max_documents = 10
        self.start_date = None
        self.end_date = None

    def discover(self) -> list[SourceDocument]:
        return [self.fetch_document(target) for target in self.discover_targets()]

    def discover_targets(self) -> list[CollectionTarget]:
        return [
            CollectionTarget(
                source_url="fixture://busan/planning/1",
                source_title="1분기 업무추진비",
                published_at="2026-03-31",
                department_name="기획담당관",
            ),
            CollectionTarget(
                source_url="fixture://busan/planning/2",
                source_title="2분기 업무추진비",
                published_at="2026-06-30",
                department_name="청년정책과",
            ),
        ][: self.max_documents]

    def fetch_document(self, target: CollectionTarget) -> SourceDocument:
        return SourceDocument(
            source_url=target.source_url,
            source_title=target.source_title,
            published_at=target.published_at,
            content=f"planning-{target.source_url}",
            department_name=target.department_name,
        )

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        suffix = document.source_url.rsplit("/", 1)[-1]
        return [
            RawExpenseRow(
                row_number=1,
                department_name=document.department_name,
                used_date="2026-02-01",
                place_name=f"계획식당{suffix}",
                address="",
                purpose="간담회",
                amount=10000 + int(suffix),
            )
        ]


class FailingParsePlanningAdapter(PlanningAdapter):
    def discover_targets(self) -> list[CollectionTarget]:
        return [
            CollectionTarget(
                source_url="fixture://busan/planning/drm",
                source_title="DRM 업무추진비",
                published_at="2026-06-30",
                department_name="토목시설부",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        raise ValueError("unsupported encrypted DRM file")


class FlakyParsePlanningAdapter(PlanningAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.extract_calls = 0

    def discover_targets(self) -> list[CollectionTarget]:
        return [
            CollectionTarget(
                source_url="fixture://busan/planning/flaky",
                source_title="임시 실패 업무추진비",
                published_at="2026-06-30",
                department_name="청년정책과",
            )
        ]

    def extract(self, document: SourceDocument) -> list[RawExpenseRow]:
        self.extract_calls += 1
        if self.extract_calls == 1:
            raise RuntimeError("temporary parser outage")
        return [
            RawExpenseRow(
                row_number=1,
                department_name=document.department_name,
                used_date="2026-06-30",
                place_name="재시도식당",
                address="",
                purpose="간담회",
                amount=18000,
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


class TimeoutPermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        raise TimeoutError("The read operation timed out")


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


class MultiCandidateNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-multi-001",
                name="씨드",
                category="cafe",
                address="부산광역시 해운대구 우동 123",
                road_address="부산광역시 해운대구 센텀중앙로 97",
                longitude=129.129,
                latitude=35.173,
            ),
            PlaceCandidate(
                provider_place_id="naver-multi-002",
                name="씨드커피",
                category="cafe",
                address="부산광역시 연제구 연산동 100",
                road_address="부산광역시 연제구 중앙대로 1000",
                longitude=129.075,
                latitude=35.18,
            ),
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


class FloorAddressNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-floor-address-001",
                name="토곡정",
                category="restaurant",
                address="부산광역시 연제구 연산동 490-30 1층",
                road_address="부산광역시 연제구 토곡로 7 1층",
                longitude=129.101,
                latitude=35.186,
            )
        ]


class WrongAddressNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-wrong-address-001",
                name="토곡정",
                category="restaurant",
                address="부산광역시 연제구 연산동 481-1",
                road_address="부산광역시 연제구 과정로 207",
                longitude=129.102,
                latitude=35.187,
            )
        ]


class OtherAddressNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-other-address-001",
                name="토곡정",
                category="other",
                address="부산광역시 연제구 연산동 490-30 1층",
                road_address="부산광역시 연제구 토곡로 7 1층",
                longitude=129.101,
                latitude=35.186,
            )
        ]


class OtherNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-other-001",
                name="동자상회",
                category="other",
                address="부산광역시 연제구 거제동 486-2",
                road_address="부산광역시 연제구 거제천로 79",
                longitude=129.074,
                latitude=35.184,
            )
        ]


class UnknownMismatchNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-unknown-mismatch-001",
                name="미상",
                category="restaurant",
                address="부산광역시 동구 범일동 1422-6",
                road_address="부산광역시 동구 범곡북로 14 1층",
                longitude=129.052,
                latitude=35.139,
            )
        ]


class EdiyaNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-ediya-001",
                name="이디야커피 김해국제공항 국제선점",
                category="cafe",
                address="부산광역시 강서구 대저2동 2350-1",
                road_address="부산광역시 강서구 공항진입로 108",
                longitude=128.948,
                latitude=35.173,
            )
        ]


class BaseSuffixNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-base-suffix-001",
                name="취원 본점",
                category="restaurant",
                address="부산광역시 연제구 연산동 1499-26",
                road_address="부산광역시 연제구 신촌로 38",
                longitude=129.081,
                latitude=35.185,
            )
        ]


class YeyijeNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-yeyije-001",
                name="예이제 해운대본점",
                category="restaurant",
                address="부산광역시 해운대구 중동 1123 푸르지오시티2층",
                road_address="부산광역시 해운대구 해운대해변로298번길 29 푸르지오시티2층",
                longitude=129.164,
                latitude=35.161,
            )
        ]


class DongjingaNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-dongjinga-001",
                name="동진가식육식당",
                category="restaurant",
                address="부산광역시 연제구 거제동 453-3 상가1층 동진가식육식당",
                road_address="부산광역시 연제구 거제시장로14번길 55 상가1층 동진가식육식당",
                longitude=129.075,
                latitude=35.186,
            )
        ]


class BranchConflictNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-branch-conflict-001",
                name="대박통 덕천점",
                category="bar",
                address="부산광역시 북구 덕천동 414-35 1층 대박통",
                road_address="부산광역시 북구 덕천2길 79 1층 대박통",
                longitude=129.008,
                latitude=35.211,
            )
        ]


class NonBusanFuzzyNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-non-busan-fuzzy-001",
                name="강남면옥 정동점",
                category="restaurant",
                address="서울특별시 종로구 신문로2가 12-5",
                road_address="서울특별시 종로구 새문안로 38",
                longitude=126.972,
                latitude=37.569,
            )
        ]


class StarbucksNaverClient:
    def search_local(self, row: NormalizedExpenseRow) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                provider_place_id="naver-starbucks-ks-001",
                name="스타벅스 경성대점",
                category="cafe",
                address="부산광역시 남구 대연동 55-1",
                road_address="부산광역시 남구 수영로 312",
                longitude=129.101,
                latitude=35.137,
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


class ClosedYeyijePermitClient:
    def lookup(self, row: NormalizedExpenseRow) -> PermitSnapshot | None:
        return PermitSnapshot(
            permit_id="permit-closed-yeyije",
            category="restaurant",
            business_status="closed",
            address="부산광역시 해운대구 해운대해변로298번길 29",
            raw_response_json={"BPLC_NM": "예이제"},
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
