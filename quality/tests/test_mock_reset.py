"""Exercise mock readiness polling with Kubernetes API faults."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/reset-standing-mocks.sh'
POLL = SCRIPT.read_text().split('info "Waiting for Running and MocksReady"', 1)[1].split('# --- verify SUT routing', 1)[0]


def replay(name, ready=True):
    return {'metadata': {'name': name}, 'status': {'conditions': [
        {'type': 'Running', 'status': 'True' if ready else 'False'},
        {'type': 'MocksReady', 'status': 'True' if ready else 'False'}]}}


class MockResetTest(unittest.TestCase):
    def run_poll(self, mode, items=None, inventory=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {'items': items if items is not None else [replay('mock-a'), replay('mock-b')]}
            setup = r'''
set -euo pipefail
NS=banking-app
READY_TIMEOUT=30
DELETE_TIMEOUT=30
STATUS_REQUEST_TIMEOUT=10s
trs=(mock-a mock-b)
clock=0
info() { echo "$*"; }
warn() { echo "$*"; }
error() { echo "$*" >&2; }
date() { echo "$clock"; }
sleep() { clock=$((clock + $1)); }
kubectl() {
  printf '%s\n' "$*" >> "$TEST_ROOT/calls"
  n=$(wc -l < "$TEST_ROOT/calls" | tr -d ' ')
  if [ "$MODE" = permanent ] || { [ "$MODE" = transient ] && [ "$n" -eq 1 ]; }; then
    echo 'Unable to connect to the server: context deadline exceeded' >&2
    return 1
  fi
  if [ "$MODE" = malformed ]; then echo '{broken'; else echo "$PAYLOAD"; fi
}
'''
            block = POLL
            if inventory:
                block = SCRIPT.read_text().split('info \"Waiting for the operator to remove responder and redis inventory\"', 1)[1].split('# The operator names', 1)[0]
            result = subprocess.run(['bash', '-c', setup + block],
                                    env=dict(os.environ, TEST_ROOT=tmp, MODE=mode, PAYLOAD=json.dumps(payload)),
                                    capture_output=True, text=True, timeout=5)
            return result, (root / 'calls').read_text().splitlines()

    def test_transient_read_error_recovers_and_is_visible(self):
        result, calls = self.run_poll('transient')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('context deadline exceeded', result.stderr)
        self.assertIn('retrying', result.stdout)
        self.assertIn('2/2 ready (10s/30s elapsed)', result.stdout)
        self.assertTrue(all('--request-timeout=10s' in call for call in calls))

    def test_cleanup_transient_read_error_recovers(self):
        result, calls = self.run_poll('transient', [], inventory=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn('Unable to read mock inventory', result.stdout)
        self.assertIn('Inventory removed', result.stdout)

    def test_cleanup_errors_or_remaining_inventory_cannot_pass(self):
        for mode, items in [('permanent', []), ('malformed', []),
                            ('ready', [{'metadata': {'name': 'speedscale-responder-test'}}])]:
            with self.subTest(mode=mode):
                result, _ = self.run_poll(mode, items, inventory=True)
                self.assertEqual(result.returncode, 1)
                self.assertIn('Inventory still present after 30s', result.stderr)

    def test_ready_list_uses_one_read(self):
        result, calls = self.run_poll('ready')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_persistent_api_errors_fail_at_deadline(self):
        result, _ = self.run_poll('permanent')
        self.assertEqual(result.returncode, 1)
        self.assertIn('after 30s', result.stderr)
        self.assertIn('0/2 ready (30s/30s elapsed)', result.stdout)

    def test_unrelated_ready_mock_does_not_hide_missing_mock(self):
        result, _ = self.run_poll('ready', [replay('mock-a'), replay('unrelated')])
        self.assertEqual(result.returncode, 1)
        self.assertIn('Only 1/2', result.stderr)

    def test_malformed_status_and_unready_mocks_cannot_pass(self):
        for mode, items in [('malformed', None), ('ready', [replay('mock-a', False), replay('mock-b')])]:
            with self.subTest(mode=mode):
                result, _ = self.run_poll(mode, items)
                self.assertEqual(result.returncode, 1)
                self.assertIn('after 30s', result.stderr)


if __name__ == '__main__':
    unittest.main()
