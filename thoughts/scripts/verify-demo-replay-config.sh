#!/usr/bin/env bash
# Proves: demo-infra targets daily replays at banking-replay, tags reports, and versions DLP session tagging.
# Created: 2026-06-18 after isolating replay traffic from the live demo namespace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

bash -n quality/scripts/run-replay.sh
bash -n quality/scripts/run-proxymock-scenario.sh
bash -n quality/scripts/apply-postman-auth-preflight.sh
jq empty quality/dlp/banking-app-keys.json
jq empty quality/test-configs/banking-daily-replay.json
jq empty quality/transforms/banking-jwt-resign.json
jq empty quality/postman/banking-auth.postman_collection.json

jq -e '
  .generator.dlpConfigId == "banking-app-keys"
  and .responder.dlpConfigId == "banking-app-keys"
' quality/test-configs/banking-daily-replay.json >/dev/null || {
  echo "FAIL: banking-daily-replay must use banking-app-keys for generator and responder DLP"
  exit 1
}

jq -e '
  .cluster.responderResources.requests.cpu == "100m"
  and .cluster.responderResources.requests.memory == "134217728"
  and .cluster.responderResources.limits.cpu == "500m"
  and .cluster.responderResources.limits.memory == "1073741824"
' quality/test-configs/banking-daily-replay.json >/dev/null || {
  echo "FAIL: banking-daily-replay should keep responder resources small enough for demo clusters"
  exit 1
}

for cluster_config in clusters/dev-decoy/argocd/speedscale-operator.yaml clusters/staging-decoy/argocd/speedscale-operator.yaml; do
  grep -q 'collector:' "$cluster_config" || {
    echo "FAIL: $cluster_config should override replay collector resources"
    exit 1
  }
  grep -q 'cpu: 100m' "$cluster_config" || {
    echo "FAIL: $cluster_config should lower replay component CPU requests"
    exit 1
  }
  grep -q 'runAsUser: 999' "$cluster_config" || {
    echo "FAIL: $cluster_config should run replay Redis as the image redis user"
    exit 1
  }
  grep -q 'fsGroup: 999' "$cluster_config" || {
    echo "FAIL: $cluster_config should allow replay Redis to write /data without root"
    exit 1
  }
done

if jq -e '.transforms[] | select((.filters.filters | length) == 0)' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: every DLP transform chain should have an explicit subset filter"
  exit 1
fi

if ! jq -e '
  .transforms[]
  | select(.extractor.type == "http_req_header")
  | select(.extractor.config.name == "Authorization")
  | select(any(.filters.filters[]; .include == true and .direction == "OUT"))
  | select(any(.filters.filters[]; .operator == "REGEX" and .network_address == "(^banking-|\\.svc|localhost|127\\.0\\.0\\.1)"))
' quality/dlp/banking-app-keys.json >/dev/null; then
  echo "FAIL: Authorization DLP must target outbound traffic while excluding internal service calls"
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

  for field in name workload namespace snapshotID devSnapshotID stagingSnapshotID testConfigID service servicePort localPort proxymockTarget; do
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

  base_snapshot=$(awk '$1=="snapshotID:" {print $2; exit}' "$cfg")
  dev_snapshot=$(awk '$1=="devSnapshotID:" {print $2; exit}' "$cfg")
  staging_snapshot=$(awk '$1=="stagingSnapshotID:" {print $2; exit}' "$cfg")
  if [ "$base_snapshot" != "$staging_snapshot" ] && [ "$dev_snapshot" != "$staging_snapshot" ]; then
    echo "FAIL: $cfg stagingSnapshotID should match snapshotID or the promoted devSnapshotID"
    exit 1
  fi
done

grep -q 'devSnapshotID' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not read devSnapshotID"
  exit 1
}

grep -q 'stagingSnapshotID' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not read stagingSnapshotID"
  exit 1
}

grep -q 'dev-decoy)' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not select dev snapshots for dev-decoy"
  exit 1
}

grep -q 'staging-decoy)' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not select staging snapshots for staging-decoy"
  exit 1
}

grep -q -- '--build-tag "$build_tag"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not pass build tags to speedctl"
  exit 1
}

grep -q 'build_tag="qd:${cluster_tag}:${workload_tag}:${run_id}.${run_attempt}"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not use compact build tags"
  exit 1
}

grep -q '\${#build_tag} -gt 50' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not guard build tag length"
  exit 1
}

for cluster in dev staging; do
  for replay in banking-accounts banking-ai banking-fraud banking-gateway banking-notification banking-transactions banking-user; do
    tag="qd:${cluster}:${replay#banking-}:27792876679.1"
    if [ ${#tag} -gt 50 ]; then
      echo "FAIL: expected build tag is too long (${#tag} chars): $tag"
      exit 1
    fi
  done
done

grep -q 'speedctl_args=(--config "$SPEEDCTL_CONFIG")' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not allow explicit SPEEDCTL_CONFIG selection"
  exit 1
}

grep -q 'export SPEEDSCALE_HOME' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not export SPEEDSCALE_HOME for local snapshot metadata"
  exit 1
}

grep -q 'SYNC_SPEEDSCALE_ARTIFACTS="${SYNC_SPEEDSCALE_ARTIFACTS:-false}"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner must default artifact syncing off for service-account CI"
  exit 1
}

grep -q 'sync_enabled' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not gate artifact syncing behind sync_enabled"
  exit 1
}

grep -q 'require_cloud_artifact dlp-config "$DLP_CONFIG_ID"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner should validate existing DLP config when syncing is disabled"
  exit 1
}

grep -q 'require_cloud_artifact transform "$JWT_TRANSFORM_ID"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner should validate existing JWT transform when syncing is disabled"
  exit 1
}

