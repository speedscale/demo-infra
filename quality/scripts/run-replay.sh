#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CLUSTER_NAME=${1:-}
REPLAY_FILTER=${2:-all}

if [ -z "$CLUSTER_NAME" ]; then
  echo "Usage: $0 <cluster-name> [replay-name|all]"
  echo ""
  echo "Examples:"
  echo "  $0 dev-decoy              # run all replays"
  echo "  $0 dev-decoy microsvc      # run one replay"
  exit 1
fi

REPLAY_DIR="$REPO_ROOT/quality/speedctl-replay"
DLP_CONFIG_ID="banking-app-keys"
TEST_CONFIG_ID="banking-daily-replay"
JWT_TRANSFORM_ID="banking-jwt-resign"
JWT_TRANSFORM_FILE="$REPO_ROOT/quality/transforms/${JWT_TRANSFORM_ID}.json"
SYNC_SPEEDSCALE_ARTIFACTS="${SYNC_SPEEDSCALE_ARTIFACTS:-false}"

declare -a speedctl_args
if [ -n "${SPEEDCTL_CONFIG:-}" ]; then
  speedctl_args=(--config "$SPEEDCTL_CONFIG")
fi

speedctl_cmd() {
  speedctl "${speedctl_args[@]}" "$@"
}

# --- logging ---
info()  { echo -e "\033[36m$*\033[0m"; }
warn()  { echo -e "\033[33m$*\033[0m"; }
error() { echo -e "\033[31m$*\033[0m"; }

