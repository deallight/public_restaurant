# PostgreSQL migrations

`app/schema.py` is the current application data-model specification. Migration
`0001` is implemented by `m0001_app_compatible.py`, rendered by
`python3 -m scripts.render_postgres_schema`, and applied by
`python3 -m scripts.init_db --apply`.

The application never applies PostgreSQL DDL during normal startup. It checks
for `app_schema_migrations.version = '0001'` and fails closed when the reviewed
migration has not been applied.

Apply only to a new or disposable test database first:

```bash
export APP_ENV=development
export DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_test'
python3 -m scripts.render_postgres_schema > /tmp/public_restaurant-0001.sql
# Review /tmp/public_restaurant-0001.sql, then:
python3 -m scripts.init_db --apply
```

Do not apply this baseline over tables created from the older
`database/postgres_*.sql` candidates. Run `scripts.check_db_schema` first. A
non-empty mismatch means a reviewed, environment-specific ALTER migration or a
fresh database is required.

Optional migration `0002_optional_pg_trgm.sql` only adds search indexes. The
current app does not require PostGIS because bounds queries use latitude and
longitude columns directly.
