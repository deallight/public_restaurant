SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS regions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sido TEXT NOT NULL,
  sigungu TEXT,
  region_code TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (sido, sigungu)
);

CREATE TABLE IF NOT EXISTS institutions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  region_id INTEGER REFERENCES regions(id),
  name TEXT NOT NULL,
  institution_code TEXT NOT NULL,
  source_base_url TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (name, institution_code)
);

CREATE TABLE IF NOT EXISTS source_registry (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  source_key TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  adapter_name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  crawl_frequency TEXT NOT NULL DEFAULT 'daily',
  is_active INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operation_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  payload_json TEXT NOT NULL DEFAULT '{}',
  progress_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  error_message TEXT,
  dedupe_key TEXT NOT NULL DEFAULT '',
  requested_by TEXT,
  worker_id TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT,
  heartbeat_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
  CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS idx_operation_jobs_status_created
  ON operation_jobs (status, id);

CREATE INDEX IF NOT EXISTS idx_operation_jobs_type_created
  ON operation_jobs (job_type, id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_operation_jobs_active_dedupe
  ON operation_jobs (dedupe_key)
  WHERE dedupe_key <> '' AND status IN ('queued', 'running');

CREATE TABLE IF NOT EXISTS batch_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}',
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS collection_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  scan_max_pages INTEGER NOT NULL DEFAULT 1,
  scan_max_documents INTEGER NOT NULL DEFAULT 500,
  batch_size INTEGER NOT NULL DEFAULT 20,
  discovered_count INTEGER NOT NULL DEFAULT 0,
  pending_count INTEGER NOT NULL DEFAULT 0,
  collected_count INTEGER NOT NULL DEFAULT 0,
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  rows_seen INTEGER NOT NULL DEFAULT 0,
  rows_inserted INTEGER NOT NULL DEFAULT 0,
  created_batch_job_id INTEGER REFERENCES batch_jobs(id),
  summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS collection_plan_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id INTEGER NOT NULL REFERENCES collection_plans(id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  source_title TEXT,
  department_name TEXT,
  published_at TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  raw_document_id INTEGER REFERENCES raw_documents(id),
  batch_job_id INTEGER REFERENCES batch_jobs(id),
  rows_seen INTEGER NOT NULL DEFAULT 0,
  rows_inserted INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  parse_status TEXT NOT NULL DEFAULT 'not_requested',
  parse_attempts INTEGER NOT NULL DEFAULT 0,
  parse_error_message TEXT,
  parsed_at TEXT,
  error_message TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (plan_id, source_url)
);

CREATE TABLE IF NOT EXISTS raw_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  source_registry_id INTEGER REFERENCES source_registry(id),
  source_url TEXT NOT NULL,
  source_title TEXT,
  published_at TEXT,
  collected_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  raw_content_path TEXT,
  status TEXT NOT NULL DEFAULT 'collected',
  parse_status TEXT NOT NULL DEFAULT 'not_requested',
  parsed_at TEXT,
  parse_error_message TEXT,
  error_message TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (institution_id, source_url),
  UNIQUE (institution_id, content_hash)
);

CREATE TABLE IF NOT EXISTS expense_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_document_id INTEGER NOT NULL REFERENCES raw_documents(id),
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  region_id INTEGER REFERENCES regions(id),
  source_row_number INTEGER,
  department_name TEXT,
  used_at TEXT,
  used_date TEXT,
  place_name TEXT,
  purpose TEXT,
  amount INTEGER,
  participants TEXT,
  payment_method TEXT,
  original_row_json TEXT NOT NULL DEFAULT '{}',
  normalized_place_name TEXT,
  normalized_purpose TEXT,
  is_food_candidate INTEGER NOT NULL DEFAULT 0,
  candidate_reason TEXT,
  row_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (raw_document_id, row_hash),
  CHECK (amount IS NULL OR amount >= 0)
);

CREATE TABLE IF NOT EXISTS restaurant_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expense_record_id INTEGER NOT NULL UNIQUE REFERENCES expense_records(id),
  institution_id INTEGER NOT NULL REFERENCES institutions(id),
  region_id INTEGER REFERENCES regions(id),
  original_place_name TEXT NOT NULL,
  normalized_place_name TEXT NOT NULL,
  original_address TEXT,
  normalized_address TEXT,
  review_place_name TEXT,
  review_normalized_place_name TEXT,
  review_address TEXT,
  review_normalized_address TEXT,
  review_major_category TEXT,
  used_date TEXT,
  amount INTEGER,
  place_major_category TEXT NOT NULL DEFAULT 'other',
  status TEXT NOT NULL DEFAULT 'pending',
  verification_status TEXT NOT NULL DEFAULT 'not_requested',
  manual_review_status TEXT NOT NULL DEFAULT 'not_required',
  extraction_reason TEXT,
  rejection_reason TEXT,
  review_note TEXT,
  duplicate_group_key TEXT,
  suspected_copy_paste INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS place_verifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES restaurant_candidates(id),
  provider TEXT NOT NULL DEFAULT 'naver',
  provider_place_id TEXT,
  provider_place_name TEXT,
  provider_category TEXT,
  provider_address TEXT,
  provider_road_address TEXT,
  normalized_provider_name TEXT,
  normalized_provider_address TEXT,
  longitude REAL,
  latitude REAL,
  name_similarity REAL,
  address_similarity REAL,
  is_name_match INTEGER NOT NULL DEFAULT 0,
  is_address_match INTEGER NOT NULL DEFAULT 0,
  is_coordinate_valid INTEGER NOT NULL DEFAULT 0,
  is_category_valid INTEGER NOT NULL DEFAULT 0,
  verification_status TEXT NOT NULL,
  verification_reason TEXT,
  raw_response_json TEXT NOT NULL DEFAULT '{}',
  verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (candidate_id, provider, provider_place_id)
);

