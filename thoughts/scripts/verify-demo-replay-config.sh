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

grep -q '^namespace: banking-replay$' quality/speedctl-replay/banking-ai.yaml || {
  echo "FAIL: banking-ai replay config does not target banking-replay"
  exit 1
}

grep -q -- '--build-tag "$build_tag"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not pass build tags to speedctl"
  exit 1
}

jq -e '
  .transforms[]
  | select(.extractor.type == "http_req_header")
  | select(.extractor.config.name == "Authorization")
  | select(any(.transforms[]; .type == "tag_session"))
' quality/dlp/banking-app-keys.json >/dev/null || {
  echo "FAIL: banking-app-keys DLP config does not tag sessions"
  exit 1
}

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

echo "PASS: demo replay config uses banking-replay, build tags, and tag_session DLP"
