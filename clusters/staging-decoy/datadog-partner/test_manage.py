import copy
import unittest
import manage


class FanoutTests(unittest.TestCase):
    def test_on_off_preserves_existing_pipelines(self):
        original = {
            "exporters": {"otlp": {"endpoint": "jaeger:4317"}, "loki": {}},
            "service": {
                "pipelines": {
                    "traces": {"exporters": ["otlp"]},
                    "logs": {"exporters": ["loki"]},
                    "logs/custom": {"exporters": ["custom"]},
                    "metrics": {"exporters": ["prometheus"]},
                }
            },
        }
        config = manage.fanout(copy.deepcopy(original), True)
        self.assertEqual(
            config["service"]["pipelines"]["logs"]["exporters"],
            ["loki", "otlp/datadog-partner"],
        )
        self.assertEqual(manage.fanout(copy.deepcopy(config), True), config)
        self.assertEqual(manage.fanout(config, False), original)
        self.assertEqual(manage.fanout(copy.deepcopy(original), False), original)


if __name__ == "__main__":
    unittest.main()
