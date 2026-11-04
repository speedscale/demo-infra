#!/usr/bin/env bash
set -euo pipefail
namespace=${1:?namespace required}

kubectl --request-timeout=10s -n "$namespace" get deployments,pods -o json |
  jq '{
    deployments: [.items[] | select(.kind == "Deployment") | {
      name: .metadata.name,
      generation: .metadata.generation,
      observedGeneration: .status.observedGeneration,
      desired: .spec.replicas,
      replicas: .status.replicas,
      updated: .status.updatedReplicas,
      ready: .status.readyReplicas,
      replay: .metadata.labels["replay.speedscale.com/env-id"],
      report: .metadata.labels["replay.speedscale.com/report-id"],
      conditions: [.status.conditions[]? | {type, status, reason}]
    }],
    replayPods: [.items[] | select(.kind == "Pod") |
      select((.metadata.name | startswith("speedscale-"))
        or (.metadata.labels["replay.speedscale.com/env-id"] // "") != ""
        or (.metadata.labels["replay.speedscale.com/report-id"] // "") != "") | {
      name: .metadata.name,
      node: .spec.nodeName,
      deleting: .metadata.deletionTimestamp,
      phase: .status.phase,
      containers: [(.status.initContainerStatuses[]?, .status.containerStatuses[]?) |
        {name, ready, restartCount, waiting: .state.waiting.reason,
         terminated: .state.terminated.reason}]
    }]
  }'
