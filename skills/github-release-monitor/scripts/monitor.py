#!/usr/bin/env python3
"""Monitor public GitHub repositories for releases and tags."""

import argparse
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PERSISTENT_ERROR_HOURS = 6
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def gh_api(url):
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "github-release-monitor"})
    try:
        with urlopen(request, timeout=30) as response:
            if response.status == 204:
                return None
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = exc.read(200).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {exc.code}: {body[:200]}") from exc
    except URLError as exc:
        raise RuntimeError(f"URL error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc
    except Exception as exc:
        raise RuntimeError(f"request failed: {exc}") from exc


PAGE_SIZE = 100


def list_releases(owner, repo):
    """Fetch all published non-prerelease releases, keyed by release id."""
    owner_path, repo_path = quote(owner, safe=""), quote(repo, safe="")
    items = {}
    page = 1
    while True:
        data = gh_api(f"https://api.github.com/repos/{owner_path}/{repo_path}/releases?per_page={PAGE_SIZE}&page={page}")
        if not isinstance(data, list):
            raise RuntimeError("GitHub returned an invalid releases response")
        for release in data:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            release_id, tag = release.get("id"), release.get("tag_name")
            if release_id is None or not tag:
                continue
            url = release.get("html_url") or f"https://github.com/{owner_path}/{repo_path}/releases/tag/{quote(tag, safe='')}"
            items[str(release_id)] = (tag, url)
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return items


def list_tags(owner, repo):
    """Fetch all repository tags; do not assume the API's result order."""
    owner_path, repo_path = quote(owner, safe=""), quote(repo, safe="")
    items = {}
    page = 1
    while True:
        data = gh_api(f"https://api.github.com/repos/{owner_path}/{repo_path}/tags?per_page={PAGE_SIZE}&page={page}")
        if not isinstance(data, list):
            raise RuntimeError("GitHub returned an invalid tags response")
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                tag = item["name"]
                url = f"https://github.com/{owner_path}/{repo_path}/releases/tag/{quote(tag, safe='')}"
                items[tag] = (tag, url)
        if len(data) < PAGE_SIZE:
            break
        page += 1
    return items

def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    unknown = set(config) - {"repos", "check_releases", "check_tags"}
    if unknown:
        raise ValueError("unknown config keys: " + ", ".join(sorted(unknown)))
    repos = config.get("repos")
    if not isinstance(repos, list) or not repos or any(not isinstance(repo, str) or not REPO_RE.fullmatch(repo) for repo in repos):
        raise ValueError("repos must be a non-empty list of owner/repository strings")
    if len(set(repos)) != len(repos):
        raise ValueError("repos must not contain duplicates")
    for key in ("check_releases", "check_tags"):
        if key in config and not isinstance(config[key], bool):
            raise ValueError(f"{key} must be a boolean")
    if not config.get("check_releases", True) and not config.get("check_tags", True):
        raise ValueError("at least one of check_releases or check_tags must be true")
    return config


def load_config(path):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            return validate_config(json.load(stream))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON config: {exc}") from exc


def load_state(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict):
        raise ValueError("state file must contain a JSON object")
    return state


def save_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            tmp_path = Path(stream.name)
            json.dump(state, stream, indent=2)
            stream.write("\n")
            stream.flush()
        tmp_path.replace(path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def parse_repo(repo):
    if not isinstance(repo, str) or not REPO_RE.fullmatch(repo):
        raise ValueError(f"invalid repository format: {repo!r}; expected owner/repository")
    return repo.split("/", 1)


def _feed_functions(config):
    feeds = []
    if config.get("check_releases", True):
        feeds.append(("release", "known_releases", list_releases))
    if config.get("check_tags", True):
        feeds.append(("tag", "known_tags", list_tags))
    return feeds


def check(config, state_path, init_only=False, error_log=None, force=False):
    del error_log  # retained for CLI compatibility; errors are tracked in state
    validate_config(config)
    state = load_state(state_path)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    outputs = []
    changed = False

    for repo in config["repos"]:
        owner, name = parse_repo(repo)
        entry = state.setdefault(repo, {})
        errors = entry.setdefault("errors", {})
        for feed, state_key, fetcher in _feed_functions(config):
            if init_only and state_key in entry and not force:
                outputs.append(f"[init] {repo} {feed}: baseline exists; left unchanged")
                continue
            try:
                current = fetcher(owner, name)
            except Exception as exc:
                prior = errors.get(feed, {})
                first_error = prior.get("first_error_at") or now_iso
                try:
                    first_dt = datetime.fromisoformat(first_error)
                    if first_dt.tzinfo is None:
                        first_dt = first_dt.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    first_error, first_dt = now_iso, now
                failure = {
                    "first_error_at": first_error,
                    "last_error_at": now_iso,
                    "count": int(prior.get("count", 0)) + 1,
                    "last_message": str(exc)[:500],
                }
                errors[feed] = failure
                changed = True
                duration = now - first_dt
                label = "PERSISTENT ERROR" if duration >= timedelta(hours=PERSISTENT_ERROR_HOURS) else "ERROR"
                outputs.append(f"[{repo}] {label} ({feed}): {failure['last_message']} ({failure['count']} occurrences since {first_error})")
                continue

            if feed in errors:
                errors.pop(feed, None)
                changed = True
            if init_only:
                entry[state_key] = sorted(current)
                changed = True
                outputs.append(f"[init] {repo} {feed}: baseline recorded for {len(current)} item(s)")
                continue

            if state_key not in entry:
                outputs.append(f"[{repo}] MISSING BASELINE ({feed}); run init first")
                continue
            previous = entry[state_key]
            if not isinstance(previous, list) or any(not isinstance(item, str) for item in previous):
                raise ValueError(f"invalid {feed} baseline for {repo}; run init --force")
            old_items = set(previous)
            new_item_ids = sorted(set(current) - old_items)
            for item_id in new_item_ids:
                tag_name, url = current[item_id]
                outputs.append(f"[{repo}] NEW {feed.upper()}: {tag_name} — {url}")
            if new_item_ids or old_items != set(current):
                entry[state_key] = sorted(current)
                changed = True

        entry["last_checked"] = now_iso
        changed = True
        if not errors:
            entry.pop("errors", None)

    if changed:
        save_state(state_path, state)
    for line in outputs:
        print(line)


def main():
    parser = argparse.ArgumentParser(description="Monitor GitHub repository releases and tags")
    sub = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (("check", "check for changes"), ("init", "establish current baselines")):
        subparser = sub.add_parser(command, help=help_text)
        subparser.add_argument("--config", type=Path, required=True)
        subparser.add_argument("--state", type=Path, required=True)
        subparser.add_argument("--force", action="store_true", help="with init, replace existing baselines")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        check(config, args.state, init_only=args.command == "init", force=args.force)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
