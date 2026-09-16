"""Fault-inject the replay/report stage without a cluster or cloud credentials."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run-proxymock-scenario.sh"
STAGE = SCRIPT.read_text().split('info "Replaying proxymock scenario: $name"', 1)[1]


class ReportingTest(unittest.TestCase):
    def run_stage(self, failures=0, failure_mode="missing", buckets=None,
                  replay_status=0, results=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "results").mkdir()
            (root / "reports").mkdir()
            if results:
                (root / "results/request.md").write_text("recorded result")
            # A previous report must not hide a failed generation attempt.
            report = root / "reports/banking-fraud.json"
            report.write_text('{"reliability":{"statusBreakdown":[{"bucket":"2xx","count":1}]}}')
            payload = {"reliability": {"statusBreakdown": buckets if buckets is not None
                                      else [{"bucket": "2xx", "count": 467}]}}
            setup = r'''
set -euo pipefail
name=banking-fraud
SPEEDCTL_HOME="$TEST_ROOT"
snapshot_dir="$TEST_ROOT/snapshot"
result_dir="$TEST_ROOT/results"
report_dir="$TEST_ROOT/reports"
target=localhost:15051
recorded_total=467
recorded_4xx=0
attempts=0
info() { echo "$*"; }
warn() { echo "$*"; }
sleep() { :; }
proxymock() {
  local command=$1 out=""
  shift
  if [ "$command" = replay ]; then
    return "$REPLAY_STATUS"
  fi
  attempts=$((attempts + 1))
  echo "$attempts" > "$TEST_ROOT/attempts"
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --out ]; then out=$2; break; fi
    shift
  done
  if [ "$attempts" -le "$FAILURES" ]; then
    case "$FAILURE_MODE" in
      missing) return 0 ;;
      nonzero) return 1 ;;
      malformed) echo '{broken' > "$out"; return 0 ;;
      empty) echo '{}' > "$out"; return 0 ;;
    esac
  fi
  echo "$PAYLOAD" > "$out"
}
'''
            env = dict(os.environ, TEST_ROOT=tmp, FAILURES=str(failures),
                       FAILURE_MODE=failure_mode, PAYLOAD=json.dumps(payload),
                       REPLAY_STATUS=str(replay_status))
            result = subprocess.run(["bash", "-c", setup + STAGE], env=env,
                                    capture_output=True, text=True)
            count_file = root / "attempts"
            count = int(count_file.read_text()) if count_file.exists() else 0
            return result, count, report.exists()

    def test_recovers_from_report_failures(self):
        for mode in ("missing", "nonzero", "malformed", "empty"):
            with self.subTest(mode=mode):
                result, count, exists = self.run_stage(failures=1, failure_mode=mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(count, 2)
                self.assertTrue(exists)

    def test_exhausted_retries_fail_without_stale_report(self):
        result, count, exists = self.run_stage(failures=3)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 3)
        self.assertFalse(exists)
        self.assertIn("Could not generate a valid proxymock report", result.stdout)

    def test_preserves_replay_failure(self):
        result, count, _ = self.run_stage(failures=1, replay_status=7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(count, 2)

    def test_rejects_server_errors(self):
        result, _, _ = self.run_stage(buckets=[{"bucket": "5xx", "count": 1}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("1 5xx response", result.stdout)

    def test_rejects_auth_drift(self):
        result, _, _ = self.run_stage(buckets=[{"bucket": "4xx", "count": 467}])
        self.assertEqual(result.returncode, 1)
        self.assertIn("exceeds recorded baseline", result.stdout)

    def test_rejects_empty_or_invalid_counts(self):
        for buckets in ([], [{"bucket": "2xx", "count": 0}],
                        [{"bucket": "2xx", "count": -1}],
                        [{"bucket": "2xx", "count": "467"}]):
            with self.subTest(buckets=buckets):
                result, count, exists = self.run_stage(buckets=buckets)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(count, 3)
                self.assertFalse(exists)

    def test_missing_results_fail(self):
        result, count, exists = self.run_stage(results=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 0)
        self.assertFalse(exists)
        self.assertIn("No proxymock results written", result.stdout)


if __name__ == "__main__":
    unittest.main()
