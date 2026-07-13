# PostgreSQL cutover and rollback runbook

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

## 2. Back up and dry-run

Stop SQLite writers, then make an immutable copy. Never run the migrator against
`var/public_restaurant.db` itself.

```bash
sqlite3 var/public_restaurant.db '.backup /secure-backup/public_restaurant-cutover.db'
python3 -m scripts.migrate_sqlite_to_postgres \
  --source-copy /secure-backup/public_restaurant-cutover.db --batch-size 500
```

The default is dry-run. It checks every table/column and reports counts without
printing row values. Schema mismatches and row errors return a non-zero process
exit code. Resolve every mismatch or error before continuing.

## 3. Migrate and verify

```bash
python3 -m scripts.migrate_sqlite_to_postgres \
  --source-copy /secure-backup/public_restaurant-cutover.db --apply --batch-size 500

python3 -m scripts.compare_databases \
  --sqlite-copy /secure-backup/public_restaurant-cutover.db
```

The transfer uses explicit IDs and ID-based upserts, then advances every
sequence. Rows are streamed in bounded batches, so table size does not determine
Python memory use. It is safe to rerun and restores seed timestamps exactly. Cut over
only when schema differences are empty, core table count/content fingerprints
match, service contracts match, the full SQLite suite passes, and the
PostgreSQL integration suite passes.

## 4. Production start

```bash
export APP_ENV=production
export DATABASE_URL='postgresql://restaurant_app@127.0.0.1:5432/public_restaurant'
python3 -m app.server --host 127.0.0.1 --port 8000
```

Production mode rejects SQLite and a missing `DATABASE_URL`. Normal application
startup verifies migration 0001 plus every required table, column, and PostgreSQL
data type, and never applies DDL. Schema drift causes startup to fail closed.

## 5. Roll back

Preferred immediate rollback is the untouched cutover SQLite copy: stop the app,
unset `DATABASE_URL`, set `APP_ENV=development`, and point `APP_DB_PATH` to a
working copy of the backup. Any writes accepted after PostgreSQL cutover will not
exist in that snapshot.

To retain post-cutover writes, stop writers and export PostgreSQL to a new file:

```bash
python3 -m scripts.export_postgres_to_sqlite --output /secure-backup/rollback-new.db
python3 -m scripts.export_postgres_to_sqlite --output /secure-backup/rollback-new.db --apply
```

Verify the exported file before switching. The exporter refuses to overwrite an
existing path. Keep PostgreSQL intact until rollback validation is complete.
