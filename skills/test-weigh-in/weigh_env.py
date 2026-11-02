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
# the wrapper's cached uv; a direct `python3 weigh.py` uses the one on PATH
# the mutation tier's tool and its closure, every package pinned and installed --no-deps
MUTMUT = "mutmut==3.8.1"
# `uv pip compile --universal --python-version 3.10` of MUTMUT, minus pytest and its own closure (pluggy, iniconfig,
# packaging, exceptiongroup, tomli): those come from the target repo
MUTMUT_CLOSURE = (
    MUTMUT, "click==8.5.0", "colorama==0.4.6; sys_platform == 'win32'",
    "coverage==7.16.2; python_full_version < '3.11'", "coverage==7.17.0; python_full_version >= '3.11'",
    "libcst==1.9.0", "linkify-it-py==2.2.0", "markdown-it-py==4.2.0", "mdit-py-plugins==0.6.1", "mdurl==0.1.2",
    "platformdirs==4.12.4; python_full_version < '3.11'", "platformdirs==4.14.3; python_full_version >= '3.11'",
    "pygments==2.21.0", "pyyaml==6.0.3; python_full_version != '3.13.*'", "pyyaml-ft==8.0.0; python_full_version == '3.13.*'",
    "rich==15.0.0", "setproctitle==1.3.8", "textual==8.2.8", "toml==0.10.2; python_full_version < '3.11'",
    "typing-extensions==4.16.0",
)
UV = os.environ.get("TEST_WEIGH_IN_UV") or "uv"


class SetupError(Exception):
    pass


def find_interpreter(repo, explicit=None):
    """--python wins, then a venv synced from uv.lock when .venv is missing (frozen: the lock is never rewritten), then .venv, then venv."""
    if explicit:
        return Path(explicit).absolute()  # not resolve(): a venv's python is a symlink to the base one
    if (repo / "uv.lock").is_file() and not (repo / ".venv").exists():
        proc = subprocess.run([UV, "sync", "--frozen", "-q"], cwd=repo, capture_output=True, text=True)
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


def cache_root():
    return Path(os.environ.get("TEST_WEIGH_IN_CACHE_DIR") or Path.home() / ".cache" / "test-weigh-in")


def _python_version(python):
    return subprocess.run([str(python), "-I", "-B", "-c", "import sys;print(*sys.version_info[:2], sys.implementation.name)"],
                          capture_output=True, text=True).stdout.strip()


def mutmut_overlay(python):
    """The pinned mutmut closure, without pytest's (the target's own is used), keyed by the pins and the interpreter's version."""
    root = cache_root()
    overlay = root / f"mutmut-{hashlib.sha256(chr(0).join((*MUTMUT_CLOSURE, _python_version(python))).encode()).hexdigest()[:16]}"
    if not overlay.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=root))
        proc = subprocess.run([UV, "pip", "install", "-q", "--python", str(python), "--no-deps", "--target", str(tmp), *MUTMUT_CLOSURE],
                              capture_output=True, text=True)
        if proc.returncode:
            shutil.rmtree(tmp)
            raise SetupError(f"cannot install {MUTMUT} into the overlay:\n" + proc.stdout + proc.stderr)
        try:
            tmp.rename(overlay)
        except OSError:  # another run won the race
            shutil.rmtree(tmp)
    return overlay


def build_overlay(python):
    """Plugin and coverage (installed --no-deps, so no pytest: the repo's pytest is the one used); built aside, then renamed into place.

    Keyed by the plugin's content and the interpreter's version, so an overlay left by an older plugin or another Python is never reused."""
    root = cache_root()
    version = _python_version(python)
    digest = hashlib.sha256((HERE / PLUGIN).read_bytes() + version.encode()).hexdigest()[:16]
    overlay = root / f"overlay-{digest}"
    if not overlay.is_dir():
        root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=root))
        shutil.copy(HERE / PLUGIN, tmp)
        proc = subprocess.run([UV, "pip", "install", "-q", "--no-deps", "--python", str(python), "--target", str(tmp), "coverage"],
                              capture_output=True, text=True)
        if proc.returncode:
            shutil.rmtree(tmp)
            raise SetupError("cannot install coverage into the overlay:\n" + proc.stdout + proc.stderr)
        try:
            tmp.rename(overlay)
        except OSError:  # another run won the race
            shutil.rmtree(tmp)
    return overlay
