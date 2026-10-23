"""Exercise readiness with real HTTP failures and delayed first responses."""
import http.server
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/wait-for-proxymock-readiness.py'


class HTTPReadinessTest(unittest.TestCase):
    def run_probe(self, statuses, delay=0, auth=True, method='GET', deadline=1):
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                index = len(requests)
                requests.append((self.path, self.headers.get('Authorization')))
                if index == 0 and delay:
                    time.sleep(delay)
                status = statuses[min(index, len(statuses) - 1)]
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/redirected')
                self.end_headers()
                try:
                    self.wfile.write(b'[]')
                except BrokenPipeError:
                    pass
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                header = 'Authorization: Bearer test.token.value\n' if auth else ''
                Path(tmp, 'request.md').write_text(f'''### REQUEST ###
```
{method} http://unused.invalid/api/transactions?limit=1
{header}```
### RESPONSE ###
```
HTTP/1.1 200 OK
```
### METADATA ###
```
direction: IN
```
''')
                result = subprocess.run([sys.executable, str(SCRIPT), tmp,
                    f'http://127.0.0.1:{server.server_port}', '/api/transactions',
                    '--timeout', '.05', '--deadline', str(deadline), '--interval', '.01'],
                    capture_output=True, text=True)
                return result, requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_uses_the_target_and_recorded_authorization(self):
        result, requests = self.run_probe([200])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(requests, [('/api/transactions?limit=1', 'Bearer test.token.value')] * 2)
        self.assertNotIn('test.token.value', result.stdout + result.stderr)

    def test_waits_for_two_successes_after_a_server_failure(self):
        result, requests = self.run_probe([503, 200])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(requests), 3)

    def test_recovers_from_a_slow_first_request(self):
        result, requests = self.run_probe([200], delay=.15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(len(requests), 3)

    def test_persistent_failure_stops_at_the_deadline(self):
        result, _ = self.run_probe([503], deadline=.15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('deadline', result.stderr)

    def test_auth_failure_is_not_treated_as_readiness(self):
        result, requests = self.run_probe([401])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(requests), 1)

    def test_does_not_forward_authorization_through_redirects(self):
        result, requests = self.run_probe([302], deadline=.15)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(all(path == '/api/transactions?limit=1' for path, _ in requests))

    def test_local_probe_does_not_use_environment_proxies(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1',
                                    'http_proxy': 'http://127.0.0.1:1',
                                    'NO_PROXY': '', 'no_proxy': ''}):
            result, _ = self.run_probe([200])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_auth_or_a_write_request_is_not_replayed(self):
        for options in ({'auth': False}, {'method': 'POST'}):
            with self.subTest(options=options):
                result, requests = self.run_probe([200], **options)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
