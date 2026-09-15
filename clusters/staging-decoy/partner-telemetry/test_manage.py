import copy
import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).with_name("manage.py")
spec = importlib.util.spec_from_file_location("manage", path)
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class FanoutTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "exporters": {"otlp": {}},
            "service": {
                "pipelines": {
                    "traces": {"exporters": ["otlp"]},
                    "logs": {"exporters": ["debug"]},
                }
            },
        }

    def test_destinations_are_independent_and_idempotent(self):
        config = copy.deepcopy(self.config)
        manage.fanout(config, "dynatrace", True)
        manage.fanout(config, "dynatrace", True)
        manage.fanout(config, "newrelic", True)
        self.assertEqual(config["service"]["pipelines"]["traces"]["exporters"].count("otlp/dynatrace-partner"), 1)
        self.assertIn("otlp/newrelic-partner", config["service"]["pipelines"]["logs"]["exporters"])
        manage.fanout(config, "dynatrace", False)
        self.assertNotIn("otlp/dynatrace-partner", config["exporters"])
        self.assertIn("otlp/newrelic-partner", config["exporters"])
        self.assertEqual(config["service"]["pipelines"]["logs"]["exporters"][0], "debug")


if __name__ == "__main__":
    unittest.main()
