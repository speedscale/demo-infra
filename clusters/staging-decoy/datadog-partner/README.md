# Microsvc + Datadog partner demo

For the existing GKE demo cluster, use the [GKE partner overlay](../../byoc-gcs-validation/microsvc/README.md), which preserves GCS payload storage and Datadog links. The installer below is a separate opt-in path for staging-decoy.

Use the existing Apex Banking application on `do-nyc1-staging-decoy`. Its simulator generates banking sessions, and its services already emit OpenTelemetry traces. Speedscale captures HTTP requests and responses. An opt-in collector sends both to the dedicated Datadog partner account under `env:partner-demo`.

The service names stay `frontend`, `api-gateway`, `user-service`, `accounts-service`, `transactions-service`, `fraud-service`, `notification-service`, and `ai-service`. The simulator uses `simulation-client`. Kubernetes workload names remain available on capture logs as `speedscale.workload`.

## Install

Prerequisites: Python 3 with `PyYAML==6.0.3`, Docker for local validation, kubectl access to staging-decoy, and confirmed partner credentials. Never source default production Datadog credentials for this demo.

Set `DATADOG_PARTNER_API_KEY`, `DATADOG_PARTNER_APP_KEY`, and `DATADOG_PARTNER_SITE` through your secret manager or shell environment. The API key controls ingestion. The app key is used only for local queries and needs `logs_read_data` and `apm_read`; it is never uploaded to Kubernetes. API validation checks key validity, not organization identity, so verify the organization in Datadog before installing.

```bash
python3 test_collector.py
python3 manage.py install --context do-nyc1-staging-decoy --partner-account-verified
python3 verify.py
```

Installation adds a dedicated collector and an OTLP destination to the existing collector's traces and logs pipelines. Jaeger, Loki, traffic metrics, and unrelated configuration are preserved. The shared collector is restarted to load the added destination. Its ConfigMap update uses Kubernetes resource-version checking to reject concurrent edits.

This option sends HTTP capture bodies into the partner account. Authorization, cookie, proxy-authorization, and API-key headers are removed, and authentication routes are excluded. This is scoped to the seeded staging banking app; it is not general-purpose payload redaction. Approve this data path before enabling it. Full bodies can contain sensitive data in other applications.

## Five-minute walkthrough

1. Open the banking UI with the command below. Sign in with the seeded demo customer shown on the login screen. Show the customer's accounts and the transactions screen; the existing simulator supplies continuous traffic.
2. In the **partner organization**, open Datadog APM and search `env:partner-demo service:transactions-service`. Open a recent banking request and show its upstream and downstream calls.
3. Open Logs with `env:partner-demo service:transactions-service @namespace:banking-app`. Select a request with `otel.trace_id`, inspect the captured HTTP request and response, and open its associated trace. Run `python3 verify.py` to confirm a same-service match through both APIs.
4. Explain the capture-to-test handoff: the existing [Replay Lab](https://github.com/speedscale/microsvc/tree/4fec0db33158d5a6d8f69d68b7ae2a6f1d2b2e6b/replay-lab-demo) reproduces a captured deposit failure locally and verifies the fix. That replay uses its own committed or bucket-exported recordings; this collector does not yet download Datadog logs into a replay folder.
5. Finish on the request payload and trace: APM shows where time and errors occur; the captured traffic supplies the request and dependency responses needed to reproduce the behavior.

```bash
kubectl --context do-nyc1-staging-decoy -n banking-app port-forward service/banking-frontend 13080:80
# Open http://localhost:13080
```

Useful views on the US1 site, after selecting the partner organization:

- [APM](https://app.datadoghq.com/apm/traces?query=env%3Apartner-demo%20service%3Atransactions-service)
- [Captured requests](https://app.datadoghq.com/logs?query=env%3Apartner-demo%20service%3Atransactions-service%20%40namespace%3Abanking-app)

## Correlation details

The currently deployed capture exporter predates native OTLP trace context. The collector reads W3C `traceparent` from captured HTTP headers into OTLP trace fields. An outbound capture can identify the emitting client span. An incoming header identifies the caller's span, so incoming captures are linked at trace level rather than falsely attributed to the receiving service's server span. Requests without propagated context remain searchable payload logs.

Kubernetes capture names such as `banking-transactions` are mapped to their existing SDK service names such as `transactions-service`. The original workload name remains in the body and `speedscale.workload`. Datadog correlation uses native OTLP trace context and the resource service name ([Datadog documentation](https://docs.datadoghq.com/opentelemetry/correlate/logs_and_traces/)).

This is the direct-to-Datadog payload path. The BYOC PR 44 reference stores full payloads in GCS and sends links to Datadog; that storage mode is separate and is not deployed by this installer.

## Remove the optional export

```bash
python3 manage.py remove --context do-nyc1-staging-decoy
```

Removal detaches only this OTLP destination, restarts the shared collector, and removes the dedicated collector and its secret. It does not stop the banking app, simulator, or existing observability stack. Reapplying the base observability manifest also removes the optional fanout; reinstall this demo afterward if needed.
