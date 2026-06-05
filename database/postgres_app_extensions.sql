-- Additional operational tables for the web service and semi-automated pipeline.
-- Run after database/postgres_schema.sql.

DO $$
BEGIN
  ALTER TYPE place_major_category ADD VALUE IF NOT EXISTS 'other';
EXCEPTION WHEN undefined_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS source_registry (
  id BIGSERIAL PRIMARY KEY,
  institution_id BIGINT NOT NULL REFERENCES institutions(id),
  source_key TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  adapter_name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  crawl_frequency TEXT NOT NULL DEFAULT 'daily',
  is_active BOOLEAN NOT NULL DEFAULT true,
  config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS batch_jobs (
  id BIGSERIAL PRIMARY KEY,
  job_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
  id BIGSERIAL PRIMARY KEY,
  batch_job_id BIGINT REFERENCES batch_jobs(id),
  stage TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  error_message TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS api_call_logs (
  id BIGSERIAL PRIMARY KEY,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  request_hash CHAR(64) NOT NULL,
  status_code INTEGER,
  duration_ms INTEGER,
  success BOOLEAN NOT NULL DEFAULT false,
  error_message TEXT,
  called_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permit_snapshots (
  id BIGSERIAL PRIMARY KEY,
  normalized_place_name TEXT NOT NULL,
  normalized_address TEXT,
  permit_id TEXT UNIQUE,
  permit_category TEXT NOT NULL,
  business_status TEXT NOT NULL,
  road_address TEXT,
  longitude NUMERIC(10, 7),
  latitude NUMERIC(10, 7),
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_response_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS alias_memory (
  id BIGSERIAL PRIMARY KEY,
  restaurant_id BIGINT REFERENCES restaurants(id),
  alias_text TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  confidence NUMERIC(5, 4) NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (restaurant_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS entity_status_history (
  id BIGSERIAL PRIMARY KEY,
  restaurant_id BIGINT NOT NULL REFERENCES restaurants(id),
  status TEXT NOT NULL,
  reason TEXT,
  evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decision_audit_logs (
  id BIGSERIAL PRIMARY KEY,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id BIGINT,
  before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  reason_codes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS oauth_accounts (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL,
  provider_subject TEXT NOT NULL,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at TIMESTAMPTZ,
  UNIQUE (provider, provider_subject)
);

CREATE TABLE IF NOT EXISTS account_merge_requests (
  id BIGSERIAL PRIMARY KEY,
  source_user_id BIGINT NOT NULL REFERENCES users(id),
  target_user_id BIGINT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT,
  requested_by TEXT,
  resolved_by TEXT,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (source_user_id <> target_user_id)
);

CREATE TABLE IF NOT EXISTS restaurant_reviews (
  id BIGSERIAL PRIMARY KEY,
  restaurant_id BIGINT NOT NULL REFERENCES restaurants(id),
  user_id BIGINT REFERENCES users(id),
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  body TEXT NOT NULL,
  reviewer_label TEXT NOT NULL DEFAULT '방문자',
  ip_hash CHAR(64),
  status TEXT NOT NULL DEFAULT 'visible',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_reports (
  id BIGSERIAL PRIMARY KEY,
  review_id BIGINT NOT NULL REFERENCES restaurant_reviews(id),
  reason TEXT NOT NULL,
  reporter_user_id BIGINT REFERENCES users(id),
  reporter_ip_hash CHAR(64),
  status TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS review_moderation_logs (
  id BIGSERIAL PRIMARY KEY,
  review_id BIGINT NOT NULL REFERENCES restaurant_reviews(id),
  action TEXT NOT NULL,
  moderator_id TEXT,
  reason TEXT,
  before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_registry_active ON source_registry (is_active);
CREATE INDEX IF NOT EXISTS idx_batch_jobs_started ON batch_jobs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_dlq_status ON dead_letter_queue (status, created_at);
CREATE INDEX IF NOT EXISTS idx_api_call_provider ON api_call_logs (provider, called_at DESC);
CREATE INDEX IF NOT EXISTS idx_permit_snapshots_name_trgm
ON permit_snapshots USING gin (normalized_place_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_alias_memory_trgm
ON alias_memory USING gin (normalized_alias gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_reviews_restaurant ON restaurant_reviews (restaurant_id, status);
CREATE INDEX IF NOT EXISTS idx_review_reports_status ON review_reports (status, created_at);
