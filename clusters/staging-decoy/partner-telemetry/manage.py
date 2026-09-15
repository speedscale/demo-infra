#!/usr/bin/env python3
"""Toggle staging banking telemetry fanout to partner collectors."""

import argparse
import json
import subprocess

import yaml

DESTINATIONS = {
    "dynatrace": {
        "exporter": "otlp/dynatrace-partner",
        "endpoint": "otel-collector.byoc-dynatrace.svc.cluster.local:4317",
        "namespace": "byoc-dynatrace",
        "signals": ("traces",),
    },
}


def kubectl(context, namespace, *args, data=None):
    return subprocess.check_output(
        ["kubectl", "--context", context, "-n", namespace, *args],
        input=json.dumps(data).encode() if data is not None else None,
    ).decode()


def fanout(config, destination, enabled):
    spec = DESTINATIONS[destination]
    key = spec["exporter"]
    if enabled:
        config["exporters"][key] = {
            "endpoint": spec["endpoint"],
            "tls": {"insecure": True},
        }
    else:
        config["exporters"].pop(key, None)
    for signal in spec["signals"]:
        exporters = config["service"]["pipelines"][signal]["exporters"]
        exporters[:] = [item for item in exporters if item != key]
        if enabled:
            exporters.append(key)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("on", "off", "status"))
    parser.add_argument("destination", choices=DESTINATIONS)
    parser.add_argument("--context", required=True)
    parser.add_argument("--partner-account-verified", action="store_true")
    args = parser.parse_args()
    if args.context != "do-nyc1-staging-decoy":
        parser.error("This demo is scoped to do-nyc1-staging-decoy")
    spec = DESTINATIONS[args.destination]
    cm = json.loads(
        kubectl(args.context, "observability", "get", "configmap/otel-collector-conf", "-o", "json")
    )
    field = "otel-collector-config.yaml"
    config = yaml.safe_load(cm["data"][field])
    key = spec["exporter"]
    if args.action == "status":
        attached = all(
            key in config["service"]["pipelines"][signal]["exporters"]
            for signal in spec["signals"]
        )
        print(args.destination + " fanout: " + ("on" if attached else "off"))
        print(kubectl(args.context, spec["namespace"], "get", "deployment/otel-collector", "--ignore-not-found"))
        return
    enabled = args.action == "on"
    if enabled:
        if not args.partner_account_verified:
            parser.error("Verify the destination partner account, then pass --partner-account-verified")
        kubectl(args.context, spec["namespace"], "rollout", "restart", "deployment/otel-collector")
        kubectl(
            args.context,
            spec["namespace"],
            "rollout",
            "status",
            "deployment/otel-collector",
            "--timeout=120s",
        )
    cm["data"][field] = yaml.safe_dump(fanout(config, args.destination, enabled), sort_keys=False)
    kubectl(args.context, "observability", "replace", "-f", "-", data=cm)
    kubectl(args.context, "observability", "rollout", "restart", "deployment/otel-collector")
    kubectl(args.context, "observability", "rollout", "status", "deployment/otel-collector", "--timeout=120s")
    print(args.destination + " fanout " + ("enabled" if enabled else "disabled"))


if __name__ == "__main__":
    main()
