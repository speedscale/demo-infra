import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("health", Path(__file__).with_name("check-nightlies.py"))
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)
NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
CHECK = {"host": "gitlab", "repo": "speedscale/example", "project": 1, "schedule": 2, "name": "security", "url": "https://gitlab.com/example", "max_age_hours": 28, "grace_hours": 4, "min_jobs": 2}


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.active = True
        self.runs = [{"id": 10, "status": "success", "created_at": "2026-10-22T04:00:00Z", "web_url": "https://gitlab.com/run/10", "sha": "commit"}]
        self.jobs = [{"name": "scan: [one]", "status": "success"}, {"name": "scan: [two]", "status": "success"}]
        self.sent = []

    def get(self, host, path):
        if "/jobs?" in path:
            return self.jobs
        if "/pipelines?" in path:
            return self.runs
        return {"active": self.active}

    def run_check(self, previous=None, when=NOW):
        return health.evaluate([CHECK], previous or {}, self.get, self.sent.append, when)

    def test_healthy_runs_are_quiet(self):
        self.assertFalse(self.run_check()["bad"])
        self.assertFalse(self.sent)

    def test_failures_group_matrix_retries_and_remind_daily_then_recover(self):
        self.jobs = [dict(j, status="failed") for j in self.jobs]
        state = self.run_check()
        self.assertIn("scan (2)", self.sent[0])
        self.runs[0]["id"] = 11
        state = self.run_check(state)
        self.assertEqual(1, len(self.sent))
        state = self.run_check(state, NOW + timedelta(hours=13))
        self.assertEqual(2, len(self.sent))
        self.jobs = [dict(j, status="success") for j in self.jobs]
        self.run_check(state)
        self.assertIn("recovered", self.sent[-1])

    def test_disabled_missing_stale_and_incomplete_runs_fail(self):
        self.active = False
        self.assertIn("disabled", str(self.run_check()["bad"]))
        self.active = True
        self.jobs = self.jobs[:1]
        self.assertIn("missing", str(self.run_check()["bad"]))
        self.jobs[0]["status"] = "skipped"
        self.assertIn("incomplete", str(self.run_check()["bad"]))
        self.runs[0]["created_at"] = "2026-10-20T00:00:00Z"
        self.assertIn("stale", str(self.run_check()["bad"]))
        self.runs = []
        self.assertIn("no completed", str(self.run_check()["bad"]))

    def test_queued_run_does_not_hide_failure_and_stuck_runs_alert(self):
        self.jobs[0]["status"] = "failed"
        self.runs.insert(0, dict(self.runs[0], id=11, status="pending", created_at="2026-10-22T11:00:00Z"))
        self.assertIn("failed/incomplete", str(self.run_check()["bad"]))
        self.runs[0]["created_at"] = "2026-10-22T06:00:00Z"
        self.assertIn("stuck", str(self.run_check()["bad"]))

    def test_notification_failure_does_not_produce_new_state(self):
        self.active = False
        def reject(text):
            raise RuntimeError("delivery failed")
        with self.assertRaises(RuntimeError):
            health.evaluate([CHECK], {}, self.get, reject, NOW)

    def test_github_skipped_checks_and_cancellation_fail(self):
        check = dict(CHECK, host="github", workflow="quality.yaml")
        run = dict(self.runs[0], conclusion="success", status="completed", html_url="https://github.com/run/10")
        def get(host, path):
            if "/jobs?" in path:
                return {"jobs": [{"name": "replay", "status": "completed", "conclusion": "skipped"}]}
            if "/runs?" in path:
                return {"workflow_runs": [run]}
            return {"state": "active"}
        self.assertIn("incomplete", health.observe(check, get, NOW)["problem"])
        run["conclusion"] = "cancelled"
        self.assertTrue(health.observe(check, get, NOW)["problem"])


if __name__ == "__main__":
    unittest.main()
