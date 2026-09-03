#!/usr/bin/env bash
# Recreate the standing responder-only mocks in banking-app from scratch.
#
# The six replay-banking-* TrafficReplays serve every third-party call for the
# banking app from recorded snapshots. They are long-lived (ArgoCD-managed from
# microsvc), so the responder, its redis, and the SUT reroute drift as pods get
# rescheduled and inventory ages. Deleting each TR makes the operator tear its
# inventory down; re-applying the same manifest provisions a fresh responder and
# re-patches the SUT. The final check mirrors microsvc's PostSync reroll hook.
#
# Usage: reset-standing-mocks.sh <cluster-name> [namespace]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

CLUSTER_NAME=${1:-}
NS=${2:-banking-app}
if [ -z "$CLUSTER_NAME" ]; then
  echo "Usage: $0 <cluster-name> [namespace]"
  exit 1
fi

DELETE_TIMEOUT=${MOCK_DELETE_TIMEOUT:-300}
READY_TIMEOUT=${MOCK_READY_TIMEOUT:-600}
ROLLOUT_TIMEOUT=${MOCK_ROLLOUT_TIMEOUT:-300}

info()  { echo -e "\033[36m$*\033[0m"; }
warn()  { echo -e "\033[33m$*\033[0m"; }
error() { echo -e "\033[31m$*\033[0m"; }

info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

workdir=$(mktemp -d)
trap 'rm -rf "$workdir"' EXIT

