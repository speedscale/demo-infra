#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CLUSTER_NAME=${1:-}
REPLAY_NAME=${2:-}

if [ -z "$CLUSTER_NAME" ] || [ -z "$REPLAY_NAME" ]; then
  echo "Usage: $0 <cluster-name> <replay-name>"
  exit 1
fi

CONFIG_FILE="$REPO_ROOT/quality/speedctl-replay/${REPLAY_NAME}.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "Replay config not found: $CONFIG_FILE"
  exit 1
fi

info() { echo -e "\033[36m$*\033[0m"; }
warn() { echo -e "\033[33m$*\033[0m"; }

get_config_value() {
  local file=$1 key=$2
  awk -v k="$key" '$1==k":" {gsub(/["'"'"']/, "", $2); print $2; exit}' "$file"
}

name=$(get_config_value "$CONFIG_FILE" "name")
namespace=$(get_config_value "$CONFIG_FILE" "namespace")
snapshot_id=$(get_config_value "$CONFIG_FILE" "snapshotID")
dev_snapshot_id=$(get_config_value "$CONFIG_FILE" "devSnapshotID")
staging_snapshot_id=$(get_config_value "$CONFIG_FILE" "stagingSnapshotID")
service=$(get_config_value "$CONFIG_FILE" "service")
service_port=$(get_config_value "$CONFIG_FILE" "servicePort")
local_port=$(get_config_value "$CONFIG_FILE" "localPort")
target=$(get_config_value "$CONFIG_FILE" "proxymockTarget")

case "$CLUSTER_NAME" in
  dev-decoy)
    [ -n "$dev_snapshot_id" ] && snapshot_id="$dev_snapshot_id"
    ;;
  staging-decoy)
    [ -n "$staging_snapshot_id" ] && snapshot_id="$staging_snapshot_id"
    ;;
esac

if [ -z "$name" ] || [ -z "$namespace" ] || [ -z "$snapshot_id" ] || [ -z "$service" ] || [ -z "$service_port" ] || [ -z "$local_port" ] || [ -z "$target" ]; then
  echo "Replay config is missing required proxymock fields: $CONFIG_FILE"
  exit 1
fi

SPEEDCTL_HOME="${SPEEDCTL_HOME:-$HOME/.speedscale}"
SPEEDSCALE_HOME="${SPEEDSCALE_HOME:-$SPEEDCTL_HOME}"
export SPEEDCTL_HOME SPEEDSCALE_HOME
runner_temp="${RUNNER_TEMP:-/tmp}"

snapshot_dir="$runner_temp/proxymock/${name}/snapshot"
result_dir="$runner_temp/proxymock/${name}/results"
report_dir="$REPO_ROOT/quality/proxymock-reports/${CLUSTER_NAME}"
rm -rf "$snapshot_dir" "$result_dir"
mkdir -p "$snapshot_dir" "$result_dir" "$report_dir"
prune_file="$REPO_ROOT/quality/proxymock-prune/${name}.patterns"

info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

info "Pulling snapshot: $snapshot_id"
proxymock cloud pull snapshot "$snapshot_id" \
  --config "$SPEEDCTL_HOME/config.yaml" \
  --out "$snapshot_dir"

if [ -f "$prune_file" ]; then
  info "Pruning proxymock requests listed in $prune_file"
  prune_count=0
  prune_names_file="$runner_temp/${name}-prune-names.txt"
  prune_patterns_file="$runner_temp/${name}-prune-patterns.txt"
  : >"$prune_names_file"
  : >"$prune_patterns_file"

  while IFS= read -r pattern || [ -n "$pattern" ]; do
    case "$pattern" in
      ""|\#*) continue ;;
    esac

    case "$pattern" in
      *.md|*.json)
        echo "$pattern" >>"$prune_names_file"
        ;;
      *)
        echo "$pattern" >>"$prune_patterns_file"
        ;;
    esac
  done < "$prune_file"

  while IFS= read -r rrpair_file; do
    [ -f "$rrpair_file" ] || continue
    rrpair_name=$(basename "$rrpair_file")

    if grep -Fxq -- "$rrpair_name" "$prune_names_file"; then
      rm "$rrpair_file"
      prune_count=$((prune_count + 1))
      continue
    fi

    if [ -s "$prune_patterns_file" ]; then
      while IFS= read -r pattern || [ -n "$pattern" ]; do
        if [[ "$rrpair_file" =~ $pattern ]] || rg -q -- "$pattern" "$rrpair_file"; then
          rm "$rrpair_file"
          prune_count=$((prune_count + 1))
          break
        fi
      done < "$prune_patterns_file"
    fi
  done < <(find "$snapshot_dir" -type f)

  info "Pruned $prune_count request(s)"
fi

info "Forwarding $namespace/$service:$service_port to localhost:$local_port"
if ! kubectl -n "$namespace" wait --for=condition=available "deployment/$service" --timeout=5m; then
  warn "Deployment $namespace/$service is not available"
  kubectl -n "$namespace" get deployment "$service" || true
  exit 1
fi
port_forward_log="$runner_temp/${name}-port-forward.log"
kubectl -n "$namespace" port-forward "service/$service" "$local_port:$service_port" >"$port_forward_log" 2>&1 &
pf_pid=$!
trap 'kill "$pf_pid" 2>/dev/null || true' EXIT

for attempt in {1..30}; do
  if timeout 1 bash -c "</dev/tcp/127.0.0.1/$local_port" 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 30 ]; then
    echo "Port-forward did not become ready"
    cat "$port_forward_log" || true
    exit 1
  fi
  sleep 1
done

info "Replaying proxymock scenario: $name"
replay_status=0
proxymock replay \
  --config "$SPEEDCTL_HOME/config.yaml" \
  --in "$snapshot_dir" \
  --out "$result_dir" \
  --test-against "$target" \
  --rewrite-host \
  --fail-if "requests.failed > 0" || replay_status=$?

if [ -d "$result_dir" ] && [ "$(find "$result_dir" -type f | wc -l | tr -d ' ')" -gt 0 ]; then
  proxymock report \
    --config "$SPEEDCTL_HOME/config.yaml" \
    --in "$result_dir" \
    --out "$report_dir/${name}.json"

  server_errors=$(jq '[.reliability.statusBreakdown[]? | select(.bucket == "5xx") | .count] | add // 0' "$report_dir/${name}.json")
  if [ "$server_errors" -gt 0 ]; then
    echo "Proxymock report contains $server_errors 5xx response(s)"
    exit 1
  fi
else
  warn "No proxymock results written for $name"
fi

info "Proxymock report: $report_dir/${name}.json"
exit "$replay_status"
