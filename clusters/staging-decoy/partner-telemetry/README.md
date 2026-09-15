# Dynatrace partner telemetry

The staging microsvc banking app sends application telemetry through `partner-trace-router` in `observability`. The router preserves the existing Jaeger, Loki, and Prometheus flows through the shared collector, while sending traces to the dedicated Datadog and Dynatrace partner collectors. Dynatrace continues receiving its existing DLP-filtered Speedscale capture logs.

The credential stays in the destination namespace. Dynatrace reads `byoc-dynatrace/byoc-dynatrace` key `dataIngestToken`. Never use production monitoring credentials or add credentials to git.

The configured destination is `uim8926h.sprint.dynatracelabs.com`. Confirm it is the approved partner sandbox before passing `--partner-account-verified`.

The router and application endpoints are GitOps-owned by demo-infra. To turn partner trace export off, remove the router application and the endpoint patches from `argocd/microsvc.yaml`; Argo then returns application telemetry directly to the shared collector.

The banking service allowlist accepts the Java SDK's absent namespace but rejects other named namespaces and unrelated services.
