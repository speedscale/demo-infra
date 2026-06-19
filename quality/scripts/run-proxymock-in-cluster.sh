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
target=$(get_config_value "$CONFIG_FILE" "proxymockTarget")

case "$CLUSTER_NAME" in
  dev-decoy)
    [ -n "$dev_snapshot_id" ] && snapshot_id="$dev_snapshot_id"
    ;;
  staging-decoy)
    [ -n "$staging_snapshot_id" ] && snapshot_id="$staging_snapshot_id"
    ;;
esac

if [ -z "$name" ] || [ -z "$namespace" ] || [ -z "$snapshot_id" ] || [ -z "$service" ] || [ -z "$service_port" ] || [ -z "$target" ]; then
  echo "Replay config is missing required proxymock fields: $CONFIG_FILE"
  exit 1
fi

if [[ "$target" == http://localhost:* ]]; then
  target="http://${service}.${namespace}.svc.cluster.local:${service_port}"
elif [[ "$target" == localhost:* ]]; then
  target="${service}.${namespace}.svc.cluster.local:${service_port}"
fi

SPEEDCTL_HOME="${SPEEDCTL_HOME:-$HOME/.speedscale}"
config_file="${SPEEDCTL_CONFIG:-$SPEEDCTL_HOME/config.yaml}"
if [ ! -f "$config_file" ]; then
  echo "Speedscale config not found: $config_file"
  exit 1
fi

run_suffix=$(date +%H%M%S)
job_name=$(echo "proxymock-${name#banking-}-${run_suffix}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | cut -c1-63 | sed 's/-$//')
secret_name="${job_name}-cfg"
image="${PROXYMOCK_JOB_IMAGE:-gcr.io/speedscale/proxymock:latest}"
report_dir="$REPO_ROOT/quality/proxymock-reports/${CLUSTER_NAME}/in-cluster"
mkdir -p "$report_dir"

cleanup() {
  kubectl -n "$namespace" delete job "$job_name" --ignore-not-found >/dev/null 2>&1 || true
  kubectl -n "$namespace" delete secret "$secret_name" --ignore-not-found >/dev/null 2>&1 || true
}
trap cleanup EXIT

info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

info "Creating proxymock replay job: $namespace/$job_name"
cleanup
kubectl -n "$namespace" create secret generic "$secret_name" --from-file=config.yaml="$config_file" >/dev/null
kubectl -n "$namespace" apply -f - >/dev/null <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: $job_name
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      volumes:
        - name: speedscale-config
          secret:
            secretName: $secret_name
      containers:
        - name: proxymock
          image: $image
          env:
            - name: HOME
              value: /tmp
            - name: SPEEDSCALE_HOME
              value: /tmp/.speedscale
            - name: SPEEDCTL_HOME
              value: /tmp/.speedscale
          volumeMounts:
            - name: speedscale-config
              mountPath: /config
              readOnly: true
          command:
            - /bin/sh
            - -lc
          args:
            - |
              mkdir -p /tmp/.speedscale /tmp/snapshot /tmp/results
              cp /config/config.yaml /tmp/.speedscale/config.yaml
              proxymock cloud pull snapshot $snapshot_id --config /tmp/.speedscale/config.yaml --out /tmp/snapshot
              token_response=\$(wget -q -O- --post-data='{"usernameOrEmail":"harper.clark.001","password":"SimUser123!"}' --header='Content-Type: application/json' http://banking-gateway.$namespace.svc.cluster.local/api/users/login)
              token=\$(printf '%s' "\$token_response" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
              if [ -z "\$token" ]; then echo "Postman auth preflight did not return a token"; exit 1; fi
              export FRESH_TOKEN="\$token"
              find /tmp/snapshot -type f \( -name '*.md' -o -name '*.json' \) -exec perl -0pi -e 'next unless /direction:\s+IN\b|Host:\s+banking-|http:host is banking-|"host":"banking-/; s/(Authorization:\s*Bearer\s+)[^\r\n\\]+/\${1}\$ENV{FRESH_TOKEN}/g; s/("Authorization"\s*:\s*\[\s*"Bearer\s+)[^"]+/\${1}\$ENV{FRESH_TOKEN}/g;' {} \;
              proxymock replay --config /tmp/.speedscale/config.yaml --in /tmp/snapshot --out /tmp/results --test-against $target --rewrite-host --fail-if 'requests.failed > 0' --output json
EOF

info "Waiting for job to finish"
if ! kubectl -n "$namespace" wait --for=condition=complete "job/$job_name" --timeout="${PROXYMOCK_JOB_TIMEOUT:-20m}"; then
  warn "Job did not complete successfully"
  kubectl -n "$namespace" logs "job/$job_name" > "$report_dir/${name}.log" || true
  cat "$report_dir/${name}.log"
  exit 1
fi

kubectl -n "$namespace" logs "job/$job_name" > "$report_dir/${name}.log"
info "In-cluster proxymock log: $report_dir/${name}.log"
cat "$report_dir/${name}.log"
