#!/usr/bin/env python3
"""Remove unusable recorded Postgres responses without dropping regression requests."""
import argparse
import base64
from collections import Counter
import copy
import json
from pathlib import Path
import uuid


def invalid_response(pair):
    if pair.get("direction") != "OUT" or "postgres" not in pair:
        return None
    pg = pair["postgres"]
    request, response = pg.get("request", {}), pg.get("response", {})
    for command in ("parse", "bind"):
        if command in request and not response.get(command):
            return command + " has no recorded acknowledgement"
    for command in ("query", "describe", "execute"):
        if command not in request:
            continue
        messages = response.get(command, {}).get("messages", [])
        if not messages:
            return command + " has no recorded response"
        if command == "execute":
            query = request[command].get("bindRequest", {}).get("parseRequest", {}).get("query", "")
            if query.strip(" \t\r\n;") and all("empty" in message for message in messages):
                return "non-empty query recorded as EmptyQuery"
    return None


def setup_pairs(pair, metadata):
    pg = pair.get("postgres", {})
    bind = pg.get("request", {}).get("execute", {}).get("bindRequest")
    messages = pg.get("response", {}).get("execute", {}).get("messages", [])
    if not bind or not any("complete" in m for m in messages) or any("error" in m for m in messages):
        return []
    parse = bind.get("parseRequest", {})
    description = metadata.get(parse.get("query", ""))
    if not description or "error" in description:
        return []
    formats = bind.get("resultFormatCodes", [])
    fields = []
    for index, field in enumerate(description.get("Fields") or []):
        fields.append({"name": field["Name"], "tableOid": field["TableOID"],
                       "tableAttributeNumber": field["TableAttributeNumber"], "dataTypeOid": field["DataTypeOID"],
                       "dataTypeSize": field["DataTypeSize"] % (1 << 32), "typeModifier": field["TypeModifier"],
                       "format": formats[0] if len(formats) == 1 else formats[index] if formats else 0})
    replies = [("parse", parse, {"complete": {}}),
               ("bind", bind, {"complete": {}}),
               ("describe", {"objectType": "OBJECT_TYPE_PORTAL", "name": bind.get("portal", ""), "parseRequest": parse},
                {"messages": [{"rowDescription": {"fields": fields}}] if fields else [{"noData": {}}]})]
    result = []
    for command, request, response in replies:
        rr = copy.deepcopy(pair)
        rr["uuid"] = base64.b64encode(uuid.uuid5(uuid.NAMESPACE_URL, pair["uuid"] + "/" + command).bytes).decode()
        rr["postgres"] = {"request": {command: copy.deepcopy(request)}, "response": {command: response}}
        rr.pop("signature", None)
        rr.setdefault("tags", {})["source"] = "repaired-protocol-metadata"
        result.append(rr)
    return result


def repair(pairs, catalog=None, metadata=None, replace_setup=False):
    repaired, removed = [], Counter()
    for pair in pairs:
        if pair.get("tags", {}).get("source") == "repaired-protocol-metadata":
            continue
        pg = pair.get("postgres", {})
        request = pg.get("request", {})
        if replace_setup and pair.get("direction") == "OUT":
            if any(command in request for command in ("parse", "bind", "describe")):
                response = pg.get("response", {})
                if any("error" in response.get(command, {}) for command in ("parse", "bind")) or any(
                        "error" in message for message in response.get("describe", {}).get("messages", [])):
                    repaired.append(pair)
                continue
            for command in ("query", "execute"):
                query = request.get(command, {}).get("query", "") if command == "query" else request.get(command, {}).get("bindRequest", {}).get("parseRequest", {}).get("query", "")
                description = (metadata or {}).get(query)
                if description:
                    count = len(description.get("Fields") or [])
                    if any("dataRow" in message and len(message["dataRow"].get("values", [])) != count
                           for message in pg.get("response", {}).get(command, {}).get("messages", [])):
                        removed["row width differs from schema"] += 1
                        break
            else:
                reason = invalid_response(pair)
                if reason:
                    removed[reason] += 1
                else:
                    repaired.append(pair)
                continue
            continue
        reason = invalid_response(pair)
        if reason:
            removed[reason] += 1
        else:
            repaired.append(pair)
    if metadata:
        setup = [r for pair in repaired for r in setup_pairs(pair, metadata)]
        repaired.extend(setup)
    if catalog:
        catalog = copy.deepcopy(catalog)
        recorded = [p["ts"] for p in pairs if p.get("direction") == "IN" and "ts" in p]
        if recorded:
            catalog["ts"] = min(recorded)
        query = catalog["postgres"]["request"]["query"]["query"]
        repaired = [p for p in repaired if p.get("postgres", {}).get("request", {}).get("query", {}).get("query") != query]
        repaired.append(catalog)
    return repaired, removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--replace-setup", action="store_true")
    args = parser.parse_args()
    pairs = [json.loads(line) for line in args.source.read_text().splitlines() if line.strip()]
    catalog = json.loads(args.catalog.read_text()) if args.catalog else None
    metadata = json.loads(args.metadata.read_text()) if args.metadata else None
    repaired, removed = repair(pairs, catalog, metadata, args.replace_setup)
    before = [p for p in pairs if p.get("direction") == "IN"]
    after = [p for p in repaired if p.get("direction") == "IN"]
    if before != after:
        raise ValueError("regression requests changed")
    args.output.write_text("".join(json.dumps(pair) + "\n" for pair in repaired))
    print(json.dumps({"regressionRequestsPreserved": len(before), "removedDependencyRecords": removed}))


if __name__ == "__main__":
    main()
