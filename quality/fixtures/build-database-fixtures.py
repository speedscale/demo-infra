"""Synthetic database-backed API contracts; startup data contains schema metadata only."""
import base64
import copy
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import struct

ROOT = Path(__file__).parent

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

http = load("database_http_fixtures", ROOT / "build-load-fixtures.py")
repair = load("database_protocol_repair", ROOT.parent / "scripts/repair-postgres-fixture.py")
STAMP = "2026-01-01T00:00:00"


def bearer(user_id):
    def encode(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    unsigned = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode({"sub": f"contract-user-{user_id}", "userId": str(user_id), "roles": "USER", "exp": 1893456000})
    signature = base64.urlsafe_b64encode(hmac.new(b"public-synthetic-database-fixture-key", unsigned.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return "Bearer " + unsigned + "." + signature


def camel(name):
    first, *rest = name.split("_")
    return first + "".join(part.title() for part in rest)


class DatabaseFixture:
    def __init__(self, name):
        self.name = name
        self.service = "banking-" + name
        self.metadata = json.loads((ROOT / f"{name}-statements.json").read_text())
        self.pairs = []
        for entry in json.loads((ROOT / f"{name}-jdbc-bootstrap.json").read_text()):
            self.sql(entry["query"], entry["params"], entry["rows"])
            if not entry["params"]:
                self.sql(entry["query"], [], entry["rows"], simple=True)

    def query(self, *parts):
        return next(q for q in self.metadata if all(part in q.lower() for part in parts))

    def api(self, method, uri, request, response, user_id, status=200):
        pair = http.pair(self.service, "IN", self.service, 8080, method, uri, request, response, status)
        pair["http"]["req"]["headers"]["Authorization"] = [bearer(user_id)]
        if response is None:
            pair["http"]["res"].pop("contentType", None)
            pair["http"]["res"]["headers"] = {}
        self.pairs.append(pair)

    def dependency(self, host, method, uri, request, response, status=200):
        pair = http.pair(self.service, "OUT", host, 443 if ".com" in host else 80, method, uri, request, response, status)
        if response is None:
            pair["http"]["res"].pop("contentType", None)
            pair["http"]["res"]["headers"] = {}
        self.pairs.append(pair)
        return pair

    def sql(self, query, params, rows, simple=False, error=False):
        description = self.metadata[query]
        fields = repair.row_fields(description)
        values = []
        for row in rows:
            if isinstance(row, dict):
                row = [row[camel(field["name"])] for field in fields]
            assert len(row) == len(fields), "row width differs from schema"
            values.append({"dataRow": {"values": [{"isNull": True} if value is None else {"asString": str(value).replace("T", " ") if field["dataTypeOid"] in (1114, 1184) else str(value)} for value, field in zip(row, fields)]}})
        tag = "INSERT 0 1" if query.lower().startswith("insert") else "UPDATE 1" if query.lower().startswith("update") else f"SELECT {len(rows)}"
        messages = values + [{"complete": {"tag": tag}}]
        if error:
            messages = [{"error": {"severity": "ERROR", "severityUnlocalized": "ERROR", "code": "XX000", "msg": "Synthetic database unavailable"}}]
        parse = {"query": query, "parameterOids": description.get("ParamOIDs") or [], "describedParameterOids": description.get("ParamOIDs") or [], "describedRow": {"fields": fields}}
        bind = {"parseRequest": parse, "parameters": [{"isNull": True} if p is None else {"asString": str(p)} for p in params]}
        request = {"query": {"query": query}} if simple else {"execute": {"bindRequest": bind}}
        if simple:
            messages = ([{"rowDescription": {"fields": fields}}] if fields else []) + messages
        pair = http.pair(self.service, "OUT", "banking-postgres", 5432, "", "", None, None)
        pair.pop("http")
        pair.update(l7protocol="postgres", tech="Postgres", postgres={"request": request, "response": {"query" if simple else "execute": {"messages": messages}}})
        self.pairs.append(pair)
        if error and not simple:
            setup = copy.deepcopy(pair)
            setup["postgres"]["response"] = {"execute": {"messages": [{"complete": {"tag": "SELECT 0"}}]}}
            self.pairs.extend(repair.setup_pairs(setup, self.metadata, True))

    def finish(self):
        pairs = list(self.pairs)
        # Prepared descriptions are required when JDBC switches a statement to binary format.
        setup = [rr for pair in pairs for rr in repair.setup_pairs(pair, self.metadata, True)]
        pairs.extend({json.dumps(rr["postgres"], sort_keys=True): rr for rr in setup}.values())
        return pairs


def account(user_id, account_id, kind="SAVINGS", balance=125.75):
    return dict(id=account_id, userId=user_id, accountNumber=str(100000000000 + account_id), accountType=kind, balance=balance, createdAt=STAMP, updatedAt=STAMP)


def accounts_pairs():
    f = DatabaseFixture("accounts")
    select_user = f.query("select a1_0.id,a1_0.account_number", "where a1_0.user_id=$1")
    select_one = f.query("select a1_0.id,a1_0.account_number", "where a1_0.id=$1")
    unique = f.query("where a1_0.account_number=$1")
    insert = f.query("insert into accounts_service.accounts")
    update = f.query("update accounts_service.accounts")
    for user in range(91001, 91004):
        accounts = [account(user, 92000 + (user - 91000) * 10 + i, kind, 125.75 + i) for i, kind in enumerate(["CHECKING", "SAVINGS", "CREDIT"])]
        f.sql(select_user, [user], accounts)
        f.api("GET", "/api/accounts", None, accounts, user)
        for item in accounts:
            aid = item["id"]
            for suffix in ["", "/balance"]:
                f.sql(select_one, [aid, user], [item])
                response = item if not suffix else dict(accountId=aid, accountNumber=item["accountNumber"], balance=item["balance"])
                f.api("GET", f"/api/accounts/{aid}" + suffix, None, response, user)
            f.sql(select_one, [aid, user], [item])
            changed = dict(item, balance=item["balance"] + 10.25)
            f.sql(update, [item["accountNumber"], item["accountType"], changed["balance"], STAMP, user, aid], [])
            f.api("PUT", f"/api/accounts/{aid}/balance", {"balance": changed["balance"]}, None, user)
            f.sql(select_one, [aid, user], [changed])
            f.api("GET", f"/api/accounts/{aid}", None, changed, user)
        for i, kind in enumerate(["CHECKING", "SAVINGS", "CREDIT"]):
            item = account(user, 93000 + (user - 91000) * 10 + i, kind, 25.75 + i)
            f.sql(unique, [item["accountNumber"], 1], [])
            f.sql(insert, [item["accountNumber"], kind, item["balance"], STAMP, STAMP, user], [item])
            f.api("POST", "/api/accounts", {"accountType": kind, "initialBalance": item["balance"]}, item, user, 201)
        f.sql(select_one, [999999, user], [])
        f.api("GET", "/api/accounts/999999", None, None, user, 404)
    f.sql(select_user, [91998], [])
    f.api("GET", "/api/accounts", None, [], 91998)
    f.sql(select_user, [91999], [], error=True)
    f.api("GET", "/api/accounts", None, None, 91999, 500)
    return f.finish()


def grpc_check(f, account_id, user_id, amount, kind, approved=True):
    def text(field, value):
        value = str(value).encode()
        return bytes([field * 8 + 2, len(value)]) + value
    for mcc in (["6011"] if kind != "TRANSFER" else ["5411", "5812", "5912", "5732", "7995", "5999", "5541"]):
        request = text(1, account_id) + text(2, user_id) + b"\x19" + struct.pack("<d", amount) + text(4, kind) + text(5, mcc)
        response = (b"\x08\x01" if approved else b"") + text(3, "Synthetic approved" if approved else "Synthetic rejected")
        pair = f.dependency("banking-fraud:8080", "POST", "/fraud.v1.FraudChecker/CheckTransaction", None, None)
        pair["netinfo"]["upstream"].update(hostname="banking-fraud", port=8080)
        pair.update(tech="gRPC")
        for message, payload in [(pair["http"]["req"], request), (pair["http"]["res"], response)]:
            message["bodyBase64"] = base64.b64encode(b"\0" + struct.pack(">I", len(payload)) + payload).decode()
            message["headers"]["Content-Type"] = ["application/grpc"]
        pair["http"]["req"]["version"] = "2.0"
        pair["http"]["res"].update(contentType="application/grpc", trailers={"Grpc-Status": ["0"]})


def transactions_pairs():
    f = DatabaseFixture("transactions")
    select_user = f.query("select t1_0.id", "where t1_0.user_id=$1 order")
    select_account = f.query("select t1_0.id", "or t1_0.to_account_id")
    insert = f.query("insert into transactions_service.transactions")
    for user in range(91001, 91004):
        aid = 92000 + (user - 91000) * 10
        history = [dict(id=94000 + user - 91000, userId=user, fromAccountId=None, toAccountId=aid, amount=12.75, type="DEPOSIT", description="synthetic history", status="COMPLETED", createdAt=STAMP, processedAt=STAMP)]
        f.sql(select_user, [user], history)
        f.api("GET", "/api/transactions", None, history, user)
        f.sql(select_account, [user, aid, aid], history)
        f.api("GET", f"/api/transactions?accountId={aid}", None, history, user)
        for i, kind in enumerate(["DEPOSIT", "WITHDRAWAL", "TRANSFER"]):
            amount = 12.75 + i
            tid = 95000 + (user - 91000) * 10 + i
            item = dict(id=tid, userId=user, fromAccountId=aid if kind != "DEPOSIT" else None, toAccountId=aid + 1 if kind == "TRANSFER" else aid if kind == "DEPOSIT" else None, amount=amount, type=kind, description=f"synthetic {kind.lower()}", status="COMPLETED", createdAt=STAMP, processedAt=STAMP)
            grpc_check(f, aid, user, amount, kind)
            f.dependency("banking-accounts", "GET", f"/api/accounts/{aid}", None, account(user, aid))
            for account_id in ([aid, aid + 1] if kind == "TRANSFER" else [aid]):
                f.dependency("banking-accounts", "GET", f"/api/accounts/{account_id}/balance", None, dict(accountId=account_id, balance=125.75))
                value = 125.75 + amount if kind == "DEPOSIT" or account_id == aid + 1 else 125.75 - amount
                f.dependency("banking-accounts", "PUT", f"/api/accounts/{account_id}/balance", {"balance": value}, None)
            f.sql(insert, [amount, STAMP, item["description"], item["fromAccountId"], STAMP, "COMPLETED", item["toAccountId"], kind, user], [item])
            request = dict(accountId=aid, amount=amount, description=item["description"])
            if kind == "TRANSFER":
                request = dict(fromAccountId=aid, toAccountId=aid + 1, amount=amount, description=item["description"])
            payment_dependencies(f, item)
            f.api("POST", "/api/transactions/" + {"DEPOSIT": "deposit", "WITHDRAWAL": "withdraw", "TRANSFER": "transfer"}[kind], request, item, user, 201)
            # The create route dispatches through the same operations.
            item = dict(item, id=tid + 100, description=item["description"] + " create")
            grpc_check(f, aid, user, amount, kind)
            f.sql(insert, [amount, STAMP, item["description"], item["fromAccountId"], STAMP, "COMPLETED", item["toAccountId"], kind, user], [item])
            payment_dependencies(f, item)
            f.api("POST", "/api/transactions/create", dict(accountId=aid, toAccountId=aid + 1, amount=amount, type=kind, currency="USD", description=item["description"]), item, user, 201)
        f.api("POST", "/api/transactions/transfer", dict(fromAccountId=aid, toAccountId=aid, amount=1.25), None, user, 400)
    f.sql(select_user, [91998], [])
    f.api("GET", "/api/transactions", None, [], 91998)
    f.sql(select_user, [91999], [], error=True)
    f.api("GET", "/api/transactions", None, None, 91999, 500)
    user, aid = 91001, 92010
    grpc_check(f, aid, user, 990.25, "DEPOSIT", approved=False)
    f.api("POST", "/api/transactions/deposit", dict(accountId=aid, amount=990.25), None, user, 400)
    grpc_check(f, 999999, user, 12.75, "WITHDRAWAL")
    f.dependency("banking-accounts", "GET", "/api/accounts/999999", None, None, 404)
    f.api("POST", "/api/transactions/withdraw", dict(accountId=999999, amount=12.75), None, user, 400)
    grpc_check(f, aid, user, 500.25, "WITHDRAWAL")
    f.api("POST", "/api/transactions/withdraw", dict(accountId=aid, amount=500.25), None, user, 400)
    f.api("POST", "/api/transactions/create", dict(accountId=aid, amount=-1, type="DEPOSIT", currency="USD"), {"amount": "Amount must be greater than 0"}, user, 400)
    kafka_producer(f)
    return f.finish()


def kafka_producer(f):
    def pair(key, version, request, response):
        rr = http.pair(f.service, "OUT", "banking-kafka", 9092, "", "", None, None)
        rr.pop("http")
        rr.update(l7protocol="kafka", tech="Kafka", kafka={"request": dict(apiKey="KAFKA_API_KEY_" + key, apiVersion=version, **request), "response": response})
        f.pairs.append(rr)
    pair("API_VERSIONS", 3, {"ApiVersionsRequest": {}}, {"ApiVersionsResponse": {"error": "KAFKA_ERROR_NONE", "apiKeys": [dict(apiKey=k, minVersion=0, maxVersion=v) for k, v in [(0, 8), (3, 9), (18, 3), (22, 4)]]}})
    pair("METADATA", 9, {"MetadataRequest": {"topics": [{"name": "transaction-events"}]}}, {"MetadataResponse": {"brokers": [{"host": "banking-kafka", "port": 9092}], "clusterId": "synthetic-regression", "topics": [{"error": "KAFKA_ERROR_NONE", "name": "transaction-events", "partitions": [{"error": "KAFKA_ERROR_NONE", "replicaNodes": [0], "isrNodes": [0]}]}]}})
    pair("INIT_PRODUCER_ID", 4, {"InitProducerIdRequest": {"transactionTimeoutMs": 60000, "producerId": "-1", "producerEpoch": -1}}, {"InitProducerIdResponse": {"producerId": "1"}})
    pair("PRODUCE", 8, {"ProduceRequest": {"acks": -1, "timeoutMs": 30000, "topics": [{"name": "transaction-events", "partitions": [{"records": {"magic": 2, "attributes": ["ATTRIBUTE_NO_COMPRESSION"], "records": [{"valueString": "{}"}]}}]}]}}, {"ProduceResponse": {"topics": [{"name": "transaction-events", "partitions": [{"error": "KAFKA_ERROR_NONE", "baseOffset": "1", "logAppendTimeMs": "-1"}]}]}})


def payment_dependencies(f, transaction):
    amount, tid = transaction["amount"], transaction["id"]
    stripe = f.dependency("api.stripe.com", "POST", "/v1/payment_intents", None, {"id": f"synthetic-{tid}"})
    form = f"amount={int(amount)}&currency=usd&payment_method_types[]=card"
    stripe["http"]["req"]["bodyBase64"] = base64.b64encode(form.encode()).decode()
    stripe["http"]["req"]["headers"]["Content-Type"] = ["application/x-www-form-urlencoded"]
    f.dependency("api-m.sandbox.paypal.com", "POST", "/v2/checkout/orders", {"intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "USD", "value": f"{amount:.2f}"}}]}, {"id": f"synthetic-{tid}"})
    terms = [f"Transaction {tid}"]
    if transaction["type"] == "TRANSFER":
        terms.append(f"Transfer {transaction['fromAccountId']} to {transaction['toAccountId']}")
    for term in terms:
        f.dependency("api.complyadvantage.com", "POST", "/searches", {"search_term": term, "search_type": "individual", "fuzziness": 0.6}, {"content": {"data": {"total_matches": 0, "risk_level": "low"}}})
