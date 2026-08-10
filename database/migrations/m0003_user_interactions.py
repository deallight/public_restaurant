from __future__ import annotations


VERSION = "0003_user_interactions"
DESCRIPTION = "review reactions and user restaurant images"

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS review_reactions (
      review_id BIGINT NOT NULL REFERENCES restaurant_reviews(id) ON DELETE CASCADE,
      user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      reaction TEXT NOT NULL CHECK (reaction IN ('up', 'down')),
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (review_id, user_id)
    )
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS restaurant_user_images (
      id BIGSERIAL PRIMARY KEY,
      restaurant_id BIGINT NOT NULL REFERENCES restaurants(id) ON DELETE CASCADE,
      user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      storage_key TEXT NOT NULL UNIQUE,
      original_filename TEXT NOT NULL,
      content_type TEXT NOT NULL,
      alt_text TEXT NOT NULL DEFAULT '',
      sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK (sort_order >= 0)
    )
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_review_reactions_user
      ON review_reactions (user_id, updated_at)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_restaurant_user_images_order
      ON restaurant_user_images (restaurant_id, sort_order, id)
    """.strip(),
    """
    CREATE INDEX IF NOT EXISTS idx_restaurant_user_images_owner
      ON restaurant_user_images (user_id, updated_at)
    """.strip(),
)
