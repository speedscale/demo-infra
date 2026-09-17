#!/usr/bin/env python3
"""Render the partner router from the vendor destination registry."""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIGNAL_PIPELINES = {
    "traces": "traces",
    "logs": "logs",
    "metrics": "metrics",
    "captures": "logs/captures",
}


def load_destinations():
    return json.loads((HERE / "destinations.json").read_text())


def validate_destinations(destinations):
    names = set()
    for destination in destinations:
        name = destination["name"]
        if name in names:
            raise ValueError("duplicate destination: " + name)
        names.add(name)
        if not destination["endpoint"] or not destination["signals"]:
            raise ValueError("destination requires endpoint and signals: " + name)
        unknown = set(destination["signals"]) - set(SIGNAL_PIPELINES)
        if unknown:
            raise ValueError("unknown signals for " + name + ": " + ", ".join(sorted(unknown)))


def build_config(destinations):
    validate_destinations(destinations)
    config = {
        "receivers": {
            "otlp/apps": {
                "protocols": {
                    "grpc": {"endpoint": "0.0.0.0:4317"},
                    "http": {"endpoint": "0.0.0.0:4318"},
                }
            },
            "otlp/captures": {
                "protocols": {
                    "grpc": {"endpoint": "0.0.0.0:5317"},
                    "http": {"endpoint": "0.0.0.0:5318"},
                }
            },
        },
        "processors": {
            "memory_limiter": {
                "check_interval": "1s",
                "limit_mib": 400,
                "spike_limit_mib": 80,
            },
            "batch": {"timeout": "1s", "send_batch_size": 100},
        },
        "exporters": {
            "otlp/original": {
                "endpoint": "otel-collector.observability.svc.cluster.local:4317",
                "tls": {"insecure": True},
            },
            "nop/captures": {},
        },
        "extensions": {"health_check": {"endpoint": "0.0.0.0:13133"}},
        "service": {
            "extensions": ["health_check"],
            "pipelines": {
                "traces": {
                    "receivers": ["otlp/apps"],
                    "processors": ["memory_limiter", "batch"],
                    "exporters": ["otlp/original"],
                },
                "logs": {
                    "receivers": ["otlp/apps"],
                    "processors": ["memory_limiter", "batch"],
                    "exporters": ["otlp/original"],
                },
                "metrics": {
                    "receivers": ["otlp/apps"],
                    "processors": ["memory_limiter", "batch"],
                    "exporters": ["otlp/original"],
                },
                "logs/captures": {
                    "receivers": ["otlp/captures"],
                    "processors": ["memory_limiter", "batch"],
                    "exporters": ["nop/captures"],
                },
            },
        },
    }
    for destination in destinations:
        if destination["enabled"]:
            set_destination(config, destination, True)
    return config


def set_destination(config, destination, enabled):
    key = "otlp/" + destination["name"]
    if enabled:
        config["exporters"][key] = {
            "endpoint": destination["endpoint"],
            "tls": {"insecure": True},
        }
    else:
        config["exporters"].pop(key, None)
    for signal in SIGNAL_PIPELINES.values():
        exporters = config["service"]["pipelines"][signal]["exporters"]
        exporters[:] = [item for item in exporters if item != key]
    if enabled:
        for signal in destination["signals"]:
            config["service"]["pipelines"][SIGNAL_PIPELINES[signal]]["exporters"].append(key)
    return config


def serialize(config):
    return json.dumps(config, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = serialize(build_config(load_destinations()))
    target = HERE / "otel.yaml"
    if args.check:
        if target.read_text() != rendered:
            parser.error("otel.yaml is stale; run render.py")
        print("partner router is current")
        return
    target.write_text(rendered)


if __name__ == "__main__":
    main()
