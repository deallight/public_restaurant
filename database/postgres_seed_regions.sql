INSERT INTO regions (sido, sigungu, region_code)
VALUES ('부산광역시', NULL, '26')
ON CONFLICT DO NOTHING;

INSERT INTO institutions (
  region_id,
  name,
  institution_code,
  source_base_url,
  is_active
)
SELECT
  id,
  '부산광역시청',
  'busan_city',
  NULL,
  true
FROM regions
WHERE sido = '부산광역시'
  AND sigungu IS NULL
ON CONFLICT (name, institution_code) DO NOTHING;
