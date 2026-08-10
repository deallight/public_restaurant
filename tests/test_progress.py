from __future__ import annotations

import unittest

from app.progress import VerificationProgressStore


class VerificationProgressStoreTests(unittest.TestCase):
    def test_snapshot_counts_live_verification_classifications(self) -> None:
        store = VerificationProgressStore()
        store.update({"event": "batch_started", "batch_id": 7, "total": 4, "limit": 4})
        store.update(
            {
                "event": "candidate_done",
                "batch_id": 7,
                "candidate_id": 1,
                "status": "completed",
                "decision": "approved",
            }
        )
        store.update(
            {
                "event": "candidate_done",
                "batch_id": 7,
                "candidate_id": 2,
                "status": "completed",
                "decision": "needs_review",
            }
        )
        store.update(
            {
                "event": "candidate_done",
                "batch_id": 7,
                "candidate_id": 3,
                "status": "completed",
                "decision": "rejected",
            }
        )
        store.update(
            {
                "event": "candidate_done",
                "batch_id": 7,
                "candidate_id": 4,
                "status": "failed",
            }
        )

        snapshot = store.snapshot()

        self.assertTrue(snapshot["active"])
        self.assertEqual(snapshot["processed"], 4)
        self.assertEqual(
            snapshot["classifications"],
            {"approved": 1, "needs_review": 1, "rejected": 1, "failed": 1},
        )

    def test_finished_summary_is_the_classification_source(self) -> None:
        store = VerificationProgressStore()
        store.update({"event": "batch_started", "batch_id": 8, "total": 5, "limit": 5})
        store.update(
            {
                "event": "batch_finished",
                "batch_id": 8,
                "processed": 5,
                "summary": {"approved": 2, "needs_review": 1, "rejected": 1, "dlq": 1},
            }
        )

        snapshot = store.snapshot()

        self.assertFalse(snapshot["active"])
        self.assertEqual(snapshot["processed"], 5)
        self.assertEqual(
            snapshot["classifications"],
            {"approved": 2, "needs_review": 1, "rejected": 1, "failed": 1},
        )


if __name__ == "__main__":
    unittest.main()
