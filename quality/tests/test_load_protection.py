"""Load measurements must survive voluntary node consolidation without hiding errors."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

path = Path(__file__).resolve().parents[1] / "scripts/protect-load-workloads.py"
spec = importlib.util.spec_from_file_location("load_protection", path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class LoadProtectionTest(unittest.TestCase):
    def run_load(self, exit_code=0, healthy=True, create_error=False):
        documents = []
        calls = []
        def kubectl(namespace, args, document=None):
            calls.append(args)
            self.assertEqual(namespace, "banking-replay")
            if args[:2] == ["get", "deployments"]:
                return {"items": [{"metadata": {"name": name}, "spec": {
                    "selector": {"matchLabels": {"app": name}}}}
                    for name in ["banking-ai", "banking-gateway", "unrelated"]]}
            if args[0] == "create":
                documents.append(document)
                if document["kind"] == "PodDisruptionBudget" and create_error:
                    raise RuntimeError("API rejected protection")
                return {"metadata": {"uid": "lease-uid"}}
            if args[:2] == ["get", "pdb"]:
                return {"items": [{"metadata": {"generation": 1}, "status": {
                    "observedGeneration": 1, "expectedPods": 1, "desiredHealthy": 1,
                    "currentHealthy": int(healthy), "disruptionsAllowed": 0}}
                    for _ in range(2)]}
            return {}
        child = Mock()
        child.wait.return_value = exit_code
        with patch.object(runner, "kubectl", side_effect=kubectl), \
                patch.object(runner.subprocess, "Popen", return_value=child) as popen, \
                patch.object(runner.time, "monotonic", side_effect=[0, 301]):
            if create_error or not healthy:
                with self.assertRaises(RuntimeError):
                    runner.protect("banking-replay", ["load-test"])
                popen.assert_not_called()
            else:
                self.assertEqual(runner.protect("banking-replay", ["load-test"]), exit_code)
                self.assertEqual(popen.call_args.args[0], ["load-test"])
                self.assertEqual(popen.call_args.kwargs["env"]["PROXYMOCK_LOAD_PROTECTED"], "true")
        self.assertTrue(any(a[:2] == ["delete", "job"] for a in calls))
        return documents

    def test_protection_is_scoped_and_has_automatic_cleanup_backstop(self):
        documents = self.run_load()
        lease = documents[0]
        self.assertEqual(lease["kind"], "Job")
        self.assertEqual(lease["spec"]["activeDeadlineSeconds"], 3600)
        self.assertEqual(lease["spec"]["ttlSecondsAfterFinished"], 60)
        self.assertFalse(lease["spec"]["template"]["spec"]["automountServiceAccountToken"])
        budgets = [d for d in documents if d["kind"] == "PodDisruptionBudget"]
        self.assertEqual(len(budgets), 2)
        for budget in budgets:
            self.assertEqual(budget["spec"]["maxUnavailable"], 0)
            self.assertTrue(budget["spec"]["selector"]["matchLabels"]["app"].startswith("banking-"))
            self.assertEqual(budget["metadata"]["ownerReferences"][0]["uid"], "lease-uid")

    def test_load_failure_stays_failed(self):
        self.run_load(exit_code=7)

    def test_missing_protection_or_unhealthy_workloads_never_start_traffic(self):
        self.run_load(create_error=True)
        self.run_load(healthy=False)


if __name__ == "__main__":
    unittest.main()
