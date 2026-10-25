from __future__ import annotations


VERSION = "0007_oauth_lifecycle_hardening"
DESCRIPTION = "serialize OAuth lifecycle transitions and revoke every merged Naver link"

STATEMENTS = (
    """
    ALTER TABLE oauth_lifecycle_jobs
      DROP CONSTRAINT IF EXISTS oauth_lifecycle_jobs_source_check
    """.strip(),
    """
    ALTER TABLE oauth_lifecycle_jobs
      ADD CONSTRAINT oauth_lifecycle_jobs_source_check
      CHECK (source IN ('user', 'admin', 'provider_callback', 'provider_callback_related'))
    """.strip(),
)
