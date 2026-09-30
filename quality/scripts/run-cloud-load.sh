#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cluster=${1:-}
case "$cluster" in
  dev-decoy) app_url=https://dev.speedscale.com ;;
  staging-decoy) app_url=https://staging.speedscale.com ;;
  *) echo "Usage: $0 <dev-decoy|staging-decoy>" >&2; exit 1 ;;
esac
root="$REPO_ROOT/quality/cloud-load-reports/$cluster"
mkdir -p "$root"
summary="$root/summary.md"
printf '## Speedscale Cloud load: %s\n\n| Scenario | Result | Cloud report |\n| --- | --- | --- |\n' "$cluster" > "$summary"
failed=0
for scenario in gateway-ramp fraud-spike ai-soak; do
  service="banking-${scenario%%-*}"
  dir="$root/$scenario"
  mkdir -p "$dir"
  result=PASS
  if ! CLOUD_REPORT_DIR="$dir" REPLAY_TIMEOUT_MINUTES=20 \
      "$SCRIPT_DIR/run-replay.sh" "$cluster" "$service" "$scenario" 2>&1 | tee "$dir/run.log"; then
    result=FAIL
    failed=1
  fi
  link='No report created'
  if [ -s "$dir/report-id.txt" ]; then
    rid=$(cat "$dir/report-id.txt")
    link="[Open report]($app_url/report/$rid)"
  fi
  printf '| %s | %s | %s |\n' "$scenario" "$result" "$link" >> "$summary"
  # An unconfirmed terminal state may still own the workload.
  if [ -s "$dir/report-id.txt" ] && ! jq -e '.report.status | ascii_upcase | IN("PASSED", "MISSED GOALS", "MISSED_GOALS", "ERROR", "CANCELED", "CANCELLED")' "$dir/report.json" >/dev/null 2>&1; then
    echo 'Remaining scenarios skipped because the previous replay has no confirmed terminal state.' >> "$summary"
    failed=1
    break
  fi
done
cat "$summary"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then cat "$summary" >> "$GITHUB_STEP_SUMMARY"; fi
exit "$failed"
