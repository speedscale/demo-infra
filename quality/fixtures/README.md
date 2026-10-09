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
