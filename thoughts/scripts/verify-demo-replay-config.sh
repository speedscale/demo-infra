#!/usr/bin/env bash
# Proves: demo-infra targets daily replays at banking-replay, tags reports, and versions DLP session tagging.
# Created: 2026-06-18 after isolating replay traffic from the live demo namespace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

bash -n quality/scripts/run-replay.sh
jq empty quality/dlp/banking-app-keys.json

if jq -e '.transforms[] | select(has("filters"))' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: DLP transform chains should omit filters when they apply to all traffic"
  exit 1
fi

if jq -e '.redactlist.entries.all[] | select(. == "authorization")' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: Authorization must be redacted by the ordered transform, not the global redactlist"
  exit 1
fi

if jq -e '
  .transforms[]
  | select(.extractor.type == "json_path")
  | select(.extractor.config.path == "http.req.http.headers[\"Authorization\"][0].jwt.claims.sub")
' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: JWT claim session path must use RRPair path syntax, not http.req.http"
  exit 1
fi

grep -q '^namespace: banking-replay$' quality/speedctl-replay/banking-ai.yaml || {
  echo "FAIL: banking-ai replay config does not target banking-replay"
  exit 1
}

grep -q -- '--build-tag "$build_tag"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not pass build tags to speedctl"
  exit 1
}

jq -e '
  [ .transforms | to_entries[] | {
      index: .key,
      extractor: .value.extractor,
      transformTypes: [.value.transforms[].type]
    }
  ] as $transforms
  | ($transforms
      | map(select(.extractor.type == "json_path"))
      | map(select(.extractor.config.path == "http.req.headers.Authorization.0.jwt.claims.sub"))
      | map(select(.extractor.config.regex == "^(?i)Bearer (.*)(?-i)"))
      | map(select(.transformTypes == ["tag_session"]))
      | first
    ) as $sessionTransform
  | ($transforms
      | map(select(.extractor.type == "http_req_header"))
      | map(select(.extractor.config.name == "Authorization"))
      | map(select(.transformTypes == ["split", "base64", "dlp_field"]))
      | first
    ) as $redactTransform
  | ($sessionTransform != null)
    and ($redactTransform != null)
    and ($sessionTransform.index < $redactTransform.index)
' quality/dlp/banking-app-keys.json >/dev/null || {
  echo "FAIL: banking-app-keys must tag sessions from Bearer JWT claims.sub before redacting JWTs"
  exit 1
}

if jq -e '
  .transforms[]
  | select(.extractor.type == "http_req_header")
  | select(.extractor.config.name == "Authorization")
  | select([.transforms[].type] == ["split", "tag_session"])
' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: banking-app-keys must not tag the raw Authorization bearer token as the session"
  exit 1
fi

for cluster in dev-decoy staging-decoy; do
  app="clusters/${cluster}/argocd/microsvc-replay.yaml"
  grep -q 'path: kubernetes/overlays/replay' "$app" || {
    echo "FAIL: $app does not use the replay overlay"
    exit 1
  }
  grep -q 'namespace: banking-replay' "$app" || {
    echo "FAIL: $app does not deploy to banking-replay"
    exit 1
  }
done

echo "PASS: demo replay config uses banking-replay, build tags, and JWT-claim session DLP"
