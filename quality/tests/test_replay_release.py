"""Refuse overlap while replay ownership, pods, or restored rollout remain."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check-replay-released.sh'
READY = {'metadata': {'name': 'banking-ai', 'generation': 2, 'labels': {}}, 'spec': {'replicas': 1},
         'status': {'observedGeneration': 2, 'updatedReplicas': 1,
                    'readyReplicas': 1, 'replicas': 1}}


class ReplayReleaseTest(unittest.TestCase):
    def check(self, deployment, pods, error=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / 'kubectl'
            cli.write_text('''#!/usr/bin/env python3
import os, sys
if os.environ['API_ERROR'] == 'true':
    print('API unavailable', file=sys.stderr)
    sys.exit(1)
print(os.environ['DEPLOYMENT'] if 'deployments' in sys.argv else os.environ['PODS'])
''')
            cli.chmod(0o755)
            return subprocess.run(['bash', str(SCRIPT), 'banking-replay', 'banking-ai'],
                                  env=dict(os.environ, PATH=tmp+os.pathsep+os.environ['PATH'],
                                           DEPLOYMENT=json.dumps({'items': [deployment]}), PODS=json.dumps(pods),
                                           API_ERROR=str(error).lower()),
                                  capture_output=True, text=True)

    def test_restored_rollout_without_replay_pods_is_released(self):
        self.assertEqual(self.check(READY, {'items': []}).returncode, 0)

    def test_owned_or_unfinished_rollouts_are_not_released(self):
        for field, value in [('observedGeneration', 1), ('updatedReplicas', 0),
                             ('readyReplicas', 0), ('replicas', 2)]:
            deployment = copy.deepcopy(READY)
            deployment['status'][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.check(deployment, {'items': []}).returncode, 0)
        for label in ['replay.speedscale.com/env-id', 'replay.speedscale.com/report-id']:
            deployment = copy.deepcopy(READY)
            deployment['metadata']['labels'][label] = 'previous-replay'
            self.assertNotEqual(self.check(deployment, {'items': []}).returncode, 0)

    def test_terminating_inventory_and_old_sut_pods_are_not_released(self):
        for metadata in [{'name': 'speedscale-generator-old', 'deletionTimestamp': 'now'},
                         {'name': 'banking-ai-old', 'labels': {'replay.speedscale.com/report-id': 'old'}}]:
            with self.subTest(metadata=metadata):
                self.assertNotEqual(self.check(READY, {'items': [{'metadata': metadata}]}).returncode, 0)

    def test_missing_data_and_api_errors_are_not_released(self):
        for deployment, pods, error in [({}, {'items': []}, False),
                                         (READY, {}, False), (READY, {'items': []}, True)]:
            self.assertNotEqual(self.check(deployment, pods, error).returncode, 0)


if __name__ == '__main__':
    unittest.main()
