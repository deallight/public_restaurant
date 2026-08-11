from __future__ import annotations


VERSION = "0004_operation_jobs"
DESCRIPTION = "durable operation job queue"

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS operation_jobs (
      id BIGSERIAL PRIMARY KEY,
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
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_operation_jobs_status_created
      ON operation_jobs (status, id)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_operation_jobs_type_created
      ON operation_jobs (job_type, id)
    """.strip(),
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_operation_jobs_active_dedupe
      ON operation_jobs (dedupe_key)
      WHERE dedupe_key <> '' AND status IN ('queued', 'running')
    """.strip(),
)
