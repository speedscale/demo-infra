"""Run the Cloud orchestrator end-to-end with a simulated speedctl API."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

QUALITY = Path(__file__).resolve().parents[1]


class CloudLoadTest(unittest.TestCase):
    def run_cloud(self, status):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / 'quality'
            scripts = quality / 'scripts'
            scripts.mkdir(parents=True)
            for name in ('run-replay.sh', 'run-cloud-load.sh'):
                shutil.copy(QUALITY / 'scripts' / name, scripts)
            for name in ('connect-cluster.sh', 'check-speedscale-certs.sh'):
                p = scripts / name
                p.write_text('#!/usr/bin/env bash\nexit 0\n')
                p.chmod(0o755)
            for name in ('speedctl-replay', 'cloud-load'):
                shutil.copytree(QUALITY / name, quality / name)
            cli = root / 'speedctl'
            cli.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a=sys.argv[1:]
if a[:2] == ['infra','replay']:
    with open(os.environ['CALLS'],'a') as f: f.write(json.dumps(a)+'\\n')
    print('11111111-2222-3333-4444-555555555555')
elif a[:2] == ['get','test-config']:
    print(json.dumps({'generator':{'dlpConfigId':'banking-app-keys'},'responder':{'dlpConfigId':'banking-app-keys'}}))
elif a[:2] == ['get','snapshot']:
    print(json.dumps({'tokenConfigId':'banking-jwt-resign','tokenizerConfig':{'id':'banking-jwt-resign','generator':[{'transforms':[{'type':'jwt_resign'}]}],'generatorExpectedEnvironment':{'secrets':['fixture']}},'outTraffic':[]}))
elif a[:2] == ['get','report']:
    print(json.dumps({'report':{'status':os.environ['REPORT_STATUS']}}))
else:
    print('{}')
''')
            cli.chmod(0o755)
            calls = root / 'calls.jsonl'
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       SPEEDCTL_HOME=str(root / 'speedctl-home'),
                       REPORT_STATUS=status, CALLS=str(calls),
                       REPLAY_ERROR_GRACE_MINUTES='0', REPLAY_CHECK_INTERVAL='0')
            env.pop('SPEEDCTL_CONFIG', None)
            result = subprocess.run(['bash', str(scripts / 'run-cloud-load.sh'), 'staging-decoy'],
                                    env=env, capture_output=True, text=True, timeout=15)
            launches = [json.loads(line) for line in calls.read_text().splitlines()]
            summary = (quality / 'cloud-load-reports/staging-decoy/summary.md').read_text()
            reports = list((quality / 'cloud-load-reports').glob('**/report.json'))
            return result, launches, summary, len(reports)

    def test_launches_cloud_profiles_and_saves_reports(self):
        result, launches, summary, reports = self.run_cloud('Passed')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(len(launches), 3)
        self.assertEqual(reports, 3)
        for args, profile in zip(launches, ('gateway-ramp','fraud-spike','ai-soak')):
            self.assertEqual(args[args.index('--cluster')+1], 'staging-decoy')
            override = json.loads(args[args.index('--test-override')+1])
            self.assertEqual(override, json.loads((QUALITY / 'cloud-load' / (profile+'.json')).read_text()))
            self.assertNotIn('--no-mocks', args)
        self.assertIn('https://staging2.speedscale.com/report/', summary)
        self.assertEqual(summary.count('| PASS |'), 3)

    def test_missed_goals_fail_but_all_scenarios_run(self):
        result, launches, summary, reports = self.run_cloud('Missed Goals')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(launches), 3)
        self.assertEqual(reports, 3)
        self.assertEqual(summary.count('| FAIL |'), 3)

    def test_terminal_errors_fail(self):
        result, launches, _, reports = self.run_cloud('Error')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(launches), 3)
        self.assertEqual(reports, 3)


if __name__ == '__main__':
    unittest.main()
