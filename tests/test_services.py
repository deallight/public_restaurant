from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.pipeline import DailyPipeline
from app.services import AppError, RequestContext, RestaurantService
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
