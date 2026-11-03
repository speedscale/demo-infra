#!/usr/bin/env python3
"""Run fraud load against cluster DNS, independently of kubectl tunnels."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

RESULT_MARKER = "FRAUD_LOAD_RESULT="


def install_proxymock(destination):
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    url = f"https://downloads.speedscale.com/proxymock/proxymock-linux-{arch}"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url + ".sha256", timeout=60) as response:
                expected = response.read().decode().strip()
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError("Published proxymock checksum did not match")
            destination.chmod(0o755)
            return
        except Exception:
            destination.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(5)


def worker():
    work = Path("/tmp/fraud-load")
    work.mkdir()
    binary = work / "proxymock"
    install_proxymock(binary)
    subprocess.run([str(binary), "version", "--client"], check=True)
    config_file = work / "config.yaml"
    shutil.copyfile("/config/config.yaml", config_file)
    config_file.chmod(0o600)
    config = ["--config", str(config_file)]
    snapshot = work / "snapshot"
    for attempt in range(3):
        pulled = subprocess.run([str(binary), "cloud", "pull", "snapshot", os.environ["SNAPSHOT_ID"],
                                 *config, "--out", str(snapshot)], timeout=300)
        if pulled.returncode == 0 and any(snapshot.rglob("*.md")):
            break
        if attempt == 2:
            raise RuntimeError("No snapshot available after three pull attempts")
        time.sleep(15)
    results = work / "results"
    replay_log = work / "replay.log"
    command = [str(binary), "replay", *config, "--in", str(snapshot), "--out", str(results),
               "--test-against", os.environ["LOAD_TARGET"], "--rewrite-host", "--ignore-body-changes",
               *json.loads(os.environ["LOAD_ARGS"]), "--fail-if", "requests.failed > 0"]
    print("Running fraud spike against " + os.environ["LOAD_TARGET"], flush=True)
    with replay_log.open("w") as output:
        replay = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=540)
    lines = replay_log.read_text(errors="replace").splitlines()
    print("\n".join(lines[:15] + lines[-65:]), flush=True)
    report = work / "report.json"
    for attempt in range(3):
        report.unlink(missing_ok=True)
        generated = subprocess.run([str(binary), "report", *config, "--in", str(results),
                                    "--out", str(report)], timeout=120)
        if generated.returncode == 0 and report.is_file():
            data = json.loads(report.read_text())
            validate_report(data)
            print(RESULT_MARKER + json.dumps({"exit_code": replay.returncode, "report": data}), flush=True)
            return replay.returncode
        if attempt < 2:
            time.sleep(15)
    raise RuntimeError("Could not generate fraud load report")


def validate_report(report):
    buckets = report["reliability"]["statusBreakdown"]
    if any(not isinstance(b["count"], int) or b["count"] < 0 for b in buckets):
        raise ValueError("Invalid fraud load request counts")
    if not buckets or sum(b["count"] for b in buckets) <= 0:
        raise ValueError("Fraud load report contains no requests")


def decode_result(log):
    results = [json.loads(line[len(RESULT_MARKER):]) for line in log.splitlines()
               if line.startswith(RESULT_MARKER)]
    if len(results) != 1:
        raise ValueError("Missing or ambiguous fraud load result")
    result = results[0]
    validate_report(result["report"])
    return result


def job_spec(name, namespace, snapshot, load_args):
    return {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 1200, "ttlSecondsAfterFinished": 600,
                 "template": {"spec": {
                     "restartPolicy": "Never", "automountServiceAccountToken": False,
                     "securityContext": {"runAsUser": 65532, "runAsGroup": 65532, "fsGroup": 65532},
                     "volumes": [
                         {"name": "config", "secret": {"secretName": name}},
                         {"name": "script", "configMap": {"name": name}},
                         {"name": "work", "emptyDir": {"sizeLimit": "2Gi"}}],
                     "containers": [{
                         "name": "load", "image": "python:3.13-slim", "imagePullPolicy": "Always",
                         "command": ["python3", "-u", "/scripts/worker.py", "--worker"],
                         "env": [{"name": "HOME", "value": "/tmp"},
                                 {"name": "SPEEDCTL_HOME", "value": "/tmp/.speedscale"},
                                 {"name": "SPEEDSCALE_HOME", "value": "/tmp/.speedscale"},
                                 {"name": "SNAPSHOT_ID", "value": snapshot},
                                 {"name": "LOAD_TARGET", "value": f"banking-fraud.{namespace}.svc.cluster.local:8080"},
                                 {"name": "LOAD_ARGS", "value": json.dumps(load_args)}],
                         "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                       "limits": {"cpu": "2", "memory": "2Gi"}},
                         "securityContext": {"allowPrivilegeEscalation": False,
                                             "capabilities": {"drop": ["ALL"]}},
                         "volumeMounts": [{"name": "config", "mountPath": "/config", "readOnly": True},
                                          {"name": "script", "mountPath": "/scripts", "readOnly": True},
                                          {"name": "work", "mountPath": "/tmp"}]}]}}}}


def controller(namespace, snapshot, config, report_file, load_args):
    name = "proxymock-fraud-load-" + uuid.uuid4().hex[:10]
    report_file = Path(report_file)
    report_file.unlink(missing_ok=True)

    def kubectl(args, data=None, retries=1):
        for attempt in range(retries):
            try:
                result = subprocess.run(["kubectl", "--request-timeout=20s", "-n", namespace, *args],
                                        input=json.dumps(data) if data is not None else None,
                                        capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                result = subprocess.CompletedProcess(args, 1, "", "request timed out")
            if result.returncode == 0:
                return result.stdout
            if attempt + 1 < retries:
                time.sleep(5)
        raise RuntimeError("kubectl " + args[0] + " failed: " + result.stderr[:1000])

    try:
        created = json.loads(kubectl(["create", "-f", "-", "-o", "json"],
                                     job_spec(name, namespace, snapshot, load_args)))
        owner = [{"apiVersion": "batch/v1", "kind": "Job", "name": name,
                  "uid": created["metadata"]["uid"]}]
        metadata = {"name": name, "namespace": namespace, "ownerReferences": owner}
        kubectl(["create", "-f", "-"], {"apiVersion": "v1", "kind": "Secret", "metadata": metadata,
                "immutable": True, "data": {"config.yaml": base64.b64encode(Path(config).read_bytes()).decode()}})
        kubectl(["create", "-f", "-"], {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata,
                "immutable": True, "data": {"worker.py": Path(__file__).read_text()}})
        print(f"In-cluster fraud load job: {namespace}/{name}", flush=True)
        deadline = time.monotonic() + 1260
        complete = False
        while time.monotonic() < deadline:
            status = json.loads(kubectl(["get", "job", name, "-o", "json"], retries=6)).get("status", {})
            terminal = [c for c in status.get("conditions", [])
                        if c.get("status") == "True" and c["type"] in ("Complete", "Failed")]
            if terminal:
                complete = terminal[0]["type"] == "Complete"
                break
            time.sleep(5)
        log = kubectl(["logs", "job/" + name], retries=6)
        (report_file.parent / "in-cluster.log").write_text(log)
        result = decode_result(log)
        report_file.write_text(json.dumps(result["report"], indent=2) + "\n")
        print("\n".join(line for line in log.splitlines() if not line.startswith(RESULT_MARKER)), flush=True)
        print("Proxymock report: " + str(report_file), flush=True)
        failed_requests = sum(b["count"] for b in result["report"]["reliability"]["statusBreakdown"]
                              if b["bucket"] in ("5xx", "other"))
        return 0 if complete and result["exit_code"] == 0 and failed_requests == 0 else 1
    finally:
        # Job ownership removes its credential Secret and script ConfigMap too.
        try:
            kubectl(["delete", "job", name, "--ignore-not-found", "--wait=false"], retries=3)
        except Exception as error:
            print("Load job cleanup failed; its TTL remains active: " + str(error), file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        sys.exit(worker())
    sys.exit(controller(*sys.argv[1:5], sys.argv[5:]))
