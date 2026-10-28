"""Verify snapshot transform scopes and key mounts before spending a replay run."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

QUALITY = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("replay_snapshot", QUALITY / "scripts/validate-replay-snapshot.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReplaySnapshotTests(unittest.TestCase):
    def snapshot(self, name):
        config = json.loads((QUALITY / "transforms" / (name + ".json")).read_text())
        return {"status": "Complete", "tokenConfigId": config["id"], "tokenizerConfig": config}, copy.deepcopy(config)

    def test_accepts_all_replay_templates(self):
        for name in ["banking-jwt-resign", "banking-user-replay", "banking-accounts-replay", "banking-transactions-replay", "banking-gateway-replay"]:
            with self.subTest(name=name):
                module.validate(*self.snapshot(name))

    def test_missing_or_excluding_identity_scope_is_rejected(self):
        snapshot, expected = self.snapshot("banking-gateway-replay")
        snapshot["tokenizerConfig"]["responder"][0]["filters"]["filters"][1]["include"] = False
        with self.assertRaises(ValueError):
            module.validate(snapshot, expected)

    def test_sql_rules_do_not_require_an_unset_live_direction(self):
        for name in ["banking-user-replay", "banking-accounts-replay", "banking-transactions-replay"]:
            snapshot, expected = self.snapshot(name)
            for chain in expected["responder"]:
                self.assertIn(chain["extractor"]["type"], ["sql_query", "json_path"])
                self.assertFalse(any("direction" in predicate for predicate in chain.get("filters", {}).get("filters", [])))
