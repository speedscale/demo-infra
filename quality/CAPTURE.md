# Capturing fresh banking snapshots (the seed)

## The mix: three dependency-handling patterns in one gate

The banking gate deliberately demonstrates **all three** Speedscale replay modes,
each on the services it fits best. `quality/scripts/run-quality-mix.sh` runs it.

| services | pattern | dependency handling | why this pattern |
|---|---|---|---|
| gateway, ai, fraud, notification | **mock-all** (proxymock) | every recorded dep mocked; **no DB at all** | these are stateless / proxy / gRPC / Kafka — `DB:none` on capture, so mocking is trivial + self-contained |
| **accounts** | **mock-the-DB** (proxymock) | Postgres + HTTP deps served **from the recording** | read-heavy; proxymock serves the recorded reads — **proven: GET reads 100%, zero real DB touched** |
| **user, transactions** | **restore-fixture** (db-fixture + speedctl) | **real** Postgres reset to the recording's start-state; HTTP deps mocked | write / server-assigned-ID heavy; restoring the fixture **resets the sequences** so the SUT re-assigns the *same* IDs — the one thing mocking can't reproduce |

**Evidence behind the split (measured here):**
- *Mock-the-DB works via proxymock* — accounts reads came back 100% with the
  account IDs served from the recording (they don't exist in the live DB).
- *Mock-the-DB via the in-cluster speedctl responder does NOT* — it intercepts
  Postgres (no real writes) but its query **matching is incomplete**: Java/JDBC
  gets empty results → `404`, .NET/Npgsql gets malformed frames → `500`
  ("Severity not received"). So PG mocking here = **proxymock**, not the responder.
- *Restore-fixture is the only thing that reproduces server-assigned IDs* —
  validated: banking-app `user_service` (~71k rows) restored into banking-replay
  with `users_id_seq` aligned, so register/login/create regenerate identical IDs.

**Run it:**
```bash
# Path B services need a fixture captured paired with their snapshots:
quality/scripts/db-fixture.sh capture /tmp/fix          # T0 start-state
quality/scripts/capture-snapshots.sh 15m                # [T0, T0+15m] snapshots
quality/scripts/run-quality-mix.sh staging-decoy /tmp/fix
```

**Validated (measured):**
- **restore-fixture / user → 96.3%** — paired fixture + `MOCK_EXCEPT=glob:*postgres*`
  (real DB, externals mocked). register/login/profile all deterministic; the few
  fails are a small capture-gap (tighten T0↔window alignment). This is the proof
  that the sequence-reset reproduces server-assigned IDs.
- **restore-fixture / transactions → 60.8%** — reads (`GET`) fully deterministic
  at 200; `POST` creates still `400` because they fan out to **fraud (gRPC) +
  banking-accounts (HTTP)** mocks and carry non-deterministic fields (generated
  txn id/timestamp) that don't match. Fix = ignore-transforms on those request
  matchers (cross-service, *not* a DB issue).
- **mock-the-DB / accounts (proxymock) → reads 100%**, zero real DB.
- **mock-all / ai+fraud+notification → 100%** on the daily gate.

> The correct `--mock-except` selector is **`glob:*postgres*`** (matches the
> out-service key `banking-user:banking-postgres.banking-app.svc.cluster.local:5432`).
> `host:banking-postgres` does **not** match.

> Path B requires a **paired** (fixture, snapshot): `db-fixture.sh capture` at T0,
> then `capture-snapshots.sh` over `[T0, T0+window]`, then wire those snapshot IDs
> and `db-fixture.sh restore` before replay.

## Why this exists

The daily quality replays compare each banking service's responses against a
**recorded** snapshot. Over time those snapshots **drift** out of sync with the
app's data: the recordings reference account IDs (`25742+`), user IDs (`40xxx`),
etc. that no longer exist once the shared `banking-postgres` is reseeded or
accumulates state. The result is `404`/`409`/`400` failures that are *data drift*,
not real regressions — and they can't be fixed by tuning a transform.

The durable fix is to **re-capture** periodically. A freshly captured snapshot is:

1. **Internally consistent** — the account created in the recorded flow is the
   same one read back later (same IDs), so replay is deterministic.
2. **Full-dependency** — it includes the service's *outbound* traffic
   (Postgres, Kafka, external APIs), so the replay can **mock everything** and
   never touch a real database. No drift, no cross-service coupling, no reseed.

A 15-minute capture of `banking-user` yields ~253 inbound requests **and ~1150
Postgres RR-pairs** plus the external identity APIs (socure / netverify /
haveibeenpwned) — the whole graph.

## Prerequisites (already true on staging-decoy)

- `speedctl` + `proxymock` on PATH, pointed at the capturing tenant
  (`elastic@staging`).
- The `banking-app` namespace services run Speedscale **capture sidecars**
  (`CAPTURE_MODE=proxy`, `TLS_OUT_UNWRAP=true`) — so inbound **and** outbound
  (incl. TLS-unwrapped DB) traffic is recorded continuously.
- A traffic source is exercising the app during the window. `banking-app` ships
  **`banking-sim`**, which drives representative flows continuously — so capture
  needs **no manual traffic and performs no writes**. (If you need richer/edge
  coverage, drive the `banking-app` gateway with your own script during the
  window — note that *does* write to the live app DB, so get sign-off first.)

## Procedure

### 1. Capture (no writes)

```bash
# all 7 services, last 15 minutes of banking-sim traffic
quality/scripts/capture-snapshots.sh 15m

# or specific services / window
quality/scripts/capture-snapshots.sh 30m user accounts transactions
```

It creates one snapshot per service from the recent capture window, waits for
processing, then **validates** each by pulling it and counting inbound +
outbound-DB RR-pairs. Output is a table of new snapshot IDs; keep the ones with
healthy `inbound` and `DB:<n>` (non-zero) counts.

Under the hood per service:
`speedctl create snapshot -N banking-<svc>-fresh-<ts> -S banking-<svc> --start 15m`.

### 2. Validate a snapshot by hand (optional)

```bash
proxymock cloud pull snapshot <id> --out /tmp/snap
ls /tmp/snap/snapshot-*/                 # host dirs = captured dependencies
find /tmp/snap -ipath '*postgres*' -type f | wc -l   # DB RR-pairs captured
```

Confirm `localhost/` (inbound) and `banking-postgres.*/` (outbound DB) are both
present and non-trivial. External APIs (socure/netverify/haveibeenpwned) should
appear for `user`.

### 3. Wire it in

Point the service at the new snapshot in
`quality/speedctl-replay/banking-<svc>.yaml`:

- `proxymockSnapshotID: <new-id>` — used by the **mock-all** replay path
  (`run-proxymock-scenario.sh`). This is the deterministic gate.
- (Optional) `stagingSnapshotID: <new-id>` — used by the in-cluster
  `speedctl infra replay` path, which still hits the **real** DB. Only
  deterministic if you also restore a matching DB fixture before each run.

### 4. Replay deterministically (mock-all)

```bash
quality/scripts/run-proxymock-scenario.sh staging-decoy banking-<svc>
```

The proxymock path mocks the recorded outbound (Postgres, Kafka, external APIs)
and replays the recorded inbound — independent of real DB state. It also does its
own auth via a Postman preflight (mints fresh JWTs through the gateway), and
asserts status + `Content-Type` + response schema.

> **One config caveat:** the current scenario runs in `passthroughMode`, so
> *unmatched* requests (e.g. POST creates whose signature isn't in the recording)
> fall through to the real service/DB. For a fully isolated gate, switch the
> replay test-config to mock-all (no passthrough) and tune
> `quality/proxymock-prune/<svc>.patterns` to drop non-deterministic Postgres
> queries (connection setup, `now()`, sequences) that won't match on replay.

## Refresh cadence

Re-run step 1 whenever the daily gate starts showing data-drift failures (or on a
schedule, e.g. weekly), and commit the new `proxymockSnapshotID`s. Because the
mock-all path is self-contained, a fresh capture fully resets determinism — no DB
reseed required.

## Why mock-all beats re-recording-against-the-real-DB

| Approach | DB at replay | Deterministic? | Reseed needed? |
|---|---|---|---|
| `speedctl infra replay` (today) | **real** banking-postgres | no — drifts | yes, before every run |
| Fresh snapshot + **mock-all** (this) | **mocked** from recording | yes | no |

JWT is handled on both paths (mount fix on the speedctl path; Postman preflight on
the proxymock path), so auth is not the blocker — the data layer is, and mocking
it removes the problem class entirely.

## Making the gate deterministic — two paths

The remaining work to turn the gate green is removing the replay's dependence on
live DB state. Two approaches (both validated to the point noted):

### Path A — mock the DB
Use a fresh full-dependency snapshot; the responder intercepts the SUT's `:5432`
calls. **Validated:** the interception engages (replay leaves the real DB
untouched). **Blocker:** for any query *not* in the captured window (notably the
SUT's startup/seed queries), the responder returns a malformed Postgres frame
(".NET: Severity not received") → the SUT errors. Finish options:
- Capture a window that **includes a SUT restart** so startup/seed queries are
  recorded; and/or widen matching via `proxymock-prune/<svc>.patterns`.
- Or use the **proxymock mock-all** path (passthrough off) and confirm its
  Postgres matching is more tolerant.
This path is cleanest *if* Postgres no-match is handled gracefully
(Speedscale-side) — otherwise it stays fragile.

### Path B — restore a DB fixture (recommended; `db-fixture.sh`)
Replay against the **real** DB, but reset it to the recording's start-state first.
Because the fixture restores the identity **sequences**, the SUT re-assigns the
**same** server-generated IDs — reproducing the one thing mocking/seeding can't.

1. **Capture (paired):**
   ```bash
   quality/scripts/db-fixture.sh capture /tmp/fix-$(date +%s)   # T0: dump start-state
   quality/scripts/capture-snapshots.sh 15m                     # capture [T0, T0+15m]
   ```
2. **Before each replay:** `quality/scripts/db-fixture.sh restore <dir>` — truncates
   + reloads banking-replay's `*_service` schemas and resets sequences. (This also
   cleans up any ad-hoc DB drift.)
3. **Replay with Postgres real, externals mocked:** keep `:5432` out of the mock
   set — `speedctl infra replay --mock-except 'glob:*postgres*'` (or capture
   the snapshot with Postgres filtered). The HTTP deps (socure/netverify/stripe/…)
   stay mocked from the snapshot.

**Validated:** dump→restore→sequence-reset works (banking-app `user_service`
~71k rows restored into banking-replay, `users_id_seq` aligned). **Remaining:** the
paired capture + the `--mock-except` replay wiring into the gate.
