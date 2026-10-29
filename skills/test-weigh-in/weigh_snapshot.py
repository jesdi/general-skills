"""Repo key, commit state and exclusive snapshot writing under ~/.test-weigh-in/."""
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REMOTE = re.compile(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^:/]+)[:/](.+?)(?:\.git)?/?$")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


def repo_key(repo):
    m = REMOTE.match(git(repo, "remote", "get-url", "origin"))
    return f"{m[1]}/{m[2]}" if m else f"local/{repo.name}"


def commit_state(repo):
    return git(repo, "rev-parse", "--short", "HEAD"), bool(git(repo, "status", "--porcelain"))


def write_snapshot(repo, data):
    home = Path.home() / ".test-weigh-in" / repo_key(repo)
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
