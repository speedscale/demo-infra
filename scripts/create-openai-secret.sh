#!/bin/bash
set -euo pipefail

# Create (or rotate) the openai-api-key secret consumed by agent-factory's
# engine (chart values: engine.authSecret). Prompts for the key without
# echoing, validates it against the OpenAI API before touching the cluster,
# and never prints it.
#
# Usage: $0 <cluster-name> [--sealed] [--restart]
#   --sealed   emit a SealedSecret to clusters/<cluster>/sealed/ (commit it)
#              instead of applying a plain Secret
#   --restart  rollout-restart the agent-factory deployments afterwards

NAMESPACE=agent-factory
SECRET_NAME=openai-api-key
SECRET_KEY=token
MODEL=gpt-5.4-mini

CLUSTER_NAME=${1:-}
SEALED=false
RESTART=false

if [ -z "$CLUSTER_NAME" ]; then
  echo "Usage: $0 <cluster-name> [--sealed] [--restart]"
  exit 1
fi
shift
for arg in "$@"; do
  case "$arg" in
    --sealed) SEALED=true ;;
    --restart) RESTART=true ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

if [ "$SEALED" = true ] && ! command -v kubeseal >/dev/null; then
  echo "kubeseal not found (brew install kubeseal)"
  exit 1
fi

read -r -s -p "OpenAI API key (input hidden): " OPENAI_KEY
echo
if [ -z "$OPENAI_KEY" ]; then
  echo "No key entered."
  exit 1
fi

# Auth check via a zero-cost metadata GET: 200 proves the key is valid AND
# the account can see $MODEL; 401/403 = bad key. A generative test call
# doesn't work here — reasoning models burn a tiny max_completion_tokens
# budget on reasoning and return 400 even with a valid key.
echo "Validating key against api.openai.com (GET /v1/models/$MODEL)..."
HTTP_CODE=$(curl -s -o /tmp/openai-key-check.json -w '%{http_code}' \
  "https://api.openai.com/v1/models/$MODEL" \
  -H "Authorization: Bearer $OPENAI_KEY")
if [ "$HTTP_CODE" != "200" ]; then
  echo "Key validation FAILED (HTTP $HTTP_CODE):"
  cat /tmp/openai-key-check.json
  rm -f /tmp/openai-key-check.json
  exit 1
fi
rm -f /tmp/openai-key-check.json
echo "Key is valid."

kubectl config use-context "$CLUSTER_NAME"

if [ "$SEALED" = true ]; then
  OUT_DIR="$REPO_ROOT/clusters/$CLUSTER_NAME/sealed"
  OUT_FILE="$OUT_DIR/$SECRET_NAME.yaml"
  mkdir -p "$OUT_DIR"
  kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
    --from-literal="$SECRET_KEY=$OPENAI_KEY" --dry-run=client -o yaml \
  | kubeseal --controller-namespace kube-system \
      --controller-name sealed-secrets-controller --format yaml \
  > "$OUT_FILE"
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
  kubectl apply -f "$OUT_FILE"
  echo "SealedSecret applied and written to $OUT_FILE — commit it."
else
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
  kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
    --from-literal="$SECRET_KEY=$OPENAI_KEY" --dry-run=client -o yaml \
  | kubectl apply -f -
  echo "Secret $NAMESPACE/$SECRET_NAME applied."
fi

if [ "$RESTART" = true ]; then
  echo "Restarting agent-factory deployments..."
  kubectl -n "$NAMESPACE" rollout restart deployment
  kubectl -n "$NAMESPACE" rollout status deployment --timeout=180s || true
fi
