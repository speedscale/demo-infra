# Partner telemetry routing

The staging microsvc banking app sends application telemetry through `partner-trace-router` in `observability`. The router preserves the existing Jaeger, Loki, and Prometheus flows through the shared collector, then independently fans out selected signals to vendor-specific Datadog, Dynatrace, and New Relic adapters. Speedscale sends DLP-filtered capture logs to the router's separate capture receiver.

`destinations.json` is the vendor option array. Each entry declares the adapter service, supported signals, deployment, and credential location. `enabled` defines the initial state. Add another vendor by adding its adapter and one registry entry; do not add vendor credentials or account identifiers to git.

Use `manage.py` to change one live option without disturbing the others:

```bash
python3 manage.py status dynatrace --context do-nyc1-staging-decoy
python3 manage.py off dynatrace --context do-nyc1-staging-decoy
python3 manage.py on newrelic --context do-nyc1-staging-decoy --partner-account-verified
```

Enabling checks that the vendor deployment is ready and its credential is non-empty. The router ConfigMap is the runtime switch state, and Argo ignores only its generated `otel.yaml` field so self-healing does not undo deliberate toggles. `render.py` produces the committed bootstrap configuration from the option array.

Each vendor adapter owns its filtering, batching, authentication, naming, correlation, and error mapping. The routing array only decides which signals reach each adapter.
