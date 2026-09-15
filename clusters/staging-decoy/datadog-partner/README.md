# Optional staging Datadog partner demo

Use the continuously running microsvc banking app on `do-nyc1-staging-decoy`. A dedicated collector archives traced HTTP RRPairs in GCS and sends only capture links plus application APM spans to the Datadog partner account. Existing Jaeger, Loki, Prometheus, and unrelated collector pipelines remain attached.

Application code belongs in microsvc. Customer retrieval/replay scripts and templates belong in speedscale-byoc. This directory owns deployment and lifecycle controls.

## Prerequisites

Python with PyYAML 6.0.3, kubectl access to staging-decoy, and explicit `DATADOG_PARTNER_API_KEY`, `DATADOG_PARTNER_APP_KEY`, and `DATADOG_PARTNER_SITE`. Verify the destination organization before enabling export. Default production credentials are never used; the application key stays local.

Provision a dedicated bucket-scoped GCS writer identity. Staging-decoy runs on DigitalOcean and cannot inherit GKE Workload Identity. The existing Kubernetes Secret `datadog-partner-gcs` in `observability` must contain `credentials.json` for that workload identity, plus any adjacent credential-source files it requires. Personal authorized-user ADC is rejected. External-account credentials need their own renewable subject-token source; merely copying a laptop credential file is insufficient. This manager does not create Google identities, IAM grants, or credentials. The writer needs the native GCS exporter's bucket metadata access and object-create permission. Use a separate reader for retrieval.

## Toggle

```bash
python3 manage.py on --context do-nyc1-staging-decoy \
  --partner-account-verified --gcs-project <project> --gcs-bucket <bucket>
python3 manage.py status --context do-nyc1-staging-decoy
python3 verify.py
python3 manage.py off --context do-nyc1-staging-decoy
```

Use `--gcs-credentials-secret` for a differently named pre-provisioned writer secret and `--gcs-region` if needed. `install` and `remove` remain aliases for `on` and `off`. Status shows attachment and deployment state; readiness alone does not prove delivery, so run the Datadog verifier and retrieve a capture before presenting.

On attaches an OTLP destination after the dedicated collector is ready. Off first detaches it, then removes only the partner collector and its ingestion-key Secret and storage ConfigMap. The GCS writer Secret, stored objects, banking app, simulator, and existing observability destinations are retained. Both operations restart the shared collector, causing a brief ingestion interruption. Resource-version checks reject concurrent ConfigMap changes. Reapplying the base observability manifest removes this optional live attachment; rerun on after a base redeployment if the demo is needed. This is an operator-invoked deployment toggle, not a persistent Argo values flag.

## Data and validation

Only traced banking-app HTTP records with safe service names are archived. Authentication routes are excluded. GCS retains full captured payloads and headers; Datadog receives a GCS link, native trace context, service name, and `deployment.cluster:staging-decoy`. Incoming captures correlate at trace level; outgoing captures retain the client span ID. Raw payloads and credentials must not be copied into git or shown in the shared terminal.

Run `python3 test_collector.py` to exercise the pinned collector locally and `python3 -m unittest -v test_manage.py` for fanout preservation. Before calling the migration complete, enable on staging, verify a new staging-tagged APM/log pair, retrieve its GCS payload, run the BYOC replay demo, disable and verify the export stops while Jaeger/Loki remain attached, then re-enable and verify a new trace. Staging acceptance completed on 2026-09-15: correlated gateway spans and capture links, GCS retrieval, and local replay with HTTP 200 / injected 503 / recovered 200. Off removed the partner deployment and fanout while retaining the simulator and existing pipelines; export was then re-enabled.

The temporary GKE validation cluster was deleted after validation. Its kubeconfig and Workload Identity are not staging deployment dependencies. Never delete the archive bucket as part of turning this demo off.

## Current staging settings

Project: `speedscale-demos`. Bucket: `speedscale-demos-proxymock-gcs-validation`, region `us-central1`. The retained Secret `observability/datadog-partner-gcs` holds a dedicated service-account key for `staging-datadog-writer@speedscale-demos.iam.gserviceaccount.com`; that identity has bucket-scoped object creation and metadata/list access. Turning the demo off retains this credential for the next run. Datadog ingestion settings come from the explicit partner environment variables supplied to `on`, which creates the ingestion Secret. The application key is only needed by local query scripts.

The Java SDK currently omits `service.namespace`. Trace filtering accepts only the enumerated banking service names, with either an absent namespace or `banking-app`; other namespaces and service names are rejected. Continuous traffic uses a 1 GiB collector limit, a 700 MiB Go memory target, and a 900 MiB limiter with 180 MiB spike allowance.

## APM service pages

The Datadog connector derives APM statistics from the filtered banking traces and feeds a dedicated metrics pipeline. Exporting spans alone supports trace searches but does not populate APM service charts.

The collector marks server spans with HTTP 5xx responses as errors. It also converts Java `exception` span events into Datadog errors and preserves `error.type`, `error.message`, and `error.stack`. Ordinary HTTP 4xx responses remain non-error spans unless the application records an exception.

Use `env:partner-demo` and the current service name in Trace Explorer. Clear stale operation filters such as `http.client.request` when switching from older recordings; select a current operation from the service page. The service charts start when the connector is enabled and do not backfill earlier traffic.
