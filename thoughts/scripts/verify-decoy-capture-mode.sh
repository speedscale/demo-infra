#!/usr/bin/env bash
# Proves: dev-decoy uses the eBPF overlay and staging-decoy uses the sidecar overlay.
# Created: 2026-06-18 after wiring staging-decoy to speedscale-sidecar.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

dev_app="$repo_root/clusters/dev-decoy/argocd/microsvc.yaml"
staging_app="$repo_root/clusters/staging-decoy/argocd/microsvc.yaml"

microsvc_dir="${MICROSVC_DIR:-}"
if [[ -z "$microsvc_dir" ]]; then
  for candidate in "$repo_root/../microsvc" "/Users/kahrens/spd-workspace/demos/microsvc"; do
    if [[ -d "$candidate/kubernetes/overlays/speedscale-sidecar" ]]; then
      microsvc_dir="$candidate"
      break
    fi
  done
fi

if [[ -z "$microsvc_dir" ]]; then
  echo "FAIL: set MICROSVC_DIR to a microsvc checkout" >&2
  exit 1
fi

if ! grep -q 'path: kubernetes/overlays/speedscale$' "$dev_app"; then
  echo "FAIL: dev-decoy microsvc app is not using the eBPF overlay" >&2
  exit 1
fi

if ! grep -q 'path: kubernetes/overlays/speedscale-sidecar$' "$staging_app"; then
  echo "FAIL: staging-decoy microsvc app is not using the sidecar overlay" >&2
  exit 1
fi

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
kustomize build "$microsvc_dir/kubernetes/overlays/speedscale-sidecar" > "$rendered"

sidecar_count="$(grep -c 'sidecar.speedscale.com/inject: "true"' "$rendered" || true)"
capture_count="$(grep -c 'capture.speedscale.com/enabled' "$rendered" || true)"

if [[ "$sidecar_count" -lt 8 ]]; then
  echo "FAIL: sidecar overlay rendered only $sidecar_count sidecar injection annotations" >&2
  exit 1
fi

if [[ "$capture_count" != "0" ]]; then
  echo "FAIL: sidecar overlay still renders capture.speedscale.com/enabled" >&2
  exit 1
fi

echo "PASS: dev-decoy uses eBPF; staging-decoy uses sidecar; sidecar overlay renders without eBPF capture annotations"
