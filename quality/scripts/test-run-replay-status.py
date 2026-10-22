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
                completed = subprocess.run(["bash", "-c", prelude + function + "\nwait_for_replay fixture report-id", "test", directory], env=env, timeout=5, capture_output=True)
                self.assertEqual(expected, completed.returncode, completed.stderr.decode())
                saved = Path(directory)/"quality/replay-reports/dev-decoy/report-id.json"
                self.assertEqual(status, json.loads(saved.read_text())["report"]["status"])


if __name__ == "__main__":
    unittest.main()