grep -q 'require_test_config' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner should validate existing test config content when syncing is disabled"
  exit 1
}

grep -q 'Speedscale test-config $TEST_CONFIG_ID must use DLP config $DLP_CONFIG_ID' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner should fail clearly when cloud test config DLP drifts"
  exit 1
}

grep -q 'speedctl_cmd put dlp-config "$REPO_ROOT/quality/dlp/banking-app-keys.json"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner lost the opt-in banking-app-keys DLP sync"
  exit 1
}

grep -q 'speedctl_cmd put transform "$JWT_TRANSFORM_FILE"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner lost the opt-in banking-jwt-resign transform sync"
  exit 1
}

grep -q 'speedctl_cmd put test-config "$REPO_ROOT/quality/test-configs/banking-daily-replay.json"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner lost the opt-in banking-daily-replay sync"
  exit 1
}

grep -q 'ensure_snapshot_jwt_resign "$snapshot_id"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner does not attach or validate JWT resign transform before replay"
  exit 1
}

grep -q 'Snapshot $snapshot_id is missing the $JWT_TRANSFORM_ID JWT resign transform' quality/scripts/run-replay.sh || {
  echo "FAIL: read-only replay runner should fail clearly when snapshot transform metadata is missing"
  exit 1
}

grep -q 'speedctl_cmd pull snapshot "$snapshot_id"' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner lost opt-in snapshot metadata pull before attaching JWT resign transform"
  exit 1
}

grep -q 'speedctl_cmd push snapshot "$snapshot_id" --no-analyze --force' quality/scripts/run-replay.sh || {
  echo "FAIL: replay runner should push JWT metadata without requesting snapshot analysis only when syncing is enabled"
  exit 1
}

if grep -q 'speedctl_cmd put snapshot' quality/scripts/run-replay.sh; then
  echo "FAIL: replay runner should not use put snapshot because it re-runs snapshot analysis"
  exit 1
fi

if grep -q 'SYNC_SPEEDSCALE_ARTIFACTS: true' .github/workflows/quality-post-deploy.yaml; then
  echo "FAIL: post-deploy quality workflow must not sync Speedscale artifacts with service-account CI keys"
  exit 1
fi

grep -q 'proxymock cloud pull snapshot "$snapshot_id"' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not pull snapshots from Speedscale Cloud"
  exit 1
}

grep -q 'apply-postman-auth-preflight.sh' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not run the Postman auth preflight before replay"
  exit 1
}

grep -q 'deployment/banking-gateway' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner must run auth preflight through banking-gateway"
  exit 1
}

grep -q 'PROXYMOCK_AUTH_PORT' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not isolate the auth preflight port"
  exit 1
}

grep -q 'Postman auth preflight did not update any banking Authorization headers' quality/scripts/apply-postman-auth-preflight.sh || {
  echo "FAIL: Postman auth preflight should fail if no replay auth headers are updated"
  exit 1
}

grep -q 'decode_jwt_subject' quality/scripts/apply-postman-auth-preflight.sh || {
  echo "FAIL: Postman auth preflight should preserve recorded JWT subjects"
  exit 1
}

grep -q 'for $token_count recorded JWT subject' quality/scripts/apply-postman-auth-preflight.sh || {
  echo "FAIL: Postman auth preflight should report per-subject token refresh"
  exit 1
}

jq -e '
  .info.name == "Banking Replay Auth"
  and any(.item[]; .name == "Login" and .request.method == "POST" and (.request.url.path | join("/") == "api/users/login"))
  and any(.variable[]; .key == "username" and .value == "harper.clark.001")
  and any(.variable[]; .key == "password" and .value == "SimUser123!")
' quality/postman/banking-auth.postman_collection.json >/dev/null || {
  echo "FAIL: banking Postman auth collection must log in as the seeded demo user"
  exit 1
}

grep -q 'devSnapshotID' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not read devSnapshotID"
  exit 1
}

grep -q 'stagingSnapshotID' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not read stagingSnapshotID"
  exit 1
}

grep -q 'dev-decoy)' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not select dev snapshots for dev-decoy"
  exit 1
}

grep -q 'staging-decoy)' quality/scripts/run-proxymock-scenario.sh || {
  echo "FAIL: proxymock runner does not select staging snapshots for staging-decoy"
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

grep -q '^devSnapshotID: 75ef5e98-2755-45c4-ba4a-8747c5fbb13d$' quality/speedctl-replay/banking-fraud.yaml || {
  echo "FAIL: banking-fraud does not use the dev-owned inbound fraud snapshot for dev replay"
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

jq -e '
  .id == "banking-jwt-resign"
  and .name == "banking-jwt-resign"
  and (.generator | length == 1)
  and .generator[0].extractor.type == "http_req_header"
  and .generator[0].extractor.config.name == "Authorization"
  and any(.generator[0].filters.filters[]; .include == true and .direction == "IN")
  and (.generator[0].transforms | length == 1)
  and .generator[0].transforms[0].type == "jwt_resign"
  and .generator[0].transforms[0].config.secretPath == "${{secret:banking-jwt-secret/secret}}"
' quality/transforms/banking-jwt-resign.json >/dev/null || {
  echo "FAIL: banking-jwt-resign must re-sign inbound Authorization JWTs with the banking JWT secret"
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

  operator_app="clusters/${cluster}/argocd/speedscale-operator.yaml"
  grep -q 'test_prep_timeout: 20m' "$operator_app" || {
    echo "FAIL: $operator_app does not set a 20m replay prep timeout"
    exit 1
  }
done

echo "PASS: demo replay config covers 7 workloads, read-only CI replay, proxymock CI, Postman auth preflight, build tags, banking-replay, DLP session tagging, and JWT resigning"
