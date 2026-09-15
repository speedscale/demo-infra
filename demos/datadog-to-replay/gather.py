#!/usr/bin/env python3
"""Resolve partner Datadog capture links into trace-scoped proxymock tests and mocks."""

import argparse
import base64
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import uuid

SITES = {
    "datadoghq.com",
    "datadoghq.eu",
    "us3.datadoghq.com",
    "us5.datadoghq.com",
    "ap1.datadoghq.com",
    "ap2.datadoghq.com",
}


def request(url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(
        urllib.request.Request(url, data=data, headers=headers), timeout=30
    ) as response:
        return response.read()


def value(item):
    if "kvlistValue" in item:
        return {
            entry["key"]: value(entry["value"])
            for entry in item["kvlistValue"].get("values", [])
        }
    if "arrayValue" in item:
        return [value(entry) for entry in item["arrayValue"].get("values", [])]
    if "intValue" in item:
        return int(item["intValue"])
    return next(iter(item.values()), None)


def decode_records(payload, trace):
    for line in gzip.decompress(payload).splitlines():
        document = json.loads(line)
        for resource in document.get("resourceLogs", []):
            for scope in resource.get("scopeLogs", []):
                for log in scope.get("logRecords", []):
                    if log.get("traceId") != trace:
                        raise ValueError("GCS object contains a different trace")
                    body = value(log["body"])
                    if isinstance(body, str):
                        body = json.loads(body)
                    if body.get("msgType") == "rrpair":
                        yield body


def search(site, headers, signal, query):
    results, cursor = [], None
    for _ in range(50):
        page = {"limit": 100}
        if cursor:
            page["cursor"] = cursor
        data = json.loads(
            request(
                f"https://api.{site}/api/v2/{signal}/events/search",
                headers,
                {
                    "filter": {"from": "now-24h", "to": "now", "query": query},
                    "sort": "timestamp",
                    "page": page,
                },
            )
        )
        results.extend(data.get("data", []))
        cursor = data.get("meta", {}).get("page", {}).get("after")
        if not cursor:
            return results
    raise ValueError("Query exceeded 5000 records; narrow the trace selection")


def capture_prefix(message, bucket, service, trace):
    expected = f"https://console.cloud.google.com/storage/browser/{bucket}/byoc/{service}/{trace}"
    if message.rstrip("/") != expected:
        raise ValueError("Datadog log is not the expected GCS capture link")
    return f"byoc/{service}/{trace}/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--service", default="api-gateway")
    parser.add_argument("--bucket", default="speedscale-demos-proxymock-gcs-validation")
    parser.add_argument("--gcloud-account", default="ken@speedscale.com")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{32}", args.trace_id):
        parser.error("Expected a 32-character lowercase trace ID")
    if not re.fullmatch("[A-Za-z0-9._-]+", args.service):
        parser.error("Invalid service name")
    if not re.fullmatch("[a-z0-9][a-z0-9._-]+", args.bucket):
        parser.error("Invalid bucket name")
    settings = {
        k: os.environ.get("DATADOG_PARTNER_" + k, "")
        for k in ("API_KEY", "APP_KEY", "SITE")
    }
    if not all(settings.values()) or settings["SITE"] not in SITES:
        parser.error(
            "Explicit DATADOG_PARTNER_API_KEY, DATADOG_PARTNER_APP_KEY and valid DATADOG_PARTNER_SITE are required"
        )
    headers = {
        "Content-Type": "application/json",
        "DD-API-KEY": settings["API_KEY"],
        "DD-APPLICATION-KEY": settings["APP_KEY"],
    }
    query = f"env:partner-demo service:{args.service}"
    logs = search(
        settings["SITE"], headers, "logs", query + f" @otel.trace_id:{args.trace_id}"
    )
    spans = search(
        settings["SITE"], headers, "spans", query + f" trace_id:{args.trace_id}"
    )
    if not logs or not spans:
        raise SystemExit(
            "Both Datadog capture logs and same-service APM spans are required"
        )
    prefixes = {
        capture_prefix(
            log["attributes"]["message"], args.bucket, args.service, args.trace_id
        )
        for log in logs
    }
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token", "--account=" + args.gcloud_account],
        text=True,
    ).strip()
    google = {"Authorization": "Bearer " + token}
    root = f"https://storage.googleapis.com/storage/v1/b/{args.bucket}/o"
    objects = []
    for prefix in prefixes:
        page = None
        while True:
            params = {"prefix": prefix}
            if page:
                params["pageToken"] = page
            result = json.loads(
                request(root + "?" + urllib.parse.urlencode(params), google)
            )
            objects.extend(result.get("items", []))
            page = result.get("nextPageToken")
            if not page:
                break
    records = {}
    for obj in objects:
        payload = request(
            root + "/" + urllib.parse.quote(obj["name"], safe="") + "?alt=media", google
        )
        for record in decode_records(payload, args.trace_id):
            if record.get("l7protocol") == "http" and record.get("direction") in (
                "IN",
                "OUT",
            ):
                records[record["uuid"]] = record
    incoming = [r for r in records.values() if r["direction"] == "IN"]
    outgoing = [r for r in records.values() if r["direction"] == "OUT"]
    if not incoming or not outgoing:
        raise SystemExit(
            "This trace lacks both incoming test traffic and outgoing HTTP dependency captures; select another trace"
        )
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    for group, items in [("tests", incoming), ("mocks", outgoing)]:
        directory = args.out / group
        directory.mkdir(mode=0o700)
        for item in items:
            name = str(uuid.UUID(bytes=base64.b64decode(item["uuid"])))
            path = directory / (name + ".json")
            path.write_text(json.dumps(item, indent=2))
            path.chmod(0o600)
    provenance = {
        "trace_id": args.trace_id,
        "service": args.service,
        "datadog_query": query,
        "log_ids": [x["id"] for x in logs],
        "apm_spans": [
            {
                "resource": x["attributes"].get("resource_name"),
                "span_id": x["attributes"].get("span_id"),
            }
            for x in spans
        ],
        "gcs_objects": [x["name"] for x in objects],
        "tests": len(incoming),
        "mocks": len(outgoing),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    (args.out / "provenance.json").write_text(json.dumps(provenance, indent=2))
    print(
        json.dumps(
            {
                "trace_id": args.trace_id,
                "tests": len(incoming),
                "mocks": len(outgoing),
                "out": str(args.out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
