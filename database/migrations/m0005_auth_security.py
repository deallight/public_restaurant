from __future__ import annotations


VERSION = "0005_auth_security"
DESCRIPTION = "revocable sessions, one-time action tokens, and security rate limits"

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
      session_hash TEXT PRIMARY KEY,
      user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      issued_at TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      authenticated_at TEXT NOT NULL,
      revoked_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS action_tokens (
      token_hash TEXT PRIMARY KEY,
      session_hash TEXT NOT NULL REFERENCES auth_sessions(session_hash) ON DELETE CASCADE,
      user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      action TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      used_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS security_rate_limit_events (
      id BIGSERIAL PRIMARY KEY,
      scope TEXT NOT NULL,
      subject_hash TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active
      ON auth_sessions (user_id, revoked_at, expires_at)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_action_tokens_session_action
      ON action_tokens (session_hash, action, expires_at)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_security_rate_limit_lookup
      ON security_rate_limit_events (scope, subject_hash, created_at)
    """.strip(),
)
