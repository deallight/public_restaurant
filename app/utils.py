from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"(주식회사|유한회사|\(주\)|㈜)", " ", text)
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_address(value: str | None) -> str:
    text = normalize_text(value)
    text = text.replace("부산시", "부산광역시")
    return text


def strip_address_detail(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = re.sub(r"\s*(?:지하|지상)?\s*\d+\s*층(?:\s*\d+\s*호)?", " ", text)
    text = re.sub(r"\s+\d{2,4}\s*호(?=\s|$)", " ", text)
    text = re.sub(r"\s+\bB\d+\s*F?\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+\b\d+\s*F\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def address_match_keys(value: str | None) -> set[str]:
    cleaned = strip_address_detail(value)
    normalized = normalize_address(cleaned)
    tokens = normalized.split()
    region_tokens = [token for token in tokens if token.endswith(("광역시", "특별시", "시", "구", "군"))]
    region = " ".join(region_tokens[:2])
    keys: set[str] = set()
    road_match = re.search(r"([0-9a-z가-힣]*(?:대로|로|길))\s+(\d+)(?:\s+(\d+))?", normalized)
    if road_match:
        road = road_match.group(1)
        main_number = road_match.group(2)
        sub_number = road_match.group(3) or ""
        keys.add(f"road:{region}:{road}:{main_number}:{sub_number}")
        keys.add(f"road::{road}:{main_number}:{sub_number}")
    lot_match = re.search(r"([0-9a-z가-힣]+(?:동|읍|면|리))\s+(\d+)(?:\s+(\d+))?", normalized)
    if lot_match:
        dong = lot_match.group(1)
        main_number = lot_match.group(2)
        sub_number = lot_match.group(3) or ""
        keys.add(f"lot:{region}:{dong}:{main_number}:{sub_number}")
        keys.add(f"lot::{dong}:{main_number}:{sub_number}")
    postal_match = re.search(r"(?<!\d)(\d{5})(?!\d)", normalized)
    if postal_match:
        keys.add(f"postal:{postal_match.group(1)}")
    return keys


def structured_address_match(left: str | None, right: str | None) -> bool:
    left_keys = address_match_keys(left)
    right_keys = address_match_keys(right)
    return bool(left_keys and right_keys and left_keys.intersection(right_keys))


def normalized_address_similarity(left: str | None, right: str | None) -> float:
    left_normalized = normalize_address(strip_address_detail(left))
    right_normalized = normalize_address(strip_address_detail(right))
    if not left_normalized or not right_normalized:
        return 0.0
    if structured_address_match(left_normalized, right_normalized):
        return 1.0
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def stable_hash(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def safe_json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default
