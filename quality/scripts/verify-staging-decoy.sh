#!/usr/bin/env bash
set -euo pipefail

CLUSTER="${1:-staging-decoy}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MICROSVC="${MICROSVC_REPO:-}"
NS="banking-app"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

ok() {
  echo "OK: $*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is not installed"
}

require_cmd kubectl
require_cmd jq

if [[ -z "$MICROSVC" ]]; then
  for candidate in "$ROOT/../microsvc" "$ROOT/../../../../microsvc"; do
    if [[ -d "$candidate/kubernetes/overlays/speedscale" ]]; then
      MICROSVC="$(cd "$candidate" && pwd)"
      break
    fi
  done
fi

test -d "$MICROSVC" || fail "microsvc repo not found at $MICROSVC"

echo "Checking local manifests..."
kubectl kustomize "$MICROSVC/kubernetes/overlays/speedscale" >/tmp/microsvc-speedscale-verify.yaml
grep -q 'name: seed-user-pool' /tmp/microsvc-speedscale-verify.yaml || fail "seed-user-pool job missing from rendered overlay"
if grep -q 'seed-demo-user' /tmp/microsvc-speedscale-verify.yaml; then
  fail "old seed-demo-user name still renders"
fi

for tr in accounts ai fraud notification transactions user; do
  yq_check=$(awk "/name: replay-banking-${tr}/{found=1} found && /out: true/{print; exit}" /tmp/microsvc-speedscale-verify.yaml)
  test -n "$yq_check" || fail "replay-banking-${tr} missing sidecar.tls.out: true"
done
ok "TrafficReplay manifests render with TLS-out"

for file in "$MICROSVC"/kubernetes/observability/dashboards/*.json; do
  jq empty "$file" || fail "invalid dashboard JSON: $file"
done
ok "Grafana dashboard JSON parses"

current_context="$(kubectl config current-context 2>/dev/null || true)"
case "$current_context" in
  *"$CLUSTER"*) ok "kubectl context: $current_context" ;;
  *) fail "kubectl context is '$current_context', expected it to contain '$CLUSTER'" ;;
esac

echo "Checking ArgoCD and cluster state..."
kubectl -n argocd get application "microsvc-${CLUSTER}" >/dev/null
kubectl -n observability rollout status deployment/grafana --timeout=120s
kubectl -n observability rollout status deployment/prometheus --timeout=120s
kubectl -n speedscale get deploy >/dev/null
ok "core apps are reachable"

echo "Checking TrafficReplays..."
for tr in replay-banking-accounts replay-banking-ai replay-banking-fraud replay-banking-notification replay-banking-transactions replay-banking-user; do
  running="$(kubectl -n "$NS" get trafficreplay "$tr" -o json | jq -r '[.status.conditions[]? | select(.type == "Running" and .status == "True")] | length')"
  test "$running" -gt 0 || fail "$tr is not Running"
done
ok "all TrafficReplays are Running"

echo "Checking Speedscale sidecars and responder pods..."
for deploy in banking-accounts banking-ai banking-fraud banking-notification banking-transactions banking-user; do
  pods="$(kubectl -n "$NS" get pod -l app="$deploy" --field-selector=status.phase=Running -o json | jq '.items | length')"
  test "$pods" -gt 0 || fail "$deploy has no running pods"
  with_speedscale="$(kubectl -n "$NS" get pod -l app="$deploy" --field-selector=status.phase=Running -o json | jq '[.items[] | select(((.spec.initContainers // []) | any(.name | test("responder"))) or ((.spec.containers // []) | any(.name == "speedscale-goproxy")))] | length')"
  test "$with_speedscale" -eq "$pods" || fail "$deploy Speedscale coverage ${with_speedscale}/${pods}"
done
ok "all TrafficReplay workloads have Speedscale routing"

for tr in accounts ai fraud notification transactions user; do
  responder_pods="$(kubectl -n "$NS" get pod -l "app=responder,replay.speedscale.com/env-id=replay-banking-${tr}" -o json | jq '.items | length')"
  test "$responder_pods" -gt 0 || fail "missing responder pod for replay-banking-${tr}"
done
ok "all TrafficReplay responder pods exist"

echo "Checking Grafana route and services..."
kubectl -n observability get svc grafana >/dev/null
kubectl -n observability get configmap grafana-dashboards-app grafana-dashboards-app-details grafana-dashboards-byoc >/dev/null
ok "Grafana services and dashboard ConfigMaps exist"

echo "Checking Speedscale metric services..."
kubectl -n speedscale get svc speedscale-forwarder-metrics speedscale-nettap-metrics >/dev/null
ok "Speedscale metric services exist"

echo "Done"
