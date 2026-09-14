#!/usr/bin/env python3
"""Opt-in Datadog export for the staging banking demo. Requires PyYAML."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import yaml

HERE = Path(__file__).resolve().parent
NAME = "datadog-partner"
IMAGE = "otel/opentelemetry-collector-contrib:0.160.0@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6"


def kubectl(context, *args, data=None):
    return subprocess.check_output(
        ["kubectl", "--context", context, "-n", "observability", *args],
        input=json.dumps(data).encode() if data is not None else None,
    ).decode()


def fanout(config, enabled):
    key = "otlp/datadog-partner"
    if enabled:
        config["exporters"][key] = {
            "endpoint": "datadog-partner.observability.svc.cluster.local:4317",
            "tls": {"insecure": True},
        }
    else:
        config["exporters"].pop(key, None)
    for signal in ("traces", "logs"):
        exporters = config["service"]["pipelines"][signal]["exporters"]
        exporters[:] = [item for item in exporters if item != key]
        if enabled:
            exporters.append(key)
    return config


def apply(context, objects):
    kubectl(
        context,
        "apply",
        "-f",
        "-",
        data={"apiVersion": "v1", "kind": "List", "items": objects},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "remove"])
    parser.add_argument("--context", required=True)
    parser.add_argument(
        "--partner-account-verified",
        action="store_true",
        help="Attest these credentials were checked in the dedicated partner organization",
    )
    args = parser.parse_args()
    if args.context != "do-nyc1-staging-decoy":
        parser.error("This demo is scoped to do-nyc1-staging-decoy")
    enabled = args.action == "install"
    if enabled:
        if not args.partner_account_verified:
            parser.error(
                "Verify the destination partner organization, then pass --partner-account-verified"
            )
        values = {
            k: os.environ.get("DATADOG_PARTNER_" + k, "")
            for k in ("API_KEY", "APP_KEY", "SITE")
        }
        if not all(values.values()):
            parser.error(
                "DATADOG_PARTNER_API_KEY, DATADOG_PARTNER_APP_KEY and DATADOG_PARTNER_SITE are required"
            )
        if values["SITE"] not in {
            "datadoghq.com",
            "datadoghq.eu",
            "us3.datadoghq.com",
            "us5.datadoghq.com",
            "ap1.datadoghq.com",
            "ap2.datadoghq.com",
        }:
            parser.error("Unsupported Datadog site")
        for name in ("DD_API_KEY", "DATADOG_API_KEY"):
            if os.environ.get(name) == values["API_KEY"]:
                parser.error(
                    "Partner API key must differ from default infrastructure credentials"
                )
        request = urllib.request.Request(
            "https://api." + values["SITE"] + "/api/v1/validate",
            headers={"DD-API-KEY": values["API_KEY"]},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            if not json.load(response).get("valid"):
                parser.error("Invalid partner API key")
        labels = {"app": NAME}
        metadata = {"name": NAME, "namespace": "observability"}
        apply(
            args.context,
            [
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": metadata,
                    "data": {
                        "api-key": base64.b64encode(values["API_KEY"].encode()).decode()
                    },
                },
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": metadata,
                    "data": {"collector.yaml": (HERE / "collector.yaml").read_text()},
                },
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": metadata,
                    "spec": {
                        "selector": labels,
                        "ports": [
                            {"name": "grpc", "port": 4317},
                            {"name": "http", "port": 4318},
                        ],
                    },
                },
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": metadata,
                    "spec": {
                        "replicas": 1,
                        "selector": {"matchLabels": labels},
                        "template": {
                            "metadata": {"labels": labels},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "collector",
                                        "image": IMAGE,
                                        "args": ["--config=/conf/collector.yaml"],
                                        "env": [
                                            {
                                                "name": "DATADOG_PARTNER_API_KEY",
                                                "valueFrom": {
                                                    "secretKeyRef": {
                                                        "name": NAME,
                                                        "key": "api-key",
                                                    }
                                                },
                                            },
                                            {
                                                "name": "DATADOG_PARTNER_SITE",
                                                "value": values["SITE"],
                                            },
                                        ],
                                        "resources": {
                                            "requests": {
                                                "cpu": "100m",
                                                "memory": "256Mi",
                                            },
                                            "limits": {"memory": "512Mi"},
                                        },
                                        "readinessProbe": {
                                            "httpGet": {"path": "/", "port": 13133}
                                        },
                                        "livenessProbe": {
                                            "httpGet": {"path": "/", "port": 13133}
                                        },
                                        "volumeMounts": [
                                            {
                                                "name": "config",
                                                "mountPath": "/conf",
                                                "readOnly": True,
                                            }
                                        ],
                                    }
                                ],
                                "volumes": [
                                    {"name": "config", "configMap": {"name": NAME}}
                                ],
                            },
                        },
                    },
                },
            ],
        )
        kubectl(args.context, "rollout", "restart", "deployment/" + NAME)
        kubectl(
            args.context, "rollout", "status", "deployment/" + NAME, "--timeout=120s"
        )
    cm = json.loads(
        kubectl(args.context, "get", "configmap/otel-collector-conf", "-o", "json")
    )
    field = "otel-collector-config.yaml"
    config = fanout(yaml.safe_load(cm["data"][field]), enabled)
    cm["data"][field] = yaml.safe_dump(config, sort_keys=False)
    kubectl(args.context, "replace", "-f", "-", data=cm)
    kubectl(args.context, "rollout", "restart", "deployment/otel-collector")
    kubectl(
        args.context, "rollout", "status", "deployment/otel-collector", "--timeout=120s"
    )
    if not enabled:
        kubectl(
            args.context,
            "delete",
            "deployment,service,configmap,secret",
            NAME,
            "--ignore-not-found",
        )
    print(
        "Partner export "
        + ("enabled" if enabled else "removed")
        + "; existing observability destinations preserved"
    )


if __name__ == "__main__":
    main()
