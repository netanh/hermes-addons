#!/usr/bin/env python3
"""Monitor Docker Hub tags. Standard-library only."""
import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def hub_api(url):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "docker-hub-tag-monitor"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} for {url}: {exc.read().decode('utf-8', errors='replace')[:200]}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Fetch failed for {url}: {exc}") from exc


def fetch_tags(image, page_size=100):
    data = hub_api(f"https://hub.docker.com/v2/repositories/{image}/tags?page_size={page_size}")
    if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
        raise RuntimeError("Docker Hub returned an invalid tag response")
    return [{"name": item["name"], "last_updated": item.get("last_updated"), "url": f"https://hub.docker.com/r/{image}/tags"} for item in data["results"] if isinstance(item, dict) and item.get("name")]


def select_candidate_tag(tags, exclude, tag_regex=None):
    pattern = re.compile(tag_regex) if tag_regex else None
    excluded = set(exclude or [])
    return next((tag for tag in tags if tag["name"] not in excluded and (pattern is None or pattern.search(tag["name"]))), None)


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    images = config.get("images")
    valid_image = lambda x: isinstance(x, str) and re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._-][a-z0-9]+)*", x)
    if not isinstance(images, list) or not images or any(not valid_image(x) for x in images):
        raise ValueError("images must be a non-empty list of namespace/image names")
    if len(set(images)) != len(images):
        raise ValueError("images must not contain duplicates")
    page, cap = config.get("page_size", 100), config.get("error_log_max_bytes", 52400)
    if type(page) is not int or not 1 <= page <= 100:
        raise ValueError("page_size must be an integer from 1 to 100")
    if type(cap) is not int or cap < 0:
        raise ValueError("error_log_max_bytes must be a non-negative integer")
    excludes, settings = config.get("exclude_tags", []), config.get("image_settings", {})
    if not isinstance(excludes, list) or any(not isinstance(x, str) for x in excludes):
        raise ValueError("exclude_tags must be a list of strings")
    if not isinstance(settings, dict) or any(k not in images or not isinstance(v, dict) for k, v in settings.items()):
        raise ValueError("image_settings must map configured images to objects")
    for value in settings.values():
        regex = value.get("tag_regex")
        if regex is not None:
            if not isinstance(regex, str): raise ValueError("tag_regex must be a string")
            try: re.compile(regex)
            except re.error as exc: raise ValueError(f"invalid tag_regex: {exc}") from exc
    return config


def load_config(path):
    try: return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc: raise ValueError(f"cannot read config: {exc}") from exc


def load_state(path):
    if not path.exists(): return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict): raise ValueError("state must be a JSON object")
    return data


def save_state(path, state):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            temp = f.name; json.dump(state, f, indent=2); f.write("\n"); f.flush()
        Path(temp).replace(path)
    finally:
        if temp and Path(temp).exists(): Path(temp).unlink()


def append_error_log(log_path, image, message, max_bytes):
    if not log_path or max_bytes == 0: return
    path = Path(log_path); path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()}\t{image}\t{message}\n".encode("utf-8")[-max_bytes:]
    old = path.read_bytes() if path.exists() else b""
    path.write_bytes((old + line)[-max_bytes:])


def check(config, state_path, init_only=False, error_log=None, force=False):
    validate_config(config)
    state_path = Path(state_path)
    state = load_state(state_path)
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    changed = False
    outputs = []
    for image in config["images"]:
        entry = state.setdefault(image, {})
        regex = config.get("image_settings", {}).get(image, {}).get("tag_regex")
        if init_only and "latest_tag" in entry and not force:
            outputs.append(f"[init] {image}: baseline exists; left unchanged")
            continue
        try:
            tags = fetch_tags(image, config.get("page_size", 100))
            candidate = select_candidate_tag(tags, config.get("exclude_tags", []), regex)
            if init_only:
                if candidate:
                    entry.update(latest_tag=candidate["name"], last_updated=candidate.get("last_updated") or stamp, last_checked=stamp)
                    entry.pop("errors", None)
                    changed = True
                    outputs.append(f"[init] {image}: {candidate['name']}")
                else:
                    outputs.append(f"[init] {image}: no usable tags found")
                continue
            if entry.pop("errors", None) is not None:
                changed = True
            if candidate and candidate["name"] != entry.get("latest_tag"):
                outputs.append(f"[{image}] NEW TAG: {candidate['name']} — {candidate['url']}")
                entry.update(latest_tag=candidate["name"], last_updated=candidate.get("last_updated"), last_checked=stamp)
                changed = True
            else:
                entry["last_checked"] = stamp
                changed = True
        except Exception as exc:
            message = str(exc)
            append_error_log(error_log, image, message, config.get("error_log_max_bytes", 52400))
            prior = entry.get("errors", {})
            first = prior.get("first_error_at") or stamp
            try:
                first_dt = datetime.fromisoformat(first)
                if first_dt.tzinfo is None:
                    first_dt = first_dt.replace(tzinfo=timezone.utc)
                elapsed = now - first_dt
            except (TypeError, ValueError):
                first, elapsed = stamp, timedelta(0)
            count = prior.get("count", 0) + 1
            entry["errors"] = {"first_error_at": first, "last_error_at": stamp, "count": count, "last_message": message[:500]}
            changed = True
            if elapsed >= timedelta(hours=6):
                outputs.append(f"[{image}] PERSISTENT ERROR (>6h): {message[:500]} ({count} occurrences since {first})")
            else:
                outputs.append(f"[{image}] ERROR: {message[:500]}")
    if changed:
        save_state(state_path, state)
    for line in outputs:
        print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "init"):
        p = sub.add_parser(command); p.add_argument("--config", required=True, type=Path); p.add_argument("--state", required=True, type=Path); p.add_argument("--error-log", type=Path)
        if command == "init": p.add_argument("--force", action="store_true", help="replace existing state")
    args = parser.parse_args(argv)
    try: check(load_config(args.config), args.state, args.command == "init", args.error_log, getattr(args, "force", False))
    except (ValueError, OSError) as exc: print(f"ERROR: {exc}", file=sys.stderr); return 2
    return 0

if __name__ == "__main__": raise SystemExit(main())
