#!/usr/bin/env bash
# Capture fresh, full-dependency snapshots for the banking-* services.
#
# Why: the daily-replay snapshots drift out of sync with the app's data over
# time (recorded entity IDs stop matching the live DB). A freshly captured
# snapshot is internally consistent AND includes the service's *outbound*
# dependency traffic (Postgres, Kafka, external APIs), so it can be replayed
# with everything mocked -- deterministic, with no dependency on real DB state.
#
# Prereqs:
#   - speedctl + proxymock on PATH, pointed at the tenant that captures the app
#     (elastic@staging for staging-decoy).
#   - The banking-app services run Speedscale capture sidecars (CAPTURE_MODE=proxy,
#     TLS_OUT_UNWRAP=true) so inbound AND outbound (incl. DB) traffic is recorded.
#   - banking-sim (or any traffic source) is generating representative traffic in
#     the capture window. No writes are performed by this script.
#
# Usage:
#   quality/scripts/capture-snapshots.sh [window] [svc ...]
#     window : capture lookback (default 15m)
#     svc    : service short names (default: all 7 banking services)
#
# Output: a table of new snapshot IDs + a per-snapshot validation (inbound count,
# whether outbound DB traffic was captured). Wire the IDs you keep into
# quality/speedctl-replay/banking-<svc>.yaml (proxymockSnapshotID) and replay with
# the proxymock path (mock-all) for a deterministic gate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WINDOW="${1:-15m}"; shift || true
SERVICES=("$@")
if [ ${#SERVICES[@]} -eq 0 ]; then
  SERVICES=(user accounts transactions gateway ai fraud notification)
fi

SPEEDCTL_HOME="${SPEEDCTL_HOME:-$HOME/.speedscale}"
ts="$(date -u +%Y%m%d-%H%M%S)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

info() { echo -e "\033[36m$*\033[0m"; }
warn() { echo -e "\033[33m$*\033[0m"; }

declare -a summary

for svc in "${SERVICES[@]}"; do
  name="banking-${svc}-fresh-${ts}"
  info "Creating snapshot for banking-${svc} (last ${WINDOW})"
  out="$(speedctl create snapshot -N "$name" -S "banking-${svc}" --start "$WINDOW" 2>&1)" || {
    warn "  create failed: $out"; summary+=("banking-${svc}\tCREATE_FAILED\t-\t-"); continue; }
  sid="$(echo "$out" | jq -r '.snapshot.id // empty' 2>/dev/null)"
  if [ -z "$sid" ]; then warn "  no snapshot id"; summary+=("banking-${svc}\tNO_ID\t-\t-"); continue; fi

  # wait for processing
  for _ in $(seq 1 20); do
    st="$(speedctl get snapshot "$sid" 2>/dev/null | jq -r '.status // .snapshot.status // "?"')"
    [ "$st" = "Complete" ] && break
    sleep 6
  done

  # validate: pull and count inbound (localhost) + outbound DB (postgres) RR-pairs
  d="$work/$svc"; rm -rf "$d"
  proxymock cloud pull snapshot "$sid" --out "$d" >/dev/null 2>&1 || true
  root="$(find "$d" -maxdepth 1 -type d -name 'snapshot-*' | head -1)"
  inbound=0; db=0
  if [ -n "$root" ]; then
    inbound="$(find "$root/localhost" -type f 2>/dev/null | wc -l | tr -d ' ')"
    db="$(find "$root" -type d -ipath '*postgres*' -exec find {} -type f \; 2>/dev/null | wc -l | tr -d ' ')"
  fi
  dbflag=$([ "${db:-0}" -gt 0 ] && echo "DB:${db}" || echo "DB:none")
  info "  $sid  inbound=${inbound}  ${dbflag}"
  summary+=("banking-${svc}\t${sid}\tin=${inbound}\t${dbflag}")
done

echo
info "===== fresh snapshots (${ts}) ====="
printf "%-22s %-38s %-10s %-10s\n" service snapshot_id inbound outbound_db
for row in "${summary[@]}"; do printf "%b\n" "$row" | awk -F'\t' '{printf "%-22s %-38s %-10s %-10s\n",$1,$2,$3,$4}'; done
echo
info "Next: set proxymockSnapshotID in quality/speedctl-replay/banking-<svc>.yaml to the IDs you keep,"
info "then replay deterministically with: quality/scripts/run-proxymock-scenario.sh staging-decoy banking-<svc>"
