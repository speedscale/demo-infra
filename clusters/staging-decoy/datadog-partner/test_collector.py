import copy, json, pathlib, subprocess, tempfile, time, urllib.request, yaml

temporary = tempfile.TemporaryDirectory(prefix="microsvc-partner-")
p = pathlib.Path(temporary.name)
config = yaml.safe_load(pathlib.Path(__file__).with_name("collector.yaml").read_text())
config["exporters"] = {
    "file/gcs": {"path": "/test/gcs.json"},
    "file/dd": {"path": "/test/dd.json"},
}
for name, pipeline in config["service"]["pipelines"].items():
    pipeline["exporters"] = ["file/gcs" if name == "logs/gcs" else "file/dd"]
config["processors"]["transform/correlation"]["log_statements"][0]["statements"] = [
    s.replace("${env:DATADOG_PARTNER_GCS_BUCKET}", "test-partner-bucket")
    for s in config["processors"]["transform/correlation"]["log_statements"][0][
        "statements"
    ]
]
(p / "test.yaml").write_text(yaml.safe_dump(config))


def value(x):
    if isinstance(x, dict):
        return {
            "kvlistValue": {
                "values": [{"key": k, "value": value(v)} for k, v in x.items()]
            }
        }
    if isinstance(x, list):
        return {"arrayValue": {"values": [value(v) for v in x]}}
    if isinstance(x, bool):
        return {"boolValue": x}
    if isinstance(x, (float, int)):
        return {"doubleValue": x}
    return {"stringValue": str(x)}


b = {
    "namespace": "banking-app",
    "msgType": "rrpair",
    "l7protocol": "http",
    "service": "banking-transactions",
    "direction": "OUT",
    "http": {
        "req": {
            "uri": "/api/transactions/deposit",
            "headers": {
                "Traceparent": [
                    "00-11111111111111111111111111111111-2222222222222222-01"
                ],
                "Authorization": ["secret-marker"],
                "Cookie": ["secret-marker"],
            },
        },
        "res": {"headers": {"Set-Cookie": ["secret-marker"]}},
    },
}
logs = []
for ns, route, direction in [
    ("banking-app", "/api/transactions/deposit", "OUT"),
    ("banking-app", "/api/transactions/deposit", "IN"),
    ("production", "/api/transactions/deposit", "OUT"),
    ("banking-app", "/api/users/login", "OUT"),
]:
    x = copy.deepcopy(b)
    x["namespace"] = ns
    x["http"]["req"]["uri"] = route
    x["direction"] = direction
    logs.append(
        {
            "resource": {},
            "scopeLogs": [
                {
                    "logRecords": [
                        {"timeUnixNano": str(time.time_ns()), "body": value(x)}
                    ]
                }
            ],
        }
    )
image = "otel/opentelemetry-collector-contrib:0.160.0@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6"
cid = (
    subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--user",
            "0",
            "-p",
            "127.0.0.1:14318:4318",
            "-v",
            str(p) + ":/test",
            image,
            "--config=/test/test.yaml",
        ]
    )
    .decode()
    .strip()
)
try:
    for _ in range(20):
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    "http://localhost:14318/v1/logs",
                    data=json.dumps({"resourceLogs": logs}).encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=3,
            )
            break
        except OSError:
            time.sleep(0.3)
    else:
        raise RuntimeError("collector not ready")
    time.sleep(2)
finally:
    subprocess.run(["docker", "stop", cid], stdout=subprocess.DEVNULL, check=True)
s = (p / "dd.json").read_text()
assert "secret-marker" not in s, "Datadog output leaked session headers"
assert (
    "https://console.cloud.google.com/storage/browser/test-partner-bucket/byoc/transactions-service/11111111111111111111111111111111"
    in s
)
archive = (p / "gcs.json").read_text()
assert (
    "secret-marker" in archive
), "GCS archive must preserve the original captured headers"
assert (
    sum(
        len(scope["logRecords"])
        for line in archive.splitlines()
        for r in json.loads(line).get("resourceLogs", [])
        for scope in r["scopeLogs"]
    )
    == 2
)
rows = [r for line in s.splitlines() for r in json.loads(line).get("resourceLogs", [])]
records = [l for r in rows for scope in r["scopeLogs"] for l in scope["logRecords"]]
assert len(records) == 2, len(records)
assert all(x["traceId"] == "11111111111111111111111111111111" for x in records)
assert sum(x.get("spanId") == "2222222222222222" for x in records) == 1
assert all(
    any(
        a["key"] == "service.name"
        and a["value"]["stringValue"] == "transactions-service"
        for a in r["resource"]["attributes"]
    )
    for r in rows
)
print(
    "PASS: namespace and authentication filters, full GCS captures, link-only Datadog logs, service mapping, outbound span and inbound trace correlation"
)
