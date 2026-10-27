"""Check generated write fields without relaxing business-value assertions."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/validate-generated-responses.py"
spec = importlib.util.spec_from_file_location("generated_responses", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def pair(number="123456789012", uuid="one"):
    body = {"accountNumber": number, "createdAt": "2026-10-27T12:00:01", "updatedAt": "2026-10-27T12:00:01"}
    return {"uuid": uuid, "ts": "2026-10-27T12:00:00Z", "direction": "IN", "tags": {"source": "generator"},
            "http": {"req": {"method": "POST", "url": "/api/accounts"},
                     "res": {"statusCode": 201, "bodyBase64": base64.b64encode(json.dumps(body).encode()).decode()}}}


class GeneratedResponseTests(unittest.TestCase):
    def test_accepts_current_timestamps_and_unique_account_numbers(self):
        self.assertEqual(2, module.validate([pair(), pair("987654321098", "two")], "accounts"))

    def test_repeated_export_of_same_response_is_counted_once(self):
        self.assertEqual(1, module.validate([pair(), pair()], "accounts"))

    def test_duplicate_or_malformed_numbers_cannot_pass(self):
        for pairs in [[pair(), pair(uuid="two")], [pair("short")], [pair("000000000000")]]:
            with self.subTest(pairs=len(pairs)), self.assertRaises(ValueError):
                module.validate(pairs, "accounts")

    def test_missing_writes_and_stale_timestamps_cannot_pass(self):
        with self.assertRaises(ValueError):
            module.validate([], "accounts")
        item = pair()
        body = json.loads(base64.b64decode(item["http"]["res"]["bodyBase64"]))
        body["createdAt"] = "2000-01-01T00:00:00"
        item["http"]["res"]["bodyBase64"] = base64.b64encode(json.dumps(body).encode()).decode()
        with self.assertRaises(ValueError):
            module.validate([item], "accounts")

class BusinessSQLTests(unittest.TestCase):
    def pair(self):
        return {"direction": "OUT", "tags": {"refUuid": "recorded"}, "postgres": {"request": {"execute": {"bindRequest": {"parseRequest": {"query": "select id from accounts_service.accounts where user_id=$1"}}}}}}

    def test_requires_exact_business_matches(self):
        self.assertEqual(1, module.validate_sql_matches([self.pair()], "accounts"))
        for tags in [{"refUuid": "recorded", "matchTier": "fingerprint"}, {}]:
            pair = self.pair()
            pair["tags"] = tags
            with self.assertRaises(ValueError):
                module.validate_sql_matches([pair], "accounts")
        with self.assertRaises(ValueError):
            module.validate_sql_matches([], "accounts")
