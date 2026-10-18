---
name: github-release-monitor
description: Monitor GitHub repositories for new releases and tags.
version: 0.1.0
metadata:
  hermes:
    tags: [github, releases, tags, monitoring, version-tracking]
---

# GitHub release monitor

Monitor public GitHub Releases and Git tags independently, retaining a baseline for each enabled feed. The implementation uses Python's standard library and the unauthenticated GitHub REST API; no token is required for public repositories.

## When to use

Use for recurring checks on public GitHub repositories. Initialize before the first scheduled check. Private repositories and authenticated API access are outside this monitor's current scope.

## Install into Hermes (runtime use)

Consumers install the skill directly from the GitHub tap; **cloning the repository is not required to use it**:

```sh
hermes skills install netanh/hermes-addons/skills/github-release-monitor
```

Hermes security-scans the third-party package and stores the installed skill and its referenced support files under the active profile's `$HERMES_HOME/skills/github-release-monitor/` (normally `~/.hermes/skills/github-release-monitor/`). Use that installed copy for normal operation. If the repository is private, follow Hermes' Skills Hub instructions for configuring `GITHUB_TOKEN`; do not put credentials in the skill config.

Store operational data **directly inside the installed skill directory** so it travels with that skill for straightforward backup. The published package contains only `examples/config.json`; runtime files (`config.json`, `state.json`) are user-created and must never be committed to the source repo. Back up the entire skill directory before reinstalling or updating the skill, then verify these local files remain present afterward:

```sh
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/github-release-monitor"
cp "$SKILL_DIR/examples/config.json" "$SKILL_DIR/config.json"
```

Edit `$SKILL_DIR/config.json` as needed. The installed [example config](examples/config.json) tracks `NousResearch/hermes-agent` and `netanh/hermes-addons`, with both releases and tags enabled.

## First run and normal operation

Initialize once, then check:

```sh
python3 "$SKILL_DIR/scripts/monitor.py" init \
  --config "$SKILL_DIR/config.json" \
  --state "$SKILL_DIR/state.json"

python3 "$SKILL_DIR/scripts/monitor.py" check \
  --config "$SKILL_DIR/config.json" \
  --state "$SKILL_DIR/state.json"
```

Review the `[init]` output before scheduling. `init` establishes a full independent snapshot for each enabled feed. Re-running it skips feeds that already have a snapshot and initializes missing feeds; `init --force` re-baselines enabled feeds for configured repositories without deleting unrelated repository state.

`check` fetches all pages for each enabled feed (100 items per API request), compares the complete current inventory with the saved snapshot, and reports every newly observed item. It does not assume the API's first item is chronologically latest. A new release does not suppress new tags, or vice versa. Partial errors are reported while successful feeds continue and update. Success clears only that feed's error streak. A missing full snapshot—including state from the older latest-only format—is reported as `MISSING BASELINE`; run `init` to establish it before tracking deltas. This one-time initialization records what exists now, so it cannot reconstruct events missed before the full snapshot was created.

## Configuration format

```json
{
  "repos": [
    "NousResearch/hermes-agent",
    "netanh/hermes-addons"
  ],
  "check_releases": true,
  "check_tags": true
}
```

