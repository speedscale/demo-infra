# staging-decoy demo runbook

## Story

Observability shows that something is wrong, but it does not give the agent a faithful reproduction harness.

1. Open Grafana: `https://apex-grafana-staging.trafficreplay.com`
2. Start on **Banking Application - Overview**.
3. Drill into **Banking Application - Errors** and isolate the failing endpoint or service.
4. Open the related code in `microsvc` without telling the agent this is a staged demo.
5. Hand the agent Speedscale traffic for the failing service so it can reproduce the production request shape, downstream calls, TLS egress, and protocol mix.
6. After a fix merges, ArgoCD redeploys the app and the TrafficReplay responders reattach through the PostSync hook.

## Demo Beats

- **Grafana tells us where to look.** The audience sees service health, request rate, p95 latency, error percentage, logs, and trace links.
- **Observability stops at symptoms.** Dashboards can identify a bad endpoint and nearby logs, but they cannot replay the exact production interaction locally.
- **Speedscale supplies the missing harness.** Captured traffic gives the agent real requests, response expectations, third-party mocks, TLS egress, and protocol-aware downstream behavior.
- **The agent fixes against reality.** The agent uses code plus traffic replay instead of guessing from logs or writing synthetic unit tests from scratch.

## Required Runtime State

- ArgoCD app `microsvc-staging-decoy` is Synced and Healthy.
- Grafana is reachable through `apex-grafana-staging.trafficreplay.com`.
- All dashboard JSON files load from the Grafana provisioning ConfigMaps.
- Speedscale operator has eBPF enabled and forwards traffic to:
  - Agent Factory OTLP intake
  - BYOC Grafana OTLP collector
  - BYOC S3 OTLP collector
- All six responder `TrafficReplay` objects are `Running`.
- Every active pod for `banking-accounts`, `banking-ai`, `banking-fraud`, `banking-notification`, `banking-transactions`, and `banking-user` has the responder init container.
- Each responder `TrafficReplay` has `sidecar.tls.out: true`.

## Verification

Run:

```sh
./quality/scripts/verify-staging-decoy.sh
```

The script checks local manifests first, then the active cluster if `kubectl` is pointed at `staging-decoy`.
