from __future__ import annotations

import sqlite3
from typing import Iterable

from .agents import (
    PlaceCandidate,
    VerificationDecision,
    alias_keys_for_place,
    candidate_address_similarity,
    name_similarity_with_branch,
)


def remember_aliases(
    conn: sqlite3.Connection,
    restaurant_id: int,
    alias_texts: Iterable[str],
    source: str,
    confidence: float = 1.0,
) -> None:
    for alias_text in alias_texts:
        for normalized_alias in alias_keys_for_place(alias_text):
            conn.execute(
                """
                INSERT INTO alias_memory
                  (restaurant_id, alias_text, normalized_alias, source, confidence)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(restaurant_id, normalized_alias) DO UPDATE SET
                  alias_text = excluded.alias_text,
                  source = excluded.source,
                  confidence = MAX(alias_memory.confidence, excluded.confidence)
                """,
                (restaurant_id, alias_text, normalized_alias, source, confidence),
            )


def alias_memory_decision(conn: sqlite3.Connection, row) -> VerificationDecision | None:
    aliases = alias_keys_for_place(row.normalized_place_name) + alias_keys_for_place(row.place_name)
    normalized_aliases = list(dict.fromkeys(aliases))
    if not normalized_aliases:
        return None

    placeholders = ",".join("?" for _ in normalized_aliases)
    matches = conn.execute(
        f"""
        SELECT
          am.id AS alias_id,
          am.alias_text,
          am.normalized_alias,
          am.confidence,
          r.id AS restaurant_id,
          r.region_id,
          r.canonical_name,
          r.major_category,
          r.naver_place_id,
          r.address,
          r.road_address,
          r.longitude,
          r.latitude
        FROM alias_memory am
        JOIN restaurants r ON r.id = am.restaurant_id
        WHERE am.normalized_alias IN ({placeholders})
          AND r.verification_status = 'success'
          AND r.map_exposure_status = 'visible'
          AND r.longitude IS NOT NULL
          AND r.latitude IS NOT NULL
        ORDER BY am.confidence DESC, am.id DESC
        """,
        normalized_aliases,
    ).fetchall()
    if not matches:
        return None

    restaurant_ids = {int(match["restaurant_id"]) for match in matches}
    if len(restaurant_ids) != 1:
        return None

    match = matches[0]
    candidate = PlaceCandidate(
        provider_place_id=match["naver_place_id"],
        name=match["canonical_name"],
        category=match["major_category"],
        address=match["address"],
        road_address=match["road_address"] or match["address"],
        longitude=float(match["longitude"]),
        latitude=float(match["latitude"]),
    )
    address_score = candidate_address_similarity(row.normalized_address, candidate) if row.normalized_address else 0.0
    if row.normalized_address and address_score < 0.72:
        return None

    name_score = max(
        name_similarity_with_branch(row.normalized_place_name, match["alias_text"]),
        name_similarity_with_branch(row.normalized_place_name, candidate.name),
    )
    reason_codes = ["ALIAS_MEMORY_MATCH", "ALIAS_ADDRESS_MATCH" if row.normalized_address else "ALIAS_ADDRESSLESS"]
    return VerificationDecision(
        decision="approved",
        confidence=min(0.98, 0.86 + float(match["confidence"]) * 0.08 + name_score * 0.04),
        approved_by="rule",
        selected_candidate=candidate,
        category=candidate.category,
        reason_codes=reason_codes,
        evidence={
            "alias_id": int(match["alias_id"]),
            "restaurant_id": int(match["restaurant_id"]),
            "name_similarity": name_score,
            "address_similarity": address_score,
        },
    )
