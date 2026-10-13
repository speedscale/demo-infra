"""Keep load evidence mandatory and regression collection independent."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

QUALITY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cloud_guard', QUALITY / 'scripts/validate-cloud-load-report.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class CloudLowDataTest(unittest.TestCase):
    def config(self, profile):
        return json.loads((QUALITY / 'cloud-load' / (profile + '.json')).read_text())

    def report(self, profile='gateway-ramp'):
        config = self.config(profile)
        tags = {'load_group_id': profile}
        goals = [{'command': 'load_run', 'status': 'PASS'}]
        goals += [{'command': c, 'status': 'PASS', 'tags': tags} for c in ('load_delivery', 'load_requests')]
        goals += [{'command': 'p95Latency', 'status': 'PASS', 'tags': dict(tags, load_goal_id='p95')}]
        return config, {'report': {'status': 'Passed', 'actualConfig': copy.deepcopy(config), 'goals': goals}}

    def test_load_profiles_are_low_data_with_original_durations_and_gates(self):
        for profile, duration, ceiling in [('gateway-ramp', 240, 1500), ('fraud-spike', 120, 500), ('ai-soak', 300, 2000)]:
            config = self.config(profile)
            generator = config['generator']
            self.assertTrue(generator['lowDataMode'])
            self.assertTrue(config['responder']['lowDataMode'])
            self.assertEqual(config['id'], 'banking-cloud-load-' + profile)
            stages = generator.get('stages') or generator['loadGroups'][0]['stages']
            self.assertEqual(sum(int(s['duration'].removesuffix('s')) for s in stages), duration)
            rules = {r['metricName']: r for r in config['rules']}
            self.assertEqual(rules['p95Latency']['value'], ceiling)
            self.assertEqual(rules['failTransactionCount']['value'], 0)
            self.assertEqual(rules['totalTransactionCount']['value'], 1)
            if profile != 'fraud-spike':
                self.assertFalse(generator.get('stages'))
                self.assertEqual(generator['loadGroups'][0]['goals'][0]['rule']['value'], ceiling)
                self.assertEqual(generator['loadGroups'][0]['goals'][0]['minSamples'], '1')

    def test_regression_keeps_full_data_and_response_assertions(self):
        config = json.loads((QUALITY / 'test-configs/banking-daily-replay.json').read_text())
        self.assertFalse(config['generator']['lowDataMode'])
        self.assertFalse(config['responder']['lowDataMode'])
        self.assertTrue(config['assertionGroups'])
        self.assertEqual(config['rules'][0]['metricName'], 'passAssertPct')
        self.assertEqual(config['rules'][0]['value'], 98)

    def test_http_complete_evidence_passes(self):
        config, report = self.report()
        guard.validate(report, config)

    def test_missing_or_failed_http_evidence_cannot_pass(self):
        for command in ('load_run', 'load_delivery', 'load_requests', 'p95Latency'):
            for missing in (True, False):
                config, report = self.report()
                if missing:
                    report['report']['goals'] = [g for g in report['report']['goals'] if g['command'] != command]
                else:
                    next(g for g in report['report']['goals'] if g['command'] == command)['status'] = 'FAIL'
                with self.subTest(command=command, missing=missing), self.assertRaises(ValueError):
                    guard.validate(report, config)

    def test_wrong_mode_or_loosened_latency_rule_cannot_pass(self):
        for section in ('generator', 'responder'):
            config, report = self.report()
            report['report']['actualConfig'][section]['lowDataMode'] = False
            with self.assertRaises(ValueError):
                guard.validate(report, config)
        config, report = self.report()
        report['report']['actualConfig']['generator']['loadGroups'][0]['goals'][0]['rule']['value'] = 10000
        with self.assertRaises(ValueError):
            guard.validate(report, config)

    def test_grpc_errors_latency_and_missing_measurements_cannot_pass(self):
        config, report = self.report('fraud-spike')
        table = {'total': {'count': 100, 'p95': 100}}
        guard.validate(report, config, table)
        for change in ({'count': 0}, {'p95': 501}, {'p95': None}, {'failedCount': 1}, {'noMatchCount': 1}, {'errorCount': 1}):
            changed = {'total': dict(table['total'], **change)}
            with self.subTest(change=change), self.assertRaises(ValueError):
                guard.validate(report, config, changed)


if __name__ == '__main__':
    unittest.main()
