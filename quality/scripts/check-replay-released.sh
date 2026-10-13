#!/usr/bin/env bash
set -euo pipefail
namespace=${1:?namespace required}
workload=${2:?workload required}

# A report verdict does not prove the restored rollout has finished.
kubectl --request-timeout=10s -n "$namespace" get deployments -o json |
  jq -e --arg workload "$workload" '
    .items | select(type == "array") |
    map(select(.metadata.name | startswith("speedscale-") | not)) |
    any(.[]; .metadata.name == $workload) and all(.[];
    (.spec.replicas | type == "number" and . > 0)
    and (.metadata.generation | type == "number")
    and (.status.observedGeneration | type == "number")
    and (.metadata.labels["replay.speedscale.com/env-id"] // "") == ""
    and (.metadata.labels["replay.speedscale.com/report-id"] // "") == ""
    and (.status.observedGeneration >= .metadata.generation)
    and (.status.updatedReplicas == .spec.replicas)
    and (.status.readyReplicas == .spec.replicas)
    and (.status.replicas == .spec.replicas))
  ' >/dev/null

# Include terminating pods: an old generator or responder can still use CPU.
kubectl --request-timeout=10s -n "$namespace" get pods -o json |
  jq -e '.items | type == "array" and all(.[];
    (.metadata.name | startswith("speedscale-") | not)
    and (.metadata.labels["replay.speedscale.com/env-id"] // "") == ""
    and (.metadata.labels["replay.speedscale.com/report-id"] // "") == "")' >/dev/null
