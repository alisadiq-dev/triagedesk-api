#!/bin/sh
# Creates ~/.secrets/triagedesk-prod.env once, with a random database password. Never overwrites, never prints values.
set -eu
target="${1:-$HOME/.secrets/triagedesk-prod.env}"
if [ -e "$target" ]; then
  echo "$target already exists; not touching it"
  exit 0
fi
umask 077
mkdir -p "$(dirname "$target")"
password="$(openssl rand -hex 24)"
{
  echo "POSTGRES_PASSWORD=$password"
  grep -v '^POSTGRES_PASSWORD=' "$(dirname "$0")/prod.env.example" | grep -v '^#' | grep -v '^$'
} > "$target"
chmod 600 "$target"
echo "created $target (mode 600); POSTGRES_PASSWORD length ${#password}. Add GEMINI_API_KEY there if you want the model."
