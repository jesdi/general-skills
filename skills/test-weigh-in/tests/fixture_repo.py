"""Builds fixture target repos from testdata/default_repo and runs weigh.py against them.

Snapshot interface the tests pin (the spec leaves the JSON keys open):
  commit (short sha), dirty (bool), pytest_args (str), pytest_version, python_version,
  inventory {tests, cases, test_files, test_lines, source_lines, runtime_seconds}.
Environment: TEST_WEIGH_IN_NOW=<ISO UTC> freezes the clock used to name snapshots;
TEST_WEIGH_IN_CACHE_DIR holds the overlay.
"""
import functools
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEIGH = HERE.parent / "weigh.py"
TEMPLATE = HERE / "testdata" / "default_repo"
FAKECLOCK = (
    "import pytest\n"
    "@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    rep = (yield).get_result()\n"
    "    if rep.when == 'call':\n"
    "        rep.duration = 1.125\n"
)
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def git(root, *args):
    return subprocess.run([*GIT, *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _pad(files, total):
    have = sum(len(f.read_text().splitlines()) for f in files)
    with files[0].open("a") as out:
        out.writelines(f"_PAD_{i} = {i}\n" for i in range(total - have))


@functools.cache
def uv():
    """uv for the fixtures and the engine: TEST_WEIGH_IN_UV, then PATH, then the pinned uv that the skill's launcher downloads (CI has no uv on PATH)."""
    found = os.environ.get("TEST_WEIGH_IN_UV") or shutil.which("uv")
    if found:
        return found
    subprocess.run(["sh", str(HERE.parent / "run.sh"), "--help"], check=True, capture_output=True)
    cache = os.environ.get("TEST_WEIGH_IN_CACHE_DIR") or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "test-weigh-in"
    return str(next(Path(cache).glob("uv/*/*/uv")))


def _venv(repo, pytest_version):
    py = repo / ".venv" / "bin" / "python"
    subprocess.run([uv(), "venv", "-q", "--python", "3.12", str(repo / ".venv")], check=True)
    subprocess.run([uv(), "pip", "install", "-q", "--python", str(py), f"pytest=={pytest_version}"], check=True)
    site = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    (site / "fakeclock.py").write_text(FAKECLOCK)


def build_repo(path, origin="git@github.com:jesdi/demo.git", pytest_version="8.3.5", test_lines=None, source_lines=None):
    path = Path(path)
    shutil.copytree(TEMPLATE, path)
    if test_lines:
        _pad(sorted((path / "tests").glob("test_*.py")), test_lines)
    if source_lines:
        _pad([path / "demo" / "board.py", path / "demo" / "config.py"], source_lines)
    _venv(path, pytest_version)
    git(path, "init", "-q", "-b", "main")
    if origin:
        git(path, "remote", "add", "origin", origin)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def short_sha(repo):
    return git(repo, "rev-parse", "--short", "HEAD")


def site_files(repo):
    site = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    return sorted(str(p.relative_to(site)) for p in site.rglob("*"))


def weigh(repo, home, *flags, cache, now=None):
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH")}
    env.update(HOME=str(home), TEST_WEIGH_IN_CACHE_DIR=str(cache), TEST_WEIGH_IN_UV=uv())
    env.setdefault("UV_CACHE_DIR", subprocess.run([uv(), "cache", "dir"], capture_output=True, text=True).stdout.strip())
    if now:
        env["TEST_WEIGH_IN_NOW"] = now
    return subprocess.run([sys.executable, str(WEIGH), str(repo), *flags], env=env, capture_output=True, text=True)


def snapshots(home, key):
    return sorted((Path(home) / ".test-weigh-in" / key).glob("*.json"))


def load(path):
    return json.loads(Path(path).read_text())
