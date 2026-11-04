"""Restoration failures retain rollout and native-sidecar evidence safely."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/describe-replay-release.sh'


class ReleaseDiagnosticsTest(unittest.TestCase):
    def test_rollout_and_terminating_native_sidecar_are_identified(self):
        resources = {'items': [
            {'kind': 'Deployment', 'metadata': {'name': 'banking-fraud', 'generation': 4},
             'spec': {'replicas': 1, 'template': {'spec': {'containers': [
                 {'env': [{'name': 'API_KEY', 'value': 'do-not-print'}]}]}}},
             'status': {'observedGeneration': 4, 'replicas': 2, 'updatedReplicas': 1,
                        'readyReplicas': 1}},
            {'kind': 'Pod', 'metadata': {'name': 'speedscale-generator-old', 'deletionTimestamp': 'now'},
             'spec': {'nodeName': 'node-a'}, 'status': {'phase': 'Running',
                 'initContainerStatuses': [{'name': 'istio-proxy', 'ready': False,
                     'state': {'waiting': {'reason': 'CrashLoopBackOff'}}}]}},
            {'kind': 'Pod', 'metadata': {'name': 'ordinary-app'}, 'status': {'phase': 'Running'}}]}
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / 'kubectl'
            cli.write_text('#!/usr/bin/env python3\nimport os\nprint(os.environ["RESOURCES"])\n')
            cli.chmod(0o755)
            result = subprocess.run(['bash', str(SCRIPT), 'banking-replay'], capture_output=True, text=True,
                                    env=dict(os.environ, PATH=tmp+os.pathsep+os.environ['PATH'],
                                             RESOURCES=json.dumps(resources)))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['deployments'][0]['replicas'], 2)
        self.assertEqual(len(data['replayPods']), 1)
        self.assertEqual(data['replayPods'][0]['containers'][0]['waiting'], 'CrashLoopBackOff')
        self.assertEqual(data['replayPods'][0]['deleting'], 'now')
        self.assertNotIn('do-not-print', result.stdout)


if __name__ == '__main__':
    unittest.main()