- `repos` (**required**): non-empty, unique list of `owner/repo` strings.
- `check_releases` (default `true`): scan every page of published non-prerelease releases (drafts and prereleases are excluded, matching GitHub's latest full-release behavior).
- `check_tags` (default `true`): scan every page of Git tags independently, even when releases exist.
- At least one feed must be enabled. Unknown fields and malformed values are rejected. Every page is scanned so tags/releases between checks are not silently skipped. API call volume grows with repository history; schedule modestly and watch for rate-limit errors.

The executable is [scripts/monitor.py](scripts/monitor.py). Hermes' GitHub installer downloads the `SKILL.md` and its explicitly referenced support files; a repository checkout is needed only by maintainers who want to change source or run tests.

## State file and lifecycle

The state file is a JSON object keyed by repository. `known_releases` stores release IDs as strings; `known_tags` stores tag names. These are complete inventory snapshots, not only the most recent item. `last_checked` is an ISO-8601 timestamp. A repository with API failures may also contain per-feed error streaks:

```json
{
  "NousResearch/hermes-agent": {
    "known_releases": ["12345", "12346"],
    "known_tags": ["v2026.9.14", "v2026.9.15", "v2026.9.16"],
    "last_checked": "2026-10-18T12:00:00+00:00",
    "errors": {
      "release": {
        "first_error_at": "2026-10-18T06:00:00+00:00",
        "last_error_at": "2026-10-18T12:00:00+00:00",
        "count": 25,
        "last_message": "HTTP 403: API rate limit exceeded"
      }
    }
  }
}
```

Each check fetches all pages before replacing that feed's snapshot. If any page fails, the incomplete feed snapshot is not advanced; a successful independent feed can still update. Success clears only that feed's error record. `errors` is omitted when no feeds are failing; each error stores first/last failure times, count, and latest message (truncated to 500 characters). State writes use a unique temporary file followed by atomic replacement. Prevent overlapping invocations against one state file; atomic replacement does not serialize concurrent read/modify/write runs.

A full set-diff detects tags/releases that are still present at the next poll, regardless of where they appeared in the API's ordering. It cannot detect an item created and deleted between polls, or a tag deleted and recreated with the same name. Polling is not a substitute for a GitHub webhook/event log. Scanning every page costs more API requests for repositories with long histories.

## Output and error handling

Multiple releases/tags discovered between polls are all emitted, for example:

```text
[netanh/hermes-addons] NEW TAG: v1.0.1 — https://github.com/netanh/hermes-addons/releases/tag/v1.0.1
[netanh/hermes-addons] NEW TAG: v1.0.2 — https://github.com/netanh/hermes-addons/releases/tag/v1.0.2
[netanh/hermes-addons] NEW TAG: v1.0.3 — https://github.com/netanh/hermes-addons/releases/tag/v1.0.3
```

If both feeds change, both events are reported in the same check. With no changes and no API failures, stdout is empty. API failures are tracked per feed and reported to stdout on every failed check as `ERROR`; the label becomes `PERSISTENT ERROR` after six hours of continuous failure. Successful feeds still update and clear only their own error streak. Operational errors go to stdout; invalid config/state/CLI errors are fatal, printed to stderr by argparse, and return non-zero. No separate error log is created; error details and counters are stored in state. The monitor does not retry HTTP errors. The full-inventory scan finds items still present at check time; it cannot reconstruct tags/releases deleted before a poll or detect a removed-and-recreated tag with the same identifier.

## Scheduling with Hermes cronjob

The cronjob script loader accepts a **bare filename** resolved under `$HERMES_HOME/scripts/`, not an arbitrary path. Create a wrapper there that invokes the **installed** skill directory and keeps state/config alongside it. Example wrapper `$HERMES_HOME/scripts/github-release-monitor.sh`:

```sh
#!/bin/sh
set -eu
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/github-release-monitor"
exec python3 "$SKILL_DIR/scripts/monitor.py" check \
  --config "$SKILL_DIR/config.json" \
  --state "$SKILL_DIR/state.json"
```

Initialize the config/state files in `SKILL_DIR` once before creating the job. A generic job example (replace the notification destination):

```text
cronjob_manage(
  action="create",
  name="github-release-monitor",
  schedule="0,15,30,45 * * * *",
  no_agent=true,
  script="github-release-monitor.sh",
  deliver="telegram:<chat_id>",
  failure_deliver="telegram:<chat_id>"
)
```

With `no_agent=true`, stdout is delivered verbatim and empty stdout sends nothing; process failures use `failure_deliver`. Because operational API errors are stdout, they are delivered as monitor output. Check for an existing job before creating a duplicate and prevent overlapping invocations. Plain cron can call the same wrapper.

## Verification and maintenance

Maintainers may clone the source repository to develop or run mocked tests; runtime users do not need the checkout. From the source repository root:

```sh
python3 -m unittest discover -s skills/github-release-monitor/tests -v
```

Before updating/reinstalling, back up the full installed skill directory, including `config.json` and `state.json`; verify those runtime files after the operation. Runtime users do not need a source checkout.
