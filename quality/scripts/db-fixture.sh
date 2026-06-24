#!/usr/bin/env bash
# Path B: capture / restore a banking DB fixture so real-DB replays are deterministic.
#
# Why: replaying against a real DB only matches the recording if the DB is in the
# state it was at the *start* of the captured window. Restoring a fixture also
# resets the identity SEQUENCES, so the SUT re-assigns the SAME server-generated
# IDs in the same order -- which mocking/seeding could not reproduce.
#
# Pairing (important): dump the fixture at T0, then capture the snapshot over
# [T0, T0+window]. The fixture is the window's START state; the window's writes
# re-apply cleanly onto it at replay.
#
# Usage:
#   db-fixture.sh capture <out-dir>     # at T0, before capture-snapshots.sh
#   db-fixture.sh restore <dir>         # before each replay
#
# Notes:
#   - Dumps from banking-app (the captured app); restores into banking-replay
#     (the replay SUTs' DB). Data-only, excludes Flyway history (schema differs
#     between the .NET and Java services), --disable-triggers for cross-schema FKs.
#   - At replay, keep Postgres OUT of the responder mock set (so the SUT hits the
#     restored real DB): speedctl infra replay --mock-except 'host:banking-postgres'
#     (or capture the snapshot with Postgres filtered out). External HTTP deps stay
#     mocked from the snapshot.
set -euo pipefail

MODE="${1:-}"; DIR="${2:-}"
CTX="${KUBE_CONTEXT:-do-nyc1-staging-decoy}"
SCHEMAS=(user_service accounts_service transactions_service)
declare -A PW=( [user_service]=user_service_pass [accounts_service]=accounts_service_pass [transactions_service]=transactions_service_pass )

info() { echo -e "\033[36m$*\033[0m"; }
err()  { echo -e "\033[31m$*\033[0m" >&2; }

pgpod() { kubectl --context "$CTX" -n "$1" get pods 2>/dev/null | awk '/banking-postgres/{print $1; exit}'; }

if [ -z "$MODE" ] || [ -z "$DIR" ]; then echo "usage: $0 {capture|restore} <dir>"; exit 1; fi

case "$MODE" in
  capture)
    mkdir -p "$DIR"
    pod=$(pgpod banking-app); [ -z "$pod" ] && { err "no banking-app postgres pod"; exit 1; }
    for s in "${SCHEMAS[@]}"; do
      info "dump $s (banking-app) -> $DIR/$s.sql"
      kubectl --context "$CTX" -n banking-app exec "$pod" -- sh -c \
        "PGPASSWORD=\$POSTGRES_PASSWORD pg_dump -U \$POSTGRES_USER -d banking_app -n $s -T '$s.flyway_schema_history' --data-only --disable-triggers --no-owner" \
        > "$DIR/$s.sql" 2>/dev/null
      echo "  $(wc -c < "$DIR/$s.sql") bytes"
    done
    date -u +"fixture captured at %Y-%m-%dT%H:%M:%SZ -> $DIR (now run capture-snapshots.sh for the paired window)"
    ;;
  restore)
    [ -d "$DIR" ] || { err "fixture dir not found: $DIR"; exit 1; }
    pod=$(pgpod banking-replay); [ -z "$pod" ] && { err "no banking-replay postgres pod"; exit 1; }
    # truncate every target schema's data tables (CASCADE handles cross-schema FKs), then load
    for s in "${SCHEMAS[@]}"; do
      [ -f "$DIR/$s.sql" ] || continue
      info "restore $s -> banking-replay"
      tbls=$(kubectl --context "$CTX" -n banking-replay exec "$pod" -- env PGPASSWORD="${PW[$s]}" \
        psql -h localhost -U "${s}_user" -d banking_app -At \
        -c "SELECT string_agg(format('%I.%I', schemaname, tablename), ',') FROM pg_tables WHERE schemaname='$s' AND tablename <> 'flyway_schema_history';" 2>/dev/null | tail -1)
      [ -n "$tbls" ] && kubectl --context "$CTX" -n banking-replay exec "$pod" -- env PGPASSWORD="${PW[$s]}" \
        psql -h localhost -U "${s}_user" -d banking_app -q -c "TRUNCATE $tbls RESTART IDENTITY CASCADE;" 2>&1 | grep -i error || true
      kubectl --context "$CTX" -n banking-replay exec -i "$pod" -- env PGPASSWORD="${PW[$s]}" \
        psql -h localhost -U "${s}_user" -d banking_app -q < "$DIR/$s.sql" 2>&1 | grep -i error | head -3 || true
    done
    info "restore complete (banking-replay aligned to fixture; sequences reset)"
    ;;
  *) echo "usage: $0 {capture|restore} <dir>"; exit 1 ;;
esac
