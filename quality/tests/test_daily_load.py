"""Exercise load orchestration with failing and missing scenario reports."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


class DailyLoadTest(unittest.TestCase):
    def run_mix(self, mode):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "quality/scripts"
            scripts.mkdir(parents=True)
            shutil.copy(SCRIPTS / "run-daily-load.sh", scripts)
            stub = scripts / "run-proxymock-scenario.sh"
            stub.write_text('''#!/usr/bin/env bash
set -eu
printf '%s\\n' "$3" >> "$CALLS"
report="$(dirname "$0")/../proxymock-reports/$1/load/$3/$2.json"
if [ "$MODE" = missing ] && [ "$3" = fraud-spike ]; then exit 0; fi
cat > "$report" <<'EOF'
{"performance":{"summary":{"count":100,"rps":10,"p95":20,"p99":30}}}
EOF
if [ "$MODE" = failure ] && [ "$3" = gateway-ramp ]; then exit 7; fi
''')
            stub.chmod(0o755)
            calls = root / "calls"
            summary = root / "summary.md"
            result = subprocess.run(["bash", str(scripts / "run-daily-load.sh"), "dev-decoy"],
                                    env=dict(os.environ, MODE=mode, CALLS=str(calls),
                                             GITHUB_STEP_SUMMARY=str(summary)),
                                    capture_output=True, text=True)
            return result, calls.read_text().splitlines(), summary.read_text()

    def test_all_scenarios_run_and_report(self):
        result, calls, summary = self.run_mix("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["gateway-ramp", "fraud-spike", "ai-soak"])
        self.assertEqual(summary.count("| PASS |"), 3)
        self.assertIn("| 100 | 10 | 20 | 30 |", summary)

    def test_failure_does_not_skip_remaining_scenarios(self):
        result, calls, summary = self.run_mix("failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn("| gateway-ramp | FAIL |", summary)
        self.assertIn("| ai-soak | PASS |", summary)

    def test_missing_metrics_cannot_pass(self):
        result, calls, summary = self.run_mix("missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertIn("| fraud-spike | FAIL (missing metrics)", summary)

    def test_invalid_profile_fails_before_cluster_access(self):
        result = subprocess.run(["bash", str(SCRIPTS / "run-proxymock-scenario.sh"),
                                 "dev-decoy", "banking-user", "fraud-spike"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported load profile/service", result.stderr)
        self.assertNotIn("Connecting to cluster", result.stdout)

    def test_profiles_include_delivery_and_latency_gates(self):
        for profile, service in (("gateway-ramp", "banking-gateway"),
                                 ("fraud-spike", "banking-fraud"),
                                 ("ai-soak", "banking-ai")):
            result = subprocess.run(["bash", "-c", 'source "$1"; configure_load_profile "$2" "$3"; printf "%s\\n" "${load_args[@]}"',
                                     "test", str(SCRIPTS / "load-profiles.sh"), profile, service],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("requests.total < 1", result.stdout)
            self.assertIn("latency.p95 >", result.stdout)
            self.assertIn("--timeout\n8m", result.stdout)


if __name__ == "__main__":
    unittest.main()
