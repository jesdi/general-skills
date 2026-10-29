"""Launcher acceptance tests, through `sh run.sh` with a temporary cache seeded from a provisioned one."""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
RUN = SKILL / "run.sh"
ROOT = SKILL.parents[1]


def setting(path, name):
    """A `name=value` line of a shell file: the wrapper and the launcher define the pins and the Python."""
    return re.search(rf'^{name}="?([^"\n]*)"?$', path.read_text(), re.M).group(1)


REQUIREMENTS = setting(RUN, "pins").split()
VENV = f'{setting(SKILL / "launcher.sh", "python_version")}-{setting(RUN, "venv_key")}'
# The dist-info names that pip writes for the pins: jsonschema-specifications==2025.9.1 → jsonschema_specifications-2025.9.1.
PINS = {req.replace("-", "_").replace("==", "-") for req in REQUIREMENTS}


@pytest.fixture
def seed():
    """A temporary cache holding uv and Python (symlinked from a provisioned cache) but no venv."""
    source = Path(os.environ.get("IMPLEMENT_SPEC_TEST_CACHE") or Path.home() / ".cache/implement-spec")
    if not (source / "uv").is_dir() or not (source / "python").is_dir():
        pytest.skip("Set IMPLEMENT_SPEC_TEST_CACHE to a cache provisioned by run.sh --help")

    def make(tmp_path, name="cache", spy=None, barrier=0):
        """With `spy`, uv is a script that appends each `pip install` argument line to `spy`, runs the real uv, then waits until `barrier` installs have finished, so that concurrent runs reach publication together."""
        cache = tmp_path / name
        cache.mkdir()
        (cache / "python").symlink_to(source / "python")
        if spy is None:
            (cache / "uv").symlink_to(source / "uv")
            return cache
        real = next((source / "uv").glob("*/*/uv"))
        fake = cache / "uv" / real.parent.relative_to(source / "uv") / "uv"
        fake.parent.mkdir(parents=True)
        fake.write_text(f'''#!/bin/sh
case " $* " in *" pip install "*) echo "$*" >> {spy}
    {real} "$@" || exit $?
    echo done >> {spy}.done
    n=0
    while [ "$(wc -l < {spy}.done)" -lt {barrier} ] && [ $n -lt 600 ]; do sleep 0.1; n=$((n+1)); done
    exit 0 ;; esac
exec {real} "$@"
''')
        fake.chmod(0o755)
        return cache
    return make


def launch(cache, *args, **env):
    full = {k: v for k, v in os.environ.items() if not k.startswith("IMPLEMENT_SPEC_")}
    full.update(IMPLEMENT_SPEC_CACHE_DIR=str(cache), **env)
    return subprocess.run(["/bin/sh", str(RUN), *args], env=full, text=True, capture_output=True, timeout=600)


def need_network():
    if subprocess.run([shutil.which("curl") or "curl", "--silent", "--head", "--max-time", "15", "https://pypi.org"],
                      capture_output=True).returncode != 0:
        pytest.skip("network access to pypi.org is required")


def installed(venv):
    site = next((venv / "lib").glob("python*/site-packages"))
    return {p.name.removesuffix(".dist-info") for p in site.glob("*.dist-info")}


def test_online_run_builds_pinned_venv_and_runs_driver(tmp_path, seed):
    need_network()
    spy = tmp_path / "pip-install.log"
    cache = seed(tmp_path, spy=spy)
    proc = launch(cache, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "usage: implement-spec" in proc.stdout
    assert [p.name for p in (cache / "venv").iterdir()] == [VENV]
    requested = spy.read_text().split()
    for pin in REQUIREMENTS:
        assert pin in requested, f"{pin} is not requested explicitly: {requested}"
    # Equal, not a subset: a dependency that run.sh does not pin still gets installed, and fails here.
    assert installed(cache / "venv" / VENV) == PINS


def test_offline_cache_without_venv_exits_3_naming_jsonschema(tmp_path, seed):
    cache = seed(tmp_path)
    proc = launch(cache, "--help", IMPLEMENT_SPEC_OFFLINE="1")
    assert proc.returncode == 3
    assert "jsonschema is not cached" in proc.stderr
    assert not (cache / "venv" / VENV / "bin/python").exists()


def test_concurrent_first_runs_share_one_complete_venv_and_leave_no_partial(tmp_path, seed):
    need_network()
    runs = 4
    cache = seed(tmp_path, spy=tmp_path / "pip-install.log", barrier=runs)
    published = cache / "venv" / VENV
    env = {k: v for k, v in os.environ.items() if not k.startswith("IMPLEMENT_SPEC_")}
    env["IMPLEMENT_SPEC_CACHE_DIR"] = str(cache)
    procs = [subprocess.Popen(["/bin/sh", str(RUN), "--help"], env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(runs)]
    early = []  # a reader that sees the published venv before its packages are installed
    while any(p.poll() is None for p in procs):
        if (published / "bin/python").exists() and not any(published.glob("lib/python*/site-packages/jsonschema-*.dist-info")):
            early.append("published venv has no jsonschema")
        time.sleep(0.02)
    assert not early, early[0]
    results = [(p.communicate(timeout=600), p.returncode) for p in procs]
    for (out, err), code in results:
        assert code == 0, err
        assert "usage: implement-spec" in out
    assert [p.name for p in (cache / "venv").iterdir()] == [VENV]
    assert installed(cache / "venv" / VENV) == PINS
    assert (cache / "venv" / VENV / "bin/python").exists()
    assert not (published / "venv").exists(), "a losing run's environment was moved inside the published venv"


def test_crap_gate_without_pinned_packages_builds_no_venv(tmp_path, seed):
    cache = seed(tmp_path)
    env = dict(os.environ, CRAP_GATE_CACHE_DIR=str(cache), CRAP_GATE_OFFLINE="1")
    proc = subprocess.run(["/bin/sh", str(ROOT / "skills/crap-gate/run.sh"), "--help"], env=env,
                          text=True, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert not (cache / "venv").exists()


@pytest.mark.parametrize("skill", ["implement-spec", "crap-gate"])
def test_launcher_copy_is_byte_identical_to_shared_launcher(skill):
    copy = ROOT / "skills" / skill / "launcher.sh"
    assert copy.is_file(), f"{copy} is missing"
    assert copy.read_bytes() == (ROOT / "shared/launcher.sh").read_bytes()
