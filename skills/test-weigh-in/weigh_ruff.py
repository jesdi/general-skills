"""Ruff's PT rules over the target repo's test files: [(path, row, code)] for the rules in rules.toml."""
import json
import subprocess
from pathlib import Path

from weigh_env import UV, SetupError
from weigh_inventory import is_test

RUFF = "ruff==0.17.3"


def violations(repo, codes):
    """--isolated: the target repo's own ruff configuration changes nothing; uv runs ruff outside the repo's environment."""
    tracked = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    files = [f for f in tracked.split("\0") if f and is_test(f)]
    if not files or not codes:
        return []
    proc = subprocess.run([UV, "tool", "run", "--isolated", RUFF, "check", "--isolated", "--no-cache", "--select", ",".join(codes),
                           "--output-format", "json", "--", *files], cwd=repo, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise SetupError("ruff failed:\n" + proc.stdout + proc.stderr)
    root = repo.resolve()
    return [(Path(v["filename"]).resolve().relative_to(root).as_posix(), v["location"]["row"], v["code"]) for v in json.loads(proc.stdout)]
