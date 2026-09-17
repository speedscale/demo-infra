#!/usr/bin/env python3
"""Opt-in Datadog export for the staging banking demo. Requires PyYAML."""

import argparse
import base64
import json
import os
import re
from pathlib import Path
import subprocess
import urllib.request

HERE = Path(__file__).resolve().parent
NAME = "datadog-partner"
IMAGE = "otel/opentelemetry-collector-contrib:0.160.0@sha256:799dc6cf12c96192af37b5bdba804da8c10b3bc563b43cb90c3f3c58d9572ad6"


def kubectl(context, *args, data=None):
    return subprocess.check_output(
        ["kubectl", "--context", context, "-n", "observability", *args],
        input=json.dumps(data).encode() if data is not None else None,
    ).decode()


def apply(context, objects):
    kubectl(
        context,
        "apply",
        "-f",
        "-",
        data={"apiVersion": "v1", "kind": "List", "items": objects},
    )


def route_enabled(context):
    raw = kubectl(
        context, "get", "configmap/partner-trace-router-config", "-o", "json"
    )
    config_map = json.loads(raw)
    config = json.loads(config_map["data"]["otel.yaml"])
    return "otlp/datadog" in config.get("exporters", {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "remove", "status"])
    parser.add_argument("--context", required=True)
    parser.add_argument(
        "--partner-account-verified",
        action="store_true",
        help="Attest these credentials were checked in the dedicated partner organization",
    )
    parser.add_argument("--gcs-bucket")
    parser.add_argument("--gcs-project")
    parser.add_argument("--gcs-region", default="us-central1")
    parser.add_argument("--gcs-credentials-secret", default="datadog-partner-gcs")
    args = parser.parse_args()
    if args.context != "do-nyc1-staging-decoy":
        parser.error("This demo is scoped to do-nyc1-staging-decoy")
    if args.action == "status":
        print(kubectl(args.context, "get", "deployment/" + NAME, "--ignore-not-found"))
        print("Datadog route: " + ("on" if route_enabled(args.context) else "off"))
        return
    enabled = args.action == "install"
    if enabled:
        if not args.gcs_bucket or not args.gcs_project:
            parser.error("Explicit --gcs-bucket and --gcs-project are required")
        for value in (
            args.gcs_bucket,
            args.gcs_project,
            args.gcs_region,
            args.gcs_credentials_secret,
        ):
            if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value):
                parser.error("Invalid GCS destination or credential secret name")
        secret = json.loads(
            kubectl(
                args.context,
                "get",
                "secret/" + args.gcs_credentials_secret,
                "-o",
                "json",
            )
        )
        credential = json.loads(
            base64.b64decode(secret.get("data", {}).get("credentials.json", ""))
        )
        if credential.get("type") not in ("service_account", "external_account"):
            parser.error(
                "GCS writer must use a dedicated workload identity, not personal ADC"
            )
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
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": NAME + "-storage",
                        "namespace": "observability",
                    },
                    "data": {
                        "DATADOG_PARTNER_GCS_BUCKET": args.gcs_bucket,
                        "DATADOG_PARTNER_GCS_PROJECT": args.gcs_project,
                        "DATADOG_PARTNER_GCS_REGION": args.gcs_region,
                        "GOOGLE_APPLICATION_CREDENTIALS": "/gcs/credentials.json",
                    },
                },
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
                                            {"name": "GOMEMLIMIT", "value": "700MiB"},
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
                                        "envFrom": [
                                            {
                                                "configMapRef": {
                                                    "name": NAME + "-storage"
                                                }
                                            }
                                        ],
                                        "resources": {
                                            "requests": {
                                                "cpu": "100m",
                                                "memory": "256Mi",
                                            },
                                            "limits": {"memory": "1Gi"},
                                        },
                                        "readinessProbe": {
                                            "httpGet": {"path": "/", "port": 13133}
                                        },
                                        "livenessProbe": {
                                            "httpGet": {"path": "/", "port": 13133}
                                        },
                                        "volumeMounts": [
                                            {
                                                "name": "gcs-credentials",
                                                "mountPath": "/gcs",
                                                "readOnly": True,
                                            },
                                            {
                                                "name": "config",
                                                "mountPath": "/conf",
                                                "readOnly": True,
                                            },
                                        ],
                                    }
                                ],
                                "volumes": [
                                    {
                                        "name": "gcs-credentials",
                                        "secret": {
                                            "secretName": args.gcs_credentials_secret
                                        },
                                    },
                                    {"name": "config", "configMap": {"name": NAME}},
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
    if not enabled:
        if route_enabled(args.context):
            parser.error("Turn off the central Datadog route before removing its adapter")
        kubectl(
            args.context,
            "delete",
            "deployment,service,configmap,secret",
            NAME,
            "--ignore-not-found",
        )
        kubectl(
            args.context, "delete", "configmap", NAME + "-storage", "--ignore-not-found"
        )
    print("Datadog adapter " + ("installed" if enabled else "removed"))


if __name__ == "__main__":
    main()
