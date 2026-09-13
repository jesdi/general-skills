"""Launcher regressions. Real-runtime cases use a pre-provisioned cache in CI."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_cli import BASE_SRC, repo  # noqa: F401 — shared git repository fixture

LAUNCHER = Path(__file__).with_name("run.sh").resolve()


def launch(env, *args, cwd=None, script=LAUNCHER):
    return subprocess.run(
        ["/bin/sh", str(script), *args], env=env, cwd=cwd,
        text=True, capture_output=True, timeout=60,
    )


def test_empty_offline_cache_is_json_tool_error(tmp_path):
    env = dict(os.environ, CRAP_GATE_CACHE_DIR=str(tmp_path / "cache"), CRAP_GATE_OFFLINE="1")
    proc = launch(env, "--json")
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["kind"] == "tooling"
    assert "not cached" in proc.stderr
    assert not (tmp_path / "cache").exists()


def test_bad_download_is_rejected_before_execution(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text('#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\nprintf corrupt > "$2"\n')
    curl.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               CRAP_GATE_CACHE_DIR=str(tmp_path / "cache"), CRAP_GATE_OFFLINE="0")
    proc = launch(env, "--json")
    assert proc.returncode == 2
    assert "checksum mismatch" in proc.stderr
    assert json.loads(proc.stdout)["error"]["kind"] == "tooling"
    assert not list((tmp_path / "cache").rglob("uv.tar.gz"))
    assert not list((tmp_path / "cache").rglob(".download.*"))


@pytest.fixture
def runtime_env():
    cache = os.environ.get("CRAP_GATE_TEST_CACHE")
    if not cache:
        pytest.skip("Set CRAP_GATE_TEST_CACHE to a cache provisioned by run.sh --help")
    return dict(os.environ, CRAP_GATE_CACHE_DIR=cache, CRAP_GATE_OFFLINE="1")


@pytest.mark.parametrize("python_on_path", ["absent", "incompatible"])
def test_runtime_ignores_host_python_and_preserves_coverage_environment(
    repo, tmp_path, runtime_env, python_on_path,
):
    bin_dir = tmp_path / "host bin"
    bin_dir.mkdir()
    for name in ("uname", "dirname", "git", "sh", "env"):
        (bin_dir / name).symlink_to(shutil.which(name))
    if python_on_path == "incompatible":
        for name in ("python", "python3"):
            executable = bin_dir / name
            executable.write_text("#!/bin/sh\necho wrong-python >&2\nexit 99\n")
            executable.chmod(0o755)
    runtime_env.update(PATH=str(bin_dir), VIRTUAL_ENV=str(tmp_path / "wrong venv"),
                       PYTHONHOME="/nonexistent-python", PYTHONPATH="/nonexistent-modules")
    # Both files must be ignored by runtime discovery.
    (repo / ".python-version").write_text("3.9.6\n")
    (repo / "uv.toml").write_text("deliberately invalid configuration\n")
    (repo / "pkg/m.py").write_text(BASE_SRC + "\ndef added():\n    return 1\n")
    (repo / ".crap").mkdir()
    cfg = json.loads((repo / ".crap-gate.json").read_text())
    cfg["targets"][0]["coverage"]["command"] = (
        "env > coverage-env.txt; printf '%s' '{\"files\":{}}' > .crap/coverage.json"
    )
    (repo / ".crap-gate.json").write_text(json.dumps(cfg))
    proc = launch(runtime_env, "--json", cwd=repo)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["functions"][0]["name"] == "added"
    inherited = dict(line.split("=", 1) for line in (repo / "coverage-env.txt").read_text().splitlines())
    for name in ("PATH", "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
        assert inherited[name] == runtime_env[name]
    assert "UV_PYTHON_INSTALL_DIR" not in inherited or inherited["UV_PYTHON_INSTALL_DIR"] == os.environ.get("UV_PYTHON_INSTALL_DIR")
    assert not (repo / ".venv").exists()


def test_symlink_spaces_and_exit_codes(repo, tmp_path, runtime_env):
    link = tmp_path / "launcher link"
    link.symlink_to(LAUNCHER)
    (repo / "pkg/m.py").write_text(BASE_SRC + "\ndef unnew(a, b):\n    return a if b else (b if a else 0)\n")
    (repo / ".crap").mkdir()
    (repo / ".crap/coverage.json").write_text('{"files":{}}')
    proc = launch(runtime_env, "--json", "--no-run", cwd=repo, script=link)
    assert proc.returncode == 1, proc.stderr
    assert json.loads(proc.stdout)["summary"]["fail"] == 1
    proc = launch(runtime_env, "--json", "--base", "missing-ref", cwd=repo, script=link)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["error"]["kind"] == "tooling"


def test_offline_cache_missing_python_is_tool_error(tmp_path, runtime_env):
    original = Path(runtime_env["CRAP_GATE_CACHE_DIR"])
    cache = tmp_path / "uv only cache"
    for executable in (original / "uv").rglob("uv"):
        if executable.is_file():
            target = cache / executable.relative_to(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(executable)
    runtime_env["CRAP_GATE_CACHE_DIR"] = str(cache)
    proc = launch(runtime_env, "--json")
    assert proc.returncode == 2
    assert "Python is not cached" in proc.stderr
    assert json.loads(proc.stdout)["error"]["kind"] == "tooling"
