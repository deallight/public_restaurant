# PostgreSQL migrations

`app/schema.py` is the current application data-model specification. The
bootstrap schema is implemented by `m0001_app_compatible.py` and rendered by
`python3 -m scripts.render_postgres_schema`. Existing PostgreSQL databases are
advanced by explicit migration modules such as
`m0003_user_interactions.py`. Both paths are applied transactionally by
`python3 -m scripts.init_db --apply`.

The application never applies PostgreSQL DDL during normal startup. It checks
every version in `REQUIRED_POSTGRES_MIGRATIONS` and fails closed when a reviewed
migration has not been applied. On an existing baseline database,
`scripts.init_db --apply` skips the bootstrap DDL and applies only missing
incremental migrations.

Apply to a new or disposable test database first:

```bash
export APP_ENV=development
export DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_test'
python3 -m scripts.render_postgres_schema > /tmp/public_restaurant-0001.sql
# Review /tmp/public_restaurant-0001.sql, then:
python3 -m scripts.init_db --apply
```

Do not apply the bootstrap baseline over tables created from the older
`database/postgres_*.sql` candidates. Run `scripts.check_db_schema` first. A
non-empty mismatch means a reviewed, environment-specific ALTER migration or a
fresh database is required.

For an existing database with migration `0001`, review the incremental module,
take and validate a PostgreSQL backup, run `scripts.init_db --apply`, and require
`scripts.check_db_schema` to return `compatible` before restarting the service.

Optional migration `0002_optional_pg_trgm.sql` only adds search indexes. The
current app does not require PostGIS because bounds queries use latitude and
longitude columns directly.