sync_enabled() {
  case "$SYNC_SPEEDSCALE_ARTIFACTS" in
    true|TRUE|1|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

require_cloud_artifact() {
  local kind=$1 id=$2

  if ! speedctl_cmd get "$kind" "$id" >/dev/null; then
    error "Required Speedscale $kind not found or not readable: $id"
    error "Run with SYNC_SPEEDSCALE_ARTIFACTS=true using a user/admin key to upload replay artifacts."
    return 1
  fi
}

require_test_config() {
  local current
  current=$(mktemp)

  if ! speedctl_cmd get test-config "$TEST_CONFIG_ID" > "$current"; then
    rm -f "$current"
    error "Required Speedscale test-config not found or not readable: $TEST_CONFIG_ID"
    error "Run with SYNC_SPEEDSCALE_ARTIFACTS=true using a user/admin key to upload replay artifacts."
    return 1
  fi

  if ! jq -e --arg id "$DLP_CONFIG_ID" '
    .generator.dlpConfigId == $id
    and .responder.dlpConfigId == $id
  ' "$current" >/dev/null; then
    rm -f "$current"
    error "Speedscale test-config $TEST_CONFIG_ID must use DLP config $DLP_CONFIG_ID for generator and responder."
    error "Run with SYNC_SPEEDSCALE_ARTIFACTS=true using a user/admin key to update replay artifacts."
    return 1
  fi

  rm -f "$current"
}

# --- connect ---
info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

info "Setting up speedctl"
SPEEDSCALE_HOME="${SPEEDSCALE_HOME:-${SPEEDCTL_HOME:-$HOME/.speedscale}}"
export SPEEDSCALE_HOME
mkdir -p "$SPEEDSCALE_HOME"
speedctl_cmd check

if sync_enabled; then
  info "Syncing banking DLP rule"
  speedctl_cmd put dlp-config "$REPO_ROOT/quality/dlp/banking-app-keys.json"

  info "Syncing banking JWT resign transform"
  speedctl_cmd put transform "$JWT_TRANSFORM_FILE"

  info "Syncing daily replay test config"
  speedctl_cmd put test-config "$REPO_ROOT/quality/test-configs/banking-daily-replay.json"
else
  info "Validating banking replay artifacts"
  require_cloud_artifact dlp-config "$DLP_CONFIG_ID"
  require_cloud_artifact transform "$JWT_TRANSFORM_ID"
  require_test_config
fi

# --- collect replay configs ---
declare -a replay_files
if [ "$REPLAY_FILTER" = "all" ]; then
  for f in "$REPLAY_DIR"/*.yaml; do
    [ -f "$f" ] && replay_files+=("$f")
  done
else
  f="$REPLAY_DIR/${REPLAY_FILTER}.yaml"
  if [ ! -f "$f" ]; then
    error "Replay config not found: $f"
    exit 1
  fi
  replay_files=("$f")
fi

if [ ${#replay_files[@]} -eq 0 ]; then
  error "No replay configs found in $REPLAY_DIR"
  exit 1
fi

info "Found ${#replay_files[@]} replay config(s)"

# --- launch and monitor replays ---
declare -A report_ids
declare -A replay_statuses

get_config_value() {
  local file=$1 key=$2
  awk -v k="$key" '$1==k":" {gsub(/["'"'"']/, "", $2); print $2; exit}' "$file"
}

ensure_snapshot_jwt_resign() {
  local snapshot_id=$1
  local current snapshot_file updated

  current=$(mktemp)
  updated=$(mktemp)
  snapshot_file="$SPEEDSCALE_HOME/data/snapshots/${snapshot_id}.json"

  speedctl_cmd get snapshot "$snapshot_id" > "$current"

  if jq -e --arg id "$JWT_TRANSFORM_ID" '
    .tokenConfigId == $id
    and .tokenizerConfig.id == $id
    and any((.tokenizerConfig.generator // [])[]; any(.transforms[]?; .type == "jwt_resign"))
  ' "$current" >/dev/null; then
    rm -f "$current" "$updated"
    return 0
  fi

  if ! sync_enabled; then
    rm -f "$current" "$updated"
    error "Snapshot $snapshot_id is missing the $JWT_TRANSFORM_ID JWT resign transform."
    error "Run with SYNC_SPEEDSCALE_ARTIFACTS=true using a user/admin key to attach replay transforms."
    return 1
  fi

  speedctl_cmd pull snapshot "$snapshot_id"

  if [ ! -f "$snapshot_file" ]; then
    rm -f "$current" "$updated"
    error "Pulled snapshot metadata not found: $snapshot_file"
    return 1
  fi

  jq --slurpfile transform "$JWT_TRANSFORM_FILE" '
    .tokenConfigId = $transform[0].id
    | .tokenizerConfig = $transform[0]
  ' "$snapshot_file" > "$updated"

  mv "$updated" "$snapshot_file"
  speedctl_cmd push snapshot "$snapshot_id" --no-analyze --force
  rm -f "$current" "$updated"
}

wait_for_replay() {
  local name=$1 rid=$2
  local timeout_minutes=${REPLAY_TIMEOUT_MINUTES:-30}
  local timeout_seconds=$((timeout_minutes * 60))
  local check_interval=${REPLAY_CHECK_INTERVAL:-60}
  local error_grace_minutes=${REPLAY_ERROR_GRACE_MINUTES:-15}
  local error_grace_seconds=$((error_grace_minutes * 60))
  local start_time elapsed report report_status norm

  start_time=$(date +%s)
  while true; do
    elapsed=$(( $(date +%s) - start_time ))

    if [ $elapsed -gt $timeout_seconds ]; then
      error "  $name: timeout reached (${timeout_minutes}m)"
      return 1
    fi

    report=$(speedctl_cmd get report "$rid" 2>/dev/null || echo "")

    if [ -n "$report" ]; then
      report_status=$(echo "$report" | jq -r '.report.status // "unknown"' 2>/dev/null || echo "unknown")
      norm=$(echo "$report_status" | tr '[:lower:]' '[:upper:]' | tr ' ' '_')
      info "  $name: $report_status (${elapsed}s elapsed)"

      case "$norm" in
        PASSED|MISSED_GOALS)
          return 0
          ;;
        CANCELED|*CANCEL*)
          error "  $name: terminal status $report_status"
          return 1
          ;;
        *ERROR*)
          if [ $elapsed -lt $error_grace_seconds ]; then
            warn "  $name: status $report_status before ${error_grace_minutes}m grace; continuing"
          else
            error "  $name: terminal status $report_status"
            return 1
          fi
          ;;
      esac
    else
      info "  $name: waiting for report... (${elapsed}s elapsed)"
    fi

    sleep "$check_interval"
  done
}

for f in "${replay_files[@]}"; do
  name=$(get_config_value "$f" "name")
  workload=$(get_config_value "$f" "workload")
  namespace=$(get_config_value "$f" "namespace")
  snapshot_id=$(get_config_value "$f" "snapshotID")
  dev_snapshot_id=$(get_config_value "$f" "devSnapshotID")
  staging_snapshot_id=$(get_config_value "$f" "stagingSnapshotID")
  test_config_id=$(get_config_value "$f" "testConfigID")
  run_id="${GITHUB_RUN_ID:-local}"
  run_attempt="${GITHUB_RUN_ATTEMPT:-1}"
  cluster_tag="${CLUSTER_NAME%-decoy}"
  workload_tag="${name#banking-}"
  build_tag="qd:${cluster_tag}:${workload_tag}:${run_id}.${run_attempt}"

  case "$CLUSTER_NAME" in
    dev-decoy)
      [ -n "$dev_snapshot_id" ] && snapshot_id="$dev_snapshot_id"
      ;;
    staging-decoy)
      [ -n "$staging_snapshot_id" ] && snapshot_id="$staging_snapshot_id"
      ;;
  esac

  if [ ${#build_tag} -gt 50 ]; then
    error "Build tag is too long (${#build_tag} chars): $build_tag"
    exit 1
  fi

  info "Ensuring JWT resign transform on snapshot $snapshot_id"
  ensure_snapshot_jwt_resign "$snapshot_id"

  info "Launching replay: $name (workload=$workload, ns=$namespace, snapshot=$snapshot_id, tag=$build_tag)"

  report_id=""
  for attempt in 1 2 3; do
    output=$(speedctl_cmd infra replay \
      --cluster "$CLUSTER_NAME" \
      --namespace "$namespace" \
      --service "$workload" \
      --snapshot-id "$snapshot_id" \
      --test-config-id "$test_config_id" \
      --build-tag "$build_tag" \
      --id-only 2>&1) || true

    report_id=$(echo "$output" | grep -Eo '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1 || true)
    if [ -n "$report_id" ]; then
      break
    fi
    warn "  attempt $attempt: no report ID returned"
    [ "$attempt" -lt 3 ] && sleep 5
  done

  if [ -z "$report_id" ]; then
    error "Failed to start replay for $name"
    error "Output: $output"
    exit 1
  fi

  info "  Report ID: $report_id"
  report_ids["$name"]="$report_id"
  replay_statuses["$name"]="running"

  if wait_for_replay "$name" "$report_id"; then
    replay_statuses["$name"]="completed"
  else
    replay_statuses["$name"]="failed"
  fi
done

# --- summary ---
failed=0
info "========================================"
info "Results:"
for name in "${!replay_statuses[@]}"; do
  status="${replay_statuses[$name]}"
  rid="${report_ids[$name]}"
  if [ "$status" = "failed" ]; then
    error "  FAIL: $name (report: $rid)"
    failed=$((failed + 1))
  else
    info "  PASS: $name (report: $rid)"
  fi
done
info "========================================"

if [ $failed -gt 0 ]; then
  error "$failed replay(s) failed"
  exit 1
fi

info "All replays passed"
