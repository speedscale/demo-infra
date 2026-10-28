#!/usr/bin/env python3
"""Require full-response assertions, completed requests and latency evidence."""
import json
from pathlib import Path
import sys


def validate(report, expected, errors):
    if report.get("status") != "Passed":
        raise ValueError("regression report did not pass")
    actual = report.get("actualConfig", {})
    if actual.get("id") != expected["id"]:
        raise ValueError("unexpected regression configuration")
    for component in ("generator", "responder"):
        if actual.get(component, {}).get("lowDataMode", False) is not False:
            raise ValueError("regression must use full data mode")
    def filters(group):
        predicates = group.get("filters", {}).get("filters", [])
        aliases = {"detected_location": "detectedLocation", "detected_command": "detectedCommand", "detected_status": "detectedStatus"}
        return [dict({aliases.get(k, k): v for k, v in predicate.items()}, include=predicate.get("include", False),
                     operator=predicate.get("operator", "EQUAL")) for predicate in predicates]
    for group in expected["assertionGroups"]:
        if not any(filters(group) == filters(ag) and all(config in ag.get("configs", []) for config in group["configs"])
                   for ag in actual.get("assertionGroups", [])):
            raise ValueError("required response assertion or endpoint scope was changed")
    if not isinstance(errors, dict) or not isinstance(errors.get("values", []), list):
        raise ValueError("missing status comparison evidence")
    if any(row.get("assertionType") == "Status Code" and row.get("errorCount", 0) > 0 for row in errors.get("values", [])):
        raise ValueError("an HTTP or RPC status differed from its recording")
    goals = report.get("goals", [])
    if any(goal.get("status") == "FAIL" for goal in goals):
        raise ValueError("a regression goal failed")
    for rule in expected["rules"]:
        if not any(r.get("metricName") == rule["metricName"] and r.get("type", "TOO_HIGH") == rule["type"]
                   and r.get("value", 0) == rule["value"] and r.get("action", "IGNORE") == rule.get("action", "IGNORE") for r in actual.get("rules", [])):
            raise ValueError("required error, assertion or latency rule changed")
        if not any(g.get("command") == {"p95": "p95Latency"}.get(rule["metricName"], rule["metricName"]) and g.get("status") == "PASS" for g in goals):
            raise ValueError("missing passing evidence for " + rule["metricName"])
    if not any(a.get("name") == "totalTransactionCount" and a.get("tags", {}).get("source") == "generator"
               and a.get("gaugeVal", {}).get("val", 0) > 0 for a in report.get("aggregates", [])):
        raise ValueError("no completed regression requests")


def main():
    try:
        validate(json.loads(Path(sys.argv[1]).read_text())["report"], json.loads(Path(sys.argv[2]).read_text()), json.loads(Path(sys.argv[3]).read_text()))
    except (ValueError, KeyError, TypeError, OSError, IndexError) as error:
        print("Regression evidence failed: " + str(error), file=sys.stderr)
        return 1
    print("Full-data regression assertions, errors and latency evidence passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
