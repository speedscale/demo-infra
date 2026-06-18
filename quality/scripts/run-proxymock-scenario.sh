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

get_config_value() {
  local file=$1 key=$2
  awk -v k="$key" '$1==k":" {gsub(/["'"'"']/, "", $2); print $2; exit}' "$file"
}

name=$(get_config_value "$CONFIG_FILE" "name")
namespace=$(get_config_value "$CONFIG_FILE" "namespace")
snapshot_id=$(get_config_value "$CONFIG_FILE" "snapshotID")
service=$(get_config_value "$CONFIG_FILE" "service")
service_port=$(get_config_value "$CONFIG_FILE" "servicePort")
local_port=$(get_config_value "$CONFIG_FILE" "localPort")
target=$(get_config_value "$CONFIG_FILE" "proxymockTarget")

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
mkdir -p "$snapshot_dir" "$result_dir" "$report_dir"

info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

info "Pulling snapshot: $snapshot_id"
proxymock cloud pull snapshot "$snapshot_id" \
  --config "$SPEEDCTL_HOME/config.yaml" \
  --out "$snapshot_dir"

info "Forwarding $namespace/$service:$service_port to localhost:$local_port"
kubectl -n "$namespace" rollout status "deployment/$service" --timeout=5m
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
proxymock replay \
  --config "$SPEEDCTL_HOME/config.yaml" \
  --in "$snapshot_dir" \
  --out "$result_dir" \
  --test-against "$target" \
  --rewrite-host \
  --fail-if "requests.failed > 0"

proxymock report \
  --config "$SPEEDCTL_HOME/config.yaml" \
  --in "$result_dir" \
  --out "$report_dir/${name}.json"

info "Proxymock report: $report_dir/${name}.json"
