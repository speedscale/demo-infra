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

AUTH_PASSWORD="${AUTH_PASSWORD:-$(collection_value password)}"
AUTH_PATH="$(jq -r '.item[] | select(.name == "Login") | "/" + (.request.url.path | join("/"))' "$AUTH_COLLECTION" | head -1)"
AUTH_BODY_TEMPLATE="$(jq -r '.item[] | select(.name == "Login") | .request.body.raw' "$AUTH_COLLECTION" | head -1)"

if [ -z "$AUTH_PASSWORD" ] || [ -z "$AUTH_PATH" ] || [ -z "$AUTH_BODY_TEMPLATE" ]; then
  echo "Postman auth collection is missing password, path, or body"
  exit 1
fi

base64_decode() {
  if base64 --help 2>&1 | grep -q -- '--decode'; then
    base64 --decode
  else
    base64 -D
  fi
}

decode_jwt_subject() {
  local token=$1 payload padding
  payload="${token#*.}"
  payload="${payload%%.*}"
  padding=$(( (4 - ${#payload} % 4) % 4 ))
  payload="$(printf '%s' "$payload" | tr '_-' '/+')"
  if [ "$padding" -gt 0 ]; then
    payload="${payload}$(printf '=%.0s' $(seq 1 "$padding"))"
  fi

  printf '%s' "$payload" | base64_decode 2>/dev/null | jq -r '.sub // empty'
}

recorded_tokens=$(mktemp)
find "$SNAPSHOT_DIR" -type f \( -name '*.md' -o -name '*.json' \) -print0 |
  xargs -0 perl -0ne '
    next unless /direction:\s+IN\b|Host:\s+banking-|http:host is banking-|"host":"banking-/;
    while (/Authorization:\s*Bearer\s+([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)/g) {
      print "$1\n";
    }
  ' | sort -u > "$recorded_tokens"

if [ ! -s "$recorded_tokens" ]; then
  rm -f "$recorded_tokens"
  echo "Postman auth preflight found no parsable banking JWTs to refresh"
  exit 1
fi

token_count=0
updated=0
while IFS= read -r recorded_token || [ -n "$recorded_token" ]; do
  [ -n "$recorded_token" ] || continue
  username=$(decode_jwt_subject "$recorded_token")
  if [ -z "$username" ]; then
    rm -f "$recorded_tokens"
    echo "Postman auth preflight could not read the recorded JWT subject"
    exit 1
  fi

  auth_body="${AUTH_BODY_TEMPLATE//\{\{username\}\}/$username}"
  auth_body="${auth_body//\{\{password\}\}/$AUTH_PASSWORD}"
  auth_response=$(curl -fsS \
    -X POST "${AUTH_BASE_URL%/}$AUTH_PATH" \
    -H 'Content-Type: application/json' \
    --data "$auth_body")

  fresh_token=$(printf '%s' "$auth_response" | jq -r '.token // .access_token // empty')
  if [ -z "$fresh_token" ] || [ "$fresh_token" = "null" ]; then
    rm -f "$recorded_tokens"
    echo "Postman auth preflight did not return token or access_token for $username"
    exit 1
  fi

  export RECORDED_TOKEN="$recorded_token"
  export FRESH_TOKEN="$fresh_token"
  while IFS= read -r -d '' rrpair_file; do
    before_sum=$(shasum -a 256 "$rrpair_file" | awk '{print $1}')
    perl -0pi -e 's/\Q$ENV{RECORDED_TOKEN}\E/$ENV{FRESH_TOKEN}/g' "$rrpair_file"
    after_sum=$(shasum -a 256 "$rrpair_file" | awk '{print $1}')
    if [ "$before_sum" != "$after_sum" ]; then
      updated=$((updated + 1))
    fi
  done < <(find "$SNAPSHOT_DIR" -type f \( -name '*.md' -o -name '*.json' \) -print0)
  token_count=$((token_count + 1))
done < "$recorded_tokens"

rm -f "$recorded_tokens"

if [ "$updated" -eq 0 ]; then
  echo "Postman auth preflight did not update any banking Authorization headers"
  exit 1
fi

echo "Applied Postman auth preflight for $token_count recorded JWT subject(s) across $updated RRPair file update(s)"
