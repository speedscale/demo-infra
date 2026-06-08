#!/bin/bash
set -euo pipefail

CLUSTER_NAME=${1:-}

if [ -z "$CLUSTER_NAME" ]; then
  echo "Usage: $0 <cluster-name>"
  exit 1
fi

MAX_RETRIES=10
RETRY_COUNT=0
SUCCESS=false

echo "Saving kubeconfig for cluster: $CLUSTER_NAME"

while [ $RETRY_COUNT -lt $MAX_RETRIES ] && [ "$SUCCESS" = false ]; do
  echo "Attempt $((RETRY_COUNT + 1)) of $MAX_RETRIES"

  if doctl kubernetes cluster kubeconfig save "$CLUSTER_NAME"; then
    echo "Successfully saved kubeconfig for $CLUSTER_NAME"
    SUCCESS=true
  else
    echo "Failed to save kubeconfig, retrying in 5 seconds..."
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
      sleep 5
    fi
  fi
done

if [ "$SUCCESS" = false ]; then
  echo "Error: failed to save kubeconfig after $MAX_RETRIES attempts"
  exit 1
fi
