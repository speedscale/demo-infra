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
AUTH_USERNAME="${AUTH_USERNAME:-$(collection_value username)}"
AUTH_PATH="$(jq -r '.item[] | select(.name == "Login") | "/" + (.request.url.path | join("/"))' "$AUTH_COLLECTION" | head -1)"
AUTH_BODY_TEMPLATE="$(jq -r '.item[] | select(.name == "Login") | .request.body.raw' "$AUTH_COLLECTION" | head -1)"
AUTH_REQUEST_TIMEOUT="${AUTH_REQUEST_TIMEOUT:-20}"
AUTH_PREFLIGHT_RETRIES="${AUTH_PREFLIGHT_RETRIES:-30}"
AUTH_PREFLIGHT_RETRY_DELAY="${AUTH_PREFLIGHT_RETRY_DELAY:-2}"

if [ -z "$AUTH_PASSWORD" ] || [ -z "$AUTH_USERNAME" ] || [ -z "$AUTH_PATH" ] || [ -z "$AUTH_BODY_TEMPLATE" ]; then
  echo "Postman auth collection is missing username, password, path, or body"
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

  printf '%s' "$payload" | base64_decode 2>/dev/null | jq -r '.sub // empty' 2>/dev/null || true
}

POST_JSON_STATUS=
POST_JSON_BODY=
post_json() {
  local url=$1 body=$2 response_file
  response_file=$(mktemp)
  POST_JSON_STATUS=$(curl -sS \
    --connect-timeout 5 \
    --max-time "$AUTH_REQUEST_TIMEOUT" \
    -o "$response_file" \
    -w '%{http_code}' \
    -X POST "$url" \
    -H 'Content-Type: application/json' \
    --data "$body" || true)
  POST_JSON_BODY=$(cat "$response_file")
  rm -f "$response_file"
}

login_token() {
  local username=$1 auth_body fresh_token
  auth_body="${AUTH_BODY_TEMPLATE//\{\{username\}\}/$username}"
  auth_body="${auth_body//\{\{password\}\}/$AUTH_PASSWORD}"

  post_json "${AUTH_BASE_URL%/}$AUTH_PATH" "$auth_body"
  if [ "$POST_JSON_STATUS" != "200" ]; then
    return 1
  fi

  fresh_token=$(printf '%s' "$POST_JSON_BODY" | jq -r '.token // .access_token // empty')
  if [ -z "$fresh_token" ] || [ "$fresh_token" = "null" ]; then
    echo "Postman auth preflight did not return token or access_token for $username"
    exit 1
  fi

  printf '%s' "$fresh_token"
}

login_token_with_retries() {
  local username=$1 fresh_token attempt

  for attempt in $(seq 1 "$AUTH_PREFLIGHT_RETRIES"); do
    fresh_token=$(login_token "$username" || true)
    if [ -n "$fresh_token" ] && [ "$fresh_token" != "null" ]; then
      printf '%s' "$fresh_token"
      return 0
    fi

    if [ "$attempt" -lt "$AUTH_PREFLIGHT_RETRIES" ]; then
      echo "Postman auth preflight login for $username returned HTTP $POST_JSON_STATUS; retrying ($attempt/$AUTH_PREFLIGHT_RETRIES)" >&2
      sleep "$AUTH_PREFLIGHT_RETRY_DELAY"
    fi
  done

  return 1
}

FALLBACK_FRESH_TOKEN=
ensure_fallback_token() {
  local reason=$1 fresh_token
  if [ -z "$FALLBACK_FRESH_TOKEN" ]; then
    fresh_token=$(login_token_with_retries "$AUTH_USERNAME" || true)
    if [ -z "$fresh_token" ] || [ "$fresh_token" = "null" ]; then
      echo "Postman auth preflight could not refresh JWT for $reason or fallback user $AUTH_USERNAME: HTTP $POST_JSON_STATUS" >&2
      exit 1
    fi
    FALLBACK_FRESH_TOKEN=$fresh_token
  fi
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
  echo "Postman auth preflight found no parsable banking JWTs; continuing without refresh"
  exit 0
fi

ensure_fallback_token "fallback user"
echo "Postman auth preflight fallback token is ready"

token_count=0
updated=0
token_map=$(mktemp)
recorded_token_total=$(wc -l < "$recorded_tokens" | tr -d ' ')
echo "Postman auth preflight refreshing $recorded_token_total recorded JWT subject(s)"
while IFS= read -r recorded_token || [ -n "$recorded_token" ]; do
  [ -n "$recorded_token" ] || continue
  username=$(decode_jwt_subject "$recorded_token")
  if [ -z "$username" ]; then
    fresh_token=$FALLBACK_FRESH_TOKEN
    printf '%s\t%s\n' "$recorded_token" "$fresh_token" >> "$token_map"
    token_count=$((token_count + 1))
    if [ $((token_count % 10)) -eq 0 ] || [ "$token_count" -eq "$recorded_token_total" ]; then
      echo "Postman auth preflight refreshed $token_count/$recorded_token_total subject(s)"
    fi
    continue
  fi

  fresh_token=$FALLBACK_FRESH_TOKEN

  printf '%s\t%s\n' "$recorded_token" "$fresh_token" >> "$token_map"
  token_count=$((token_count + 1))
  if [ $((token_count % 10)) -eq 0 ] || [ "$token_count" -eq "$recorded_token_total" ]; then
    echo "Postman auth preflight refreshed $token_count/$recorded_token_total subject(s)"
  fi
done < "$recorded_tokens"

export TOKEN_MAP="$token_map"
echo "Postman auth preflight applying token replacements"
while IFS= read -r -d '' rrpair_file; do
  before_sum=$(shasum -a 256 "$rrpair_file" | awk '{print $1}')
  perl -0pi -e '
    BEGIN {
      our (%tokens, $token_pattern);
      open my $fh, "<", $ENV{TOKEN_MAP} or die "open token map: $!";
      local $/ = "\n";
      while (my $line = <$fh>) {
        chomp $line;
        my ($old, $new) = split /\t/, $line, 2;
        $tokens{$old} = $new if defined $old && defined $new;
      }
      $token_pattern = join "|", map { quotemeta($_) } keys %tokens;
    }
    our (%tokens, $token_pattern);
    if (length $token_pattern) {
      s/($token_pattern)/$tokens{$1}/ge;
    }
  ' "$rrpair_file"
  after_sum=$(shasum -a 256 "$rrpair_file" | awk '{print $1}')
  if [ "$before_sum" != "$after_sum" ]; then
    updated=$((updated + 1))
  fi
done < <(find "$SNAPSHOT_DIR" -type f \( -name '*.md' -o -name '*.json' \) -print0)

rm -f "$recorded_tokens" "$token_map"

if [ "$updated" -eq 0 ]; then
  echo "Postman auth preflight did not update any banking Authorization headers"
  exit 1
fi

echo "Applied Postman auth preflight for $token_count recorded JWT subject(s) across $updated RRPair file update(s)"
