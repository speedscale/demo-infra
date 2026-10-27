# Repeatable daily load fixtures

The gateway ramp uses username and email availability lookups plus a missing-token request that must return 401. These operations can repeat at increasing concurrency without registering users, creating accounts, or changing balances. Stateful account and transaction behavior remains in the CI/CD regression replays. Outbound gateway fixtures target `banking-user:80`, matching `USER_SERVICE_URL`; port 8080 is the dependency container port and does not intercept gateway calls. The replay namespace sets `FAULT_INJECTION_RATE=0`; the capture/demo namespace retains its injected faults.

The AI soak exercises three en-US questions across all five providers. Provider responses are synthetic and fixed. Request bodies include the current locale instruction and remain fully matched; Gemini uses the public dummy key configured by the banking demo (`mock-gemini-key-served-by-speedscale-responder`). Cloud status and response-body assertions remain enforced, with `durationMs` ignored by the existing base config.

Build the credential-free RRPair inputs with:

```bash
python3 quality/fixtures/build-load-fixtures.py /tmp/daily-load-fixtures
```

Publish each directory with `proxymock --config <test-tenant-config> cloud push snapshot --in <directory> --name <fixture-name> --id <loadSnapshotID>`. Publish to both test tenants before changing the IDs in `quality/speedctl-replay/`. These fixtures contain no captured user data or credentials. Fixture IDs are separate from regression snapshot IDs and are selected only for timed load profiles.

Attach the `banking-jwt-resign` tokenizer configuration from `quality/transforms/banking-jwt-resign.json` to each fixture snapshot. Match provider requests exactly, including the demo Gemini query key; a scrub transform does not normalize query-parameter matching. Never substitute a real provider credential into these fixtures.

Use `speedctl pull snapshot <id>`, edit only `tokenConfigId` and `tokenizerConfig` in the downloaded snapshot metadata, then `speedctl push snapshot <id> --force`. Wait for `Complete` before replaying. Validate the five-minute AI soak and four-minute gateway ramp against both clusters; uploading a fixture is not validation.

## Full-data regressions

Cloud load profiles keep low data mode enabled. Regression runs use full data mode, response bodies and schemas, zero transport failures, zero status mismatches, and a 2,000 ms p95 limit. The assertion percentage must remain at least 98%. Dynamic login tokens, registration times, account numbers and transaction times have separate semantic checks; stable business fields still compare against their recordings.

AI regression fixtures cover five providers, account context, Spanish, health, and request validation. Notification fixtures feed three synthetic Kafka events and verify buffer ordering, user filtering and limits. All outbound delivery calls are mocked. These regression IDs are separate from the timed load IDs.

Publish with the latest `speedctl` and `proxymock` on PATH:

```bash
quality/scripts/publish-regression-fixtures.sh ~/.speedscale/dev.yaml dev-decoy
```

Validate in dev before publishing the same changes to the staging tenant with its explicit configuration and `staging-decoy`. The publisher uses a temporary credential directory and updates existing captured snapshot IDs. It does not recreate captured snapshots from expired raw traffic. `speedctl put snapshot` creates a snapshot; use pull/edit/push to repair archived fixtures.

The Postgres repair removes unusable dependency responses while preserving all inbound requests. User fixtures also need catalog and Parse/Bind/Describe setup metadata; the checked-in catalog and statement descriptions contain schema metadata, not application rows. SQL mock signatures include parameter values, excluding only generated write values. SQL transform chains omit direction filters because live Postgres requests do not carry direction when the responder builds their signatures. Gateway HTTP mocks include the JWT subject so identical URLs for different users cannot select each other's responses; Redis stays live in the isolated replay namespace.

The user snapshot contains both captured application traffic on port 8080 and earlier generator traffic on port 80. Only the 873 captured application requests run as this regression. The excluded generator responses do not have corresponding recorded database dependencies.
