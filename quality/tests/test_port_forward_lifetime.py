"""A dead tunnel must stop traffic promptly without retrying the replay."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run-with-port-forward.sh"


class ForwardLifetimeTest(unittest.TestCase):
    def run_replay(self, forward_seconds, replay_seconds, exit_code):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "forward.log").write_text("lost connection to pod\n")
            shell = '''sleep "$1" &
pf=$!
trap 'kill "$pf" 2>/dev/null || true' EXIT
bash "$2" "$pf" "$3/forward.log" bash -c 'echo replay >> "$1/count"; if [ "$3" = 0 ]; then exec sleep "$2"; fi; sleep "$2"; exit "$3"' test "$3" "$4" "$5"
'''
            result = subprocess.run(["bash", "-c", shell, "test", str(forward_seconds),
                                     str(SCRIPT), tmp, str(replay_seconds), str(exit_code)],
                                    capture_output=True, text=True, timeout=5,
                                    env=dict(os.environ, PROXYMOCK_FORWARD_CHECK_INTERVAL="0.02"))
            self.assertEqual((root / "count").read_text().splitlines(), ["replay"])
            return result

    def test_exit_status_is_preserved_with_a_live_tunnel(self):
        self.assertEqual(self.run_replay(3, 0.05, 0).returncode, 0)
        self.assertEqual(self.run_replay(3, 0.05, 7).returncode, 7)

    def test_tunnel_death_stops_the_long_replay_and_retains_its_reason(self):
        result = self.run_replay(0.05, 20, 0)
        self.assertEqual(result.returncode, 1)
        self.assertIn("target port-forward exited", result.stderr)
        self.assertIn("lost connection to pod", result.stderr)


if __name__ == "__main__":
    unittest.main()
