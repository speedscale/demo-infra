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
    if name == "traces":
        pipeline["exporters"].append("datadog/apm")
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
    resources = []
    for name, namespace in [("api-gateway", None), ("accounts-service", "banking-app"), ("api-gateway", "production"), ("unrelated", None)]:
        attrs = {"service.name": name}
        if namespace:
            attrs["service.namespace"] = namespace
        resources.append({
            "resource": {"attributes": [{"key": k, "value": value(v)} for k, v in attrs.items()]},
            "scopeSpans": [{"spans": [{
                "traceId": "11111111111111111111111111111111",
                "spanId": "2222222222222222", "name": name, "kind": 2,
                "startTimeUnixNano": str(time.time_ns()),
                "endTimeUnixNano": str(time.time_ns() + 1000000)
            }]}]
        })
    resources.append({
        "resource": {"attributes": [{"key": "service.name", "value": value("api-gateway")}]},
        "scopeSpans": [{"spans": [
            {
                "traceId": "33333333333333333333333333333333",
                "spanId": "4444444444444444", "name": "server-500", "kind": 2,
                "startTimeUnixNano": str(time.time_ns()),
                "endTimeUnixNano": str(time.time_ns() + 1000000),
                "attributes": [{"key": "http.status_code", "value": {"intValue": "500"}}],
            },
            {
                "traceId": "55555555555555555555555555555555",
                "spanId": "6666666666666666", "name": "exception-span", "kind": 1,
                "startTimeUnixNano": str(time.time_ns()),
                "endTimeUnixNano": str(time.time_ns() + 1000000),
                "events": [{
                    "timeUnixNano": str(time.time_ns()), "name": "exception",
                    "attributes": [
                        {"key": "exception.type", "value": value("java.lang.RuntimeException")},
                        {"key": "exception.message", "value": value("test failure")},
                        {"key": "exception.stacktrace", "value": value("example.Test.run(Test.java:10)")},
                    ],
                }],
            },
            {
                "traceId": "77777777777777777777777777777777",
                "spanId": "8888888888888888", "name": "client-400", "kind": 2,
                "startTimeUnixNano": str(time.time_ns()),
                "endTimeUnixNano": str(time.time_ns() + 1000000),
                "attributes": [{"key": "http.status_code", "value": {"intValue": "400"}}],
            },
        ]}]
    })
    urllib.request.urlopen(urllib.request.Request(
        "http://localhost:14318/v1/traces",
        data=json.dumps({"resourceSpans": resources}).encode(),
        headers={"Content-Type": "application/json"}), timeout=3)
    time.sleep(20)
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

spans = [span for line in s.splitlines()
         for resource in json.loads(line).get("resourceSpans", [])
         for scope in resource["scopeSpans"] for span in scope["spans"]]
assert sorted(span["name"] for span in spans) == ["accounts-service", "api-gateway", "client-400", "exception-span", "server-500"]
print("PASS: banking Java spans without namespace accepted; other namespaces and services rejected")

statuses = {span["name"]: span.get("status", {}).get("code", 0) for span in spans}
assert statuses["server-500"] == 2
assert statuses["exception-span"] == 2
assert statuses["client-400"] != 2
exception = next(span for span in spans if span["name"] == "exception-span")
error_attributes = {item["key"]: item["value"]["stringValue"] for item in exception["attributes"] if item["key"].startswith("error.")}
assert error_attributes == {
    "error.type": "java.lang.RuntimeException",
    "error.message": "test failure",
    "error.stack": "example.Test.run(Test.java:10)",
}
print("PASS: 5xx and exception spans are errors with details; client 4xx remains non-error")

assert any(json.loads(line).get("resourceMetrics") for line in s.splitlines()), "APM statistics missing"
print("PASS: Datadog connector emits APM statistics from accepted banking spans")
