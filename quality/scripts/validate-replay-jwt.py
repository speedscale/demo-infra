#!/usr/bin/env python3
"""Verify replayed login tokens without logging tokens or signing keys."""
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys
import time


def decode(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def verify(body, secret, timestamp):
    token = body.get("token")
    if not isinstance(token, str):
        raise ValueError("successful login omitted its token")
    parts = token.split(".")
    if len(parts) != 3 or json.loads(decode(parts[0])).get("alg") != "HS256":
        raise ValueError("unexpected login token algorithm")
    expected = hmac.new(secret, (parts[0] + "." + parts[1]).encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, decode(parts[2])):
        raise ValueError("login token signature did not validate")
    claims = json.loads(decode(parts[1]))
    expires = claims.get("exp")
    if not isinstance(expires, (int, float)) or not timestamp < expires <= timestamp + 30 * 60 * 60:
        raise ValueError("login token has an invalid expiry")
    if claims.get("sub") != body.get("username") or str(claims.get("userId")) != str(body.get("id")):
        raise ValueError("login token identity differs from its response")
    if claims.get("roles") != body.get("roles"):
        raise ValueError("login token roles differ from its response")
    if not body.get("username") or not body.get("id") or not body.get("roles"):
        raise ValueError("login response omitted identity or roles")


def validate(pairs, secret, timestamp):
    count = 0
    registrations = 0
    for pair in pairs:
        if pair.get("direction") != "IN" or pair.get("tags", {}).get("source") != "generator":
            continue
        http = pair.get("http", {})
        location = http.get("req", {}).get("url", pair.get("location"))
        response = http.get("res", {})
        if location == "/api/users/register" and response.get("statusCode") == 201:
            body = json.loads(base64.b64decode(response.get("bodyBase64", "")))
            created = datetime.fromisoformat(body["createdAt"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            sent = datetime.fromisoformat(pair["ts"].replace("Z", "+00:00"))
            if abs((created - sent).total_seconds()) > 120:
                raise ValueError("registration time differs from its request time")
            registrations += 1
        if location != "/api/users/login":
            continue
        if response.get("statusCode") == 200:
            body = json.loads(base64.b64decode(response.get("bodyBase64", "")))
            verify(body, secret, timestamp)
            count += 1
    if not count:
        raise ValueError("no successful logins were verified")
    if not registrations:
        raise ValueError("no successful registrations were verified")
    return count


def main():
    try:
        result = subprocess.run(["kubectl", "--request-timeout=10s", "-n", "banking-replay", "get", "secret", "banking-jwt-secret", "-o", "json"],
                                check=True, capture_output=True, text=True, timeout=15)
        secret = base64.b64decode(json.loads(result.stdout)["data"]["secret"])
        with Path(sys.argv[1]).open() as source:
            count = validate((json.loads(line) for line in source if line.strip()), secret, time.time())
    except (ValueError, KeyError, TypeError, OSError, IndexError, subprocess.SubprocessError):
        print("Login JWT verification failed", file=sys.stderr)
        return 1
    print(f"Verified signatures, expiry, identity and roles for {count} login responses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
