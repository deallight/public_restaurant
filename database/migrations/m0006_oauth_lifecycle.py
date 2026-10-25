from __future__ import annotations


VERSION = "0006_oauth_lifecycle"
DESCRIPTION = "encrypted OAuth credentials and durable account lifecycle jobs"

STATEMENTS = (
    "ALTER TABLE oauth_accounts ADD COLUMN IF NOT EXISTS refresh_token_ciphertext TEXT",
    "ALTER TABLE oauth_accounts ADD COLUMN IF NOT EXISTS token_updated_at TEXT",
    """
    CREATE TABLE IF NOT EXISTS oauth_flow_intents (
      token_hash TEXT PRIMARY KEY,
      user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
      provider TEXT NOT NULL,
      purpose TEXT NOT NULL,
      expected_subject_hmac TEXT,
      return_to TEXT NOT NULL DEFAULT '/',
      expires_at TEXT NOT NULL,
      used_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK (purpose IN ('login', 'account_delete'))
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS oauth_lifecycle_jobs (
      id BIGSERIAL PRIMARY KEY,
      user_id BIGINT REFERENCES users(id),
      provider TEXT NOT NULL,
      subject_hmac TEXT NOT NULL,
      token_ciphertext TEXT,
      token_type_hint TEXT,
      source TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued',
      local_delete_completed INTEGER NOT NULL DEFAULT 0,
      attempts INTEGER NOT NULL DEFAULT 0,
      next_attempt_at TEXT,
      last_error_code TEXT,
      worker_id TEXT,
      started_at TEXT,
      completed_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK (source IN ('user', 'admin', 'provider_callback', 'provider_callback_related')),
      CHECK (status IN ('queued', 'running', 'retry_wait', 'awaiting_token', 'manual_review', 'completed')),
      CHECK (local_delete_completed IN (0, 1)),
      CHECK (attempts >= 0)
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS oauth_disconnect_events (
      event_digest TEXT PRIMARY KEY,
      lifecycle_job_id BIGINT REFERENCES oauth_lifecycle_jobs(id),
      provider TEXT NOT NULL,
      subject_hmac TEXT NOT NULL,
      provider_timestamp BIGINT NOT NULL,
      result TEXT NOT NULL DEFAULT 'accepted',
      received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      processed_at TEXT
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_oauth_flow_intents_user
      ON oauth_flow_intents (user_id, purpose, expires_at)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_oauth_lifecycle_due
      ON oauth_lifecycle_jobs (status, next_attempt_at, id)
    """.strip(),
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_lifecycle_active_subject
      ON oauth_lifecycle_jobs (provider, subject_hmac)
      WHERE status IN ('queued', 'running', 'retry_wait', 'awaiting_token', 'manual_review')
    """.strip(),
)