CREATE TABLE IF NOT EXISTS restaurants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  region_id INTEGER REFERENCES regions(id),
  place_verification_id INTEGER REFERENCES place_verifications(id),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  major_category TEXT NOT NULL,
  naver_place_id TEXT UNIQUE,
  address TEXT NOT NULL,
  road_address TEXT,
  normalized_address TEXT NOT NULL,
  longitude REAL NOT NULL,
  latitude REAL NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'success',
  map_exposure_status TEXT NOT NULL DEFAULT 'visible',
  first_verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (longitude BETWEEN -180 AND 180),
  CHECK (latitude BETWEEN -90 AND 90)
);

CREATE TABLE IF NOT EXISTS restaurant_expense_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
  expense_record_id INTEGER NOT NULL REFERENCES expense_records(id),
  candidate_id INTEGER NOT NULL UNIQUE REFERENCES restaurant_candidates(id),
  used_date TEXT,
  amount INTEGER,
  link_reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (restaurant_id, expense_record_id)
);

CREATE TABLE IF NOT EXISTS manual_review_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL UNIQUE REFERENCES restaurant_candidates(id),
  status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT NOT NULL,
  reviewer_note TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS permit_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  normalized_place_name TEXT NOT NULL,
  normalized_address TEXT,
  permit_id TEXT,
  permit_category TEXT NOT NULL,
  business_status TEXT NOT NULL,
  road_address TEXT,
  longitude REAL,
  latitude REAL,
  fetched_at TEXT NOT NULL,
  raw_response_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (permit_id)
);

CREATE TABLE IF NOT EXISTS api_call_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status_code INTEGER,
  duration_ms INTEGER,
  success INTEGER NOT NULL DEFAULT 0,
  error_message TEXT,
  called_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dead_letter_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_job_id INTEGER REFERENCES batch_jobs(id),
  stage TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  error_message TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS alias_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER REFERENCES restaurants(id),
  alias_text TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual',
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (restaurant_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS entity_status_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
  status TEXT NOT NULL,
  reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS decision_audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id INTEGER,
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS oauth_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  provider TEXT NOT NULL,
  provider_subject TEXT NOT NULL,
  display_name TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TEXT,
  UNIQUE (provider, provider_subject)
);

CREATE TABLE IF NOT EXISTS account_merge_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_user_id INTEGER NOT NULL REFERENCES users(id),
  target_user_id INTEGER NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending',
  reason TEXT,
  requested_by TEXT,
  resolved_by TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_saved_restaurants (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, restaurant_id)
);

CREATE TABLE IF NOT EXISTS restaurant_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
  user_id INTEGER REFERENCES users(id),
  rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  body TEXT NOT NULL,
  reviewer_label TEXT NOT NULL DEFAULT '방문자',
  ip_hash TEXT,
  status TEXT NOT NULL DEFAULT 'visible',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_reactions (
  review_id INTEGER NOT NULL REFERENCES restaurant_reviews(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reaction TEXT NOT NULL CHECK (reaction IN ('up', 'down')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (review_id, user_id)
);

CREATE TABLE IF NOT EXISTS restaurant_ai_summaries (
  restaurant_id INTEGER PRIMARY KEY REFERENCES restaurants(id),
  summary_text TEXT NOT NULL DEFAULT '',
  summarized_review_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'idle',
  provider TEXT,
  model TEXT,
  last_generated_at TEXT,
  last_attempted_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (summarized_review_count >= 0)
);

CREATE TABLE IF NOT EXISTS restaurant_user_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  storage_key TEXT NOT NULL UNIQUE,
  original_filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  alt_text TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (sort_order >= 0)
);

CREATE TABLE IF NOT EXISTS restaurant_admin_images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  restaurant_id INTEGER NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
  storage_key TEXT NOT NULL UNIQUE,
  original_filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  alt_text TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (sort_order >= 0)
);

CREATE TABLE IF NOT EXISTS review_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_id INTEGER NOT NULL REFERENCES restaurant_reviews(id),
  reason TEXT NOT NULL,
  reporter_user_id INTEGER REFERENCES users(id),
  reporter_ip_hash TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS review_moderation_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  review_id INTEGER NOT NULL REFERENCES restaurant_reviews(id),
  action TEXT NOT NULL,
  moderator_id TEXT,
  reason TEXT,
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_restaurants_category ON restaurants (major_category);
CREATE INDEX IF NOT EXISTS idx_restaurants_region ON restaurants (region_id);
CREATE INDEX IF NOT EXISTS idx_reviews_restaurant ON restaurant_reviews (restaurant_id, status);
CREATE INDEX IF NOT EXISTS idx_review_reactions_user ON review_reactions (user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_user_saved_restaurants_created
  ON user_saved_restaurants (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_restaurant_user_images_order
  ON restaurant_user_images (restaurant_id, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_restaurant_user_images_owner
  ON restaurant_user_images (user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_restaurant_admin_images_order
  ON restaurant_admin_images (restaurant_id, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_review_reports_status ON review_reports (status);
CREATE INDEX IF NOT EXISTS idx_expense_records_date ON expense_records (used_date);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON restaurant_candidates (status);
CREATE INDEX IF NOT EXISTS idx_collection_plan_documents_status
  ON collection_plan_documents (plan_id, status, published_at);
"""
