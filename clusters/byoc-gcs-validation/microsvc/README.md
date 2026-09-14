# GKE partner banking demo

This overlay migrates the existing `byoc-gcs-validation` GKE demo in `speedscale-demos` to microsvc. Banking runs in `banking-app`; the existing capture agent, forwarder, GCS collector, partner Datadog secret, and direction-split pipeline stay in `byoc-gcs-validation`.

Application traces go directly to the existing collector. Captured payloads remain in the private GCS bucket, with trace-linked GCS URLs sent to Datadog. This does not enable the separate direct-payload Datadog exporter in `clusters/staging-decoy/datadog-partner`. Service names match the application SDKs and use `env:partner-demo`.

Microsvc is consumed as a remote Kustomize base pinned to commit `4fec0db33158d5a6d8f69d68b7ae2a6f1d2b2e6b` (v1.4.128). The app repository owns the base application manifests; demo-infra owns cluster overlays, partner credentials, collector routing, and the migration lifecycle. Updating the application means reviewing and changing that pinned revision.

## Deploy and verify

Use Python 3 with PyYAML, kubectl, and a kubeconfig for this exact GKE cluster. Set `DATADOG_PARTNER_API_KEY` from the verified partner secret source. The migration refuses to proceed unless it matches the existing collector secret and that deployment references the partner secret.

```bash
python3 migrate.py --kubeconfig /path/to/demo/kubeconfig
kubectl --kubeconfig /path/to/demo/kubeconfig -n banking-app get deployments,pods
kubectl --kubeconfig /path/to/demo/kubeconfig -n banking-app wait --for=condition=complete job/seed-user-pool --timeout=600s
kubectl --kubeconfig /path/to/demo/kubeconfig -n banking-app port-forward service/banking-frontend 13081:80
```

Open `http://localhost:13081` and sign in with the seeded customer shown on the login screen. Verify accounts, deposits, and transaction history. Then start the low-volume simulator:

```bash
kubectl --kubeconfig /path/to/demo/kubeconfig -n banking-app scale deployment/banking-sim --replicas=1
```

In the partner Datadog organization, search APM and Logs for `env:partner-demo service:transactions-service`. Open a captured log's GCS link and confirm the stored RRPair's trace ID matches the APM trace. Incoming capture context identifies the caller, so incoming logs retain the trace ID without falsely assigning that caller span to the receiving service. Outbound captures retain exact client-span context.

Only after verification, pause the old fixture app and generator:

```bash
kubectl --kubeconfig /path/to/demo/kubeconfig -n byoc-gcs-validation scale deployment/byoc-demo-traffic deployment/byoc-demo-checkout deployment/byoc-demo-inventory --replicas=0
```

Do not remove the shared forwarder, collector, capture DaemonSet, or direction-split service. Reapplying this overlay pauses banking-sim for validation again. No HPA is installed, so the simulator stays at the explicitly selected replica count.

## Demo boundaries

The app uses its seeded demo credentials, a persistent Postgres volume, Kafka, Redis, MongoDB, and its real backend services. The simulator has one user, no traffic bursts, no statement export, and no AI chat. Two small GKE nodes provide room for the banking stack alongside capture; check capacity before installing elsewhere.

External provider hosts resolve to loopback in these demo pods, preventing calls with the repository's dummy keys from reaching those providers. Compliance screening uses a local deterministic low-risk response so the transfer path can run. Payment, identity, notification, fraud-provider, and AI-provider responses are not simulated by the staging cluster's operator-managed responders here; provider errors can appear in traces. The banking services and their internal database/RPC calls remain real.

## Rollback

Pause `banking-sim`, restore the three old fixture deployments to one replica, and verify their partner logs. Keep the banking namespace and Postgres PVC until its data is no longer needed. Collector configuration is updated by resource-version-checked replacement; unrelated exporters and pipelines are preserved.
