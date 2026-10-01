#!/bin/sh
# PgBouncer entrypoint wrapper: register the least-privilege application role.
#
# The edoburu image writes ONE user (DB_USER, the owner `agentverse`) into its
# auth file. The API and workers now connect as APP_DB_USER (NOSUPERUSER,
# NOBYPASSRLS), so that role's credentials are added here too — scram-sha-256
# with a plain-text auth file, as the image does for DB_USER — before handing
# over to the image's own entrypoint.
set -eu

: "${APP_DB_USER:?APP_DB_USER must be set}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"

auth_file="${AUTH_FILE:-/etc/pgbouncer/userlist.txt}"
touch "$auth_file"
if ! grep -q "^\"${APP_DB_USER}\" " "$auth_file"; then
  printf '"%s" "%s"\n' "$APP_DB_USER" "$APP_DB_PASSWORD" >> "$auth_file"
  echo "Wrote authentication credentials for '${APP_DB_USER}' to ${auth_file}"
fi

exec /entrypoint.sh "$@"
