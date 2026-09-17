#!/usr/bin/env python3
"""Enable or disable one vendor adapter in the live staging partner router."""

import argparse
import base64
import json
import subprocess

import render

CONTEXT = "do-nyc1-staging-decoy"
NAMESPACE = "observability"
CONFIG_MAP = "partner-trace-router-config"


def kubectl(context, namespace, *args, data=None):
    return subprocess.check_output(
        ["kubectl", "--context", context, "-n", namespace, *args],
        input=json.dumps(data).encode() if data is not None else None,
    ).decode()


def configured(config, destination):
    key = "otlp/" + destination["name"]
    return key in config["exporters"] and all(
        key in config["service"]["pipelines"][render.SIGNAL_PIPELINES[signal]]["exporters"]
        for signal in destination["signals"]
    )


def verify_adapter(context, destination):
    kubectl(
        context,
        destination["namespace"],
        "rollout",
        "status",
        "deployment/" + destination["deployment"],
        "--timeout=120s",
    )
    secret = json.loads(
        kubectl(
            context,
            destination["namespace"],
            "get",
            "secret/" + destination["secret"],
            "-o",
            "json",
        )
    )
    value = secret.get("data", {}).get(destination["secretKey"], "")
    if not value or not base64.b64decode(value):
        raise RuntimeError(destination["name"] + " credential is not configured")


def main():
    destinations = render.load_destinations()
    by_name = {item["name"]: item for item in destinations}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("on", "off", "status"))
    parser.add_argument("destination", choices=sorted(by_name))
    parser.add_argument("--context", required=True)
    parser.add_argument("--partner-account-verified", action="store_true")
    args = parser.parse_args()
    if args.context != CONTEXT:
        parser.error("This demo is scoped to " + CONTEXT)
    destination = by_name[args.destination]
    cm = json.loads(kubectl(args.context, NAMESPACE, "get", "configmap/" + CONFIG_MAP, "-o", "json"))
    config = json.loads(cm["data"]["otel.yaml"])
    if args.action == "status":
        print(args.destination + ": " + ("on" if configured(config, destination) else "off"))
        return
    enabled = args.action == "on"
    if enabled:
        if not args.partner_account_verified:
            parser.error("Verify the destination partner account, then pass --partner-account-verified")
        verify_adapter(args.context, destination)
    cm["data"]["otel.yaml"] = render.serialize(render.set_destination(config, destination, enabled))
    kubectl(args.context, NAMESPACE, "replace", "-f", "-", data=cm)
    kubectl(args.context, NAMESPACE, "rollout", "restart", "deployment/partner-trace-router")
    kubectl(
        args.context,
        NAMESPACE,
        "rollout",
        "status",
        "deployment/partner-trace-router",
        "--timeout=120s",
    )
    print(args.destination + ": " + ("on" if enabled else "off"))


if __name__ == "__main__":
    main()
