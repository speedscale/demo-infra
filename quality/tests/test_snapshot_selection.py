"""Check production snapshot selection without cluster or cloud access."""
import os
from pathlib import Path
import subprocess
import unittest

QUALITY = Path(__file__).resolve().parents[1]
SCRIPT = (QUALITY / 'scripts/run-proxymock-scenario.sh').read_text()
GET_VALUE = SCRIPT.split('get_config_value() {', 1)[1].split('\n}', 1)[0]
SELECT = SCRIPT.split('name=$(get_config_value', 1)[1].split('if [ -z "$name" ]', 1)[0]


class SnapshotSelectionTest(unittest.TestCase):
    def select(self, cluster, service):
        shell = 'set -eu\nget_config_value() {' + GET_VALUE + '\n}\nname=$(get_config_value' + SELECT
        shell += '\nprintf "%s" "$snapshot_id"\n'
        result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True,
                                env=dict(os.environ, CLUSTER_NAME=cluster,
                                         CONFIG_FILE=str(QUALITY / 'speedctl-replay' / (service + '.yaml'))))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_gateway_staging_uses_staging_snapshot(self):
        self.assertEqual(self.select('staging-decoy', 'banking-gateway'),
                         '6c480801-53d2-4c10-b6a0-2089de176ce9')

    def test_existing_dev_overrides_are_preserved(self):
        expected = {
            'banking-gateway': '3e22c0d6-c2aa-4403-b3d4-79342d4a253b',
            'banking-user': '30546f66-7ea4-4893-be71-af818e568635',
            'banking-accounts': '67205c03-8f0a-4d88-b00e-c69a01aecaee',
            'banking-transactions': 'd77982d8-6804-451a-a61a-5e7dc385c64c',
        }
        for service, snapshot in expected.items():
            with self.subTest(service=service):
                self.assertEqual(self.select('dev-decoy', service), snapshot)

    def test_all_staging_services_use_their_own_snapshot(self):
        for config in (QUALITY / 'speedctl-replay').glob('*.yaml'):
            fields = dict(line.split(': ', 1) for line in config.read_text().splitlines()
                          if ': ' in line and not line.startswith('#'))
            with self.subTest(service=config.stem):
                self.assertEqual(self.select('staging-decoy', config.stem),
                                 fields.get('stagingProxymockSnapshotID', fields.get('stagingSnapshotID', fields['snapshotID'])))


if __name__ == '__main__':
    unittest.main()
