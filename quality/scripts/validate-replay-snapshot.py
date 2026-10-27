#!/usr/bin/env python3
"""Check replay transform scope and signing-key mounts before launching traffic."""
import json
from pathlib import Path
import sys


def chain_key(chain):
    aliases = {"detected_location": "detectedLocation", "detected_command": "detectedCommand", "detected_status": "detectedStatus"}
    predicates = [dict({aliases.get(k, k): v for k, v in p.items()}, include=p.get("include", False), operator=p.get("operator", "EQUAL"))
                  for p in chain.get("filters", {}).get("filters", [])]
    extractor = chain.get("extractor", {})
    return [predicates, extractor.get("type"), extractor.get("config", {}),
            [[t.get("type"), t.get("config", {})] for t in chain.get("transforms", [])]]


def validate(snapshot, expected):
    current = snapshot.get("tokenizerConfig", {})
    if snapshot.get("status") != "Complete":
        raise ValueError("snapshot processing is not complete")
    if snapshot.get("tokenConfigId") != expected["id"] or current.get("id") != expected["id"]:
        raise ValueError("snapshot transform identifier differs from the expected configuration")
    secrets = current.get("generatorExpectedEnvironment", {}).get("secrets", [])
    if not any(s in ["banking-jwt-secret/secret", "${{secret:banking-jwt-secret/secret}}"] for s in secrets):
        raise ValueError("missing generator secret declaration")
    for side in ("generator", "responder"):
        actual = [chain_key(chain) for chain in current.get(side, [])]
        if any(chain_key(chain) not in actual for chain in expected.get(side, [])):
            raise ValueError("missing or incorrectly scoped " + side + " transform")


def main():
    try:
        validate(json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text()))
    except (ValueError, KeyError, TypeError, OSError, IndexError) as error:
        print("Replay preflight failed: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
