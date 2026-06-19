# Proofs

## Demo replay config uses banking-replay, build tags, and JWT claim session DLP
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; MCP pull showed `http.req.http...` fails at transform time, and `Bearer` must be stripped by extractor regex before reading `jwt.claims.sub`.
- **Status**: PROVEN
- **Date**: 2026-06-18

## Live banking-accounts sessions use JWT sub claims
- **Level**: Live traffic
- **Evidence**: Proxymock MCP snapshot `f2aab847-ca40-4d93-b628-e76a2c20cb2c`, `2026-06-19T01:23:37Z` to `2026-06-19T01:28:37Z`: 458 inbound `banking-accounts` records had plain sessions; 0 were JWT-shaped, redacted, blank, or hex.

## Daily replay build tags stay short enough for Speedscale
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `bash -n quality/scripts/run-replay.sh` passed; longest expected daily tag is below the 50-character guard
- **Status**: PROVEN
- **Date**: 2026-06-18

## Daily workflow is split into CI proxymock, CD dev replay, and CD staging replay
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `bash -n` passed for replay scripts; `quality-daily.yaml` parsed successfully; `git diff --check` passed
- **Status**: PROVEN
- **Date**: 2026-06-18

## Demo replay config covers daily Speedscale and proxymock runs
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `bash -n` passed for replay scripts; YAML and JSON parsed successfully; fraud snapshot `7b3d0b6e-f0df-489d-8254-d23f56cce131` is Complete with inbound traffic in staging and dev
- **Status**: PROVEN
- **Date**: 2026-06-18

## Daily CD replays use tenant-owned snapshots and longer prep timeout
- **Level**: Integration
- **Evidence**: dev snapshots `4b7fdcac-4203-4474-b4d6-2b6984e14723`, `592d055c-1d40-4706-aa9b-52d8c889d428`, `75ef5e98-2755-45c4-ba4a-8747c5fbb13d`, `6c480801-53d2-4c10-b6a0-2089de176ce9`, `f3cc7c23-fb1f-4d66-9883-6a883af1d573`, `03965c2e-40fc-4227-8757-530d598a487a`, and `fb2382d4-40f4-48a1-a146-172830ede77d` are Complete with `cluster=dev-decoy` and inbound traffic; staging snapshots `f450f457-bb1d-43c2-acd3-24273e28b58f` and `3b3bc298-0f5d-456c-ae1d-20f1ad2022b8` repair the PG startup corpus for accounts and transactions; both operator apps set `test_prep_timeout: 20m`
- **Status**: PROVEN
- **Date**: 2026-06-18

## Replay namespace disables DB migrations and schema validation
- **Level**: Integration
- **Evidence**: `kubectl kustomize demos/microsvc/kubernetes/overlays/replay` renders `SPRING_FLYWAY_ENABLED=false` and `SPRING_JPA_HIBERNATE_DDL_AUTO=none` for banking-user, banking-accounts, and banking-transactions; dev smoke replays `125ed8c9-4775-4815-86ac-863c72a0c01a` and `79fd1a97-8e44-45c7-8fc1-cb28f5302048` reached `Missed Goals` instead of `Error`
- **Status**: PROVEN
- **Date**: 2026-06-18

## Proxymock CI pulls snapshots from the configured Speedscale tenant
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS after failed run `27796833698` showed dev CI could not pull staging-only accounts and transactions snapshots
- **Status**: PROVEN
- **Date**: 2026-06-19

## Rebased PR 48 replay config still validates
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS after rebasing `demo-daily-workload-replays` onto current `origin/main`; verifier accepts staging snapshots that intentionally use promoted dev snapshot IDs.
- **Status**: PROVEN
- **Date**: 2026-06-19

## Replay auth uses JWT resigning instead of DLP exceptions
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `banking-jwt-resign` applies `jwt_resign` to inbound Authorization headers using `${{secret:banking-jwt-secret/secret}}`, and the replay runner syncs and attaches that transform to each selected snapshot before replay. DLP remains scoped to session tagging and sensitive outbound credentials.
- **Status**: PROVEN
- **Date**: 2026-06-19

## Proxymock CI runs a Postman auth preflight before replay
- **Level**: Integration
- **Evidence**: `thoughts/scripts/verify-demo-replay-config.sh` PASS; `quality/postman/banking-auth.postman_collection.json` defines the login request, and `quality/scripts/run-proxymock-scenario.sh` runs that preflight through `banking-gateway` before replacing each recorded banking JWT subject with a fresh login token for the same subject.
- **Status**: PROVEN
- **Date**: 2026-06-19
