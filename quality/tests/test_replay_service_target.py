"""Service ports must be explicit and must exist before launching traffic."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

QUALITY = Path(__file__).resolve().parents[1]
SOURCE = (QUALITY / 'scripts/run-replay.sh').read_text()
HELPERS = '\n'.join(re.search(r'^' + name + r'\(\) \{.*?^\}', SOURCE, re.M | re.S).group()
                    for name in ['get_config_value', 'replay_service_target', 'replay_service_routes'])


class ReplayServiceTargetTest(unittest.TestCase):
    def target(self, service, port, service_port=80, api_error=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'replay.yaml'
            config.write_text(f'name: {service}\nnamespace: banking-replay\nservice: {service}\nservicePort: {port}\n')
            shell = '''set -euo pipefail
error() { echo "$*" >&2; }
kubectl() {
 if [ "$API_ERROR" = true ]; then return 1; fi
 printf '%s' "$SERVICE_JSON"
}
'''
            return subprocess.run(['bash', '-c', shell + HELPERS + '\nreplay_service_target "$1"',
                                   'test', str(config)], capture_output=True, text=True,
                                  env=dict(os.environ, API_ERROR=str(api_error).lower(),
                                           SERVICE_JSON=json.dumps({'spec': {'ports': [
                                               {'port': service_port, 'targetPort': 8080}]}})))

    def test_recorded_container_port_does_not_replace_service_port(self):
        result = self.target('banking-transactions', 80)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'banking-transactions=banking-transactions:80')
        result = self.target('banking-transactions', 8080)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not expose Service port 8080', result.stderr)

    def test_grpc_service_keeps_its_exposed_port(self):
        result = self.target('banking-fraud', 8080, service_port=8080)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'banking-fraud=banking-fraud:8080')

    def test_invalid_ports_and_api_failure_fail_closed(self):
        for port in ['', '0', '65536', 'abc']:
            with self.subTest(port=port):
                self.assertNotEqual(self.target('banking-transactions', port).returncode, 0)
        self.assertNotEqual(self.target('banking-transactions', 80, api_error=True).returncode, 0)

    def test_routes_use_exact_keys_and_omit_excluded_user_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'user.yaml'
            config.write_text('name: banking-user\n')
            shell = '''set -euo pipefail
speedctl_cmd() { printf '%s' "$SNAPSHOT_JSON"; }
'''
            snapshot = {'inTraffic': {'banking-user:banking-user:80': {},
                                      'banking-user:banking-user:8080': {},
                                      'another:another:8080': {}}}
            command = '\nreplay_service_routes "$1" fixture banking-user=banking-user:80 banking-user:banking-user:80'
            result = subprocess.run(['bash', '-c', shell + HELPERS + command, 'test', str(config)],
                                    capture_output=True, text=True,
                                    env=dict(os.environ, SNAPSHOT_JSON=json.dumps(snapshot)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'banking-user:banking-user:8080=http://banking-user:80\n')
            snapshot['inTraffic'] = {}
            result = subprocess.run(['bash', '-c', shell + HELPERS + command, 'test', str(config)],
                                    capture_output=True, text=True,
                                    env=dict(os.environ, SNAPSHOT_JSON=json.dumps(snapshot)))
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
