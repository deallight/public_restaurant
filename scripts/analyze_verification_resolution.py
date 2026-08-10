from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.agents import NormalizedExpenseRow, PermitSnapshot
from app.config import load_settings
from app.database import Database
from app.pipeline import (
    DailyPipeline,
    advisory_permit_resolution_decision,
    existing_provider_evidence_decision,
    stored_provider_evidence_review_decision,
)
from app.utils import normalize_address, normalize_text, safe_json_loads


def _normalized_row(row: Any) -> NormalizedExpenseRow:
    place_name = row["review_place_name"] or row["original_place_name"] or ""
    address = row["review_address"] or row["original_address"] or ""
    return NormalizedExpenseRow(
        row_number=int(row["source_row_number"] or 0),
        department_name=row["department_name"] or "",
        used_date=row["used_date"] or "",
        place_name=place_name,
        address=address,
        purpose=row["purpose"] or "",
        amount=int(row["amount"] or 0),
        normalized_place_name=row["review_normalized_place_name"]
        or row["normalized_place_name"]
        or normalize_text(place_name),
        normalized_address=row["review_normalized_address"]
        or row["normalized_address"]
        or normalize_address(address),
    )


def _resolution_counts(conn: Any) -> dict[str, int | float]:
    counts = conn.execute(
        """
        SELECT
          SUM(CASE WHEN status IN ('verified', 'rejected') THEN 1 ELSE 0 END) AS resolved,
          SUM(
            CASE
              WHEN status = 'needs_review' AND verification_status <> 'not_requested'
              THEN 1 ELSE 0
            END
          ) AS manual
        FROM restaurant_candidates
        """
    ).fetchone()
    resolved = int(counts["resolved"] or 0)
    manual = int(counts["manual"] or 0)
    processed = resolved + manual
    return {
        "resolved": resolved,
        "manual": manual,
        "processed": processed,
        "resolution_percent": round(100.0 * resolved / processed, 2) if processed else 0.0,
    }


def _stored_evidence_resolutions(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          c.*,
          er.source_row_number,
          er.department_name,
          er.purpose
        FROM restaurant_candidates c
        JOIN expense_records er ON er.id = c.expense_record_id
        WHERE c.status = 'needs_review'
          AND c.verification_status <> 'not_requested'
        ORDER BY c.id
        """
    ).fetchall()
    resolutions: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalized_row(row)
        decision = existing_provider_evidence_decision(
            conn,
            int(row["id"]),
            normalized,
        )
        if decision is None:
            review_decision = stored_provider_evidence_review_decision(
                conn,
                int(row["id"]),
                normalized,
            )
            permit_row = conn.execute(
                """
                SELECT *
                FROM permit_snapshots
                WHERE normalized_place_name = ?
                  AND COALESCE(normalized_address, '') = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (normalized.normalized_place_name, normalized.normalized_address),
            ).fetchone()
            if review_decision is not None and permit_row is not None:
                permit = PermitSnapshot(
                    permit_id=permit_row["permit_id"],
                    category=permit_row["permit_category"],
                    business_status=permit_row["business_status"],
                    address=permit_row["road_address"] or permit_row["normalized_address"] or "",
                    longitude=permit_row["longitude"],
                    latitude=permit_row["latitude"],
                    raw_response_json=safe_json_loads(permit_row["raw_response_json"], {}),
                )
                decision = advisory_permit_resolution_decision(
                    normalized,
                    review_decision,
                    permit,
                )
        if decision is None or decision.decision not in {"approved", "rejected"}:
            continue
        resolutions.append(
            {
                "candidate_id": int(row["id"]),
                "expense_record_id": int(row["expense_record_id"]),
                "region_id": int(row["region_id"]),
                "decision": decision,
            }
        )
    return resolutions


def analyze(database: Database) -> dict[str, Any]:
    with database.session() as conn:
        baseline = _resolution_counts(conn)
        resolutions = _stored_evidence_resolutions(conn)
    resolved = int(baseline["resolved"])
    manual = int(baseline["manual"])
    processed = int(baseline["processed"])
    projected_resolved = resolved + len(resolutions)
    return {
        "baseline": baseline,
        "stored_evidence_projection": {
            "newly_resolved": len(resolutions),
            "decisions": [
                {
                    "candidate_id": item["candidate_id"],
                    "decision": item["decision"].decision,
                    "reason_codes": item["decision"].reason_codes,
                }
                for item in resolutions
            ],
            "resolved": projected_resolved,
            "manual": manual - len(resolutions),
            "processed": processed,
            "resolution_percent": round(100.0 * projected_resolved / processed, 2)
            if processed
            else 0.0,
        },
    }


def apply_stored_evidence_resolutions(
    database: Database,
    minimum_percent: float = 90.0,
) -> dict[str, Any]:
    pipeline = DailyPipeline(database)
    with database.session() as conn:
        baseline = _resolution_counts(conn)
        resolutions = _stored_evidence_resolutions(conn)
        processed = int(baseline["processed"])
        projected_percent = (
            100.0 * (int(baseline["resolved"]) + len(resolutions)) / processed
            if processed
            else 0.0
        )
        if projected_percent < minimum_percent:
            raise RuntimeError(
                f"stored evidence projection {projected_percent:.2f}% is below "
                f"required {minimum_percent:.2f}%"
            )
        applied: list[dict[str, Any]] = []
        for item in resolutions:
            decision = item["decision"]
            reason_codes = list(decision.reason_codes)
            if "STORED_EVIDENCE_REVERIFY" not in reason_codes:
                reason_codes.append("STORED_EVIDENCE_REVERIFY")
            audited_decision = replace(decision, reason_codes=reason_codes)
            pipeline._persist_decision(
                conn,
                {"region_id": item["region_id"]},
                item["expense_record_id"],
                item["candidate_id"],
                audited_decision,
            )
            applied.append(
                {
                    "candidate_id": item["candidate_id"],
                    "decision": audited_decision.decision,
                    "reason_codes": audited_decision.reason_codes,
                }
            )
        after = _resolution_counts(conn)
    return {
        "baseline": baseline,
        "applied": applied,
        "after": after,
        "minimum_percent": minimum_percent,
    }


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(
        description="Analyze or apply stored-evidence approval/rejection resolutions."
    )
    parser.add_argument("--db", default="", help="SQLite development override")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply only stored-evidence resolutions in one transaction",
    )
    parser.add_argument(
        "--minimum-percent",
        type=float,
        default=90.0,
        help="Refuse --apply unless the projected resolution percentage meets this value",
    )
    args = parser.parse_args()
    if args.db and settings.database_url:
        raise SystemExit("--db cannot override DATABASE_URL")
    target = Path(args.db) if args.db else settings.database_url or settings.db_path
    database = Database(target)
    result = (
        apply_stored_evidence_resolutions(database, args.minimum_percent)
        if args.apply
        else analyze(database)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
