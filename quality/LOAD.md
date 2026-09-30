# Daily load scenarios

The daily suite combines seven CI regression replays, seven CD replays in each cluster, and three load scenarios in each cluster. Load starts after both CD jobs finish, even if a regression job failed. Scenarios execute sequentially within each cluster so they do not compete with one another. Manual workflow dispatch honors the selected cluster.

| Scenario | Service | Traffic shape | Duration | p95 ceiling |
| --- | --- | --- | --- | --- |
| Gateway ramp | banking-gateway | 1 user for 30s; ramp to 5 over 30s within a 60s stage; ramp to 10 over 30s within a 90s stage; 2 users for 60s | 4 minutes | 1,500 ms |
| Fraud spike and recovery | banking-fraud | 2 users for 30s; ramp to 20 over 5s within a 30s stage; 2 users for 60s | 2 minutes | 500 ms |
| AI soak | banking-ai | 2 concurrent users | 5 minutes | 2,000 ms |

These are initial test budgets, not capacity claims or production SLOs. The CLI uses closed-loop virtual users; achieved RPS depends on service latency. Each profile has an eight-minute replay timeout. Snapshot download, authentication, and report generation add time to the eleven minutes of traffic per cluster.

Run the mix with `./quality/scripts/run-daily-load.sh dev-decoy`, or one profile with `./quality/scripts/run-proxymock-scenario.sh dev-decoy banking-fraud fraud-spike`. The runner uses the existing cluster-specific snapshots, auth preflight, and banking-replay services. It generates real application requests and may replay recorded writes; it does not restore the database. It does not add or change external telemetry destinations.

The load profiles retain response collection and existing regression goals, request-failure checks, 5xx checks, and the recorded 4xx baseline check. They additionally fail on zero delivered requests or a missed p95 budget. Existing assertion failures remain failures. This is bounded concurrency testing with diagnostics, not the CLI's reduced-data `--load-test` mode. Latency budgets cover the whole scenario; recovery-stage latency is not evaluated separately.

Each cluster gets a GitHub job summary with scenario status, delivered requests, achieved RPS, p95, and p99. JSON reports and command logs are uploaded even on failure. All three scenarios are attempted; a failed scenario fails the final job. Requests travel through kubectl port forwarding from the runner, so latency includes that connection and throughput can be limited by it. Use an in-cluster generator for capacity measurements.

## Speedscale Cloud load

The `Speedscale Cloud load` jobs run after the proxymock load jobs on dev and staging. They launch `speedctl infra replay` against the cluster's `banking-replay` workloads with the gateway-ramp, fraud-spike, and ai-soak profiles in `quality/cloud-load/`. Generation happens in Kubernetes and produces Speedscale Cloud reports. Outbound mocking follows the snapshot and the base test configuration; snapshots without outbound traffic have no dependencies to mock.

The CLI applies `--test-override` using protobuf merge: profile stages and rules append to the existing `banking-daily-replay` configuration. Its single-pass baseline therefore precedes the timed load stages, and its original assertions remain enforced. The added goals require at least one attempted transaction, zero failed transactions, and p95 below 1,500/500/2,000 ms for gateway/fraud/AI respectively. A Cloud report with `Missed Goals`, `Error`, or cancellation fails the load job. Existing CD regression status handling is unchanged.

Use workflow dispatch with `cloud_load_only=true` and `cluster=dev-decoy`, `staging-decoy`, or `all` to run Cloud scenarios without the other jobs. From a configured shell, run `./quality/scripts/run-cloud-load.sh dev-decoy`. Reports, base configuration, report IDs, and logs are saved under `quality/cloud-load-reports/` and uploaded as `cloud-load-<cluster>` artifacts. The job summary links directly to each Cloud report. Profiles have a 20-minute provisioning/run/analysis wait budget each. If the prior replay has no confirmed terminal state, later profiles are skipped to avoid overlapping an active replay.

The Cloud path does not fix generator cancellation accounting or application errors exposed by load. These remain visible as failed goals. Live validation on September 30 passed fraud-spike in both clusters. Gateway-ramp and ai-soak failed provisioning in both clusters because the operator could not create a responder Istio peer-auth policy (`resource already exists`), before their load stages ran. See [the validation run](https://github.com/speedscale/demo-infra/actions/runs/36737446861). Those profiles need successful reruns after the operator issue is fixed before treating the suite as established coverage.
