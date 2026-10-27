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
    def run_cloud(self, status, pipeline=False, blocked=False, finished=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            quality = root / 'quality'
            scripts = quality / 'scripts'
            scripts.mkdir(parents=True)
            for name in ('run-replay.sh', 'run-cloud-load.sh', 'check-replay-released.sh', 'validate-cloud-load-report.py', 'validate-replay-snapshot.py'):
                shutil.copy(QUALITY / 'scripts' / name, scripts)
            for name in ('connect-cluster.sh', 'check-speedscale-certs.sh'):
                p = scripts / name
                p.write_text('#!/usr/bin/env bash\nexit 0\n')
                p.chmod(0o755)
            for name in ('speedctl-replay', 'cloud-load', 'transforms'):
                shutil.copytree(QUALITY / name, quality / name)
            kubectl = root / 'kubectl'
            kubectl.write_text('#!/usr/bin/env python3\nimport json, os, sys\nif os.environ[\'BLOCKED\'] == \'true\': print(\'{}\')\nelif \'deployments\' in sys.argv:\n print(json.dumps({\'items\':[{\'metadata\':{\'name\':\'banking-\'+name,\'generation\':1,\'labels\':{}},\'spec\':{\'replicas\':1},\'status\':{\'observedGeneration\':1,\'updatedReplicas\':1,\'readyReplicas\':1,\'replicas\':1}} for name in [\'gateway\',\'fraud\',\'ai\']]}))\nelse: print(\'{"items":[]}\')\n')
            kubectl.chmod(0o755)
            cli = root / 'speedctl'
            cli.write_text('''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
a=sys.argv[1:]
if a[:2] == ['infra','replay']:
    with open(os.environ['CALLS'],'a') as f: f.write(json.dumps(a)+'\\n')
    count=len(Path(os.environ['CALLS']).read_text().splitlines())
    print('11111111-2222-3333-4444-'+format(count,'012d'))
elif a[:2] == ['get','test-config']:
    profile=a[2].removeprefix('banking-cloud-load-')
    print((Path(os.environ['CFG_DIR'])/(profile+'.json')).read_text())
elif a[:2] == ['get','snapshot']:
    config=json.loads((Path(os.environ['CFG_DIR']).parent/'transforms/banking-jwt-resign.json').read_text())
    print(json.dumps({'status':'Complete','tokenConfigId':'banking-jwt-resign','tokenizerConfig':config,'outTraffic':[]}))
elif a[:3] == ['get','report','latencytable']:
    print(json.dumps({'total':{'count':100,'p95':20}}))
elif a[:2] == ['get','report']:
    status=os.environ['REPORT_STATUS']
    if os.environ['PIPELINE'] == 'true' and len(Path(os.environ['CALLS']).read_text().splitlines()) < 3:
        status='Testing' if os.environ['FINISHED'] == 'true' else 'Analyzing'
        time.sleep(0.02)
    index=int(a[2].split('-')[-1])-1
    launch=json.loads(Path(os.environ['CALLS']).read_text().splitlines()[index])
    profile=launch[launch.index('--test-config-id')+1].removeprefix('banking-cloud-load-')
    config=json.loads((Path(os.environ['CFG_DIR'])/(profile+'.json')).read_text())
    tags={'load_group_id':profile}
    goals=[{'command':'load_run','status':'PASS'}]+[{'command':c,'status':'PASS','tags':tags} for c in ['load_requests','load_delivery']]
    goals.append({'command':'p95Latency','status':'PASS','tags':dict(tags,load_goal_id='p95')})
    print(json.dumps({'report':{'status':status, 'actualConfig':config, 'goals':goals, 'lifecycleEvents':[{'component':'operator','beat':'finished','state':'RLS_COMPLETED'}] if os.environ['FINISHED'] == 'true' else []}}))
else:
    print('{}')
''')
            cli.chmod(0o755)
            calls = root / 'calls.jsonl'
            env = dict(os.environ, PATH=str(root)+os.pathsep+os.environ['PATH'],
                       SPEEDCTL_HOME=str(root / 'speedctl-home'),
                       REPORT_STATUS=status, CALLS=str(calls), CFG_DIR=str(quality / 'cloud-load'), PIPELINE=str(pipeline).lower(),
                       BLOCKED=str(blocked).lower(), FINISHED=str(finished).lower(), REPLAY_RELEASE_TIMEOUT_SECONDS='0',
                       REPLAY_ERROR_GRACE_MINUTES='0', REPLAY_CHECK_INTERVAL='0', REPLAY_RELEASE_CHECK_INTERVAL='0.01')
            env.pop('SPEEDCTL_CONFIG', None)
            result = subprocess.run(['bash', str(scripts / 'run-cloud-load.sh'), 'staging-decoy'],
                                    env=env, capture_output=True, text=True, timeout=15)
            launches = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
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
            self.assertNotIn('--test-override', args)
            self.assertEqual(args[args.index('--test-config-id')+1], 'banking-cloud-load-'+profile)
            self.assertNotIn('--no-mocks', args)
            if profile in ('gateway-ramp', 'ai-soak'):
                expected = {'gateway-ramp': '4f2b0637-f69d-4787-8f11-24cecc812cb2',
                            'ai-soak': '35ae88c7-f4c2-4d56-89c3-0b645c7d8e11'}[profile]
                self.assertEqual(args[args.index('--snapshot-id')+1], expected)
        self.assertIn('https://staging2.speedscale.com/report/', summary)
        self.assertEqual(summary.count('| PASS |'), 3)

    def test_missed_goals_fail_but_all_scenarios_run(self):
        result, launches, summary, reports = self.run_cloud('Missed Goals')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(launches), 3)
        self.assertEqual(reports, 3)
        self.assertEqual(summary.count('| FAIL |'), 3)

    def test_analysis_overlaps_without_skipping_verdicts(self):
        result, launches, summary, reports = self.run_cloud('Passed', pipeline=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(launches), 3)
        self.assertEqual(reports, 3)
        self.assertEqual(summary.count('| PASS |'), 3)
        self.assertIn('Analyzing', result.stdout)
        self.assertIn('workload restored and replay pods gone', result.stdout)

    def test_finished_lifecycle_can_release_before_status_changes(self):
        result, launches, summary, _ = self.run_cloud('Passed', pipeline=True, finished=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(launches), 3)
        self.assertIn('Testing', result.stdout)
        self.assertEqual(summary.count('| PASS |'), 3)

    def test_missing_restoration_stops_remaining_traffic(self):
        result, launches, summary, _ = self.run_cloud('Passed', blocked=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(launches), 0)
        self.assertIn('previous replay workloads have not been restored', result.stderr)

    def test_terminal_errors_fail(self):
        result, launches, _, reports = self.run_cloud('Error')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(launches), 3)
        self.assertEqual(reports, 3)


if __name__ == '__main__':
    unittest.main()
