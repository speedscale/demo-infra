#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SNAPSHOT_DIR=${1:-}
AUTH_BASE_URL=${2:-}

if [ -z "$SNAPSHOT_DIR" ] || [ -z "$AUTH_BASE_URL" ]; then
  echo "Usage: $0 <snapshot-dir> <auth-base-url>"
  exit 1
fi

if [ ! -d "$SNAPSHOT_DIR" ]; then
  echo "Snapshot directory not found: $SNAPSHOT_DIR"
  exit 1
fi

AUTH_COLLECTION="${AUTH_COLLECTION:-$REPO_ROOT/quality/postman/banking-auth.postman_collection.json}"
if [ ! -f "$AUTH_COLLECTION" ]; then
  echo "Postman auth collection not found: $AUTH_COLLECTION"
  exit 1
fi

collection_value() {
  jq -r --arg key "$1" '.variable[]? | select(.key == $key) | .value // empty' "$AUTH_COLLECTION" | head -1
}

AUTH_USERNAME="${AUTH_USERNAME:-$(collection_value username)}"
AUTH_PASSWORD="${AUTH_PASSWORD:-$(collection_value password)}"
AUTH_PATH="$(jq -r '.item[] | select(.name == "Login") | "/" + (.request.url.path | join("/"))' "$AUTH_COLLECTION" | head -1)"
AUTH_BODY_TEMPLATE="$(jq -r '.item[] | select(.name == "Login") | .request.body.raw' "$AUTH_COLLECTION" | head -1)"

if [ -z "$AUTH_USERNAME" ] || [ -z "$AUTH_PASSWORD" ] || [ -z "$AUTH_PATH" ] || [ -z "$AUTH_BODY_TEMPLATE" ]; then
  echo "Postman auth collection is missing username, password, path, or body"
  exit 1
fi

AUTH_BODY="${AUTH_BODY_TEMPLATE//\{\{username\}\}/$AUTH_USERNAME}"
AUTH_BODY="${AUTH_BODY//\{\{password\}\}/$AUTH_PASSWORD}"

auth_response=$(curl -fsS \
  -X POST "${AUTH_BASE_URL%/}$AUTH_PATH" \
  -H 'Content-Type: application/json' \
  --data "$AUTH_BODY")

fresh_token=$(printf '%s' "$auth_response" | jq -r '.token // .access_token // empty')
if [ -z "$fresh_token" ] || [ "$fresh_token" = "null" ]; then
  echo "Postman auth preflight did not return token or access_token"
  exit 1
fi

export FRESH_TOKEN="$fresh_token"

updated=0
while IFS= read -r -d '' rrpair_file; do
  before_sum=$(shasum -a 256 "$rrpair_file" | awk '{print $1}')
  perl -0pi -e '
    next unless /direction:\s+IN\b|Host:\s+banking-|http:host is banking-|"host":"banking-/;
    s/(Authorization:\s*Bearer\s+)[^\r\n\\]+/${1}$ENV{FRESH_TOKEN}/g;
    s/("Authorization"\s*:\s*\[\s*"Bearer\s+)[^"]+/${1}$ENV{FRESH_TOKEN}/g;
  ' "$rrpair_file"
  after_sum=$(shasum -a 256 "$rrpair_file" | awk '{print $1}')
  if [ "$before_sum" != "$after_sum" ]; then
    updated=$((updated + 1))
  fi
done < <(find "$SNAPSHOT_DIR" -type f \( -name '*.md' -o -name '*.json' \) -print0)

if [ "$updated" -eq 0 ]; then
  echo "Postman auth preflight did not update any banking Authorization headers"
  exit 1
fi

echo "Applied Postman auth preflight token to $updated RRPair file(s)"
