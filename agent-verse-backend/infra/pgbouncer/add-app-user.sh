#!/bin/sh
# PgBouncer entrypoint wrapper: register the application and maintenance roles.
#
# The edoburu image writes ONE user (DB_USER, the owner `agentverse`) into its
# auth file. The API and workers connect as APP_DB_USER (NOSUPERUSER,
# NOBYPASSRLS), and the cross-tenant system jobs (MAINTENANCE_DATABASE_URL) as
# the BYPASSRLS maintenance role MAINTENANCE_DB_USER — the owner by default, a
# separate role when one is provisioned. Those credentials are added here too —
# scram-sha-256 with a plain-text auth file, as the image does for DB_USER —
# before handing over to the image's own entrypoint. Passwords are never echoed.
set -eu

: "${APP_DB_USER:?APP_DB_USER must be set}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"

auth_file="${AUTH_FILE:-/etc/pgbouncer/userlist.txt}"
touch "$auth_file"

add_user() {
  if ! grep -q "^\"$1\" " "$auth_file"; then
    printf '"%s" "%s"\n' "$1" "$2" >> "$auth_file"
    echo "Wrote authentication credentials for '$1' to ${auth_file}"
  fi
}

add_user "$APP_DB_USER" "$APP_DB_PASSWORD"

# Optional: unset/empty, or the owner (DB_USER, which the image writes itself),
# needs no entry of its own.
maintenance_user="${MAINTENANCE_DB_USER:-}"
if [ -n "$maintenance_user" ] && [ "$maintenance_user" != "${DB_USER:-}" ]; then
  if [ "$maintenance_user" = "$APP_DB_USER" ]; then
    # The app role is NOBYPASSRLS: as the maintenance role every cross-tenant
    # system job would silently see one tenant (or none).
    echo "MAINTENANCE_DB_USER must not be the application role '${APP_DB_USER}'" >&2
    exit 1
  fi
  : "${MAINTENANCE_DB_PASSWORD:?MAINTENANCE_DB_PASSWORD must be set with MAINTENANCE_DB_USER}"
  add_user "$maintenance_user" "$MAINTENANCE_DB_PASSWORD"
fi

exec /entrypoint.sh "$@"
