#!/usr/bin/env python3
"""Check scheduled CI runs and consolidate their notifications."""

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def age_hours(value, now):
    return (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds() / 3600


def fetch(host, path):
    base = "https://gitlab.com/api/v4" if host == "gitlab" else "https://api.github.com"
    token = os.environ["NIGHTLY_GITLAB_TOKEN" if host == "gitlab" else "NIGHTLY_GITHUB_TOKEN"]
    headers = {"PRIVATE-TOKEN": token} if host == "gitlab" else {"Authorization": "Bearer " + token}
    with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=20) as response:
        return json.load(response)


def observe(check, get, now):
    host, repo = check["host"], check["repo"]
    row = {"check": check["name"], "repo": repo, "problem": "", "url": check["url"]}
    try:
        if host == "gitlab":
            base = "/projects/" + str(check["project"])
            if not check.get("schedule"):
                return dict(row, problem="scheduled scan is not configured")
            schedule = get(host, base + "/pipeline_schedules/" + str(check["schedule"]))
            if not schedule["active"]:
                return dict(row, problem="schedule disabled")
            runs = get(host, base + "/pipeline_schedules/" + str(check["schedule"]) + "/pipelines?sort=desc&per_page=20")
        else:
            base = "/repos/" + repo
            workflow = get(host, base + "/actions/workflows/" + check["workflow"])
            if workflow["state"] != "active":
                return dict(row, problem="workflow disabled")
            runs = get(host, base + "/actions/workflows/" + check["workflow"] + "/runs?event=schedule&per_page=20")["workflow_runs"]
        for run in runs:
            status = (run.get("conclusion") or run["status"]) if host == "github" else run["status"]
            row.update(url=run.get("web_url") or run["html_url"], run_id=run["id"], commit=run.get("sha") or run.get("head_sha"), created_at=run["created_at"], status=status)
            if status not in {"success", "failed", "failure", "canceled", "cancelled", "skipped", "timed_out", "neutral", "action_required", "stale"}:
                if age_hours(run["created_at"], now) > check["grace_hours"]:
                    return dict(row, problem="run stuck or queued beyond its grace period")
                continue
            if age_hours(run["created_at"], now) > check["max_age_hours"]:
                return dict(row, problem="completed run is stale")
            jobs_path = "/pipelines/" + str(run["id"]) + "/jobs?per_page=100" if host == "gitlab" else "/actions/runs/" + str(run["id"]) + "/jobs?filter=latest&per_page=100"
            jobs = get(host, base + jobs_path)
            jobs = jobs if host == "gitlab" else jobs["jobs"]
            jobs = [j for j in jobs if j["name"] not in check.get("ignore_jobs", [])]
            bad = Counter(j["name"].split(": [", 1)[0] for j in jobs if (j.get("conclusion") or j["status"]) != "success")
            if bad:
                return dict(row, problem="failed/incomplete checks: " + ", ".join(f"{name} ({count})" for name, count in sorted(bad.items())))
            if status != "success" or len(jobs) < check["min_jobs"]:
                return dict(row, problem="run " + status + " or required checks missing")
            return row
        return dict(row, problem="no completed scheduled run")
    except urllib.error.HTTPError as error:
        return dict(row, problem="cannot read schedule: HTTP " + str(error.code))
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        return dict(row, problem="cannot read schedule or job results")


def evaluate(checks, previous, get, send, now):
    rows = [observe(check, get, now) for check in checks]
    bad = {r["repo"] + "/" + r["check"]: r["problem"] for r in rows if r["problem"]}
    day = now.date().isoformat()
    changed = bad != previous.get("bad", {})
    if changed or (bad and previous.get("notified_day") != day):
        title = "Nightly checks need attention" if bad else "Nightly checks recovered"
        lines = [f"{r['repo']} / {r['check']}: {r['problem']}\n{r['url']}" for r in rows if r["problem"]]
        send("\n\n".join([title, *lines, "Owner: engineering on-call. Review the run logs and retained scanner reports."]))
        previous = dict(previous, notified_day=day)
    return {"bad": bad, "notified_day": previous.get("notified_day"), "checks": rows}


def notify(text):
    url = os.environ["NIGHTLY_SLACK_WEBHOOK"]
    if urllib.parse.urlparse(url).hostname != "hooks.slack.com" or not url.startswith("https://"):
        raise ValueError("nightly webhook must be a Slack HTTPS incoming webhook")
    request = urllib.request.Request(url, data=json.dumps({"text": text, "unfurl_links": False}).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.read().strip() != b"ok":
                raise RuntimeError("nightly notification was not accepted")
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("nightly notification failed") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    checks = json.loads(Path("quality/nightly-checks.json").read_text())
    prior = Path("previous/nightly-health.json")
    previous = json.loads(prior.read_text()) if prior.exists() else {}
    state = evaluate(checks, previous, fetch, print if args.dry_run else notify, datetime.now(timezone.utc))
    summary = "\n".join(f"- {r['repo']} / {r['check']}: {r['problem'] or 'healthy'} ({r['url']})" for r in state["checks"])
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(os.environ["GITHUB_STEP_SUMMARY"]).write_text(summary + "\n")
    if not args.dry_run:
        Path("nightly-health.json").write_text(json.dumps(state, indent=2) + "\n")
