# Dynatrace partner telemetry

The staging microsvc banking app can send application traces and Speedscale capture logs to the dedicated Dynatrace partner account. The existing `byoc-dynatrace` collector receives traces in addition to its existing DLP-filtered capture logs.

The credential stays in the destination namespace. Dynatrace reads `byoc-dynatrace/byoc-dynatrace` key `dataIngestToken`. Never use production monitoring credentials or add credentials to git.

The configured destination is `uim8926h.sprint.dynatracelabs.com`. Confirm it is the approved partner sandbox before passing `--partner-account-verified`.

After the destination collector is ready, attach or detach its shared-collector fanout:

```bash
python3 manage.py on dynatrace --context do-nyc1-staging-decoy --partner-account-verified
python3 manage.py status dynatrace --context do-nyc1-staging-decoy
python3 manage.py off dynatrace --context do-nyc1-staging-decoy
```

The toggle preserves all other exporters and restarts the Dynatrace and shared collectors so current ConfigMaps are loaded. Reapplying the base observability manifest removes optional fanout, so rerun `on` when needed. The banking service allowlist accepts the Java SDK's absent namespace but rejects other named namespaces and unrelated services.
