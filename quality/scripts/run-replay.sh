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

# --- connect ---
info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

info "Setting up speedctl"
mkdir -p "${SPEEDCTL_HOME:-$HOME/.speedscale}"
SPEEDCTL_HOME="${SPEEDCTL_HOME:-$HOME/.speedscale}"
speedctl_cmd check

info "Syncing daily replay test config"
speedctl_cmd put test-config "$REPO_ROOT/quality/test-configs/banking-daily-replay.json"

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

wait_for_replay() {
  local name=$1 rid=$2
  local timeout_minutes=${REPLAY_TIMEOUT_MINUTES:-30}
  local timeout_seconds=$((timeout_minutes * 60))
  local check_interval=${REPLAY_CHECK_INTERVAL:-60}
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
        *ERROR*|CANCELED|*CANCEL*)
          error "  $name: terminal status $report_status"
          return 1
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
