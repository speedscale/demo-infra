#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cluster=${1:-}
case "$cluster" in
  dev-decoy) app_url=https://dev.speedscale.com ;;
  staging-decoy) app_url=https://staging2.speedscale.com ;;
  *) echo "Usage: $0 <dev-decoy|staging-decoy>" >&2; exit 1 ;;
esac
root="$REPO_ROOT/quality/cloud-load-reports/$cluster"
mkdir -p "$root"
summary="$root/summary.md"
printf '## Speedscale Cloud load: %s\n\n| Scenario | Result | Cloud report |\n| --- | --- | --- |\n' "$cluster" > "$summary"
failed=0
pids=()
profiles=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
all_released=true
release_start=$(date +%s)
while ! "$SCRIPT_DIR/check-replay-released.sh" banking-replay banking-gateway; do
  if [ $(( $(date +%s) - release_start )) -ge "${REPLAY_RELEASE_TIMEOUT_SECONDS:-300}" ]; then
    echo 'Cloud load stopped: previous replay workloads have not been restored.' >&2
    exit 1
  fi
  sleep "${REPLAY_RELEASE_CHECK_INTERVAL:-5}"
done
for scenario in gateway-ramp fraud-spike ai-soak; do
  service="banking-${scenario%%-*}"
  dir="$root/$scenario"
  mkdir -p "$dir"
  rm -f "$dir/workload-released" "$dir/exit-code"
  (
    result=0
    CLOUD_REPORT_DIR="$dir" REPLAY_RELEASE_FILE="$dir/workload-released" \
      REPLAY_CHECK_INTERVAL="${REPLAY_CHECK_INTERVAL:-10}" \
      REPLAY_TIMEOUT_MINUTES=20 REPLAY_ERROR_GRACE_MINUTES=0 \
      "$SCRIPT_DIR/run-replay.sh" "$cluster" "$service" "$scenario" \
      2>&1 | tee "$dir/run.log" || result=$?
    printf '%s\n' "$result" > "$dir/exit-code"
    exit "$result"
  ) &
  pid=$!
  pids+=("$pid")
  profiles+=("$scenario")
  while [ ! -f "$dir/workload-released" ] && [ ! -f "$dir/exit-code" ]; do
    sleep "${REPLAY_RELEASE_CHECK_INTERVAL:-5}"
  done
  if [ ! -f "$dir/workload-released" ]; then
    echo "Remaining traffic skipped: $scenario has no confirmed workload restoration." >&2
    all_released=false
    failed=1
    break
  fi
  echo "$scenario: restored; starting the next test while report analysis finishes"
done

# Proxymock needs the same services, but not the Cloud analyzer.
proxy_pid=""
if [ "${RUN_PROXYMOCK_LOAD:-false}" = true ]; then
  if [ "$all_released" = true ]; then
    timeout --kill-after=30s 45m "$SCRIPT_DIR/run-daily-load.sh" "$cluster" &
    proxy_pid=$!
    pids+=("$proxy_pid")
  else
    echo 'Proxymock load skipped: Cloud workloads were not released.' >&2
    failed=1
  fi
fi
for index in "${!profiles[@]}"; do
  scenario=${profiles[$index]}
  dir="$root/$scenario"
  result=PASS
  if ! wait "${pids[$index]}"; then result=FAIL; failed=1; fi
  link='No report created'
  if [ -s "$dir/report-id.txt" ]; then
    rid=$(cat "$dir/report-id.txt")
    link="[Open report]($app_url/report/$rid)"
  fi
  printf '| %s | %s | %s |\n' "$scenario" "$result" "$link" >> "$summary"
done
if [ -n "$proxy_pid" ] && ! wait "$proxy_pid"; then failed=1; fi
cat "$summary"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$summary" >> "$GITHUB_STEP_SUMMARY"; fi
exit "$failed"
