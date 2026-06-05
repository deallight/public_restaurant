SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;

SELECT typname
FROM pg_type
WHERE typname IN (
  'collection_status',
  'candidate_status',
  'verification_status',
  'manual_review_status',
  'place_major_category',
  'map_exposure_status'
)
ORDER BY typname;

SELECT indexname, tablename
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;

SELECT table_name
FROM information_schema.views
WHERE table_schema = 'public'
ORDER BY table_name;

SELECT *
FROM regions
WHERE sido = '부산광역시';

SELECT *
FROM institutions
WHERE institution_code = 'busan_city';
