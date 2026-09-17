import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import manage
import render


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.destinations = render.load_destinations()
        self.by_name = {item["name"]: item for item in self.destinations}

    def test_default_destinations_are_independent(self):
        config = render.build_config(self.destinations)
        self.assertTrue(manage.configured(config, self.by_name["datadog"]))
        self.assertTrue(manage.configured(config, self.by_name["dynatrace"]))
        self.assertFalse(manage.configured(config, self.by_name["newrelic"]))
        self.assertNotIn("otlp/datadog", config["service"]["pipelines"]["metrics"]["exporters"])
        self.assertIn("otlp/dynatrace", config["service"]["pipelines"]["metrics"]["exporters"])

    def test_toggle_preserves_other_vendors_and_signal_choices(self):
        config = render.build_config(self.destinations)
        render.set_destination(config, self.by_name["newrelic"], True)
        render.set_destination(config, self.by_name["datadog"], False)
        self.assertTrue(manage.configured(config, self.by_name["newrelic"]))
        self.assertFalse(manage.configured(config, self.by_name["datadog"]))
        self.assertTrue(manage.configured(config, self.by_name["dynatrace"]))
        self.assertNotIn("otlp/newrelic", config["service"]["pipelines"]["logs"]["exporters"])
        self.assertIn("otlp/newrelic", config["service"]["pipelines"]["logs/captures"]["exporters"])

    def test_toggle_is_idempotent(self):
        config = render.build_config(self.destinations)
        destination = self.by_name["dynatrace"]
        render.set_destination(config, destination, True)
        render.set_destination(config, destination, True)
        for signal in destination["signals"]:
            exporters = config["service"]["pipelines"][render.SIGNAL_PIPELINES[signal]]["exporters"]
            self.assertEqual(exporters.count("otlp/dynatrace"), 1)

    def test_duplicate_vendor_is_rejected(self):
        destinations = copy.deepcopy(self.destinations)
        destinations.append(copy.deepcopy(destinations[0]))
        with self.assertRaises(ValueError):
            render.build_config(destinations)


if __name__ == "__main__":
    unittest.main()
