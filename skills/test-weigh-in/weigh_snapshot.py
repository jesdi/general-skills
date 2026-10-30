"""Repo key, commit state and exclusive snapshot writing under ~/.test-weigh-in/."""
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from weigh_env import SetupError

REMOTE = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^:/]+)[:/](.+?)(?:\.git)?/?$")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


def repo_key(repo):
    origin = git(repo, "remote", "get-url", "origin")
    m = REMOTE.match(origin)
    key = f"{m[1]}/{m[2]}" if m else f"local/{repo.name}"
    # The key is a path under ~/.test-weigh-in: an origin like host:../../x must not climb out of it.
    if any(part in ("", ".", "..") for part in key.split("/")):
        raise SetupError(f"cannot store snapshots for origin {origin!r}: its path has an empty, . or .. segment")
    return key


def commit_state(repo):
    return git(repo, "rev-parse", "--short", "HEAD"), bool(git(repo, "status", "--porcelain"))


def home_dir(repo):
    return Path.home() / ".test-weigh-in" / repo_key(repo)


def write_snapshot(home, data):
    home.mkdir(parents=True, exist_ok=True)
    now = os.environ.get("TEST_WEIGH_IN_NOW")
    when = datetime.fromisoformat(now.replace("Z", "+00:00")) if now else datetime.now(timezone.utc)
    stem = f"{when:%Y%m%dT%H%M%SZ}_{data['commit']}"
    n = 1
    while True:
        path = home / (f"{stem}.json" if n == 1 else f"{stem}-{n}.json")
        try:
            with path.open("x") as out:
                json.dump(data, out, indent=2)
            return path
        except FileExistsError:
            n += 1
