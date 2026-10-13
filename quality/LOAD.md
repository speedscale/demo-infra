# Daily load scenarios

The daily suite combines seven CI regression replays, seven CD replays in each cluster, and three load scenarios in each cluster. Dev and staging have independent test chains after mock reset. Dev CD waits for the seven CI replays, which use dev services; staging CD can run alongside dev CI. Each cluster runs CD, Cloud load, then proxymock load. Cloud report analysis overlaps later traffic only after workload restoration is confirmed. Manual workflow dispatch honors the selected cluster.

| Scenario | Service | Traffic shape | Duration | p95 ceiling |
| --- | --- | --- | --- | --- |
| Gateway ramp | banking-gateway | 1 user for 30s; ramp to 5 over 30s within a 60s stage; ramp to 10 over 30s within a 90s stage; 2 users for 60s | 4 minutes | 1,500 ms |
| Fraud spike and recovery | banking-fraud | 2 users for 30s; ramp to 20 over 5s within a 30s stage; 2 users for 60s | 2 minutes | 500 ms |
| AI soak | banking-ai | 2 concurrent users | 5 minutes | 2,000 ms |

These are initial test budgets, not capacity claims or production SLOs. The CLI uses closed-loop virtual users; achieved RPS depends on service latency. Each profile has an eight-minute replay timeout. Snapshot download, authentication, and report generation add time to the eleven minutes of traffic per cluster.

Run the mix with `./quality/scripts/run-daily-load.sh dev-decoy`, or one profile with `./quality/scripts/run-proxymock-scenario.sh dev-decoy banking-fraud fraud-spike`. The runner uses the existing cluster-specific snapshots, auth preflight, and banking-replay services. It generates real application requests and may replay recorded writes; it does not restore the database. It does not add or change external telemetry destinations.

The load profiles retain response collection and existing regression goals, request-failure checks, 5xx checks, and the recorded 4xx baseline check. They additionally fail on zero delivered requests or a missed p95 budget. Existing assertion failures remain failures. This is bounded concurrency testing with diagnostics, not the CLI's reduced-data `--load-test` mode. Latency budgets cover the whole scenario; recovery-stage latency is not evaluated separately.

The proxymock AI soak selects `quality/test-configs/proxymock-ai-load.json`, which retains the regression assertions and gives each HTTP request a 40-second deadline. The application waits for five provider calls with 30-second provider timeouts, so proxymock's implicit 10-second deadline can abort a valid slow response. The native Cloud AI override uses the same 40-second request deadline. The 5-minute soak, 8-minute proxymock replay limit, 2,000 ms p95 ceiling, and zero-failed-request gate remain enforced. This does not identify or repair a particular slow provider or port-forward interruption.

Each cluster gets a GitHub job summary with scenario status, delivered requests, achieved RPS, p95, and p99. JSON reports and command logs are uploaded even on failure. All three scenarios are attempted; a failed scenario fails the final job. Requests travel through kubectl port forwarding from the runner, so latency includes that connection and throughput can be limited by it. Use an in-cluster generator for capacity measurements.

## Speedscale Cloud load

The `Speedscale Cloud load` jobs run after the proxymock load jobs on dev and staging. They launch `speedctl infra replay` against the cluster's `banking-replay` workloads with the gateway-ramp, fraud-spike, and ai-soak profiles in `quality/cloud-load/`. Generation happens in Kubernetes and produces Speedscale Cloud reports. Outbound mocking follows the snapshot and the base test configuration; snapshots without outbound traffic have no dependencies to mock.

The CLI applies `--test-override` using protobuf merge: profile stages and rules append to the existing `banking-daily-replay` configuration. Its single-pass baseline therefore precedes the timed load stages, and its original assertions remain enforced. The added goals require at least one attempted transaction, zero failed transactions, and p95 below 1,500/500/2,000 ms for gateway/fraud/AI respectively. A Cloud report with `Missed Goals`, `Error`, or cancellation fails the load job. Existing CD regression status handling is unchanged.

Use workflow dispatch with `cloud_load_only=true` and `cluster=dev-decoy`, `staging-decoy`, or `all` to run Cloud scenarios without the other jobs. From a configured shell, run `./quality/scripts/run-cloud-load.sh dev-decoy`. Reports, base configuration, report IDs, and logs are saved under `quality/cloud-load-reports/` and uploaded as `cloud-load-<cluster>` artifacts. The job summary links directly to each Cloud report. Profiles have a 20-minute provisioning/run/analysis wait budget each. If workload restoration cannot be confirmed, later traffic is skipped. Every launched report must still finish with a passing verdict and pass the mock-utilization check.

Timed gateway and AI profiles select the repeatable synthetic fixtures described in [fixtures/README.md](fixtures/README.md); CI/CD regression snapshots remain separate. Gateway load covers read-only availability lookups and an authentication rejection, rather than looping stateful account creation against one shared rate-limit bucket. AI fixtures match the current locale-aware requests to all five providers.

The Cloud path does not fix generator cancellation accounting or application errors exposed by load. These remain visible as failed goals.

## October 9 validation

All six full native Cloud profiles and all six full proxymock profiles passed on dev and staging. Every Cloud report passed 100% of response assertions and recorded zero failed transactions. Gateway and AI had zero outbound mock misses; fraud has no outbound mocks.

