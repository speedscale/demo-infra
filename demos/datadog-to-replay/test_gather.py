import base64
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import gather

TRACE = "a" * 32
BUCKET = "speedscale-demos-proxymock-gcs-validation"
LINK = f"https://console.cloud.google.com/storage/browser/{BUCKET}/byoc/api-gateway/{TRACE}"


def encode(item):
    if isinstance(item, dict):
        return {
            "kvlistValue": {
                "values": [{"key": k, "value": encode(v)} for k, v in item.items()]
            }
        }
    if isinstance(item, list):
        return {"arrayValue": {"values": [encode(v) for v in item]}}
    if isinstance(item, int):
        return {"intValue": str(item)}
    return {"stringValue": item}


def archive():
    logs = []
    for i, direction in enumerate(("IN", "OUT"), 1):
        record = {
            "msgType": "rrpair",
            "uuid": base64.b64encode(bytes([i]) * 16).decode(),
            "l7protocol": "http",
            "direction": direction,
            "http": {
                "req": {
                    "method": "GET",
                    "uri": "/api/accounts",
                    "headers": {"Accept": ["application/json"]},
                },
                "res": {
                    "statusCode": 200,
                    "bodyBase64": base64.b64encode(b'[{"balance":100}]').decode(),
                },
            },
        }
        logs.append({"traceId": TRACE, "body": encode(record)})
    return gzip.compress(
        json.dumps({"resourceLogs": [{"scopeLogs": [{"logRecords": logs}]}]}).encode()
    )


class GatherTests(unittest.TestCase):
    def test_rejects_foreign_capture_links(self):
        for link in [
            LINK.replace("console.cloud.google.com", "attacker.example"),
            LINK.replace(BUCKET, "other-bucket"),
            LINK + "/extra",
            LINK.replace(TRACE, "b" * 32),
        ]:
            with self.assertRaises(ValueError):
                gather.capture_prefix(link, BUCKET, "api-gateway", TRACE)

    def test_rejects_mixed_trace_payload(self):
        with self.assertRaises(ValueError):
            list(gather.decode_records(archive(), "b" * 32))

    def test_preserves_native_headers_and_response(self):
        records = list(gather.decode_records(archive(), TRACE))
        self.assertEqual(
            records[0]["http"]["req"]["headers"]["Accept"], ["application/json"]
        )
        self.assertEqual(records[0]["http"]["res"]["statusCode"], 200)
        self.assertEqual(
            base64.b64decode(records[0]["http"]["res"]["bodyBase64"]),
            b'[{"balance":100}]',
        )

    def test_query_to_test_and_mock_files(self):
        def search(site, headers, signal, query):
            self.assertEqual(headers["DD-API-KEY"], "partner-test")
            self.assertIn(TRACE, query)
            if signal == "logs":
                return [{"id": "log-id", "attributes": {"message": LINK}}]
            return [
                {"attributes": {"resource_name": "GET /api/accounts", "span_id": "123"}}
            ]

        def request(url, headers, body=None):
            self.assertEqual(headers, {"Authorization": "Bearer google-test"})
            if "alt=media" in url:
                return archive()
            return json.dumps(
                {"items": [{"name": f"byoc/api-gateway/{TRACE}/capture.gz"}]}
            ).encode()

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "capture"
            argv = ["gather.py", "--trace-id", TRACE, "--out", str(out)]
            env = {
                "DATADOG_PARTNER_API_KEY": "partner-test",
                "DATADOG_PARTNER_APP_KEY": "partner-app-test",
                "DATADOG_PARTNER_SITE": "datadoghq.com",
            }
            with patch.dict(os.environ, env), patch("sys.argv", argv), patch.object(
                gather, "search", side_effect=search
            ), patch.object(gather, "request", side_effect=request), patch.object(
                gather.subprocess, "check_output", return_value="google-test\n"
            ):
                gather.main()
            self.assertEqual(len(list((out / "tests").glob("*.json"))), 1)
            self.assertEqual(len(list((out / "mocks").glob("*.json"))), 1)
            self.assertEqual(
                json.loads((out / "provenance.json").read_text())["log_ids"], ["log-id"]
            )


if __name__ == "__main__":
    unittest.main()
