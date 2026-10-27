#!/usr/bin/env bash
set -euo pipefail
umask 077

config_file=${1:?Usage: publish-regression-fixtures.sh <tenant-config> <dev-decoy|staging-decoy>}
cluster=${2:?Cluster is required}
case "$cluster" in dev-decoy) snapshot_key=devSnapshotID ;; staging-decoy) snapshot_key=stagingSnapshotID ;; *) exit 1 ;; esac
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export SPEEDSCALE_HOME="$work/home" SPEEDCTL_HOME="$work/home"
mkdir -p "$SPEEDSCALE_HOME"
cp "$config_file" "$SPEEDSCALE_HOME/config.yaml"
config_file="$SPEEDSCALE_HOME/config.yaml"
sctl() { speedctl --config "$config_file" "$@"; }
value() { awk -v key="$2" '$1 == key":" {print $2; exit}' "$1"; }
wait_snapshot() {
  local status
  for _ in {1..60}; do
    status=$(sctl get snapshot "$1" | jq -r .status)
    case "$status" in Complete) return 0 ;; Error) echo "Snapshot processing failed: $1" >&2; return 1 ;; esac
    sleep 5
  done
  echo "Snapshot processing timed out: $1" >&2
  return 1
}

for transform in banking-jwt-resign; do
  sctl put transform "$root/transforms/$transform.json"
done
for test_config in banking-daily-replay banking-gateway-regression banking-user-regression banking-accounts-regression banking-transactions-regression banking-notification-regression; do
  sctl put test-config "$root/test-configs/$test_config.json"
done
python3 "$root/fixtures/build-regression-fixtures.py" "$work/fixtures"
for definition in ai:63d35a86-2f5b-4f25-a0df-e4dc814275af notification:17dce184-7b75-49fd-bab2-9abb729ab96a gateway:66a84b94-f270-4b02-8dab-0840a6a6a6ac; do
  service=${definition%%:*}
  id=${definition#*:}
  proxymock --config "$config_file" cloud push snapshot --in "$work/fixtures/$service" --id "$id" --name "banking-$service-regression-contract" --force
  wait_snapshot "$id"
done

for service in accounts ai gateway notification transactions user; do
  file="$root/speedctl-replay/banking-$service.yaml"
  id=$(value "$file" cloudRegressionSnapshotID)
  if [ -z "$id" ]; then id=$(value "$file" "$snapshot_key"); fi
  if [ -z "$id" ]; then id=$(value "$file" snapshotID); fi
  transform=$(value "$file" cloudRegressionTransform)
  transform=${transform:-banking-jwt-resign}
  wait_snapshot "$id"
  sctl pull snapshot "$id"
  snapshot="$SPEEDSCALE_HOME/data/snapshots/$id.json"
  raw="$SPEEDSCALE_HOME/data/snapshots/$id/raw.jsonl"
  test -f "$snapshot" && test -s "$raw"
  python3 - "$snapshot" "$root/transforms/$transform.json" <<'PY'
import json
from pathlib import Path
import sys
path, transform = map(Path, sys.argv[1:])
data = json.loads(path.read_text())
config = json.loads(transform.read_text())
data['tokenConfigId'] = config['id']
data['tokenizerConfig'] = config
path.write_text(json.dumps(data) + '\n')
PY
  case "$service" in
    user)
      python3 "$root/scripts/repair-postgres-fixture.py" "$raw" --output "$work/repaired.jsonl" --catalog "$root/fixtures/npgsql-catalog.json" --metadata "$root/fixtures/npgsql-statements.json"
      mv "$work/repaired.jsonl" "$raw"
      ;;
    accounts|transactions)
      python3 "$root/scripts/repair-postgres-fixture.py" "$raw" --output "$work/repaired.jsonl" --metadata "$root/fixtures/$service-statements.json" --replace-setup
      mv "$work/repaired.jsonl" "$raw"
      ;;
  esac
  sctl push snapshot "$id" --force
  wait_snapshot "$id"
  echo "Published $service regression fixture for $cluster: $id"
done
