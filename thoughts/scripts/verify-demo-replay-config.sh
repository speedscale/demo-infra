#!/usr/bin/env bash
# Proves: demo-infra targets daily replays at banking-replay, tags reports, and versions DLP session tagging.
# Created: 2026-06-18 after isolating replay traffic from the live demo namespace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

bash -n quality/scripts/run-replay.sh
bash -n quality/scripts/run-proxymock-scenario.sh
jq empty quality/dlp/banking-app-keys.json
jq empty quality/test-configs/banking-daily-replay.json

if jq -e '.transforms[] | select(has("filters"))' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: DLP transform chains should omit filters when they apply to all traffic"
  exit 1
fi

replay_count=$(find quality/speedctl-replay -maxdepth 1 -name 'banking-*.yaml' | wc -l | tr -d ' ')
if [ "$replay_count" != "7" ]; then
  echo "FAIL: expected 7 workload replay configs, found $replay_count"
  exit 1
fi

for replay in banking-accounts banking-ai banking-fraud banking-gateway banking-notification banking-transactions banking-user; do
  cfg="quality/speedctl-replay/${replay}.yaml"
  [ -f "$cfg" ] || {
    echo "FAIL: missing replay config $cfg"
    exit 1
  }

  for field in name workload namespace snapshotID testConfigID service servicePort localPort proxymockTarget; do
    grep -q "^${field}:" "$cfg" || {
      echo "FAIL: $cfg missing $field"
      exit 1
    }
  done

  grep -q '^namespace: banking-replay$' "$cfg" || {
    echo "FAIL: $cfg does not target banking-replay"
    exit 1
  }

  grep -q '^testConfigID: banking-daily-replay$' "$cfg" || {
    echo "FAIL: $cfg does not use banking-daily-replay"
    exit 1
  }
done

grep -q -- '--build-tag "$build_tag"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not pass build tags to speedctl"
  exit 1
}

grep -q 'speedctl put test-config "$REPO_ROOT/quality/test-configs/banking-daily-replay.json"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not sync banking-daily-replay"
  exit 1
}

grep -q 'proxymock cloud pull snapshot "$snapshot_id"' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not pull snapshots from Speedscale Cloud"
  exit 1
}

grep -q 'proxymock replay' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not run replay"
  exit 1
}

for replay in banking-accounts banking-ai banking-fraud banking-gateway banking-notification banking-transactions banking-user; do
  grep -q -- "- ${replay}" .github/workflows/quality-daily.yaml || {
    echo "FAIL: quality workflow proxymock matrix missing $replay"
    exit 1
  }
done

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

echo "PASS: demo replay config covers 7 workloads, proxymock CI, build tags, and tag_session DLP"
