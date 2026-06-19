#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CLUSTER_NAME=${1:-}
REPLAY_NAME=${2:-}
VARIANTS_FILE=${3:-}

if [ -z "$CLUSTER_NAME" ] || [ -z "$REPLAY_NAME" ]; then
  echo "Usage: $0 <cluster-name> <replay-name> [variants.json]"
  exit 1
fi

CONFIG_FILE="$REPO_ROOT/quality/speedctl-replay/${REPLAY_NAME}.yaml"
if [ ! -f "$CONFIG_FILE" ]; then
  echo "Replay config not found: $CONFIG_FILE"
  exit 1
fi

declare -a speedctl_args
if [ -n "${SPEEDCTL_CONFIG:-}" ]; then
  speedctl_args=(--config "$SPEEDCTL_CONFIG")
fi

speedctl_cmd() {
  speedctl "${speedctl_args[@]}" "$@"
}

info() { echo -e "\033[36m$*\033[0m"; }
warn() { echo -e "\033[33m$*\033[0m"; }
error() { echo -e "\033[31m$*\033[0m"; }

get_config_value() {
  local file=$1 key=$2
  awk -v k="$key" '$1==k":" {gsub(/["'"'"']/, "", $2); print $2; exit}' "$file"
}

run_hook() {
  local hook_name=$1
  local command=${!hook_name:-}
  if [ -z "$command" ]; then
    warn "Skipping hook $hook_name; variable is not set"
    return 1
  fi
  info "Running hook: $hook_name"
  bash -lc "$command"
}

wait_for_report() {
  local report_id=$1 out_file=$2
  local timeout_seconds=$(( ${TUNE_TIMEOUT_MINUTES:-30} * 60 ))
  local poll_seconds=${TUNE_POLL_SECONDS:-30}
  local start now elapsed report status norm

  start=$(date +%s)
  while true; do
    now=$(date +%s)
    elapsed=$((now - start))
    if [ "$elapsed" -gt "$timeout_seconds" ]; then
      error "Timed out waiting for report $report_id"
      return 1
    fi

    report=$(speedctl_cmd get report "$report_id" 2>/dev/null || true)
    if [ -n "$report" ]; then
      printf "%s\n" "$report" > "$out_file"
      status=$(jq -r '.report.status // .status // "unknown"' "$out_file" 2>/dev/null || echo "unknown")
      norm=$(echo "$status" | tr '[:lower:]' '[:upper:]' | tr ' ' '_')
      info "  report $report_id: $status (${elapsed}s)"
      case "$norm" in
        PASSED|MISSED_GOALS|ERROR|CANCELED)
          return 0
          ;;
      esac
    else
      info "  waiting for report $report_id (${elapsed}s)"
    fi
    sleep "$poll_seconds"
  done
}

