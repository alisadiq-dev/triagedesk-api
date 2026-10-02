#!/bin/sh
# Backup of the production database with pg_dump (custom format). Writes backups/triagedesk-<time>.dump (mode 600).
# Env: PROD_ENV (env file, default ~/.secrets/triagedesk-prod.env), PROD_PROJECT (compose project, optional), BACKUP_DIR.
set -eu
env_file="${PROD_ENV:-$HOME/.secrets/triagedesk-prod.env}"
dir="${BACKUP_DIR:-backups}"
compose() { docker compose ${PROD_PROJECT:+-p "$PROD_PROJECT"} --env-file "$env_file" -f docker-compose.prod.yml "$@"; }
umask 077
mkdir -p "$dir"
file="$dir/triagedesk-$(date +%Y%m%d-%H%M%S).dump"
compose exec -T db pg_dump -U triagedesk -d triagedesk --format=custom --no-owner > "$file.part"
# A dump that pg_restore cannot list is not a backup.
compose exec -T db pg_restore --list < "$file.part" > /dev/null
mv "$file.part" "$file"
echo "backup written to $file ($(wc -c < "$file" | tr -d ' ') bytes)"