# --- discover ---
kubectl -n "$NS" get trafficreplays -o json > "$workdir/trs.json"
mapfile -t trs < <(jq -r '.items[] | select(.spec.mode == "responder-only") | .metadata.name' "$workdir/trs.json")
if [ ${#trs[@]} -eq 0 ]; then
  error "No responder-only TrafficReplays found in $NS"
  exit 1
fi
info "Resetting ${#trs[@]} standing mock(s) in $NS: ${trs[*]}"

# Keep the manifest as it was applied (ArgoCD tracking annotation included) so
# the recreated object is identical to what the sync would produce. Fall back
# to the minimal spec when the annotation is absent.
declare -A workloads
for tr in "${trs[@]}"; do
  jq -r --arg n "$tr" '
    .items[] | select(.metadata.name == $n) |
    (.metadata.annotations["kubectl.kubernetes.io/last-applied-configuration"] // empty)
  ' "$workdir/trs.json" > "$workdir/$tr.json"
  if [ ! -s "$workdir/$tr.json" ]; then
    jq --arg n "$tr" '
      .items[] | select(.metadata.name == $n) |
      {apiVersion, kind, metadata: {name: .metadata.name, namespace: .metadata.namespace},
       spec: {snapshotID: .spec.snapshotID, testConfigID: .spec.testConfigID, mode: .spec.mode,
              workloadRef: .spec.workloadRef, cleanup: .spec.cleanup}}
    ' "$workdir/trs.json" > "$workdir/$tr.json"
  fi
  workloads[$tr]=$(jq -r '.spec.workloadRef.name' "$workdir/$tr.json")
  info "  $tr -> ${workloads[$tr]} (snapshot $(jq -r .spec.snapshotID "$workdir/$tr.json"))"
done

# From here on, any exit re-applies the saved manifests so a failed teardown
# cannot leave banking-app without its mocks until the next ArgoCD sync.
reapply() {
  for tr in "${trs[@]}"; do
    kubectl apply -f "$workdir/$tr.json" >/dev/null 2>&1 || true
  done
  rm -rf "$workdir"
}
trap reapply EXIT

# --- tear down ---
info "Deleting TrafficReplays"
kubectl -n "$NS" delete trafficreplay "${trs[@]}" --wait --timeout="${DELETE_TIMEOUT}s"

info "Waiting for the operator to remove responder and redis inventory"
deadline=$(( $(date +%s) + DELETE_TIMEOUT ))
while true; do
  remaining=$(kubectl -n "$NS" get deploy -l 'replay.speedscale.com/env-id' -o json \
    | jq -r '[.items[] | select(.metadata.name | test("^speedscale-(responder|redis)-")) | .metadata.name] | length')
  if [ "$remaining" -eq 0 ]; then
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    error "Inventory still present after ${DELETE_TIMEOUT}s:"
    kubectl -n "$NS" get deploy -l 'replay.speedscale.com/env-id' | grep -E 'speedscale-(responder|redis)-' || true
    exit 1
  fi
  sleep 5
done
info "Inventory removed"

# --- recreate ---
# ArgoCD selfHeal would recreate these on its next refresh; applying the saved
# manifests now makes the timing deterministic and is a no-op if it already did.
info "Re-applying TrafficReplays"
for tr in "${trs[@]}"; do
  kubectl apply -f "$workdir/$tr.json"
done

info "Waiting for Running and MocksReady"
deadline=$(( $(date +%s) + READY_TIMEOUT ))
while true; do
  ready=0
  for tr in "${trs[@]}"; do
    conds=$(kubectl -n "$NS" get trafficreplay "$tr" -o json 2>/dev/null \
      | jq -r '[.status.conditions[]? | select(.status == "True") | .type] | join("+")')
    case "$conds" in
      *Running*MocksReady*|*MocksReady*Running*) ready=$((ready + 1)) ;;
    esac
  done
  info "  $ready/${#trs[@]} ready"
  if [ "$ready" -eq ${#trs[@]} ]; then
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    error "Only $ready/${#trs[@]} TrafficReplays reached Running+MocksReady after ${READY_TIMEOUT}s"
    kubectl -n "$NS" get trafficreplays
    exit 1
  fi
  sleep 10
done

# --- verify SUT routing (same test as the microsvc PostSync reroll hook) ---
count_pods() {
  kubectl -n "$NS" get pod -l "app=$1" --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sed '/^$/d' | wc -l | tr -d ' '
}
count_pods_with_initproxy() {
  kubectl -n "$NS" get pod -l "app=$1" --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.initContainers[*].name}{"\n"}{end}' \
    | awk '/speedscale-initproxy-responder/ { n++ } END { print n+0 }'
}
routing_ok() {
  local d=$1 tr=$2 pods with env ip
  pods=$(count_pods "$d")
  with=$(count_pods_with_initproxy "$d")
  env=$(kubectl -n "$NS" get deploy "$d" -o go-template='{{ index .metadata.labels "replay.speedscale.com/env-id" }}')
  ip=$(kubectl -n "$NS" get deploy "$d" -o go-template='{{ index .metadata.annotations "replay.speedscale.com/responder-ip" }}')
  [ "$pods" -gt 0 ] && [ "$with" -eq "$pods" ] && [ "$env" = "$tr" ] && [ -n "$ip" ] && [ "$ip" != "<no value>" ]
}

info "Waiting for SUT rollouts"
for tr in "${trs[@]}"; do
  kubectl -n "$NS" rollout status "deployment/${workloads[$tr]}" --timeout="${ROLLOUT_TIMEOUT}s" \
    || warn "  ${workloads[$tr]}: rollout status timed out"
done

info "Checking responder routing on each SUT"
for tr in "${trs[@]}"; do
  d=${workloads[$tr]}
  if routing_ok "$d" "$tr"; then
    info "  $d: routed to $tr"
  else
    warn "  $d: routing incomplete; restarting"
    kubectl -n "$NS" rollout restart "deployment/$d"
    kubectl -n "$NS" rollout status "deployment/$d" --timeout="${ROLLOUT_TIMEOUT}s" || true
  fi
done

fail=0
info "========================================"
for tr in "${trs[@]}"; do
  d=${workloads[$tr]}
  if routing_ok "$d" "$tr"; then
    info "  OK:   $d <- $tr"
  else
    error "  FAIL: $d is not routed to $tr"
    fail=$((fail + 1))
  fi
done
info "========================================"
if [ "$fail" -gt 0 ]; then
  error "$fail standing mock(s) failed to reset"
  exit 1
fi
info "All standing mocks reset"