metric_from_report() {
  local report_file=$1 metric=$2
  jq -r --arg metric "$metric" '
    def agg($name):
      (.report.aggregates // .aggregates // [])
      | map(select(.name == $name))
      | first
      | (.gaugeVal.val // .countVal // .value // null);

    if $metric == "successRate" then
      (.report.successRate // .successRate // agg("successRate") // null)
    elif $metric == "matchPct" then
      (agg("matchPct") // agg("requests.result-match-pct") // null)
    elif $metric == "passAssertPct" then
      (agg("passAssertPct") // null)
    elif $metric == "responseRate" then
      (agg("responseRate") // null)
    else
      agg($metric)
    end
  ' "$report_file" 2>/dev/null
}

score_number() {
  local value=${1:-}
  if [ -z "$value" ] || [ "$value" = "null" ]; then
    echo "0"
  else
    echo "$value"
  fi
}

json_number() {
  local value=${1:-}
  if [ -z "$value" ] || [ "$value" = "null" ]; then
    echo "null"
  else
    echo "$value"
  fi
}

variant_count() {
  local file=$1
  jq '.variants | length' "$file"
}

variant_value() {
  local file=$1 index=$2 expr=$3
  jq -r ".variants[$index] | $expr" "$file"
}

name=$(get_config_value "$CONFIG_FILE" "name")
workload=$(get_config_value "$CONFIG_FILE" "workload")
namespace=$(get_config_value "$CONFIG_FILE" "namespace")
snapshot_id=$(get_config_value "$CONFIG_FILE" "snapshotID")
dev_snapshot_id=$(get_config_value "$CONFIG_FILE" "devSnapshotID")
staging_snapshot_id=$(get_config_value "$CONFIG_FILE" "stagingSnapshotID")
test_config_id=$(get_config_value "$CONFIG_FILE" "testConfigID")

case "$CLUSTER_NAME" in
  dev-decoy)
    [ -n "$dev_snapshot_id" ] && snapshot_id="$dev_snapshot_id"
    ;;
  staging-decoy)
    [ -n "$staging_snapshot_id" ] && snapshot_id="$staging_snapshot_id"
    ;;
esac

if [ -z "$name" ] || [ -z "$workload" ] || [ -z "$namespace" ] || [ -z "$snapshot_id" ] || [ -z "$test_config_id" ]; then
  error "Replay config is missing required fields: $CONFIG_FILE"
  exit 1
fi

if [ -z "$VARIANTS_FILE" ]; then
  VARIANTS_FILE=$(mktemp "${TMPDIR:-/tmp}/replay-variants.XXXXXX.json")
  jq -n '{
    variants: [
      {name: "minimal", notes: "Baseline replay with the current test config."},
      {name: "test-transforms", beforeHook: "TUNE_APPLY_TEST_TRANSFORMS_CMD", notes: "Apply test-side recommendations, then replay."},
      {name: "mock-transforms", beforeHooks: ["TUNE_RESET_TRANSFORMS_CMD", "TUNE_APPLY_MOCK_TRANSFORMS_CMD"], notes: "Reset, apply mock-side recommendations, then replay."}
    ]
  }' > "$VARIANTS_FILE"
fi

if ! jq -e '.variants | type == "array" and length > 0' "$VARIANTS_FILE" >/dev/null; then
  error "Variant file must contain a non-empty variants array: $VARIANTS_FILE"
  exit 1
fi

run_id="${GITHUB_RUN_ID:-local}"
run_attempt="${GITHUB_RUN_ATTEMPT:-1}"
cluster_tag="${CLUSTER_NAME%-decoy}"
workload_tag="${name#banking-}"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
out_dir="${TUNE_OUTPUT_DIR:-$REPO_ROOT/quality/replay-tuning/$CLUSTER_NAME/$name/$timestamp}"
mkdir -p "$out_dir"

info "Connecting to cluster: $CLUSTER_NAME"
"$SCRIPT_DIR/connect-cluster.sh" "$CLUSTER_NAME"

info "Setting up speedctl"
mkdir -p "${SPEEDCTL_HOME:-$HOME/.speedscale}"
SPEEDCTL_HOME="${SPEEDCTL_HOME:-$HOME/.speedscale}"
speedctl_cmd check

info "Syncing replay test config"
speedctl_cmd put test-config "$REPO_ROOT/quality/test-configs/banking-daily-replay.json"

results_file="$out_dir/results.jsonl"
count=$(variant_count "$VARIANTS_FILE")
best_score=-1
best_variant=""

for i in $(seq 0 $((count - 1))); do
  variant=$(variant_value "$VARIANTS_FILE" "$i" '.name // ("variant-" + tostring)')
  test_config_override=$(variant_value "$VARIANTS_FILE" "$i" '.testOverride // empty')
  extra_args_json=$(jq -c ".variants[$i].speedctlArgs // []" "$VARIANTS_FILE")
  variant_tag=$(echo "$variant" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | cut -c1-8)
  build_tag="qt:${cluster_tag}:${workload_tag}:${variant_tag}:${run_id}.${run_attempt}"

  if [ ${#build_tag} -gt 50 ]; then
    build_tag="qt:${cluster_tag}:${workload_tag}:${variant_tag}:$(date +%H%M%S)"
  fi

  info "========================================"
  info "Variant: $variant"
  info "========================================"

  mapfile -t hooks < <(jq -r ".variants[$i] | ([.beforeHook] + (.beforeHooks // []))[]? // empty" "$VARIANTS_FILE")
  skipped=0
  for hook in "${hooks[@]}"; do
    if ! run_hook "$hook"; then
      skipped=1
      break
    fi
  done
  if [ "$skipped" -eq 1 ]; then
    jq -nc --arg variant "$variant" --arg status "skipped" '{variant:$variant,status:$status}' >> "$results_file"
    continue
  fi

  declare -a replay_args
  replay_args=(
    infra replay
    --cluster "$CLUSTER_NAME"
    --namespace "$namespace"
    --service "$workload"
    --snapshot-id "$snapshot_id"
    --test-config-id "$test_config_id"
    --build-tag "$build_tag"
    --id-only
  )

  if [ -n "$test_config_override" ]; then
    replay_args+=(--test-override "$test_config_override")
  fi

  mapfile -t extra_args < <(jq -r '.[]' <<<"$extra_args_json")
  if [ ${#extra_args[@]} -gt 0 ]; then
    replay_args+=("${extra_args[@]}")
  fi

  output=$(speedctl_cmd "${replay_args[@]}" 2>&1) || {
    jq -nc --arg variant "$variant" --arg status "launch_error" --arg output "$output" \
      '{variant:$variant,status:$status,output:$output}' >> "$results_file"
    continue
  }

  report_id=$(echo "$output" | grep -Eo '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1 || true)
  if [ -z "$report_id" ]; then
    jq -nc --arg variant "$variant" --arg status "missing_report_id" --arg output "$output" \
      '{variant:$variant,status:$status,output:$output}' >> "$results_file"
    continue
  fi

  report_file="$out_dir/${variant}.report.json"
  summary_file="$out_dir/${variant}.ai-summary.md"

  if ! wait_for_report "$report_id" "$report_file"; then
    jq -nc --arg variant "$variant" --arg reportId "$report_id" --arg status "timeout" \
      '{variant:$variant,reportId:$reportId,status:$status}' >> "$results_file"
    continue
  fi

  speedctl_cmd get report ai-summary "$report_id" > "$summary_file" 2>/dev/null || true

  report_status=$(jq -r '.report.status // .status // "unknown"' "$report_file")
  success_rate=$(metric_from_report "$report_file" "successRate")
  match_pct=$(metric_from_report "$report_file" "matchPct")
  pass_assert_pct=$(metric_from_report "$report_file" "passAssertPct")
  response_rate=$(metric_from_report "$report_file" "responseRate")
  score=$(jq -n --argjson s "$(score_number "$success_rate")" --argjson m "$(score_number "$match_pct")" '($s * 1000) + $m')

  jq -nc \
    --arg variant "$variant" \
    --arg reportId "$report_id" \
    --arg status "$report_status" \
    --argjson successRate "$(json_number "$success_rate")" \
    --argjson matchPct "$(json_number "$match_pct")" \
    --argjson passAssertPct "$(json_number "$pass_assert_pct")" \
    --argjson responseRate "$(json_number "$response_rate")" \
    '{variant:$variant,reportId:$reportId,status:$status,successRate:$successRate,matchPct:$matchPct,passAssertPct:$passAssertPct,responseRate:$responseRate}' \
    >> "$results_file"

  if jq -e --argjson score "$score" --argjson best "$best_score" '$score > $best' >/dev/null; then
    best_score=$score
    best_variant=$variant
  fi
done

info "Results: $results_file"
if [ -n "$best_variant" ]; then
  info "Best variant: $best_variant"
fi
