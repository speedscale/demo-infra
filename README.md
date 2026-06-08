# demo-infra

Cluster management, ArgoCD GitOps, and quality jobs for Speedscale demo clusters.

## Clusters

| Cluster | Provider | Apps |
|---------|----------|------|
| dev-decoy | DigitalOcean | outerspace-go, microsvc, speedscale-operator |
| staging-decoy | DigitalOcean | outerspace-go, microsvc, speedscale-operator |

## Quick Start

### Bootstrap a cluster (one-time setup)

```bash
./scripts/bootstrap-cluster.sh dev-decoy
```

This installs ArgoCD and applies all Application manifests for the cluster.

### Run quality replays manually

```bash
./quality/scripts/run-replay.sh dev-decoy
```

## How It Works

- **ArgoCD** manages app deployments (outerspace-go, microsvc, speedscale-operator) via GitOps
- **GitHub Actions** applies ArgoCD manifests on push to main and runs daily quality replays
- **Quality jobs** use `speedctl infra replay` to validate demo apps against known snapshots

## Directory Layout

```
clusters/<name>/argocd/    ArgoCD Application manifests per cluster
clusters/<name>/cluster.yaml   Cluster metadata
quality/speedctl-replay/   Replay configs (snapshot ID, test config, workload)
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
