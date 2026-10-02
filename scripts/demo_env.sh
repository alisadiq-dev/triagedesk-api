#!/bin/sh
# Exports tokens for the three demo users, for the curl collection (docs/curl-collection.md).
#
#   eval "$(supabase status -o env | sed 's/^/export SUPABASE_/')"
#   eval "$(sh scripts/demo_env.sh)"
#
# Prints `export NAME=value` lines for eval; nothing is shown when you eval it. The tokens last one hour.
# Needs SUPABASE_PUBLISHABLE_KEY, supabase/demo_users.json (scripts/create_demo_users.py) and python3.
set -eu
: "${SUPABASE_PUBLISHABLE_KEY:?set SUPABASE_PUBLISHABLE_KEY (see the comment at the top)}"
supabase_url="${SUPABASE_URL:-http://127.0.0.1:54321}"
for role in customer agent admin; do
  body="$(python3 - "$role" <<'PY'
import json, sys
demo = json.load(open("supabase/demo_users.json"))
user = demo["users"][sys.argv[1]]
print(json.dumps({"email": user["email"], "password": demo["password"]}))
PY
)"
  token="$(curl -s -A 'Mozilla/5.0 (compatible; triagedesk-demo-env)' \
    -H "apikey: $SUPABASE_PUBLISHABLE_KEY" -H 'Content-Type: application/json' \
    -d "$body" "$supabase_url/auth/v1/token?grant_type=password" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
  upper="$(echo "$role" | tr '[:lower:]' '[:upper:]')"
  echo "export ${upper}_TOKEN='$token'"
done
echo "export API='${API_BASE_URL:-http://127.0.0.1:8080}'"
echo "export UA='Mozilla/5.0 (compatible; curl-collection)'"