| Cloud profile | Dev transactions / p95 ms | Staging transactions / p95 ms |
| --- | ---: | ---: |
| Gateway ramp | [23,665 / 102](https://dev.speedscale.com/report/8fc63f7e-faf9-45e2-a040-990a11f1a4f9) | [24,718 / 98.59](https://staging2.speedscale.com/report/4d3d0b23-8268-4dca-b5fa-e332859b5a79) |
| Fraud spike | [14,070 / 149.16](https://dev.speedscale.com/report/5667f4e1-729c-4671-ac0b-9df93df4b55a) | [14,244 / 143.02](https://staging2.speedscale.com/report/de3d774a-b8a8-4849-aeb4-73123e21725e) |
| AI soak | [2,187 / 361.04](https://dev.speedscale.com/report/bf3f045b-7641-402f-871f-0d7c48fd8a62) | [2,123 / 383.37](https://staging2.speedscale.com/report/1297a1d3-df32-4638-b1bb-ae39ba3bf092) |

| Proxymock profile | Dev requests / p95 ms | Staging requests / p95 ms |
| --- | ---: | ---: |
| Gateway ramp | 17,524 / 114.5 | 19,114 / 106.4 |
| Fraud spike | 11,511 / 107.3 | 12,071 / 106 |
| AI soak | 1,673 / 489.4 | 1,595 / 504.3 |

The full gateway/AI Cloud runs and full proxymock mix used the deployed v2.5.1149 runtime components. Cloud fraud was launched with the current v2.5.1169 CLI against those operators. All three proxymock profiles also passed short compatibility runs on both clusters with v2.5.1169, retaining the same response-status, failure, 4xx-baseline, and latency gates. Short compatibility runs do not replace the full-duration results above.

The v2.5.1169 operator chart and staging capture tags were staged in [PR #127](https://github.com/speedscale/demo-infra/pull/127), which merged on October 9. Both live operators were confirmed on v2.5.1169 on October 12. The October 9 results above used the pre-upgrade runtime; both chart renders retained the existing tenant-secret references, DLP configuration, and exporter destinations. The earlier September 30 provisioning failure is no longer an accurate description of the current load results.

## October 12 AI timeout

[The daily run](https://github.com/speedscale/demo-infra/actions/runs/38258565985) failed only staging's proxymock AI soak: one of 1,736 `/api/chat` requests exhausted the HTTP client's implicit 10-second response-header deadline. Its 472 ms p95 passed; gateway, fraud, both CD jobs, every CI replay, and both native Cloud load jobs passed. The complete scheduled runs on October 10 and 11 had passed after PR #127 merged.

A controlled reproduction through the real local AI handler delayed one provider response by 11 seconds. The default client failed one of 40 requests while its p95 goal passed; the AI-specific 40-second request budget completed all 40 with zero failures. An invalid application route still failed the assertion goal. All 23 runner tests pass. The original artifact does not identify which provider or network hop stalled; no particular dependency outage is inferred.

Full five-minute rechecks passed with the new configuration: proxymock dev delivered 1,716 requests at p95 500.7 ms, and staging delivered 1,693 at p95 478.1 ms, both with zero request failures and 100% assertions. [The native Cloud dev soak](https://dev.speedscale.com/report/e9e514e1-2394-4043-9d1a-76abf4a551d8) passed all goals with its actual request timeout confirmed at 40 seconds. The live operators were v2.5.1169.

After merging the request-budget fix, dispatch a new Daily Quality Replays run on main. Re-running the old failed run uses its old commit and therefore its old request deadline.

## Runtime and safe overlap

The October 13 dev Cloud load step took 27m14s: gateway 13m34s, fraud 5m18s, and AI 8m22s. The configured traffic totals eleven minutes. The remaining time includes provisioning and SUT rollouts, restoration, Cloud analysis, and observing completion. Gateway load ended at 14:00:40 UTC, analysis started at 14:01:47, the verdict arrived at 14:07:38, and the old one-minute poll observed it at 14:08:20. The report also warned that the gateway container exceeded 95% of its CPU limit during the test; the runner does not mask that warning or lower traffic to avoid it.

The runner checks report status every ten seconds. Before overlapping analysis with the next test, it requires every application deployment in banking-replay to have no replay ownership labels, an observed and fully ready restored generation with no extra replicas, and no replay inventory pods or pods carrying replay ownership anywhere in banking-replay. Terminating pods count as present. Failed API reads and missing status fields cannot release the workload. A report marked Analyzing alone is insufficient.

One traffic scenario runs at a time in each cluster. Gateway, fraud, and AI still share node capacity and application dependencies, so their load generators do not run together. CI and dev CD also remain ordered because Cloud replay patches the services that CI targets. Dev and staging can run simultaneously because their workloads and cluster capacity are separate. Cloud analysis runs outside these demo clusters and can overlap later tests after the restoration checks pass. Once the final Cloud workload is released, proxymock load runs while remaining Cloud verdicts finish. The job waits for every report and preserves assertion, delivery, mock utilization, latency, and request-timeout gates.

Each scenario's release marker is removed before launch. If restoration is not confirmed, the runner stops launching further traffic and fails. Cloud waits retain the twenty-minute report budget, with up to five additional minutes to confirm restoration after a report concludes; proxymock retains its forty-five-minute orchestration budget and eight-minute per-profile replay limits. The combined load step has a seventy-five-minute limit.
