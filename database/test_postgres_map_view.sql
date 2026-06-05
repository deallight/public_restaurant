-- Temporary integration checks for restaurant_map_view exposure rules.
-- Run after postgres_schema.sql, postgres_views.sql, and postgres_seed_regions.sql.
-- The transaction rolls back so test rows do not remain in service data.

BEGIN;

INSERT INTO raw_documents (
  institution_id,
  source_url,
  source_title,
  published_at,
  content_hash,
  status
)
SELECT
  i.id,
  'test://postgres-map-view',
  'restaurant_map_view exposure test',
  DATE '2026-01-01',
  repeat('a', 64),
  'parsed'
FROM institutions i
WHERE i.institution_code = 'busan_city';

INSERT INTO expense_records (
  raw_document_id,
  institution_id,
  region_id,
  source_row_number,
  department_name,
  used_date,
  place_name,
  purpose,
  amount,
  normalized_place_name,
  normalized_purpose,
  is_food_candidate,
  candidate_reason,
  row_hash
)
SELECT
  rd.id,
  i.id,
  i.region_id,
  rows.source_row_number,
  '테스트부서',
  DATE '2026-01-02',
  rows.place_name,
  '테스트 목적',
  rows.amount,
  rows.normalized_place_name,
  '테스트 목적',
  true,
  'map_view_test',
  rows.row_hash
FROM raw_documents rd
JOIN institutions i ON i.id = rd.institution_id
CROSS JOIN (
  VALUES
    (1, '테스트 노출 식당', '테스트 노출 식당', 10000, repeat('b', 64)),
    (2, '테스트 숨김 식당', '테스트 숨김 식당', 20000, repeat('c', 64)),
    (3, '테스트 실패 식당', '테스트 실패 식당', 30000, repeat('d', 64)),
    (4, '테스트 근거없음 식당', '테스트 근거없음 식당', 40000, repeat('e', 64))
) AS rows(source_row_number, place_name, normalized_place_name, amount, row_hash)
WHERE rd.source_url = 'test://postgres-map-view'
  AND i.institution_code = 'busan_city';

INSERT INTO restaurant_candidates (
  expense_record_id,
  institution_id,
  region_id,
  original_place_name,
  normalized_place_name,
  used_date,
  amount,
  place_major_category,
  status,
  verification_status,
  extraction_reason
)
SELECT
  er.id,
  er.institution_id,
  er.region_id,
  er.place_name,
  er.normalized_place_name,
  er.used_date,
  er.amount,
  'restaurant',
  'verified',
  'success',
  'map_view_test'
FROM expense_records er
WHERE er.candidate_reason = 'map_view_test';

INSERT INTO place_verifications (
  candidate_id,
  provider,
  provider_place_id,
  provider_place_name,
  provider_category,
  provider_address,
  normalized_provider_name,
  normalized_provider_address,
  longitude,
  latitude,
  name_similarity,
  address_similarity,
  is_name_match,
  is_address_match,
  is_coordinate_valid,
  is_category_valid,
  verification_status,
  verification_reason
)
SELECT
  c.id,
  'naver',
  'test-' || c.id,
  c.original_place_name,
  '음식점',
  '부산광역시 연제구 중앙대로 1001',
  c.normalized_place_name,
  '부산광역시 연제구 중앙대로 1001',
  129.0756416,
  35.1795543,
  1,
  1,
  true,
  true,
  true,
  true,
  'success',
  'map_view_test_success'
FROM restaurant_candidates c
WHERE c.extraction_reason = 'map_view_test'
  AND c.original_place_name IN ('테스트 노출 식당', '테스트 숨김 식당', '테스트 실패 식당');

