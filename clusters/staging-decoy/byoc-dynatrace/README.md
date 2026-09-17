# Dynatrace partner adapter

This adapter sends enabled staging-decoy traces, metrics, and DLP-filtered Speedscale capture logs to the dedicated Dynatrace partner tenant. The tenant endpoint and ingest token stay in the `byoc-dynatrace/byoc-dynatrace` Secret under keys `otlpEndpoint` and `dataIngestToken`.

The adapter uses Dynatrace's OTLP path and authorization format, keeps batches below the tenant payload limit, maps Speedscale workloads to microsvc services, preserves trace and span correlation, and marks HTTP 5xx and exception spans as errors.
