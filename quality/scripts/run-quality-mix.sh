#!/usr/bin/env bash
# Run the banking quality gate as a MIX of dependency-handling patterns, each
# service using the one that fits its shape. Demonstrates all three Speedscale
# replay modes in a single gate:
#
#   mock-all       (proxymock)            no-DB / stateless services
#   mock-the-DB    (proxymock)            read-heavy DB service (accounts)
#   restore-fixture(db-fixture + speedctl) write / server-ID-heavy DB services
#
# Why split this way (evidence, see quality/CAPTURE.md):
#   - proxymock mocks recorded deps (incl. Postgres) and serves reads from the
#     recording -- proven on accounts (GET reads 100%, zero real DB).
#   - the in-cluster speedctl responder's Postgres matching is incomplete
#     (empty->404 / malformed->500), so it is NOT used for DB mocking here.
#   - server-assigned IDs + mutations are only reproducible by restoring a
#     fixture (resets sequences) -- so write-heavy services use Path B.
#
# Usage:
#   run-quality-mix.sh <cluster> [fixture-dir]
#     fixture-dir : a db-fixture.sh capture dir (required for the Path B services).
#                   Capture it paired with the snapshots (see CAPTURE.md).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLUSTER="${1:-staging-decoy}"
FIXTURE_DIR="${2:-}"

# --- the mix matrix ---
PROXYMOCK_SVCS=(gateway accounts ai fraud notification)   # mock-all + mock-the-DB
FIXTURE_SVCS=(user transactions)                          # restore-fixture (real DB)

info() { echo -e "\033[36m$*\033[0m"; }
warn() { echo -e "\033[33m$*\033[0m"; }

info "===== Pattern 1+2: proxymock (mock all recorded deps, incl. DB) ====="
for s in "${PROXYMOCK_SVCS[@]}"; do
  info "--- banking-$s (proxymock) ---"
  "$SCRIPT_DIR/run-proxymock-scenario.sh" "$CLUSTER" "banking-$s" || warn "  banking-$s proxymock failed"
done

info "===== Pattern 3: restore-fixture + real-DB replay (Postgres real, externals mocked) ====="
if [ -z "$FIXTURE_DIR" ]; then
  warn "No fixture-dir given; skipping the restore-fixture services (${FIXTURE_SVCS[*]})."
  warn "Capture one paired with the snapshots:  quality/scripts/db-fixture.sh capture <dir>"
  exit 0
fi
info "Restoring DB fixture from $FIXTURE_DIR"
"$SCRIPT_DIR/db-fixture.sh" restore "$FIXTURE_DIR"
for s in "${FIXTURE_SVCS[@]}"; do
  info "--- banking-$s (fixture + --mock-except postgres) ---"
  MOCK_EXCEPT='host:banking-postgres' "$SCRIPT_DIR/run-replay.sh" "$CLUSTER" "banking-$s" || warn "  banking-$s replay failed"
done
info "===== mix complete ====="
