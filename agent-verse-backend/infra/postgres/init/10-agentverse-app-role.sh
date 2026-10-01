#!/bin/sh
# Fresh volumes only: the postgres image runs /docker-entrypoint-initdb.d once,
# when it initialises an empty data directory. Creates the least-privilege
# APPLICATION role the API and workers connect as (NOSUPERUSER, NOBYPASSRLS, so
# row-level security isolates tenants) and default privileges so every table the
# owner ($POSTGRES_USER, which runs the migrations) creates later is usable by it.
#
# Existing volumes never run this; the `db-migrate` service converges them on the
# same role (alembic upgrade head -> app/db/app_role.py:ensure_app_role), and it
# also re-applies the grants on every existing table, so the two paths agree.
set -eu

: "${APP_DB_USER:=agentverse_app}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set for the application role}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" <<'EOSQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION',
  :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', current_database(), :'app_user')
\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  :'app_user')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
  :'app_user')
\gexec
SELECT format(
  'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO %I',
  :'app_user')
\gexec
EOSQL
