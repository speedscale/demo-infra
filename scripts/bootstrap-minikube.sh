#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CLUSTER_NAME=${1:-}

if [ -z "$CLUSTER_NAME" ]; then
  echo "Usage: $0 <cluster-name>"
  echo ""
  echo "Available minikube clusters:"
  ls "$REPO_ROOT/clusters/" | grep miniken
  echo ""
  echo "This will create a minikube profile and bootstrap ArgoCD + all apps."
  echo "Requires: minikube, kubectl, helm"
  exit 1
fi

CLUSTER_DIR="$REPO_ROOT/clusters/$CLUSTER_NAME"
if [ ! -d "$CLUSTER_DIR" ]; then
  echo "Error: cluster '$CLUSTER_NAME' not found in clusters/"
  exit 1
fi

PROFILE="$CLUSTER_NAME"

echo "=== Creating minikube cluster: $PROFILE ==="
if minikube status -p "$PROFILE" &>/dev/null; then
  echo "Minikube profile '$PROFILE' already exists, using it"
else
  minikube start -p "$PROFILE" \
    --cpus=4 \
    --memory=6144 \
    --driver=docker \
    --kubernetes-version=stable
fi

kubectl config use-context "$PROFILE"

echo ""
echo "=== Installing Gateway API CRDs ==="
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/latest/download/standard-install.yaml

echo ""
echo "=== Installing ArgoCD ==="
"$SCRIPT_DIR/install-argocd.sh" "$CLUSTER_NAME"

echo ""
echo "=== Applying ArgoCD Application manifests ==="
kubectl apply -f "$CLUSTER_DIR/argocd/"

echo ""
echo "=== Waiting for ArgoCD to create namespaces ==="
echo "Waiting for istio-system namespace..."
until kubectl get ns istio-system &>/dev/null; do sleep 5; done
echo "Waiting for banking-app namespace..."
until kubectl get ns banking-app &>/dev/null; do sleep 5; done

echo ""
echo "=== Applying Gateway API resources ==="
if [ -d "$CLUSTER_DIR/gateway" ]; then
  kubectl apply -f "$CLUSTER_DIR/gateway/"
fi

echo ""
echo "=== Applying Istio config ==="
if [ -d "$CLUSTER_DIR/istio-config" ]; then
  kubectl apply -f "$CLUSTER_DIR/istio-config/"
fi

echo ""
echo "=== Bootstrap complete for $CLUSTER_NAME ==="
echo ""
echo "To access services, run 'minikube tunnel -p $PROFILE' in another terminal,"
echo "then add to /etc/hosts (pointing at 127.0.0.1):"

if [[ "$CLUSTER_NAME" == *"dev"* ]]; then
  echo "  127.0.0.1 banking.dev.miniken.local grafana.dev.miniken.local jaeger.dev.miniken.local"
elif [[ "$CLUSTER_NAME" == *"staging"* ]]; then
  echo "  127.0.0.1 banking.staging.miniken.local grafana.staging.miniken.local jaeger.staging.miniken.local"
fi

echo ""
echo "Don't forget to create the speedscale-apikey secret:"
echo "  kubectl create secret generic speedscale-apikey -n speedscale --from-literal=apiKey=<your-key> --from-literal=apiUrl=<app-url>"
echo ""
echo "ArgoCD apps:"
kubectl get applications -n argocd -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status 2>/dev/null || echo "(ArgoCD still starting...)"
