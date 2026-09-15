#!/usr/bin/env python3
"""Verify a captured banking request and its real APM trace in the partner account."""

import json
import os
import urllib.parse
import urllib.request


def search(signal, query):
    values = {
        k: os.environ.get("DATADOG_PARTNER_" + k, "")
        for k in ("API_KEY", "APP_KEY", "SITE")
    }
    if not all(values.values()):
        raise SystemExit(
            "Explicit DATADOG_PARTNER_API_KEY, DATADOG_PARTNER_APP_KEY and DATADOG_PARTNER_SITE are required"
        )
    if values["SITE"] not in {
        "datadoghq.com",
        "datadoghq.eu",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "ap1.datadoghq.com",
        "ap2.datadoghq.com",
    }:
        raise SystemExit("Unsupported Datadog site")
    body = {
        "filter": {"query": query, "from": "now-15m", "to": "now"},
        "sort": "-timestamp",
        "page": {"limit": 100},
    }
    if signal == "spans":
        body = {"data": {"attributes": body, "type": "search_request"}}
    request = urllib.request.Request(
        f"https://api.{values['SITE']}/api/v2/{signal}/events/search",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "DD-API-KEY": values["API_KEY"],
            "DD-APPLICATION-KEY": values["APP_KEY"],
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("data", [])


def main():
    logs = search(
        "logs", "env:partner-demo @deployment.cluster:staging-decoy @otel.trace_id:*"
    )
    for row in logs:
        log = row["attributes"]
        attrs = log.get("attributes", {})
        otel = attrs.get("otel", {})
        trace = otel.get("trace_id")
        if not log.get("message", "").startswith(
            "https://console.cloud.google.com/storage/browser/"
        ):
            continue
        if not trace:
            continue
        spans = search("spans", "env:partner-demo trace_id:" + trace)
        matching = [
            s["attributes"]
            for s in spans
            if s["attributes"].get("service") == log.get("service")
        ]
        if not matching:
            continue
        exact = [
            s
            for s in matching
            if otel.get("span_id")
            and int(s.get("span_id", "0")) == int(otel["span_id"], 16)
        ]
        print(
            json.dumps(
                {
                    "service": log["service"],
                    "trace_id": trace,
                    "same_service_trace_verified": True,
                    "exact_span_verified": bool(exact),
                    "log_timestamp": log.get("timestamp"),
                    "log_id": row["id"],
                },
                indent=2,
            )
        )
        return
    raise SystemExit(
        "No same-service log/trace match found in the last 15 minutes. Check collector errors, traffic, Datadog indexing, and app-key logs_read_data/apm_read permissions."
    )


if __name__ == "__main__":
    main()
