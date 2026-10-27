"""Keep deterministic regression contracts broad enough to exercise routing and identity."""
import base64
import importlib.util
import json
from pathlib import Path
import unittest

BUILDER = Path(__file__).resolve().parents[1] / "fixtures/build-regression-fixtures.py"
spec = importlib.util.spec_from_file_location("regression_fixtures", BUILDER)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RegressionFixtureTests(unittest.TestCase):
    def test_gateway_covers_two_identities_writes_health_and_dependency_failures(self):
        pairs = module.gateway_pairs()
        inbound = [r for r in pairs if r["direction"] == "IN"]
        self.assertEqual(27, len(inbound))
        self.assertEqual({200, 201, 400, 401, 404, 503}, {r["http"]["res"]["statusCode"] for r in inbound})
        profiles = [r for r in inbound if r["location"] == "/api/users/profile" and r["http"]["res"]["statusCode"] == 200]
        self.assertEqual(2, len(profiles))
        identities = set()
        for pair in profiles:
            token = pair["http"]["req"]["headers"]["Authorization"][0].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
            body = json.loads(base64.b64decode(pair["http"]["res"]["bodyBase64"]))
            self.assertEqual(claims["sub"], body["username"])
            self.assertEqual(claims["userId"], str(body["id"]))
            identities.add(claims["sub"])
        self.assertEqual(2, len(identities))
        for operation in ["create", "deposit", "withdraw", "transfer"]:
            self.assertTrue(any(r["location"] == "/api/transactions/" + operation and r["http"]["res"]["statusCode"] == 201 for r in inbound))
        self.assertTrue(all(r["http"]["req"]["host"].startswith("banking-") for r in pairs if r["direction"] == "OUT"))

    def test_ai_keeps_context_locale_and_validation_cases(self):
        inbound = [r for r in module.ai_pairs() if r["direction"] == "IN"]
        bodies = [json.loads(base64.b64decode(r["http"]["req"]["bodyBase64"])) for r in inbound if r["http"]["req"].get("bodyBase64")]
        self.assertTrue(any(b.get("accountContext") for b in bodies))
        self.assertTrue(any(b.get("locale") == "es-ES" for b in bodies))
        self.assertTrue(any(r["http"]["res"]["statusCode"] == 422 for r in inbound))
