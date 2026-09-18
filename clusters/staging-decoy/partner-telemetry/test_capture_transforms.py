import json
import pathlib
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]
IMAGE = "otel/opentelemetry-collector-contrib:0.160.0@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6"
TARGETS = [
    ("datadog", ROOT / "datadog-partner/collector.yaml", "logs/correlation"),
    ("newrelic", ROOT / "byoc-newrelic/otel.yaml", "logs"),
    ("dynatrace", ROOT / "byoc-dynatrace/otel.yaml", "logs"),
]


def otlp_value(item):
    if isinstance(item, dict):
        return {
            "kvlistValue": {
                "values": [
                    {"key": key, "value": otlp_value(value)}
                    for key, value in item.items()
                ]
            }
        }
    if isinstance(item, list):
        return {"arrayValue": {"values": [otlp_value(value) for value in item]}}
    if isinstance(item, bool):
        return {"boolValue": item}
    if isinstance(item, int):
        return {"intValue": str(item)}
    return {"stringValue": str(item)}


records = [
    {
        "namespace": "banking-app",
        "msgType": "rrpair",
        "l7protocol": "https",
        "service": "banking-ai",
        "direction": "OUT",
        "uuid": "https/capture==",
        "command": "POST",
        "status": "200",
        "netinfo": {
            "upstream": {"hostname": "api.anthropic.com", "port": 443}
        },
        "http": {
            "req": {
                "method": "POST",
                "uri": "/v1/messages",
                "headers": {
                    "Traceparent": [
                        "00-11111111111111111111111111111111-2222222222222222-01"
                    ]
                },
            },
            "res": {"statusCode": 200},
        },
    },
    {
        "namespace": "banking-app",
        "msgType": "rrpair",
        "l7protocol": "postgres",
        "service": "banking-accounts",
        "direction": "OUT",
        "uuid": "postgres/capture==",
        "command": "Execute Prepared Statement",
        "status": "OK",
        "netinfo": {
            "upstream": {
                "hostname": "banking-postgres.banking-app.svc.cluster.local",
                "port": 5432,
            }
        },
        "postgres": {"request": {"execute": {}}, "response": {"execute": {}}},
    },
    {
        "namespace": "banking-app",
        "msgType": "rrpair",
        "l7protocol": "kafka",
        "service": "banking-notification",
        "direction": "OUT",
        "uuid": "kafka/capture==",
        "command": "Fetch",
        "status": "OK",
        "netinfo": {
            "upstream": {
                "hostname": "banking-kafka.banking-app.svc.cluster.local",
                "port": 9092,
            }
        },
        "kafka": {"request": {}, "response": {}},
    },
]
payload = {
    "resourceLogs": [
        {
            "resource": {},
            "scopeLogs": [
                {
                    "scope": {"name": "speedscale/rrpair"},
                    "logRecords": [
                        {
                            "timeUnixNano": str(time.time_ns() + offset),
                            "body": otlp_value(record),
                        }
                        for offset, record in enumerate(records)
                    ],
                }
            ],
        }
    ]
}


def attribute_map(items):
    return {
        item["key"]: next(iter(item["value"].values()))
        for item in items
    }


for offset, (name, path, pipeline_name) in enumerate(TARGETS):
    with tempfile.TemporaryDirectory(prefix=f"partner-capture-{name}-") as folder:
        directory = pathlib.Path(folder)
        config = yaml.safe_load(
            path.read_text().replace(
                "${env:DATADOG_PARTNER_GCS_BUCKET}", "test-partner-bucket"
            )
        )
        pipeline = config["service"]["pipelines"][pipeline_name]
        pipeline["exporters"] = ["file/test"]
        config["service"]["pipelines"] = {pipeline_name: pipeline}
        config["exporters"] = {"file/test": {"path": "/test/output.json"}}
        config.pop("connectors", None)
        (directory / "config.yaml").write_text(yaml.safe_dump(config))

        port = 15330 + offset
        collector = subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "--user",
                "0",
                "-p",
                f"127.0.0.1:{port}:4318",
                "-v",
                f"{directory}:/test",
                IMAGE,
                "--config=/test/config.yaml",
            ],
            text=True,
        ).strip()
        collector_logs = ""
        try:
            last_error = None
            for _ in range(30):
                try:
                    response = urllib.request.urlopen(
                        urllib.request.Request(
                            f"http://127.0.0.1:{port}/v1/logs",
                            data=json.dumps(payload).encode(),
                            headers={"Content-Type": "application/json"},
                        ),
                        timeout=2,
                    )
                    if response.status == 200:
                        break
                except (OSError, urllib.error.HTTPError) as error:
                    last_error = error
                    time.sleep(0.2)
            else:
                raise RuntimeError(f"{name} collector rejected test data: {last_error}")
            time.sleep(2)
            collector_logs = subprocess.check_output(
                ["docker", "logs", collector], stderr=subprocess.STDOUT, text=True
            )
        finally:
            subprocess.run(
                ["docker", "stop", collector], stdout=subprocess.DEVNULL, check=True
            )

        assert "failed processing logs" not in collector_logs, collector_logs
        exported = [
            json.loads(line)
            for line in (directory / "output.json").read_text().splitlines()
        ]
        output = {}
        for batch in exported:
            for resource_logs in batch.get("resourceLogs", []):
                resource = attribute_map(
                    resource_logs.get("resource", {}).get("attributes", [])
                )
                for scope in resource_logs.get("scopeLogs", []):
                    for log in scope.get("logRecords", []):
                        attributes = attribute_map(log.get("attributes", []))
                        output[attributes["speedscale.workload"]] = {
                            "body": next(iter(log["body"].values())),
                            "attributes": attributes,
                            "resource": resource,
                            "trace_id": log.get("traceId"),
                        }

        assert set(output) == {
            "banking-ai",
            "banking-accounts",
            "banking-notification",
        }, output
        expected = {
            "banking-ai": {
                "body": "OUT POST /v1/messages",
                "service": "ai-service",
                "destination": "api.anthropic.com",
                "protocol": "https",
                "command": "POST",
                "status": "200",
                "trace_id": "11111111111111111111111111111111",
            },
            "banking-accounts": {
                "body": "OUT postgres Execute Prepared Statement",
                "service": "accounts-service",
                "destination": "banking-postgres.banking-app.svc.cluster.local",
                "protocol": "postgres",
                "command": "Execute Prepared Statement",
                "status": "OK",
                "trace_id": None,
            },
            "banking-notification": {
                "body": "OUT kafka Fetch",
                "service": "notification-service",
                "destination": "banking-kafka.banking-app.svc.cluster.local",
                "protocol": "kafka",
                "command": "Fetch",
                "status": "OK",
                "trace_id": None,
            },
        }
        for workload, wanted in expected.items():
            actual = output[workload]
            assert actual["body"] == wanted["body"], actual
            assert actual["resource"]["service.name"] == wanted["service"], actual
            assert actual["attributes"]["hostname"] == wanted["destination"], actual
            assert actual["attributes"]["server.address"] == wanted["destination"], actual
            assert (
                actual["attributes"]["network.peer.address"]
                == wanted["destination"]
            ), actual
            assert actual["attributes"]["msgType"] == "rrpair", actual
            assert (
                actual["attributes"]["speedscale.protocol"] == wanted["protocol"]
            ), actual
            assert (
                actual["attributes"]["speedscale.command"] == wanted["command"]
            ), actual
            assert actual["attributes"]["speedscale.status"] == wanted["status"], actual
            assert actual["trace_id"] == wanted["trace_id"], actual
        print(f"PASS: {name} formats and attributes HTTPS, PostgreSQL, and Kafka captures")
