"""The target repo's interpreter and the overlay that carries the measurement plugin."""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = "weigh_pytest_plugin.py"
MIN_PYTHON = (3, 10)
MIN_PYTEST = (7, 0)


class SetupError(Exception):
    pass


def find_interpreter(repo, explicit=None):
    """--python wins, then a venv synced from uv.lock when .venv is missing (frozen: the lock is never rewritten), then .venv, then venv."""
    if explicit:
        return Path(explicit).absolute()  # not resolve(): a venv's python is a symlink to the base one
    if (repo / "uv.lock").is_file() and not (repo / ".venv").exists():
        proc = subprocess.run(["uv", "sync", "--frozen", "-q"], cwd=repo, capture_output=True, text=True)
        if proc.returncode:
            raise SetupError("uv sync failed:\n" + proc.stdout + proc.stderr)
    for rel in (".venv/bin/python", "venv/bin/python"):
        if (repo / rel).is_file():
            return repo / rel
    raise SetupError(f"no interpreter found in {repo}: pass --python PATH")


def check_versions(python):
    """Refuse an interpreter or pytest below the supported minimum, before any test runs."""
    code = ("import sys\nprint('.'.join(map(str, sys.version_info[:3])))\n"
            "from importlib.metadata import version\nprint(version('pytest'))")
    proc = subprocess.run([str(python), "-I", "-B", "-c", code], capture_output=True, text=True)
    if proc.returncode:
        raise SetupError(f"cannot read the versions of {python} (is pytest installed?):\n" + proc.stderr)
    py, pytest = proc.stdout.split()
    if tuple(map(int, py.split(".")[:2])) < MIN_PYTHON:
        raise SetupError(f"python {py} is below the supported minimum 3.10")
    if tuple(map(int, re.findall(r"\d+", pytest)[:2])) < MIN_PYTEST:
        raise SetupError(f"pytest {pytest} is below the supported minimum 7.0")


def build_overlay():
    """Plugin-only directory (no pytest, so the repo's pytest is the one used); built aside, then renamed into place.

    Keyed by the plugin's content, so an overlay left by an older plugin is never reused."""
    root = Path(os.environ.get("TEST_WEIGH_IN_CACHE_DIR") or Path.home() / ".cache" / "test-weigh-in")
    overlay = root / f"overlay-{hashlib.sha256((HERE / PLUGIN).read_bytes()).hexdigest()[:16]}"
    if not overlay.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=root))
        shutil.copy(HERE / PLUGIN, tmp)
        try:
            tmp.rename(overlay)
        except OSError:  # another run won the race
            shutil.rmtree(tmp)
    return overlay
