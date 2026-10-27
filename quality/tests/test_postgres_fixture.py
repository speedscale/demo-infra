"""Keep business regression traffic and valid database outcomes during fixture repair."""
import copy
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/repair-postgres-fixture.py"
spec = importlib.util.spec_from_file_location("repair_fixture", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PostgresFixtureTests(unittest.TestCase):
    def test_repair_preserves_inbound_requests_and_recorded_database_errors(self):
        inbound = {"direction": "IN", "uuid": "original", "http": {"req": {"uri": "/create"}}}
        error = {"direction": "OUT", "postgres": {"request": {"bind": {}}, "response": {"bind": {"error": {"code": "23505"}}}}}
        complete = {"direction": "OUT", "postgres": {"request": {"bind": {}}, "response": {"bind": {"complete": {}}}}}
        missing = {"direction": "OUT", "postgres": {"request": {"bind": {}}, "response": {"bind": {}}}}
        pairs = [inbound, error, missing, complete]
        original = copy.deepcopy(pairs)
        repaired, removed = module.repair(pairs)
        self.assertEqual([inbound, error, complete], repaired)
        self.assertEqual(1, sum(removed.values()))
        self.assertEqual(original, pairs)
        self.assertIn(error, module.repair(pairs, metadata={}, replace_setup=True)[0])

    def test_empty_query_response_is_only_valid_for_an_empty_query(self):
        def execute(sql):
            return {"direction": "OUT", "postgres": {"request": {"execute": {"bindRequest": {"parseRequest": {"query": sql}}}},
                                                        "response": {"execute": {"messages": [{"empty": {}}]}}}}
        empty, corrupt = execute(" ;\n"), execute("select id from users")
        repaired, removed = module.repair([empty, corrupt])
        self.assertEqual([empty], repaired)
        self.assertEqual(1, sum(removed.values()))

    def test_catalog_is_added_once_without_synthesizing_business_responses(self):
        catalog = {"direction": "OUT", "postgres": {"request": {"query": {"query": "SELECT version();"}},
                                                     "response": {"query": {"messages": [{"complete": {}}]}}}}
        repaired, _ = module.repair([], catalog)
        self.assertEqual([catalog], repaired)
        self.assertEqual([catalog], module.repair(repaired, catalog)[0])

    def test_protocol_setup_repair_is_repeatable_and_uses_only_schema_metadata(self):
        query = "select id from users where id=$1"
        execute = {"uuid": "recorded-execution", "direction": "OUT", "tags": {},
                   "postgres": {"request": {"execute": {"bindRequest": {"parseRequest": {"query": query}, "resultFormatCodes": [1]}}},
                                "response": {"execute": {"messages": [{"complete": {"tag": "SELECT 1"}}]}}}}
        metadata = {query: {"Fields": [{"Name": "id", "TableOID": 0, "TableAttributeNumber": 0, "DataTypeOID": 20,
                                        "DataTypeSize": 8, "TypeModifier": -1}]}}
        repaired, _ = module.repair([execute], metadata=metadata)
        self.assertEqual(4, len(repaired))
        self.assertEqual(repaired, module.repair(repaired, metadata=metadata)[0])
        self.assertEqual(execute, repaired[0])
        describe = repaired[-1]["postgres"]["response"]["describe"]["messages"][0]["rowDescription"]
        self.assertEqual(1, describe["fields"][0]["format"])

    def test_schema_repair_removes_wrong_width_rows_and_rebuilds_setup(self):
        query = "select id from users"
        good = {"uuid": "one", "direction": "OUT", "postgres": {
            "request": {"execute": {"bindRequest": {"parseRequest": {"query": query}}}},
            "response": {"execute": {"messages": [{"dataRow": {"values": [{"asString": "7"}]}}, {"complete": {"tag": "SELECT 1"}}]}}}}
        bad = copy.deepcopy(good)
        bad["postgres"]["response"]["execute"]["messages"][0]["dataRow"]["values"].append({"asString": "unexpected"})
        inbound = {"direction": "IN", "uuid": "inbound"}
        metadata = {query: {"Fields": [{"Name": "id", "TableOID": 0, "TableAttributeNumber": 0, "DataTypeOID": 20, "DataTypeSize": 8, "TypeModifier": -1}]}}
        repaired, removed = module.repair([inbound, good, bad], metadata=metadata, replace_setup=True)
        self.assertEqual([inbound, good], repaired[:2])
        self.assertEqual(1, removed["row width differs from schema"])
        self.assertEqual(repaired, module.repair(repaired, metadata=metadata, replace_setup=True)[0])
