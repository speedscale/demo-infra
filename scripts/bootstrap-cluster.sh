#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CLUSTER_NAME=${1:-}

if [ -z "$CLUSTER_NAME" ]; then
  echo "Usage: $0 <cluster-name>"
  echo ""
  echo "Available clusters:"
  ls "$REPO_ROOT/clusters/"
  exit 1
fi

CLUSTER_DIR="$REPO_ROOT/clusters/$CLUSTER_NAME"
if [ ! -d "$CLUSTER_DIR" ]; then
  echo "Error: cluster '$CLUSTER_NAME' not found in clusters/"
  exit 1
fi

ARGOCD_DIR="$CLUSTER_DIR/argocd"
if [ ! -d "$ARGOCD_DIR" ]; then
  echo "Error: no argocd/ directory in $CLUSTER_DIR"
  exit 1
fi

echo "Bootstrapping cluster: $CLUSTER_NAME"

# Connect to the cluster
"$REPO_ROOT/quality/scripts/connect-cluster.sh" "$CLUSTER_NAME"

# Install ArgoCD
"$SCRIPT_DIR/install-argocd.sh" "$CLUSTER_NAME"

# Apply the root ArgoCD Application. It owns and self-heals the child
# Application manifests in the same directory.
echo ""
echo "Applying root ArgoCD Application manifest..."
kubectl apply -f "$ARGOCD_DIR/demo-infra-apps.yaml"

# Apply SealedSecrets (must come after sealed-secrets controller is synced)
SEALED_DIR="$CLUSTER_DIR/sealed-secrets"
if [ -d "$SEALED_DIR" ]; then
  echo ""
  echo "Applying SealedSecret manifests..."
  kubectl apply -f "$SEALED_DIR/"
fi

# Apply Gateway API resources (Gateway, HTTPRoutes) if present
GATEWAY_DIR="$CLUSTER_DIR/gateway"
if [ -d "$GATEWAY_DIR" ]; then
  echo ""
  echo "Applying Gateway API manifests..."
  kubectl apply -f "$GATEWAY_DIR/"
fi

# Apply Istio config (ServiceEntry, Telemetry) if present
ISTIO_CONFIG_DIR="$CLUSTER_DIR/istio-config"
if [ -d "$ISTIO_CONFIG_DIR" ]; then
  echo ""
  echo "Applying Istio config manifests..."
  kubectl apply -f "$ISTIO_CONFIG_DIR/"
fi

echo ""
echo "Bootstrap complete for $CLUSTER_NAME"
echo "ArgoCD will now sync the following apps:"
kubectl get applications -n argocd -o custom-columns=NAME:.metadata.name,STATUS:.status.sync.status,HEALTH:.status.health.status
