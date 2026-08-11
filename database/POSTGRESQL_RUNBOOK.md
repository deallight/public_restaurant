# PostgreSQL-only migration and rollback runbook

All commands must be tested against a database whose name contains `test`
before the production window. Populate `DATABASE_URL` only in the process
environment or deployment secret store.

## 1. Prepare and test

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant_test'
python3 -m scripts.render_postgres_schema > /tmp/public_restaurant-0001.sql
python3 -m scripts.check_db_schema        # for a pre-existing schema
python3 -m scripts.init_db --apply        # new empty test DB only
TEST_DATABASE_URL="$DATABASE_URL" .venv/bin/python -m unittest tests.test_postgres_integration
```

`pg_trgm` is optional. Apply `database/migrations/0002_optional_pg_trgm.sql`
only with separate extension-install approval. PostGIS is not needed by the
current application.

## 2. Back up

Create a PostgreSQL custom-format backup and verify that its archive list is
readable before every migration.

```bash
pg_dump --format=custom --dbname="$DATABASE_URL" \
  --file=/secure-backup/public-restaurant-pre-migration.dump
pg_restore --list /secure-backup/public-restaurant-pre-migration.dump >/dev/null
test -s /secure-backup/public-restaurant-pre-migration.dump
```

Do not continue if the database target, environment, backup ownership, archive
list, or file size is unexpected.

## 3. Apply numbered migrations and verify

```bash
python3 -m scripts.check_db_schema
python3 -m scripts.init_db --apply
python3 -m scripts.check_db_schema
```

Only reviewed modules in `database/migrations/` are applied. Normal application
startup never applies DDL. Continue only when the final report is
`{"status":"compatible","schema_issues":[]}`.

## 4. Start the application

```bash
export APP_ENV=production
export DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant'
python3 -m app.server --host 127.0.0.1 --port 8000
```

Every environment rejects a missing or non-PostgreSQL `DATABASE_URL`. Normal
application startup verifies every required migration plus every required table,
column, and PostgreSQL data type. Schema drift causes startup to fail closed.

## 5. Roll back

Stop application and worker writers, restore the verified PostgreSQL backup into
a separate recovery database, validate its schema and service contract, then
switch `DATABASE_URL` through the reviewed deployment procedure. Never overwrite
the current PostgreSQL database in place during diagnosis.

```bash
createdb public_restaurant_recovery
pg_restore --dbname=public_restaurant_recovery \
  /secure-backup/public-restaurant-pre-migration.dump
DATABASE_URL='postgresql:///public_restaurant_recovery' \
  python3 -m scripts.check_db_schema
```

Keep the original PostgreSQL database and backup intact until recovery is fully
validated.
