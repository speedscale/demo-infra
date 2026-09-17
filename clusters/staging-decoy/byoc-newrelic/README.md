# New Relic partner adapter

This adapter sends the enabled staging-decoy traces, metrics, and DLP-filtered Speedscale capture logs to New Relic through its OTLP/HTTP ingest API. New Relic-specific authentication stays in the `byoc-newrelic/byoc-newrelic` Secret under key `licenseKey`.

The adapter maps Speedscale workload names to microsvc service names, preserves W3C trace and span correlation, adds staging partner attributes, and marks HTTP 5xx and exception spans as errors for New Relic APM and Errors Inbox.
