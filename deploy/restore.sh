#!/bin/sh
# Restores a backup made by deploy/backup.sh into the running production database.
# It REPLACES the current data (pg_restore --clean), so it needs RESTORE_CONFIRM=yes.
# Stops the app and Nginx first so nothing writes during the restore, then starts them again.
# Env: PROD_ENV, PROD_PROJECT (as in backup.sh).
set -eu
file="${1:?usage: restore.sh <backup file>}"
env_file="${PROD_ENV:-$HOME/.secrets/triagedesk-prod.env}"
compose() { docker compose ${PROD_PROJECT:+-p "$PROD_PROJECT"} --env-file "$env_file" -f docker-compose.prod.yml "$@"; }
[ -f "$file" ] || { echo "no such file: $file"; exit 2; }
if [ "${RESTORE_CONFIRM:-}" != "yes" ]; then
  echo "This replaces the data in the production database with $file."
  echo "Run again with RESTORE_CONFIRM=yes to go ahead."
  exit 2
fi
compose exec -T db pg_restore --list < "$file" > /dev/null   # refuse an unreadable file before touching anything
compose up -d --wait db
compose stop nginx app
compose exec -T db pg_restore -U triagedesk -d triagedesk --clean --if-exists --no-owner --exit-on-error < "$file"
compose up -d --wait
echo "restored from $file"
