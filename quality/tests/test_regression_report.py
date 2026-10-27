"""Reject green regression reports with missing or weakened evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

QUALITY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("regression_report", QUALITY / "scripts/validate-regression-report.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RegressionReportTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((QUALITY / "test-configs/banking-daily-replay.json").read_text())
        self.report = {"status": "Passed", "actualConfig": copy.deepcopy(self.config),
                       "goals": [{"command": name, "status": "PASS"} for name in ["passAssertPct", "failTransactionCount", "totalTransactionCount", "p95Latency"]],
                       "aggregates": [{"name": "totalTransactionCount", "tags": {"source": "generator"}, "gaugeVal": {"val": 42}}]}

    def test_accepts_full_response_error_and_latency_evidence(self):
        module.validate(self.report, self.config, {})

    def test_missing_or_failed_goal_is_rejected_even_when_report_passed(self):
        for name in [g["command"] for g in self.report["goals"]]:
            for missing in [True, False]:
                with self.subTest(goal=name, missing=missing):
                    report = copy.deepcopy(self.report)
                    if missing:
                        report["goals"] = [g for g in report["goals"] if g["command"] != name]
                    else:
                        next(g for g in report["goals"] if g["command"] == name)["status"] = "FAIL"
                    with self.assertRaises(ValueError):
                        module.validate(report, self.config, {})

    def test_low_data_or_removed_body_assertions_are_rejected(self):
        for change in ["generator", "responder", "body"]:
            with self.subTest(change=change):
                report = copy.deepcopy(self.report)
                if change == "body":
                    report["actualConfig"]["assertionGroups"][0]["configs"].pop()
                else:
                    report["actualConfig"][change]["lowDataMode"] = True
                with self.assertRaises(ValueError):
                    module.validate(report, self.config, {})

    def test_empty_run_and_relaxed_latency_rules_are_rejected(self):
        report = copy.deepcopy(self.report)
        report["aggregates"] = []
        with self.assertRaises(ValueError):
            module.validate(report, self.config, {})
        report = copy.deepcopy(self.report)
        report["actualConfig"]["rules"][-1]["value"] = 999999
        with self.assertRaises(ValueError):
            module.validate(report, self.config, {})

    def test_one_unexpected_status_fails_even_with_passing_aggregate_goals(self):
        errors = {"values": [{"assertionType": "Status Code", "errorCount": 1}]}
        with self.assertRaises(ValueError):
            module.validate(self.report, self.config, errors)

    def test_ignored_goal_cannot_pass_the_gate(self):
        report = copy.deepcopy(self.report)
        report["actualConfig"]["rules"][-1]["action"] = "IGNORE"
        with self.assertRaises(ValueError):
            module.validate(report, self.config, {})
