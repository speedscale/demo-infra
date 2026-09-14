#!/usr/bin/env python3
"""Deploy microsvc beside the existing GKE GCS/Datadog partner demo."""

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import yaml

NAMESPACE = "byoc-gcs-validation"
SERVICES = {
    "banking-accounts": "accounts-service",
    "banking-user": "user-service",
    "banking-transactions": "transactions-service",
    "banking-gateway": "api-gateway",
    "banking-frontend": "frontend",
    "banking-fraud": "fraud-service",
    "banking-notification": "notification-service",
    "banking-ai": "ai-service",
}


def configure_collector(config):
    statements = [
        f'set(resource.attributes["service.name"], "{new}") where resource.attributes["service.name"] == "{old}"'
        for old, new in SERVICES.items()
    ]
    statements.append(
        'set(log.span_id, SpanID(0x0000000000000000)) where log.body["namespace"] == "banking-app" and log.body["direction"] == "IN"'
    )
    config["processors"]["transform/microsvc"] = {
        "error_mode": "propagate",
        "log_statements": [{"context": "log", "statements": statements}],
    }
    config["processors"]["resource/partner"] = {
        "attributes": [
            {
                "key": "deployment.environment",
                "value": "partner-demo",
                "action": "upsert",
            }
        ]
    }
    for name, pipeline in config["service"]["pipelines"].items():
        additions = (
            ["transform/microsvc", "resource/partner"]
            if name.startswith("logs")
            else ["resource/partner"] if name == "traces" else []
        )
        processors = pipeline.get("processors", [])
        processors[:] = [p for p in processors if p not in additions]
        processors[1:1] = additions
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    args = parser.parse_args()
    command = [
        "kubectl",
        "--kubeconfig",
        args.kubeconfig,
        "--context",
        "gke_speedscale-demos_us-central1-a_byoc-gcs-validation",
    ]

    def kube(*args, data=None):
        return subprocess.check_output(
            [*command, *args],
            input=json.dumps(data).encode() if data is not None else None,
        )

    expected = os.environ.get("DATADOG_PARTNER_API_KEY")
    if not expected:
        parser.error(
            "DATADOG_PARTNER_API_KEY must be set to the verified partner credential"
        )
    secret = json.loads(
        kube("-n", NAMESPACE, "get", "secret/datadog-partner-api-key", "-o", "json")
    )
    if base64.b64decode(secret["data"]["api-key"]).decode() != expected:
        parser.error("Existing collector key does not match the verified partner key")
    deployment = json.loads(
        kube("-n", NAMESPACE, "get", "deployment/byoc-demo-gcs-datadog", "-o", "json")
    )
    env = deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])
    keyref = (
        next((e for e in env if e["name"] == "DD_API_KEY"), {})
        .get("valueFrom", {})
        .get("secretKeyRef", {})
    )
    if keyref != {"name": "datadog-partner-api-key", "key": "api-key"}:
        parser.error("Collector must reference the partner API-key secret")
    for name, field in [
        ("byoc-demo-gcs-datadog", "otel.yaml"),
        ("speedscale-nettap", "config.yaml"),
    ]:
        cm = json.loads(kube("-n", NAMESPACE, "get", "configmap/" + name, "-o", "json"))
        config = yaml.safe_load(cm["data"][field])
        if name == "speedscale-nettap":
            targets = config["capture"]["targets"]
            targets[:] = [t for t in targets if t.get("name") != "microsvc"]
            targets.append(
                {
                    "name": "microsvc",
                    "namespaces": ["banking-app"],
                    "podSelector": {"matchLabels": {"byoc-validation": "app"}},
                }
            )
        else:
            configure_collector(config)
        cm["data"][field] = yaml.safe_dump(config, sort_keys=False)
        kube("replace", "-f", "-", data=cm)
    kube(
        "-n",
        NAMESPACE,
        "rollout",
        "restart",
        "deployment/byoc-demo-gcs-datadog",
        "daemonset/speedscale-nettap",
    )
    subprocess.run([*command, "apply", "-k", str(Path(__file__).parent)], check=True)
    print(
        "Microsvc deployed with simulator paused. Verify the seeded app, then start banking-sim and validate Datadog/GCS before retiring the old fixtures."
    )


if __name__ == "__main__":
    main()
