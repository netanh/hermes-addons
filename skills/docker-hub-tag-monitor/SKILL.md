---
name: docker-hub-tag-monitor
description: Monitor Docker Hub image tags from a saved baseline.
version: 0.1.0
metadata:
  hermes:
    tags: [docker, docker-hub, containers, tags, monitoring]
---

# Docker Hub tag monitor

Monitor public Docker Hub repositories for new tags, retaining one selected baseline per image. The implementation uses only the Python standard library and the unauthenticated Docker Hub API v2.

## When to use

Use for recurring checks of one or more public Docker Hub images. Initialize a baseline before the first scheduled check. Private registries are outside this monitor's current scope.

## Install into Hermes (runtime use)

Consumers install the skill from the GitHub tap; **cloning the repository is not required to use it**:

```sh
hermes skills install netanh/hermes-addons/skills/docker-hub-tag-monitor
```

Hermes security-scans the third-party package and stores the installed skill and its referenced support files under the active profile's `$HERMES_HOME/skills/docker-hub-tag-monitor/` (normally `~/.hermes/skills/docker-hub-tag-monitor/`). Use that installed copy for normal operation. If the repository is private, follow Hermes' Skills Hub instructions for configuring `GITHUB_TOKEN`; do not place credentials in this skill's config.

Store operational data **directly inside the installed skill directory** so it travels with that skill for straightforward backup. The published package contains only `examples/config.json`; runtime files (`config.json`, `state.json`, `errors.log`) are user-created and must never be committed to the source repo. Back up the entire skill directory before reinstalling or updating the skill, then verify these local files remain present afterward:

```sh
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/docker-hub-tag-monitor"
cp "$SKILL_DIR/examples/config.json" "$SKILL_DIR/config.json"
```

Edit `$SKILL_DIR/config.json` for your repositories. The installed [example config](examples/config.json) monitors `nousresearch/hermes-agent` and accepts tags beginning with `v`.

## First run and normal operation

Initialize once, then check:

```sh
python3 "$SKILL_DIR/scripts/monitor.py" init \
  --config "$SKILL_DIR/config.json" \
  --state "$SKILL_DIR/state.json" \
  --error-log "$SKILL_DIR/errors.log"

python3 "$SKILL_DIR/scripts/monitor.py" check \
  --config "$SKILL_DIR/config.json" \
  --state "$SKILL_DIR/state.json" \
  --error-log "$SKILL_DIR/errors.log"
```

Review `[init]` output and confirm each intended image has the expected baseline before scheduling. `init` is additive: it skips images that already have a `latest_tag` and initializes missing images. `init --force` re-baselines only configured images and preserves unrelated state entries. Use force only when a reset is intended.

Running `check` without an initialized baseline does **not** silently baseline: the selected candidate is reported as `NEW TAG` and saved. Initialize first if existing tags should not cause a first-run alert.

## Configuration format

```json
{
  "images": ["nousresearch/hermes-agent"],
  "page_size": 100,
  "error_log_max_bytes": 52400,
  "exclude_tags": ["latest", "nightly"],
  "image_settings": {
    "nousresearch/hermes-agent": {"tag_regex": "^v"}
  }
}
```

- `images` (**required**): non-empty, unique list of lowercase `namespace/image` names.
- `page_size` (default `100`): integer 1–100; only this many recent tags are examined per image.
- `exclude_tags` (default `[]`): exact tag names to skip.
- `image_settings` (default `{}`): per-image settings. `tag_regex`, when supplied, is a Python regular expression applied with `search`.
- `error_log_max_bytes` (default `52400`): non-negative maximum error-log size; `0` disables the error log.

Invalid JSON, image names, option types, page sizes, or regular expressions are rejected before API checks. For each image, the first API-ordered tag passing both exclusions and regex is selected. If no tag passes, there is no fallback to a filtered-out tag.

The executable is [scripts/monitor.py](scripts/monitor.py). Hermes' GitHub installer downloads the `SKILL.md` and its explicitly referenced support files; a repository checkout is needed only by maintainers who want to change the source or run tests.

## State file and lifecycle

The state file is a JSON object keyed by image. A normal entry looks like:

```json
{
  "nousresearch/hermes-agent": {
    "latest_tag": "v2026.9.14",
    "last_updated": "2026-09-21T00:00:00Z",
    "last_checked": "2026-09-21T00:00:00+00:00"
  }
}
```

On API failures, the image entry can include an `errors` object:

```json
"errors": {
  "first_error_at": "2026-09-21T00:00:00+00:00",
  "last_error_at": "2026-09-21T00:15:00+00:00",
  "count": 2,
  "last_message": "HTTP 429 for Docker Hub API"
}
```

Successful checks clear that image's error streak. State writes use a unique temporary file and atomic replacement. Do not run overlapping checks against the same state file: atomic replacement prevents partial JSON but does not serialize concurrent updates.

## Output and error handling

New tags produce stdout such as:

```text
[nousresearch/hermes-agent] NEW TAG: v2026.9.15 — https://hub.docker.com/r/nousresearch/hermes-agent/tags
```

A successful unchanged check prints nothing. API failures are caught per image, printed to stdout as `[image] ERROR: ...`, and appended to the configured error log; after six hours of continuous failure the line is labeled `PERSISTENT ERROR (>6h)`. Fatal config/state/CLI errors go to stderr and return exit code 2. Per-image API failures are output events and the check command exits successfully. The error log retains newest bytes up to `error_log_max_bytes`; structured error streaks live in state.

## Scheduling with Hermes cronjob

The Hermes cronjob script loader accepts a **bare filename** resolved under `$HERMES_HOME/scripts/`, not an arbitrary path. Create a wrapper there that invokes the **installed** skill directory. The wrapper keeps all runtime data directly alongside the monitor in the skill directory. For example, save as `$HERMES_HOME/scripts/docker-hub-tag-monitor.sh`:

```sh
#!/bin/sh
set -eu
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/docker-hub-tag-monitor"
exec python3 "$SKILL_DIR/scripts/monitor.py" check \
  --config "$SKILL_DIR/config.json" \
  --state "$SKILL_DIR/state.json" \
  --error-log "$SKILL_DIR/errors.log"
```

Initialize the config/state files in `SKILL_DIR` once before creating the job. A generic job example (replace the notification destination):

```text
cronjob_manage(
  action="create",
  name="docker-hub-tag-monitor",
  schedule="0,15,30,45 * * * *",
  no_agent=true,
  script="docker-hub-tag-monitor.sh",
  deliver="telegram:<chat_id>",
  failure_deliver="telegram:<chat_id>"
)
```

With `no_agent=true`, stdout is delivered verbatim and empty stdout sends nothing; process failures use `failure_deliver`. Operational API errors are stdout, so they are delivered as monitor output. Check for an existing monitor job before creating another and prevent overlapping runs on the same state file. Plain cron can invoke the same wrapper.

## Verification and maintenance

Maintainers may clone the source repository to develop or run the mocked tests; runtime users do not need the checkout. From the source repository root:

```sh
python3 -m unittest discover -s skills/docker-hub-tag-monitor/tests -v
```

Before updating/reinstalling, back up the full installed skill directory, including `config.json`, `state.json`, and `errors.log`; verify those runtime files after the operation. Runtime users do not need a source checkout.
