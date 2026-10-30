"""Catch a stale Java CA even when the namespace PEM bundle is consistent."""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check-speedscale-certs.sh'


@unittest.skipUnless(all(shutil.which(x) for x in ['java', 'keytool', 'openssl']), 'Java and OpenSSL required')
class JavaTrustCanaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        for name in ['mock', 'unrelated']:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=Replay-' + name, '-keyout', str(cls.root / (name + '.key')),
                            '-out', str(cls.root / (name + '.crt'))], check=True, capture_output=True)
            subprocess.run(['keytool', '-importcert', '-noprompt', '-alias', 'fixture', '-file', str(cls.root / (name + '.crt')),
                            '-keystore', str(cls.root / (name + '.jks')), '-storepass', 'changeit'], check=True, capture_output=True)
        stub = cls.root / 'kubectl'
        stub.write_text('''#!/usr/bin/env bash
case "$*" in
 *jsonpath*cacerts*) printf '%s' "$TEST_JKS" ;;
 *jsonpath*) printf '%s' "$TEST_CERT" ;;
 *) printf '{}' ;;
esac
''')
        stub.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_canary(self, keystore):
        return subprocess.run(['bash', str(SCRIPT), 'banking-replay'], capture_output=True, text=True, timeout=15,
                              env=dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                                       TEST_CERT=base64.b64encode((self.root / 'mock.crt').read_bytes()).decode(),
                                       TEST_JKS=base64.b64encode((self.root / (keystore + '.jks')).read_bytes()).decode()))

    def test_valid_pem_cannot_hide_a_stale_java_truststore(self):
        result = self.run_canary('unrelated')
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn('bundle trusts its signing cert', result.stdout)
        self.assertIn('speedscale-jks cannot validate', result.stderr)

    def test_both_trust_paths_accept_the_mock_ca(self):
        result = self.run_canary('mock')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('speedscale-jks trusts its mock signing cert', result.stdout)
