#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLUSTER=${1:-}
case "$CLUSTER" in
  dev-decoy|staging-decoy) ;;
  *) echo "Usage: $0 <dev-decoy|staging-decoy>" >&2; exit 1 ;;
esac

report_root="$REPO_ROOT/quality/proxymock-reports/$CLUSTER/load"
mkdir -p "$report_root"
summary="$report_root/summary.md"
cat > "$summary" <<EOF_SUMMARY
## Daily load: $CLUSTER

| Scenario | Result | Requests | RPS | p95 ms | p99 ms |
| --- | --- | ---: | ---: | ---: | ---: |
EOF_SUMMARY
failed=0
for scenario in gateway-ramp fraud-spike ai-soak; do
  case "$scenario" in
    gateway-ramp) service=banking-gateway ;;
    fraud-spike) service=banking-fraud ;;
    ai-soak) service=banking-ai ;;
  esac
  mkdir -p "$report_root/$scenario"
  report="$report_root/$scenario/$service.json"
  rm -f "$report"
  status=PASS
  if ! "$SCRIPT_DIR/run-proxymock-scenario.sh" "$CLUSTER" "$service" "$scenario" \
      2>&1 | tee "$report_root/$scenario/run.log"; then
    status=FAIL
    failed=$((failed + 1))
  fi
  if [ -s "$report" ] && jq -e '.performance.summary | .count > 0 and (.rps | type == "number") and (.p95 | type == "number") and (.p99 | type == "number")' "$report" >/dev/null 2>&1; then
    jq -r --arg scenario "$scenario" --arg status "$status" '
      .performance.summary | "| \($scenario) | \($status) | \(.count) | \(.rps) | \(.p95) | \(.p99) |"
    ' "$report" >> "$summary"
  else
    [ "$status" = FAIL ] || failed=$((failed + 1))
    echo "| $scenario | FAIL (missing metrics) | - | - | - | - |" >> "$summary"
  fi
done
cat "$summary"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  cat "$summary" >> "$GITHUB_STEP_SUMMARY"
fi
[ "$failed" -eq 0 ]
