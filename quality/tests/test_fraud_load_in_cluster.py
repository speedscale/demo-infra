"""Keep failed traffic and missing evidence red without depending on a tunnel."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("fraud_load", SCRIPTS / "run-fraud-load-in-cluster.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FraudLoadTest(unittest.TestCase):
    def report(self):
        return {"reliability": {"statusBreakdown": [{"bucket": "2xx", "count": 100}]}}

    def test_job_uses_service_dns_and_has_no_kubernetes_token(self):
        job = runner.job_spec("test-load", "banking-replay", "snapshot", ["--stage", "vus=20,for=30s"])
        pod = job["spec"]["template"]["spec"]
        env = {e["name"]: e["value"] for e in pod["containers"][0]["env"]}
        self.assertEqual(env["LOAD_TARGET"], "banking-fraud.banking-replay.svc.cluster.local:8080")
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(job["spec"]["template"]["metadata"]["annotations"][
            "cluster-autoscaler.kubernetes.io/safe-to-evict"], "false")
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(json.loads(env["LOAD_ARGS"]), ["--stage", "vus=20,for=30s"])

    def test_missing_duplicate_and_empty_reports_are_rejected(self):
        result = runner.RESULT_MARKER + json.dumps({"exit_code": 0, "report": self.report()})
        for log in ("no report", result + "\n" + result,
                    runner.RESULT_MARKER + json.dumps({"exit_code": 0, "report": {
                        "reliability": {"statusBreakdown": []}}})):
            with self.subTest(log=log), self.assertRaises(ValueError):
                runner.decode_result(log)

    def run_controller(self, exit_code, condition="Complete", failed_requests=0, disconnect=False):
        documents = []
        calls = []
        report = self.report()
        report["reliability"]["statusBreakdown"].append({"bucket": "other", "count": failed_requests})
        disconnected = False

        def kubectl(args, **kwargs):
            nonlocal disconnected
            calls.append(args)
            if "create" in args:
                document = json.loads(kwargs["input"])
                documents.append(document)
                stdout = json.dumps({"metadata": {"uid": "job-uid"}})
            elif "get" in args:
                if disconnect and not disconnected:
                    disconnected = True
                    return subprocess.CompletedProcess(args, 1, "", "No agent available")
                stdout = json.dumps({"status": {"conditions": [{"type": condition, "status": "True"}]}})
            elif "logs" in args:
                stdout = runner.RESULT_MARKER + json.dumps({"exit_code": exit_code, "report": report})
            else:
                stdout = ""
            return subprocess.CompletedProcess(args, 0, stdout, "")

        with tempfile.TemporaryDirectory() as tmp, patch.object(runner.subprocess, "run", side_effect=kubectl), \
                patch.object(runner.time, "sleep"):
            config = Path(tmp) / "config.yaml"
            config.write_text("private-test-config")
            output = Path(tmp) / "report.json"
            status = runner.controller("banking-replay", "snapshot", config, output, [])
            self.assertEqual(json.loads(output.read_text()), report)
        secret = next(d for d in documents if d["kind"] == "Secret")
        self.assertEqual(secret["metadata"]["ownerReferences"][0]["uid"], "job-uid")
        self.assertTrue(any("delete" in c for c in calls))
        self.assertFalse(any("port-forward" in c for c in calls))
        return status

    def test_success_requires_job_and_replay_success(self):
        self.assertEqual(self.run_controller(0), 0)
        self.assertNotEqual(self.run_controller(1), 0)
        self.assertNotEqual(self.run_controller(0, "Failed"), 0)

    def test_failed_requests_cannot_pass_with_zero_exit_and_low_latency(self):
        self.assertNotEqual(self.run_controller(0, failed_requests=231386), 0)

    def test_status_disconnect_does_not_restart_the_load(self):
        self.assertEqual(self.run_controller(0, disconnect=True), 0)

    def test_checksum_mismatch_never_leaves_executable(self):
        from io import BytesIO
        with tempfile.TemporaryDirectory() as tmp, patch.object(runner.platform, "machine", return_value="x86_64"), \
                patch.object(runner.time, "sleep"), patch.object(runner.urllib.request, "urlopen",
                    side_effect=lambda url, **kwargs: BytesIO(b"wrong-checksum" if url.endswith(".sha256") else b"binary")):
            binary = Path(tmp) / "proxymock"
            with self.assertRaises(ValueError):
                runner.install_proxymock(binary)
            self.assertFalse(binary.exists())


if __name__ == "__main__":
    unittest.main()
