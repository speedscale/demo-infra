"""Reject replay auth metadata that cannot mount the signing key."""
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run-replay.sh"


class ReplayAuthTests(unittest.TestCase):
    def test_required_secret_must_be_declared_before_launch(self):
        function = re.search(r"^ensure_snapshot_jwt_resign\(\) \{.*?^\}", SCRIPT.read_text(), re.M | re.S).group()
        for secrets, expected in [(None, 1), ([], 1), (["unrelated/key"], 1),
                                  (["banking-jwt-secret/secret"], 0),
                                  (["${{secret:banking-jwt-secret/secret}}"], 0)]:
            with self.subTest(secrets=secrets):
                config = json.loads((SCRIPT.parents[1] / "transforms/banking-jwt-resign.json").read_text())
                config.pop("generatorExpectedEnvironment", None)
                if secrets is not None:
                    config["generatorExpectedEnvironment"] = {"secrets": secrets}
                snapshot = {"status": "Complete", "tokenConfigId": "banking-jwt-resign", "tokenizerConfig": config}
                env = dict(os.environ, SNAPSHOT=json.dumps(snapshot), TEST_SCRIPT_DIR=str(SCRIPT.parent))
                prelude = '''set -euo pipefail
JWT_TRANSFORM_ID=banking-jwt-resign
SCRIPT_DIR="$TEST_SCRIPT_DIR"
JWT_TRANSFORM_FILE="$SCRIPT_DIR/../transforms/banking-jwt-resign.json"
SPEEDSCALE_HOME=/unused
speedctl_cmd() { printf '%s\\n' "$SNAPSHOT"; }
sync_enabled() { return 1; }
error() { echo "$*"; }
'''
                result = subprocess.run(["bash", "-c", prelude + function + "\nensure_snapshot_jwt_resign fixture"],
                                        env=env, capture_output=True, text=True, timeout=5)
                self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
                if expected:
                    self.assertIn("generator secret declaration", result.stdout)
