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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    for name, pairs in [("ai", ai_pairs()), ("notification", notification_pairs())]:
        directory = args.destination / name
        directory.mkdir(parents=True, exist_ok=True)
        start = datetime.now(timezone.utc) - timedelta(seconds=len(pairs))
        for index, rrpair in enumerate(pairs):
            rrpair["ts"] = (start + timedelta(milliseconds=index)).isoformat()
            (directory / f"{index:03d}.json").write_text(json.dumps(rrpair, indent=2) + "\n")
        print(f"{name}: {len(pairs)} RRPairs in {directory}")