-- Case 1: success + visible + place_verification_id exists. This row must be visible.
INSERT INTO restaurants (
  region_id,
  place_verification_id,
  canonical_name,
  normalized_name,
  major_category,
  naver_place_id,
  address,
  road_address,
  normalized_address,
  longitude,
  latitude,
  verification_status,
  map_exposure_status
)
SELECT
  c.region_id,
  pv.id,
  c.original_place_name,
  c.normalized_place_name,
  'restaurant',
  pv.provider_place_id,
  pv.provider_address,
  pv.provider_road_address,
  pv.normalized_provider_address,
  pv.longitude,
  pv.latitude,
  'success',
  'visible'
FROM restaurant_candidates c
JOIN place_verifications pv ON pv.candidate_id = c.id
WHERE c.original_place_name = '테스트 노출 식당';

-- Case 2: success + hidden. This row must not be visible in restaurant_map_view.
INSERT INTO restaurants (
  region_id,
  place_verification_id,
  canonical_name,
  normalized_name,
  major_category,
  naver_place_id,
  address,
  road_address,
  normalized_address,
  longitude,
  latitude,
  verification_status,
  map_exposure_status
)
SELECT
  c.region_id,
  pv.id,
  c.original_place_name,
  c.normalized_place_name,
  'restaurant',
  pv.provider_place_id,
  pv.provider_address,
  pv.provider_road_address,
  pv.normalized_provider_address,
  pv.longitude,
  pv.latitude,
  'success',
  'hidden'
FROM restaurant_candidates c
JOIN place_verifications pv ON pv.candidate_id = c.id
WHERE c.original_place_name = '테스트 숨김 식당';

-- Expected result: visible_count = 1.
DO $$
DECLARE
  visible_count INTEGER;
BEGIN
  SELECT COUNT(*)
  INTO visible_count
  FROM restaurant_map_view
  WHERE name LIKE '테스트%식당';

  IF visible_count <> 1 THEN
    RAISE EXCEPTION 'Expected exactly 1 visible test restaurant, got %', visible_count;
  END IF;

  RAISE NOTICE 'Expected restaurant_map_view visible_count confirmed: %', visible_count;
END $$;

-- Case 3: failure + visible. Expected: CHECK violation from restaurants_visible_requires_verification.
DO $$
BEGIN
  INSERT INTO restaurants (
    region_id,
    place_verification_id,
    canonical_name,
    normalized_name,
    major_category,
    naver_place_id,
    address,
    normalized_address,
    longitude,
    latitude,
    verification_status,
    map_exposure_status
  )
  SELECT
    c.region_id,
    pv.id,
    c.original_place_name,
    c.normalized_place_name,
    'restaurant',
    pv.provider_place_id || '-failed',
    pv.provider_address,
    pv.normalized_provider_address,
    pv.longitude,
    pv.latitude,
    'address_mismatch',
    'visible'
  FROM restaurant_candidates c
  JOIN place_verifications pv ON pv.candidate_id = c.id
  WHERE c.original_place_name = '테스트 실패 식당';

  RAISE EXCEPTION 'Expected visible restaurant with failed verification to be rejected';
EXCEPTION WHEN check_violation THEN
  RAISE NOTICE 'Expected check violation received for failed verification + visible';
END $$;

-- Case 4: no place_verification_id + visible. Expected: CHECK violation.
DO $$
BEGIN
  INSERT INTO restaurants (
    region_id,
    canonical_name,
    normalized_name,
    major_category,
    address,
    normalized_address,
    longitude,
    latitude,
    verification_status,
    map_exposure_status
  )
  SELECT
    c.region_id,
    c.original_place_name,
    c.normalized_place_name,
    'restaurant',
    '부산광역시 연제구 중앙대로 1001',
    '부산광역시 연제구 중앙대로 1001',
    129.0756416,
    35.1795543,
    'success',
    'visible'
  FROM restaurant_candidates c
  WHERE c.original_place_name = '테스트 근거없음 식당';

  RAISE EXCEPTION 'Expected visible restaurant without verification evidence to be rejected';
EXCEPTION WHEN check_violation THEN
  RAISE NOTICE 'Expected check violation received for missing place_verification_id + visible';
END $$;

ROLLBACK;
