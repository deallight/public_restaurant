CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS postgis;

DO $$
BEGIN
  CREATE TYPE collection_status AS ENUM (
    'collected',
    'parsed',
    'parse_failed',
    'skipped_duplicate',
    'archived'
  );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$
BEGIN
  CREATE TYPE candidate_status AS ENUM (
    'pending',
    'verified',
    'needs_review',
    'rejected'
  );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$
BEGIN
  CREATE TYPE verification_status AS ENUM (
    'not_requested',
    'success',
    'no_result',
    'ambiguous',
    'address_mismatch',
    'coordinate_failed',
    'category_mismatch',
    'api_error'
  );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$
BEGIN
  CREATE TYPE manual_review_status AS ENUM (
    'not_required',
    'pending',
    'approved',
    'rejected'
  );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$
BEGIN
  CREATE TYPE place_major_category AS ENUM (
    'restaurant',
    'cafe',
    'bar',
    'other',
    'unknown'
  );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$
BEGIN
  CREATE TYPE map_exposure_status AS ENUM (
    'visible',
    'hidden'
  );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS regions (
  id BIGSERIAL PRIMARY KEY,
  sido VARCHAR(50) NOT NULL,
  sigungu VARCHAR(80),
  region_code VARCHAR(20),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_regions_sido_sigungu
ON regions (sido, COALESCE(sigungu, ''));

CREATE TABLE IF NOT EXISTS institutions (
  id BIGSERIAL PRIMARY KEY,
  region_id BIGINT REFERENCES regions(id),
  name VARCHAR(200) NOT NULL,
  institution_code VARCHAR(100),
  source_base_url TEXT,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (name, institution_code)
);

CREATE TABLE IF NOT EXISTS raw_documents (
  id BIGSERIAL PRIMARY KEY,
  institution_id BIGINT NOT NULL REFERENCES institutions(id),
  source_url TEXT NOT NULL,
  source_title TEXT,
  published_at DATE,
  collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash CHAR(64) NOT NULL,
  raw_content_path TEXT,
  status collection_status NOT NULL DEFAULT 'collected',
  error_message TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,

  UNIQUE (institution_id, source_url),
  UNIQUE (institution_id, content_hash)
);

CREATE TABLE IF NOT EXISTS expense_records (
  id BIGSERIAL PRIMARY KEY,
  raw_document_id BIGINT NOT NULL REFERENCES raw_documents(id),
  institution_id BIGINT NOT NULL REFERENCES institutions(id),
  region_id BIGINT REFERENCES regions(id),

  source_row_number INTEGER,
  department_name VARCHAR(200),
  used_at TIMESTAMPTZ,
  used_date DATE,
  place_name TEXT,
  purpose TEXT,
  amount INTEGER,
  participants TEXT,
  payment_method VARCHAR(100),

  original_row_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  normalized_place_name TEXT,
  normalized_purpose TEXT,

  is_food_candidate BOOLEAN NOT NULL DEFAULT false,
  candidate_reason TEXT,

  row_hash CHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (raw_document_id, row_hash),

  CHECK (amount IS NULL OR amount >= 0)
);

CREATE TABLE IF NOT EXISTS restaurant_candidates (
  id BIGSERIAL PRIMARY KEY,
  expense_record_id BIGINT NOT NULL REFERENCES expense_records(id),
  institution_id BIGINT NOT NULL REFERENCES institutions(id),
  region_id BIGINT REFERENCES regions(id),

  original_place_name TEXT NOT NULL,
  normalized_place_name TEXT NOT NULL,
  original_address TEXT,
  normalized_address TEXT,

  used_date DATE,
  amount INTEGER,

  place_major_category place_major_category NOT NULL DEFAULT 'unknown',

  status candidate_status NOT NULL DEFAULT 'pending',
  verification_status verification_status NOT NULL DEFAULT 'not_requested',
  manual_review_status manual_review_status NOT NULL DEFAULT 'not_required',

  extraction_reason TEXT,
  rejection_reason TEXT,
  review_note TEXT,

  duplicate_group_key TEXT,
  suspected_copy_paste BOOLEAN NOT NULL DEFAULT false,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (expense_record_id)
);

CREATE TABLE IF NOT EXISTS place_verifications (
  id BIGSERIAL PRIMARY KEY,
  candidate_id BIGINT NOT NULL REFERENCES restaurant_candidates(id),

  provider VARCHAR(50) NOT NULL DEFAULT 'naver',
  provider_place_id TEXT,
  provider_place_name TEXT,
  provider_category TEXT,
  provider_address TEXT,
  provider_road_address TEXT,

  normalized_provider_name TEXT,
  normalized_provider_address TEXT,

  longitude NUMERIC(10, 7),
  latitude NUMERIC(10, 7),
  geom GEOGRAPHY(Point, 4326)
    GENERATED ALWAYS AS (
      CASE
        WHEN longitude IS NOT NULL AND latitude IS NOT NULL
        THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
        ELSE NULL
      END
    ) STORED,

  name_similarity NUMERIC(5, 4),
  address_similarity NUMERIC(5, 4),

  is_name_match BOOLEAN NOT NULL DEFAULT false,
  is_address_match BOOLEAN NOT NULL DEFAULT false,
  is_coordinate_valid BOOLEAN NOT NULL DEFAULT false,
  is_category_valid BOOLEAN NOT NULL DEFAULT false,

  verification_status verification_status NOT NULL,
  verification_reason TEXT,

  raw_response_json JSONB NOT NULL DEFAULT '{}'::jsonb,

  verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
  CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
  CHECK (name_similarity IS NULL OR name_similarity BETWEEN 0 AND 1),
  CHECK (address_similarity IS NULL OR address_similarity BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS restaurants (
  id BIGSERIAL PRIMARY KEY,

  region_id BIGINT REFERENCES regions(id),
  place_verification_id BIGINT REFERENCES place_verifications(id),

  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,

  major_category place_major_category NOT NULL,
  naver_place_id TEXT,

  address TEXT NOT NULL,
  road_address TEXT,
  normalized_address TEXT NOT NULL,

  longitude NUMERIC(10, 7) NOT NULL,
  latitude NUMERIC(10, 7) NOT NULL,
  geom GEOGRAPHY(Point, 4326)
    GENERATED ALWAYS AS (
      ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
    ) STORED,

  verification_status verification_status NOT NULL DEFAULT 'success',
  map_exposure_status map_exposure_status NOT NULL DEFAULT 'visible',

  first_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CHECK (longitude BETWEEN -180 AND 180),
  CHECK (latitude BETWEEN -90 AND 90),

  CONSTRAINT restaurants_visible_requires_verification
  CHECK (
    map_exposure_status = 'hidden'
    OR (
      verification_status = 'success'
      AND place_verification_id IS NOT NULL
    )
  )
);

CREATE TABLE IF NOT EXISTS restaurant_expense_links (
  id BIGSERIAL PRIMARY KEY,
  restaurant_id BIGINT NOT NULL REFERENCES restaurants(id),
  expense_record_id BIGINT NOT NULL REFERENCES expense_records(id),
  candidate_id BIGINT NOT NULL REFERENCES restaurant_candidates(id),

  used_date DATE,
  amount INTEGER,

  link_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (restaurant_id, expense_record_id),
  UNIQUE (candidate_id)
);

CREATE TABLE IF NOT EXISTS manual_review_tasks (
  id BIGSERIAL PRIMARY KEY,
  candidate_id BIGINT NOT NULL REFERENCES restaurant_candidates(id),

  status manual_review_status NOT NULL DEFAULT 'pending',
  reason TEXT NOT NULL,
  reviewer_note TEXT,

  reviewed_by VARCHAR(100),
  reviewed_at TIMESTAMPTZ,

  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (candidate_id)
);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_restaurant_candidates_updated_at ON restaurant_candidates;

CREATE TRIGGER trg_restaurant_candidates_updated_at
BEFORE UPDATE ON restaurant_candidates
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_restaurants_updated_at ON restaurants;

CREATE TRIGGER trg_restaurants_updated_at
BEFORE UPDATE ON restaurants
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_raw_documents_institution_collected
ON raw_documents (institution_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_raw_documents_published
ON raw_documents (published_at DESC);

CREATE INDEX IF NOT EXISTS idx_expense_records_used_date
ON expense_records (used_date DESC);

CREATE INDEX IF NOT EXISTS idx_expense_records_region_date
ON expense_records (region_id, used_date DESC);

CREATE INDEX IF NOT EXISTS idx_expense_records_institution_date
ON expense_records (institution_id, used_date DESC);

CREATE INDEX IF NOT EXISTS idx_expense_records_food_candidate
ON expense_records (is_food_candidate)
WHERE is_food_candidate = true;

CREATE INDEX IF NOT EXISTS idx_expense_records_place_name_trgm
ON expense_records
USING gin (place_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_candidates_status
ON restaurant_candidates (status);

CREATE INDEX IF NOT EXISTS idx_candidates_verification_status
ON restaurant_candidates (verification_status);

CREATE INDEX IF NOT EXISTS idx_candidates_region_status
ON restaurant_candidates (region_id, status);

CREATE INDEX IF NOT EXISTS idx_candidates_used_date
ON restaurant_candidates (used_date DESC);

CREATE INDEX IF NOT EXISTS idx_candidates_name_trgm
ON restaurant_candidates
USING gin (normalized_place_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_candidates_duplicate_group
ON restaurant_candidates (duplicate_group_key)
WHERE duplicate_group_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_place_verifications_candidate
ON place_verifications (candidate_id, verified_at DESC);

CREATE INDEX IF NOT EXISTS idx_place_verifications_provider_place
ON place_verifications (provider, provider_place_id);

CREATE INDEX IF NOT EXISTS idx_place_verifications_geom
ON place_verifications
USING gist (geom);

CREATE UNIQUE INDEX IF NOT EXISTS uq_restaurants_naver_place
ON restaurants (naver_place_id)
WHERE naver_place_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_restaurants_region_category
ON restaurants (region_id, major_category);

CREATE INDEX IF NOT EXISTS idx_restaurants_geom
ON restaurants
USING gist (geom);

CREATE INDEX IF NOT EXISTS idx_restaurants_visible
ON restaurants (map_exposure_status)
WHERE map_exposure_status = 'visible';

CREATE INDEX IF NOT EXISTS idx_restaurants_name_trgm
ON restaurants
USING gin (normalized_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_restaurant_expense_links_restaurant
ON restaurant_expense_links (restaurant_id);

CREATE INDEX IF NOT EXISTS idx_restaurant_expense_links_used_date
ON restaurant_expense_links (used_date DESC);

CREATE INDEX IF NOT EXISTS idx_manual_review_pending
ON manual_review_tasks (created_at)
WHERE status = 'pending';
