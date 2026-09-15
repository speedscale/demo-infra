# From Datadog to tests and mocks

One banking request can connect four useful artifacts: an APM trace, a searchable capture log, a replay test, and dependency mocks. The application SDK emits the APM spans. Speedscale captures the actual requests and responses. Shared trace IDs connect the two.

The GKE partner demo stores payloads in GCS and puts their links in Datadog. Ordinary APM spans and summary logs do not contain enough request/response data to reconstruct faithful tests or mocks. This workflow resolves the Speedscale capture links and uses the full RRPairs as replay inputs and expected responses.

## Retrieve a captured trace

Prerequisites: Python 3, gcloud with access to the demo bucket, and the partner Datadog API/app keys. The app key needs logs_read_data and apm_read. Set DATADOG_PARTNER_API_KEY, DATADOG_PARTNER_APP_KEY, and DATADOG_PARTNER_SITE explicitly; the tool never falls back to production credentials.

Select an api-gateway trace containing a successful account request in the partner Datadog account. Copy its 32-character hexadecimal trace ID. The log and APM span must both be searchable within the last 24 hours.

```bash
python3 gather.py --trace-id <trace-id> --out runs/<trace-id>
```

The tool verifies the same-service APM trace and capture log, checks the bucket/service/trace in the GCS link, downloads the archived records, and verifies each record's trace ID. Incoming HTTP records become `tests/`; outgoing HTTP records become `mocks/`. `provenance.json` records the Datadog log IDs, APM spans, source GCS objects, and retrieval time. A trace without both incoming and outgoing HTTP records is rejected.

Recordings stay in the ignored `runs/` directory with restricted file permissions. They retain captured payloads and authentication headers, so keep them local and review/redact them before sharing. No recording is committed by this demo.

## Demonstration sequence

1. Show the selected banking request in Datadog APM and the matching Speedscale capture log.
2. Run the retrieval command. Inspect the actual request, expected response, and outgoing dependency recording.
3. Run the unchanged microsvc API gateway locally with Redis. Serve the captured account-service response through proxymock with passthrough disabled, and route the local gateway's account-service URL to that mock. Refresh the captured JWT for the isolated demo key if it has expired; preserve the recorded response assertions.
4. Replay the incoming request into that local gateway. Require `requests.result-match-pct == 100` as the automated test gate.
5. Inject a dependency HTTP 503 with proxymock's chaos option and replay the same test. The changed response should fail that gate. Remove the fault and rerun to show recovery.

Steps 3–5 require live integration verification before presenting this as a completed end-to-end demo. The retrieval conversion has fixture-based tests; Google authentication currently blocks fetching the selected real recording for the gateway replay.

The captured response provides a regression baseline, not an independent claim that the original behavior was correct. Add explicit business assertions when the desired behavior differs from what was recorded. Stateful dependencies and calls without propagated trace context can require a broader capture window; a trace alone does not guarantee a complete isolated test environment.

## Validate the retrieval tool

```bash
python3 -m unittest -v test_gather.py
```

The tests cover source-link validation, mixed-trace rejection, payload/header preservation, and conversion into separate test/mock files with provenance. They use synthetic fixtures and make no external requests.
