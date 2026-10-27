#!/usr/bin/env python3
"""Validate volatile account numbers and write timestamps retained in full-data reports."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def validate(pairs, service):
    checked = 0
    numbers = set()
    seen = set()
    for pair in pairs:
        if pair.get("direction") != "IN" or pair.get("tags", {}).get("source") != "generator":
            continue
        if pair.get("uuid") in seen:
            continue
        if pair.get("uuid"):
            seen.add(pair["uuid"])
        http = pair.get("http", {})
        request, response = http.get("req", {}), http.get("res", {})
        location = request.get("url", pair.get("location", ""))
        if request.get("method") != "POST" or response.get("statusCode") != 201:
            continue
        if service == "accounts" and location != "/api/accounts":
            continue
        if service == "transactions" and not location.startswith("/api/transactions/"):
            continue
        body = json.loads(base64.b64decode(response.get("bodyBase64", "")))
        sent = timestamp(pair["ts"])
        for field in (("createdAt", "updatedAt") if service == "accounts" else ("createdAt", "processedAt")):
            if abs((timestamp(body[field]) - sent).total_seconds()) > 120:
                raise ValueError("write timestamp differs from request time")
        if service == "accounts":
            number = body.get("accountNumber")
            if not isinstance(number, str) or not re.fullmatch(r"[1-9][0-9]{11}", number) or number in numbers:
                raise ValueError("generated account number is malformed or repeated")
            numbers.add(number)
        checked += 1
    if not checked:
        raise ValueError("no successful writes were verified")
    return checked


def main():
    try:
        with Path(sys.argv[1]).open() as source:
            count = validate((json.loads(line) for line in source if line.strip()), sys.argv[2])
    except (ValueError, KeyError, TypeError, OSError, IndexError):
        print("Generated response verification failed", file=sys.stderr)
        return 1
    print(f"Verified generated fields for {count} {sys.argv[2]} writes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
