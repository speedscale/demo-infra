import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/install-latest-tool.sh'


class LatestToolInstallTests(unittest.TestCase):
    def run_install(self, download_exit=0, install_exit=0, version_exit=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            curl = bin_dir / 'curl'
            curl.write_text('''#!/usr/bin/env bash
set -eu
while [[ "$1" != "-o" ]]; do shift; done
printf '#!/usr/bin/env bash\\nexit %s\\n' "$INSTALL_EXIT" > "$2"
exit "$DOWNLOAD_EXIT"
''')
            curl.chmod(0o755)
            tool = root / 'speedctl'
            tool.write_text('#!/usr/bin/env bash\necho "speedctl v2.5.test"\nexit "$VERSION_EXIT"\n')
            tool.chmod(0o755)
            env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}',
                       INSTALLROOT=str(root), DOWNLOAD_EXIT=str(download_exit),
                       INSTALL_EXIT=str(install_exit), VERSION_EXIT=str(version_exit),
                       GITHUB_PATH=str(root / 'path'), GITHUB_STEP_SUMMARY=str(root / 'summary'))
            result = subprocess.run(['bash', str(SCRIPT), 'speedctl'], env=env,
                                    capture_output=True, text=True)
            summary = (root / 'summary').read_text() if (root / 'summary').exists() else ''
            return result, summary

    def test_download_failure_does_not_accept_existing_binary(self):
        result, summary = self.run_install(download_exit=22)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(summary, '')

    def test_failed_install_or_version_check_fails_job(self):
        for kwargs in ({'install_exit': 1}, {'version_exit': 1}):
            with self.subTest(**kwargs):
                result, summary = self.run_install(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(summary, '')

    def test_success_records_client_version(self):
        result, summary = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('speedctl v2.5.test', summary)
