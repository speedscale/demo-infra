#!/usr/bin/env bash
# Proves: BYOC OTEL S3 layout renders and the pinned Collector accepts it.
# Created: 2026-06-19 after adding workload/time partitioning.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

byoc_dir="${BYOC_DIR:-}"
if [[ -z "$byoc_dir" ]]; then
  for candidate in "$repo_root/../speedscale-byoc" "/Users/kahrens/go/src/github.com/speedscale/speedscale-byoc" "/Users/kahrens/spd-workspace/demos/speedscale-byoc"; do
    if [[ -f "$candidate/charts/fluentbit-s3/Chart.yaml" ]]; then
      byoc_dir="$candidate"
      break
    fi
  done
fi

if [[ -z "$byoc_dir" ]]; then
  echo "FAIL: set BYOC_DIR to a speedscale-byoc checkout" >&2
  exit 1
fi

for cmd in helm yq docker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "FAIL: missing required command: $cmd" >&2
    exit 1
  fi
done

tmp_root="$repo_root/thoughts/.proof-tmp"
mkdir -p "$tmp_root"
tmp="$(mktemp -d "$tmp_root/byoc-s3-layout.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

chart="$byoc_dir/charts/fluentbit-s3"
collector_image="otel/opentelemetry-collector-contrib:0.108.0"

validate_render() {
  local name="$1"
  local rendered="$2"
  local otel="$tmp/$name-otel.yaml"

  yq e '.' "$rendered" >/dev/null
  yq e 'select(.kind == "ConfigMap" and .metadata.name == "otel-collector-config").data."otel.yaml"' "$rendered" > "$otel"

  if [[ ! -s "$otel" ]]; then
    echo "FAIL: $name did not render otel-collector-config" >&2
    exit 1
  fi

  docker run --rm -v "$tmp:/configs:ro" "$collector_image" validate --config="/configs/$(basename "$otel")" >/dev/null
  echo "PASS: $name renders valid Kubernetes YAML and Collector config"
}

helm lint "$chart" >/dev/null
echo "PASS: Helm lint passed"

default_render="$tmp/default-render.yaml"
helm template byoc-s3 "$chart" -n byoc-s3 > "$default_render"
validate_render default "$default_render"

enabled_render="$tmp/enabled-render.yaml"
helm template byoc-s3 "$chart" -n byoc-s3 \
  --set s3.bucket=do-nyc1-staging-decoy-byoc \
  --set s3.region=nyc3 \
  --set s3.endpoint=https://nyc3.digitaloceanspaces.com \
  --set s3.forcePathStyle=true \
  --set layout.enabled=true \
  --set 'layout.workloads[0].namespace=banking-app' \
  --set 'layout.workloads[0].appLabel=banking-ai' \
  --set 'layout.workloads[1].namespace=banking-app' \
  --set 'layout.workloads[1].appLabel=banking-gateway' > "$enabled_render"
validate_render enabled-layout "$enabled_render"

for cluster in dev-decoy staging-decoy; do
  app="$repo_root/clusters/$cluster/argocd/byoc-s3.yaml"
  values="$tmp/$cluster-values.yaml"
  rendered="$tmp/$cluster-render.yaml"

  yq e '.spec.source.helm.values' "$app" > "$values"
  helm template byoc-s3 "$chart" -n byoc-s3 -f "$values" > "$rendered"
  validate_render "$cluster" "$rendered"
done
