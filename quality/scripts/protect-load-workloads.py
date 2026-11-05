#!/usr/bin/env python3
"""Keep voluntary node drains out of the measured load window."""
import json
import os
import signal
import subprocess
import sys
import time
import uuid

LABEL = "quality.speedscale.com/load-protection"


def kubectl(namespace, args, document=None):
    result = subprocess.run(["kubectl", "--request-timeout=15s", "-n", namespace, *args],
                            input=json.dumps(document) if document is not None else None,
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError("Load protection: kubectl " + args[0] + " failed: " + result.stderr[:500])
    return json.loads(result.stdout) if args[0] != "delete" and result.stdout.strip() else {}


def lease_job(name, namespace):
    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": name, "namespace": namespace, "labels": {LABEL: name}},
            "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 3600,
                     "ttlSecondsAfterFinished": 60,
                     "template": {"metadata": {"annotations": {
                         "cluster-autoscaler.kubernetes.io/safe-to-evict": "false",
                         "sidecar.istio.io/inject": "false"}},
                         "spec": {"restartPolicy": "Never", "automountServiceAccountToken": False,
                                  "containers": [{"name": "lease", "image": "python:3.13-slim",
                                      "command": ["python3", "-c", "import time; time.sleep(3600)"],
                                      "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                                                    "limits": {"cpu": "100m", "memory": "128Mi"}},
                                      "securityContext": {"runAsNonRoot": True, "runAsUser": 65532,
                                          "allowPrivilegeEscalation": False,
                                          "capabilities": {"drop": ["ALL"]}}}]}}}}


def protect(namespace, command):
    name = "daily-load-guard-" + uuid.uuid4().hex[:10]
    deployments = kubectl(namespace, ["get", "deployments", "-o", "json"])["items"]
    deployments = [d for d in deployments if d["metadata"]["name"].startswith("banking-")]
    if not deployments:
        raise RuntimeError("No banking workloads available for load protection")
    created = False
    child = None

    def interrupted(signum, frame):
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise SystemExit(128 + signum)

    previous = {s: signal.signal(s, interrupted) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        job = kubectl(namespace, ["create", "-f", "-", "-o", "json"], lease_job(name, namespace))
        created = True
        owner = [{"apiVersion": "batch/v1", "kind": "Job", "name": name,
                  "uid": job["metadata"]["uid"]}]
        for deployment in deployments:
            pdb = {"apiVersion": "policy/v1", "kind": "PodDisruptionBudget",
                   "metadata": {"name": name + "-" + deployment["metadata"]["name"],
                                "namespace": namespace, "labels": {LABEL: name},
                                "ownerReferences": owner},
                   "spec": {"maxUnavailable": 0, "selector": deployment["spec"]["selector"]}}
            kubectl(namespace, ["create", "-f", "-", "-o", "json"], pdb)
        deadline = time.monotonic() + 300
        while True:
            budgets = kubectl(namespace, ["get", "pdb", "-l", LABEL + "=" + name, "-o", "json"])["items"]
            if len(budgets) == len(deployments) and all(
                b.get("status", {}).get("observedGeneration", 0) >= b["metadata"]["generation"]
                and b["status"].get("expectedPods", 0) > 0
                and b["status"].get("currentHealthy", 0) >= b["status"].get("desiredHealthy", 1)
                and b["status"].get("disruptionsAllowed") == 0 for b in budgets
            ):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Workloads did not become healthy with load protection active")
            time.sleep(5)
        print(f"Protected {len(budgets)} banking workloads from voluntary eviction for load", flush=True)
        child = subprocess.Popen(command, env=dict(os.environ, PROXYMOCK_LOAD_PROTECTED="true"))
        return child.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if created:
            kubectl(namespace, ["delete", "job", name, "--ignore-not-found", "--wait=false"])
            print("Removed load protection lease; owned disruption budgets are being collected", flush=True)


if __name__ == "__main__":
    sys.exit(protect(sys.argv[1], sys.argv[2:]))
