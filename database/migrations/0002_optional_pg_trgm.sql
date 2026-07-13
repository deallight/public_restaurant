-- Optional performance migration. Not required for API correctness.
-- Requires an authorized database owner/superuser operation.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_restaurants_name_trgm
  ON restaurants USING gin (normalized_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_restaurants_address_trgm
  ON restaurants USING gin (normalized_address gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_candidates_name_trgm
  ON restaurant_candidates USING gin (normalized_place_name gin_trgm_ops);
