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

grep -q 'grep -Eo .* || true' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner can still exit before printing speedctl startup errors"
  exit 1
}

grep -q 'kubectl -n "$namespace" wait --for=condition=available' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner should wait for deployment availability, not rollout completion"
  exit 1
}

grep -q 'replay_status=0' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not preserve replay exit status"
  exit 1
}

grep -q 'proxymock report' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not generate reports"
  exit 1
}

grep -q '^snapshotID: 7b3d0b6e-f0df-489d-8254-d23f56cce131$' quality/speedctl-replay/banking-fraud.yaml || {
  echo "FAIL: banking-fraud does not use the replayable inbound fraud snapshot"
  exit 1
}

for replay in banking-accounts banking-ai banking-fraud banking-gateway banking-notification banking-transactions banking-user; do
  grep -q -- "- ${replay}" .github/workflows/quality-daily.yaml || {
    echo "FAIL: quality workflow proxymock matrix missing $replay"
    exit 1
  }
done

for job in 'ci-replay:' 'cd-replay-dev:' 'cd-replay-staging:'; do
  grep -q "^  ${job}$" .github/workflows/quality-daily.yaml || {
    echo "FAIL: quality workflow missing job $job"
    exit 1
  }
done

for name in 'CI replay (proxymock)' 'CD replay (dev)' 'CD replay (staging)'; do
  grep -q "name: ${name}" .github/workflows/quality-daily.yaml || {
    echo "FAIL: quality workflow missing job name: $name"
    exit 1
  }
done

grep -q './quality/scripts/run-proxymock-scenario.sh dev-decoy ${{ matrix.replay }}' .github/workflows/quality-daily.yaml || {
  echo "FAIL: quality workflow CI proxymock job should run the workload matrix once against dev-decoy"
  exit 1
}

grep -q './quality/scripts/run-replay.sh dev-decoy' .github/workflows/quality-daily.yaml || {
  echo "FAIL: quality workflow missing dev CD replay job command"
  exit 1
}

grep -q './quality/scripts/run-replay.sh staging-decoy' .github/workflows/quality-daily.yaml || {
  echo "FAIL: quality workflow missing staging CD replay job command"
  exit 1
}

if grep -q 'cluster: \[dev-decoy, staging-decoy\]' .github/workflows/quality-daily.yaml; then
  echo "FAIL: quality workflow still uses the old cluster matrix"
  exit 1
fi

grep -q 'name: proxymock-ci-${{ matrix.replay }}' .github/workflows/quality-daily.yaml || {
  echo "FAIL: quality workflow proxymock artifact name should identify CI replay only"
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

echo "PASS: demo replay config covers 7 workloads, proxymock CI, build tags, banking-replay, and JWT-claim session DLP"
