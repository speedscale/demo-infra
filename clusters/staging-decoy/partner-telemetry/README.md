# Dynatrace and New Relic partner telemetry

The staging microsvc banking app can send application traces and Speedscale capture logs to dedicated partner accounts. Dynatrace uses the existing `byoc-dynatrace` collector and receives traces in addition to its existing DLP-filtered capture logs. New Relic uses the `byoc-newrelic` collector for both signals.

Each partner credential stays in its destination namespace. Dynatrace reads `byoc-dynatrace/byoc-dynatrace` key `dataIngestToken`. New Relic reads `byoc-newrelic/byoc-newrelic` key `license-key`. Never use production monitoring credentials or add credentials to git.

The configured Dynatrace destination is `uim8926h.sprint.dynatracelabs.com`. The New Relic destination is US OTLP account `3291855`; its UI currently identifies the free organization as `Organization bdd49889`. Confirm both are approved partner sandboxes before passing `--partner-account-verified`.

After the destination collector is ready, attach or detach its shared-collector fanout:

```bash
python3 manage.py on dynatrace --context do-nyc1-staging-decoy --partner-account-verified
python3 manage.py on newrelic --context do-nyc1-staging-decoy --partner-account-verified
python3 manage.py status dynatrace --context do-nyc1-staging-decoy
python3 manage.py off dynatrace --context do-nyc1-staging-decoy
```

The toggles preserve all other exporters and restart only the shared collector. Reapplying the base observability manifest removes optional fanout, so rerun `on` when needed. The banking service allowlist accepts the Java SDK's absent namespace but rejects other named namespaces and unrelated services. Authentication routes are excluded from the New Relic capture-log pipeline.
