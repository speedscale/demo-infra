#!/bin/bash
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
  echo "  $0 dev-decoy outerspace-go # run one replay"
  exit 1
fi

REPLAY_DIR="$REPO_ROOT/quality/speedctl-replay"

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
speedctl check

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

# --- launch replays ---
declare -A report_ids
declare -A replay_statuses

get_config_value() {
  local file=$1 key=$2
  awk -v k="$key" '$1==k":" {gsub(/["'"'"']/, "", $2); print $2; exit}' "$file"
}

for f in "${replay_files[@]}"; do
  name=$(get_config_value "$f" "name")
  workload=$(get_config_value "$f" "workload")
  namespace=$(get_config_value "$f" "namespace")
  snapshot_id=$(get_config_value "$f" "snapshotID")
  test_config_id=$(get_config_value "$f" "testConfigID")

  info "Launching replay: $name (workload=$workload, ns=$namespace)"

  report_id=""
  for attempt in 1 2 3; do
    output=$(speedctl infra replay \
      --cluster "$CLUSTER_NAME" \
      --namespace "$namespace" \
      --service "$workload" \
      --snapshot-id "$snapshot_id" \
      --test-config-id "$test_config_id" \
      --id-only 2>&1) || true

    report_id=$(echo "$output" | grep -Eo '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
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
done

# --- monitor replays ---
TIMEOUT_MINUTES=30
TIMEOUT_SECONDS=$((TIMEOUT_MINUTES * 60))
CHECK_INTERVAL=60
START_TIME=$(date +%s)

info "========================================"
info "Monitoring ${#report_ids[@]} replay(s)"
info "========================================"

while true; do
  elapsed=$(( $(date +%s) - START_TIME ))

  if [ $elapsed -gt $TIMEOUT_SECONDS ]; then
    error "Timeout reached (${TIMEOUT_MINUTES}m)"
    exit 1
  fi

  completed=0
  total=${#report_ids[@]}

  for name in "${!report_ids[@]}"; do
    status="${replay_statuses[$name]}"
    if [ "$status" = "completed" ] || [ "$status" = "failed" ]; then
      completed=$((completed + 1))
      continue
    fi

    rid="${report_ids[$name]}"
    report=$(speedctl get report "$rid" 2>/dev/null || echo "")

    if [ -n "$report" ]; then
      report_status=$(echo "$report" | jq -r '.report.status // "unknown"' 2>/dev/null || echo "unknown")
      norm=$(echo "$report_status" | tr '[:lower:]' '[:upper:]' | tr ' ' '_')
      info "  $name: $report_status (${elapsed}s elapsed)"

      case "$norm" in
        PASSED|MISSED_GOALS)
          replay_statuses["$name"]="completed"
          completed=$((completed + 1))
          ;;
        ERROR|CANCELED)
          error "  $name: terminal status $report_status"
          replay_statuses["$name"]="failed"
          completed=$((completed + 1))
          ;;
      esac
    else
      info "  $name: waiting for report... (${elapsed}s elapsed)"
    fi
  done

  info "Progress: $completed/$total"

  if [ $completed -eq $total ]; then
    break
  fi

  sleep $CHECK_INTERVAL
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
