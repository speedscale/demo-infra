#!/usr/bin/env python3
"""Exercise the polling wrapper with recorded API responses and no cluster access."""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("run-replay.sh")


class ReplayStatusTests(unittest.TestCase):
    def test_only_passed_reports_succeed_and_every_terminal_report_is_retained(self):
        source = SCRIPT.read_text()
        function = re.search(r"^wait_for_replay\(\) \{.*?^\}", source, re.M | re.S).group()
        for status, expected in [("Passed", 0), ("Missed Goals", 1), ("Error", 1), ("Canceled", 1)]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                report = json.dumps({"report": {"status": status}})
                prelude = '''set -euo pipefail
REPO_ROOT="$1"
CLUSTER_NAME=dev-decoy
CLOUD_LOAD_PROFILE=""
REPLAY_RELEASE_FILE=""
REPLAY_ERROR_GRACE_MINUTES=0
info() { :; }
warn() { :; }
error() { :; }
speedctl_cmd() { printf '%s\\n' "$TEST_REPORT"; }
'''
                import os
                env = dict(os.environ, TEST_REPORT=report)
                completed = subprocess.run(["bash", "-c", prelude + re.search(r"^retryable_admission_failure\(\) \{.*?^\}", source, re.M | re.S).group() + "\n" + function + "\nwait_for_replay fixture report-id", "test", directory], env=env, timeout=5, capture_output=True)
                self.assertEqual(expected, completed.returncode, completed.stderr.decode())
                saved = Path(directory)/"quality/replay-reports/dev-decoy/report-id.json"
                self.assertEqual(status, json.loads(saved.read_text())["report"]["status"])



class AdmissionRetryTests(unittest.TestCase):
    def test_retry_requires_a_pre_creation_webhook_transport_failure(self):
        source = SCRIPT.read_text()
        helper = re.search(r'^retryable_admission_failure\(\) \{.*?^\}', source, re.M | re.S).group()
        for change, expected in [({}, 0), ({'status': 'Passed'}, 1), ({'status': 'Missed Goals'}, 1),
                                 ({'tags': {'operator': 'v2.5.1274'}}, 1),
                                 ({'lifecycleEvents': [{'component': 'operator'}]}, 1),
                                 ({'aggregates': [{'name': 'totalTransactionCount', 'gaugeVal': {'val': 1}}]}, 1),
                                 ({'replayEvents': []}, 1),
                                 ({'replayEvents': [{'reason': 'FailedToInitTrafficReplay', 'description': 'invalid configuration'}]}, 1)]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                report = admission_failure()
                report.update(change)
                path = Path(directory) / 'report.json'
                path.write_text(json.dumps({'report': report}))
                result = subprocess.run(['bash', '-c', helper + '\nretryable_admission_failure "$1"', 'test', str(path)], capture_output=True)
                self.assertEqual(expected, result.returncode, result.stderr)

    def test_admission_retry_is_bounded_and_retains_every_attempt(self):
        source = SCRIPT.read_text()
        helper = re.search(r'^retryable_admission_failure\(\) \{.*?^\}', source, re.M | re.S).group()
        poll = re.search(r'^wait_for_replay\(\) \{.*?^\}', source, re.M | re.S).group()
        block = source.split('  replay_passed=false\n', 1)[1].split('  if [ "$replay_passed" = true ]; then', 1)[0]
        cases = [([admission_failure(), {'status': 'Passed'}], 0, 2),
                 ([admission_failure()] * 4, 1, 3),
                 ([{'status': 'Canceled'}], 1, 1),
                 ([{'status': 'Missed Goals'}], 1, 1)]
        for reports, code, attempts in cases:
            with self.subTest(reports=reports), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'responses.json').write_text(json.dumps(reports))
                setup = r'''set -euo pipefail
REPO_ROOT="$1"
CLUSTER_NAME=dev-decoy
CLOUD_LOAD_PROFILE=""
REPLAY_RELEASE_FILE=""
REPLAY_ERROR_GRACE_MINUTES=0
name=banking-accounts
namespace=banking-replay
workload=banking-accounts
snapshot_id=11111111-1111-1111-1111-111111111111
test_config_id=fixture
build_tag=fixture
replay_passed=false
declare -a replay_override_args=() regression_args=()
declare -A report_ids=() replay_statuses=()
info() { :; }
warn() { :; }
error() { :; }
sleep() { :; }
speedctl_cmd() {
 if [ "$1" = infra ]; then
  n=$(cat "$REPO_ROOT/count" 2>/dev/null || echo 0)
  n=$((n+1)); echo "$n" > "$REPO_ROOT/count"
  printf '00000000-0000-0000-0000-%012d\n' "$n"
 else
  python3 - "$REPO_ROOT" <<'DATA'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]); n=int((p/'count').read_text()); data=json.loads((p/'responses.json').read_text())
print(json.dumps({'report':data[min(n-1,len(data)-1)]}))
DATA
 fi
}
'''
                result = subprocess.run(['bash', '-c', setup + helper + '\n' + poll + '\n' + block + '\n[ "$replay_passed" = true ]', 'test', directory], capture_output=True, timeout=10)
                self.assertEqual(code, result.returncode, result.stderr.decode())
                self.assertEqual(attempts, int((root / 'count').read_text()))
                saved = list((root / 'quality/replay-reports/dev-decoy').glob('*.json'))
                self.assertEqual(attempts, len(saved))


def admission_failure():
    return {'status': 'Canceled', 'replayEvents': [{'reason': 'FailedToInitTrafficReplay',
            'description': 'failed to create traffic replay CR: failed calling webhook "replay.speedscale.com": No agent available'}]}


if __name__ == "__main__":
    unittest.main()
