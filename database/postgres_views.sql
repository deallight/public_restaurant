CREATE OR REPLACE VIEW restaurant_map_view AS
SELECT
  r.id AS restaurant_id,
  r.canonical_name AS name,
  r.major_category,
  r.address,
  r.road_address,
  r.longitude,
  r.latitude,
  r.geom,
  r.region_id,
  rg.sido,
  rg.sigungu,
  COUNT(rel.expense_record_id) AS visit_count,
  COALESCE(SUM(er.amount), 0) AS total_amount,
  MIN(er.used_date) AS first_used_date,
  MAX(er.used_date) AS last_used_date
FROM restaurants r
JOIN regions rg ON rg.id = r.region_id
JOIN place_verifications pv ON pv.id = r.place_verification_id
LEFT JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
LEFT JOIN expense_records er ON er.id = rel.expense_record_id
WHERE
  r.map_exposure_status = 'visible'
  AND r.verification_status = 'success'
  AND r.place_verification_id IS NOT NULL
  AND r.longitude IS NOT NULL
  AND r.latitude IS NOT NULL
  AND pv.verification_status = 'success'
  AND pv.is_address_match = true
  AND pv.is_coordinate_valid = true
GROUP BY
  r.id,
  rg.id;

CREATE OR REPLACE VIEW restaurant_ranking_view AS
SELECT
  r.id AS restaurant_id,
  r.canonical_name AS name,
  r.major_category,
  r.address,
  r.road_address,
  rg.sido,
  rg.sigungu,
  COUNT(rel.expense_record_id) AS usage_count,
  SUM(er.amount) AS total_amount,
  COUNT(DISTINCT er.institution_id) AS institution_count,
  MAX(er.used_date) AS latest_used_date
FROM restaurants r
JOIN regions rg ON rg.id = r.region_id
JOIN place_verifications pv ON pv.id = r.place_verification_id
JOIN restaurant_expense_links rel ON rel.restaurant_id = r.id
JOIN expense_records er ON er.id = rel.expense_record_id
WHERE
  r.map_exposure_status = 'visible'
  AND r.verification_status = 'success'
  AND r.place_verification_id IS NOT NULL
  AND pv.verification_status = 'success'
  AND pv.is_address_match = true
  AND pv.is_coordinate_valid = true
GROUP BY
  r.id,
  rg.id
ORDER BY
  usage_count DESC,
  total_amount DESC;

CREATE OR REPLACE VIEW manual_review_queue_view AS
SELECT
  c.id AS candidate_id,
  c.original_place_name,
  c.original_address,
  c.used_date,
  c.amount,
  c.status,
  c.verification_status,
  c.review_note,
  er.department_name,
  er.purpose,
  i.name AS institution_name,
  rg.sido,
  rg.sigungu
FROM restaurant_candidates c
JOIN expense_records er ON er.id = c.expense_record_id
JOIN institutions i ON i.id = c.institution_id
LEFT JOIN regions rg ON rg.id = c.region_id
WHERE
  c.status = 'needs_review'
  OR c.manual_review_status = 'pending';

CREATE OR REPLACE VIEW suspected_duplicate_expense_view AS
SELECT
  normalized_place_name,
  normalized_purpose,
  amount,
  department_name,
  COUNT(*) AS duplicate_count,
  MIN(used_date) AS first_used_date,
  MAX(used_date) AS last_used_date,
  ARRAY_AGG(id ORDER BY used_date DESC) AS expense_record_ids
FROM expense_records
WHERE normalized_place_name IS NOT NULL
GROUP BY
  normalized_place_name,
  normalized_purpose,
  amount,
  department_name
HAVING COUNT(*) >= 2;
