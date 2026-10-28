#!/usr/bin/env python3
"""Build synthetic full-response regression fixtures for the current banking API."""
import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("load_fixtures", Path(__file__).with_name("build-load-fixtures.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


def ai_pairs():
    cases = [(q, "en-US", "") for q in ["Explain compound interest.", "Explain card security.", "Explain a savings account."]]
    cases += [("What is my balance?", "en-US", "Synthetic savings balance: 125.50 USD."),
              ("Explica el interes compuesto.", "es-ES", "")]
    pairs = fixtures.ai_pairs(cases)
    pairs.append(fixtures.pair("banking-ai", "IN", "banking-ai", 8080, "GET", "/health", None, {"status": "ok"}))
    pairs.append(fixtures.pair("banking-ai", "IN", "banking-ai", 8080, "POST", "/api/chat", {},
                              {"detail": [{"type": "missing", "loc": ["body", "message"], "msg": "Field required", "input": {}}]}, 422))
    return pairs


def gateway_pairs():
    import base64
    import hashlib
    import hmac

    def bearer(username, user_id):
        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
        unsigned = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode({"sub": username, "userId": str(user_id), "roles": "USER", "exp": 1893456000})
        signature = base64.urlsafe_b64encode(hmac.new(b"public-synthetic-gateway-fixture-key", unsigned.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        return "Bearer " + unsigned + "." + signature

    pairs = fixtures.gateway_pairs()
    def route(host, method, uri, request, response, status=200, identity=None):
        for direction, target, port in [("OUT", host, 80), ("IN", "banking-gateway", 8080)]:
            rr = fixtures.pair("banking-gateway", direction, target, port, method, uri, request, response, status)
            if identity:
                rr["http"]["req"]["headers"]["Authorization"] = [bearer(*identity)]
            pairs.append(rr)

    for index, name in enumerate(["alpha", "beta"]):
        uid, account_id, transaction_id = 91001 + index, 92001 + index, 93001 + index
        identity = ("gateway-fixture-" + name, uid)
        profile = {"id": uid, "username": identity[0], "email": name + "@example.invalid", "roles": "USER"}
        account = {"id": account_id, "userId": uid, "accountNumber": str(100000000001 + index), "accountType": "SAVINGS", "balance": 125.5}
        transaction = {"id": transaction_id, "userId": uid, "fromAccountId": account_id, "toAccountId": None, "amount": 25.5, "type": "DEPOSIT", "status": "COMPLETED"}
        route("banking-user", "GET", "/api/users/profile", None, profile, identity=identity)
        route("banking-accounts", "GET", "/api/accounts", None, [account], identity=identity)
        route("banking-accounts", "GET", f"/api/accounts/{account_id}", None, account, identity=identity)
        route("banking-transactions", "GET", "/api/transactions", None, [transaction], identity=identity)
        for operation in ["create", "deposit", "withdraw", "transfer"]:
            request = {"fromAccountId": account_id, "toAccountId": 92003, "amount": 25.5, "type": operation.upper(), "description": "synthetic gateway regression"}
            response = dict(transaction, type=operation.upper())
            route("banking-transactions", "POST", "/api/transactions/" + operation, request, response, 201, identity)
    route("banking-user", "GET", "/api/users/profile", None, {"error": "Synthetic dependency unavailable"}, 503, ("gateway-fixture-unavailable", 91999))
    route("banking-accounts", "GET", "/api/accounts/999999", None, {"error": "Account not found"}, 404, ("gateway-fixture-alpha", 91001))
    route("banking-transactions", "POST", "/api/transactions/create", {"amount": -1}, {"error": "Invalid transaction amount"}, 400, ("gateway-fixture-alpha", 91001))
    for service in ["user", "accounts", "transactions"]:
        pairs.append(fixtures.pair("banking-gateway", "OUT", "banking-" + service, 80, "GET", "/actuator/health", None, {"status": "UP"}))
        pairs.append(fixtures.pair("banking-gateway", "IN", "banking-gateway", 8080, "GET", "/api/" + service + "-service/health", None, {"status": "UP"}))
    return pairs


def notification_pairs():
    import base64
    from urllib.parse import urlencode
    events = [dict(transaction_id=91001 + i, account_id=10001 + i, user_id=91001 + (i % 2),
                   amount=amount, transaction_type=kind, status="COMPLETED", timestamp="2026-01-01T00:00:00Z")
              for i, (amount, kind) in enumerate([(25.5, "DEPOSIT"), (7.25, "WITHDRAWAL"), (40.0, "TRANSFER")])]
    def kafka_pair(request, response):
        rr = fixtures.pair("banking-notification", "OUT", "banking-kafka.banking-app.svc.cluster.local", 9092, "", "", None, None)
        rr.pop("http")
        rr.update(l7protocol="kafka", tech="Kafka", kafka=dict(request=request, response=response))
        return rr
    versions = [(0, 8), (1, 11), (2, 5), (3, 9), (8, 6), (9, 6), (10, 3), (11, 6), (12, 4), (13, 4), (14, 4), (18, 3)]
    pairs = [kafka_pair({"apiKey": "KAFKA_API_KEY_API_VERSIONS", "apiVersion": 3, "ApiVersionsRequest": {}},
                        {"ApiVersionsResponse": {"error": "KAFKA_ERROR_NONE", "apiKeys": [dict(apiKey=k, minVersion=0, maxVersion=v) for k, v in versions]}})]
    pairs.append(kafka_pair({"apiKey": "KAFKA_API_KEY_FETCH", "apiVersion": 10, "FetchRequest": {
        "replicaId": -1, "maxWaitMs": 100, "minBytes": 1, "maxBytes": 1000000, "sessionEpoch": -1,
        "topics": [{"topic": "transaction-events", "partitions": [{"partitionMaxBytes": 1000000}]}]}},
        {"FetchResponse": {"error": "KAFKA_ERROR_NONE", "topics": [{"topic": "transaction-events", "partitions": [{
            "error": "KAFKA_ERROR_NONE", "highWatermark": "3", "lastStableOffset": "3", "records": {
                "magic": 2, "attributes": ["ATTRIBUTE_NO_COMPRESSION"], "lastOffsetDelta": 2,
                "baseTimestamp": "1767225600000", "maxTimestamp": "1767225600000", "producerId": "-1", "producerEpoch": -1,
                "records": [{"offsetDelta": i, "keyString": str(e["transaction_id"]), "valueString": json.dumps(e)} for i, e in enumerate(events)]}}]}]}}))
    for event in events:
        tid, amount, kind = event["transaction_id"], event["amount"], event["transaction_type"]
        email = {"personalizations": [{"to": [{"email": "customer@example.com"}]}], "from": {"email": "alerts@apexbanking.com"},
                 "subject": f"Transaction COMPLETED: {tid}", "content": [{"type": "text/plain", "value": f"Transaction {tid} of ${amount:.2f} ({kind}) is COMPLETED."}]}
        pairs.append(fixtures.pair("banking-notification", "OUT", "api.sendgrid.com", 443, "POST", "/v3/mail/send", email, None, 202))
        sms = fixtures.pair("banking-notification", "OUT", "api.twilio.com", 443, "POST", "/2010-04-01/Accounts/ACdummydummydummydummydummydummy00/Messages.json", None, {"sid": f"synthetic-{tid}"}, 201)
        form = urlencode({"Body": f"Apex Banking: your {kind} of ${amount:.2f} is COMPLETED (ref: {tid})", "From": "+15559876543", "To": "+15551234567"})
        sms["http"]["req"]["bodyBase64"] = base64.b64encode(form.encode()).decode()
        sms["http"]["req"]["headers"]["Content-Type"] = ["application/x-www-form-urlencoded"]
        pairs.append(sms)
        pairs.append(fixtures.pair("banking-notification", "OUT", "hooks.slack.com", 443, "POST", "/services/SET-VIA-SECRET",
                                  {"text": f"[COMPLETED] Transaction {tid}: ${amount:.2f} {kind}", "channel": "#transaction-alerts", "username": "apex-banking-bot"}, None))
    for _ in range(20):
        pairs.append(fixtures.pair("banking-notification", "IN", "banking-notification", 8080, "GET", "/health", None, {"status": "ok"}))
    cases = [("/notifications?limit=3", list(reversed(events))),
             ("/notifications/91001?limit=2", [events[2], events[0]]),
             ("/notifications/91002?limit=1", [events[1]]),
             ("/notifications/999999?limit=1", []),
             ("/notifications?limit=1", [events[2]])]
    for uri, response in cases:
        pairs.append(fixtures.pair("banking-notification", "IN", "banking-notification", 8080, "GET", uri, None, response))
    return pairs


spec = importlib.util.spec_from_file_location("database_fixtures", Path(__file__).with_name("build-database-fixtures.py"))
database = importlib.util.module_from_spec(spec)
spec.loader.exec_module(database)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    for name, pairs in [("ai", ai_pairs()), ("notification", notification_pairs()), ("gateway", gateway_pairs()), ("accounts", database.accounts_pairs()), ("transactions", database.transactions_pairs())]:
        directory = args.destination / name
        directory.mkdir(parents=True, exist_ok=True)
        start = datetime.now(timezone.utc) - timedelta(seconds=len(pairs))
        for index, rrpair in enumerate(pairs):
            rrpair["ts"] = (start + timedelta(milliseconds=index)).isoformat()
            (directory / f"{index:03d}.json").write_text(json.dumps(rrpair, indent=2) + "\n")
        print(f"{name}: {len(pairs)} RRPairs in {directory}")
