# Nightly health

The `Nightly health` GitHub Actions workflow checks the schedules listed in `quality/nightly-checks.json` hourly on GitHub-hosted runners. This covers the GitLab runner cluster from outside GKE without another service. Completed failures alert immediately at the next hourly check; running jobs get a grace period. Disabled, missing, canceled, stale and incomplete runs also alert. The minimum job count guards against a green wrapper with skipped or absent checks.

Notifications use one Slack incoming webhook pointed at the team's prominent nightly/security channel. Changed failures and recovery send a consolidated message. Unchanged failures repeat once per UTC day, including across pipeline retries. The previous result lives in a 90-day GitHub Actions artifact from the default branch; pull-request artifacts are excluded. Keep one remediation issue per underlying failure and acknowledge it in the channel. There is no automatic ticket creation or SLA engine.

After merging the reporting changes, configure `NIGHTLY_GITLAB_TOKEN` with read API access to the listed GitLab projects, `NIGHTLY_GITHUB_TOKEN` with Actions read access to the two GitHub repositories, and `NIGHTLY_SLACK_WEBHOOK`. Test with `python3 quality/scripts/check-nightlies.py --dry-run`, then set repository variable `NIGHTLY_HEALTH_ENABLED=true` and dispatch the workflow on the default branch. Verify one failure, unchanged repeat and recovery before changing any existing notification integration. The workflow stays disabled until that variable is set.

The inventory is explicit: add an entry when adding a schedule and adjust minimum counts when changing a matrix. Nettap deliberately reports missing coverage until its intended schedule is created and its real ID replaces the null value. Inactive Decoy and inherited cron definitions in forks are not enabled by this change; their owner decisions remain in the alerting ticket.

This check depends on GitHub Actions. Its own execution or Slack-delivery failures use normal Actions failure notifications. Loss of its artifact resets notification deduplication; a lost Slack response can cause a repeated message. Existing CI alerts remain available during rollout. Scanner reports and quality reports retain for 90 days in their original repositories.

Run `python3 quality/scripts/test-check-nightlies.py` for the offline health-check tests. They never send Slack messages or access clusters.
