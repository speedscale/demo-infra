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

class DatabaseContractTests(unittest.TestCase):
    def test_account_contracts_cover_users_types_writes_and_errors(self):
        pairs = module.database.accounts_pairs()
        inbound = [p for p in pairs if p["direction"] == "IN"]
        self.assertEqual(53, len(inbound))
        self.assertEqual({200, 201, 404, 500}, {p["http"]["res"]["statusCode"] for p in inbound})
        creates = [p for p in inbound if p["http"]["req"]["method"] == "POST"]
        bodies = [json.loads(base64.b64decode(p["http"]["req"]["bodyBase64"])) for p in creates]
        self.assertEqual({"CHECKING", "SAVINGS", "CREDIT"}, {p["accountType"] for p in bodies})
        self.assertEqual(9, len(creates))
        for p in inbound:
            r = p["http"]["req"]
            if r["method"] == "GET" and r["url"] == "/api/accounts" and p["http"]["res"]["statusCode"] == 200:
                self.assertIsInstance(json.loads(base64.b64decode(p["http"]["res"]["bodyBase64"])), list)

    def test_transactions_cover_success_and_real_rejection_paths(self):
        pairs = module.database.transactions_pairs()
        inbound = [p for p in pairs if p["direction"] == "IN"]
        self.assertEqual(33, len(inbound))
        self.assertEqual({200, 201, 400, 500}, {p["http"]["res"]["statusCode"] for p in inbound})
        self.assertEqual(18, sum(p["http"]["res"]["statusCode"] == 201 for p in inbound))
        for route in ["deposit", "withdraw", "transfer", "create"]:
            self.assertTrue(any(p["location"] == "/api/transactions/" + route for p in inbound))
        keys = {p["kafka"]["request"]["apiKey"] for p in pairs if "kafka" in p}
        self.assertEqual({"KAFKA_API_KEY_API_VERSIONS", "KAFKA_API_KEY_METADATA", "KAFKA_API_KEY_INIT_PRODUCER_ID", "KAFKA_API_KEY_PRODUCE"}, keys)
        self.assertTrue(any(p.get("http", {}).get("res", {}).get("trailers", {}).get("Grpc-Status") == ["0"] for p in pairs))

    def test_database_rows_have_types_and_consistent_shapes(self):
        for builder in [module.database.accounts_pairs, module.database.transactions_pairs]:
            for pair in builder():
                pg = pair.get("postgres", {})
                execute = pg.get("request", {}).get("execute")
                if not execute:
                    continue
                bind = execute["bindRequest"]
                fields = bind["parseRequest"]["describedRow"]["fields"]
                for message in pg.get("response", {}).get("execute", {}).get("messages", []):
                    if "dataRow" in message:
                        self.assertEqual(len(fields), len(message["dataRow"]["values"]))
                        self.assertTrue(all(field["dataTypeOid"] for field in fields))
