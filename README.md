# demo-infra

Cluster management, ArgoCD GitOps, and quality jobs for Speedscale demo clusters.

## Clusters

| Cluster | Provider | Apps |
|---------|----------|------|
| dev-decoy | DigitalOcean | microsvc, microsvc-replay, speedscale-operator |
| staging-decoy | DigitalOcean | microsvc, microsvc-replay, speedscale-operator |

## Quick Start

### Bootstrap a cluster (one-time setup)

```bash
./scripts/bootstrap-cluster.sh dev-decoy
```

This installs ArgoCD and applies the root Application for the cluster.

### Run quality replays manually

```bash
./quality/scripts/run-replay.sh dev-decoy
./quality/scripts/run-proxymock-scenario.sh dev-decoy banking-gateway
```

## How It Works

- **ArgoCD** manages the cluster's Application manifests through the `demo-infra-apps` root app, then manages app deployments via GitOps
- **GitHub Actions** applies the root ArgoCD app on push to main and runs daily quality replays
- **Quality jobs** run one Speedscale replay per workload against `banking-replay` so report traffic stays isolated from the live `banking-app` demo
- **Proxymock jobs** pull the same snapshot IDs from Speedscale Cloud and replay them from GitHub Actions through a port-forwarded service

## Directory Layout

```
clusters/<name>/argocd/    Root and child ArgoCD Application manifests per cluster
clusters/<name>/cluster.yaml   Cluster metadata
quality/speedctl-replay/   Replay configs (snapshot ID, test config, workload, proxymock target)
quality/test-configs/      Speedscale test configs synced by CI before replay
quality/dlp/               Speedscale DLP configs used by demo clusters
quality/scripts/           Replay runner and cluster connection scripts
scripts/                   One-time bootstrap and ArgoCD install
.github/workflows/         CI/CD and scheduled quality jobs
```

## GitHub Actions Secrets Required

| Secret | Purpose |
|--------|---------|
| `DIGITALOCEAN_ACCESS_TOKEN` | doctl auth for kubeconfig |
| `SPEEDCTL_DEV_CONFIG` | speedctl config.yaml for dev-decoy |
| `SPEEDCTL_STAGING_CONFIG` | speedctl config.yaml for staging-decoy |
| `GCR_SERVICE_ACCOUNT_KEY` | GCR image pull (if needed) |
