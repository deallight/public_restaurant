# PostgreSQL schema decisions

Original audit date: 2026-07-13. PostgreSQL-only completion: 2026-08-11. The authoritative runtime model is the tables in
`app/schema.py`; the older `postgres_schema.sql`, `postgres_app_extensions.sql`,
and `postgres_views.sql` are design candidates, not executable migrations.

## Material gaps found

| Area | Existing PostgreSQL candidates | Current application requirement |
| --- | --- | --- |
| Collection workflow | `collection_plans` and `collection_plan_documents` absent | Both tables and all parse/retry counters are required by admin and ops APIs |
| Raw documents | missing `source_registry_id`, parse status/error/timestamp columns | Required by collection, parsing, document detail, and retries |
| Candidates | missing five administrator override fields | Required by candidate edit, geocode, approve, merge, and reject flows |
| Status values | PostgreSQL enums are narrower than live text values | App writes additional pipeline states; baseline uses checked/application text values |
| JSON | candidates use `JSONB`; app passes and parses canonical JSON strings | Baseline stores canonical JSON strings as text for a stable API contract |
| Dates/timestamps | candidates use `DATE`/`TIMESTAMPTZ`; app contracts expose ISO strings | Adapter normalizes PostgreSQL values; compatibility baseline keeps persisted values as text |
| Boolean flags | candidates use `BOOLEAN`; application SQL and fixtures use `0`/`1` | Baseline uses checked integers to preserve the application contract |
| Floating point | candidate DDL used PostgreSQL `REAL` | Baseline uses `DOUBLE PRECISION` so coordinates and JSON responses retain precision |
| Spatial columns | generated PostGIS geography columns and GiST indexes | Current bounds filter uses numeric latitude/longitude only; PostGIS is not required |
| Search indexes | unconditional `pg_trgm` extension/indexes | Correctness does not require them; optional migration 0002 owns this performance feature |
| Views | candidate views require PostGIS/enums and are not queried by the app | Deferred until their contract is independently tested |

## Decision

Migration 0001 is generated from `app/schema.py`, with `BIGSERIAL` primary keys
and matching `BIGINT` foreign keys. It intentionally preserves the application's
text JSON/date contract and integer flags. PostgreSQL-specific query syntax is
isolated in `app/db_compat.py`.

Do not layer 0001 blindly over an existing candidate schema. Run:

```bash
DATABASE_URL='postgresql://...' python3 -m scripts.check_db_schema
```

If it reports differences, use a fresh test database or prepare a separately
reviewed ALTER migration after inspecting existing data. No migration tool in
this repository prints connection URLs, credentials, row values, or user data.
