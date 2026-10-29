from __future__ import annotations


VERSION = "0008_notices"
DESCRIPTION = "public notices with draft publishing and recoverable deletion"

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS notices (
      id BIGSERIAL PRIMARY KEY,
      kind TEXT NOT NULL CHECK (kind IN ('general', 'release')),
      title TEXT NOT NULL,
      body TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
      revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      published_at TEXT,
      deleted_at TEXT,
      deleted_by BIGINT REFERENCES users(id) ON DELETE SET NULL
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_notices_public
      ON notices (published_at DESC, id DESC)
      WHERE status = 'published' AND deleted_at IS NULL
    """.strip(),
)
