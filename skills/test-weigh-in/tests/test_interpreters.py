import shutil
import subprocess

import pytest

from fixture_repo import FAKECLOCK, build_repo, git, load, snapshots, uv, weigh

KEY = "github.com/jesdi/demo"
MARKER_CONFTEST = "def pytest_sessionstart(session):\n    open({marker!r}, 'a').close()\n"


def make_venv(path, pytest_version, python="3.12"):
    """A venv at `path` holding pytest and the fake clock the default repo's pytest config asks for."""
    subprocess.run([uv(), "venv", "-q", "--python", python, str(path)], check=True)
    subprocess.run([uv(), "pip", "install", "-q", "--python", str(path / "bin" / "python"), f"pytest=={pytest_version}"], check=True)
    site = next((path / "lib").glob("python*/site-packages"))
    (site / "fakeclock.py").write_text(FAKECLOCK)


def commit_all(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "reshape")


def plain_repo(tmp_path, venv_dir="venv", pytest_version="8.1.1", python="3.12"):
    """The default repo reshaped to a requirements.txt project: no uv.lock, its only venv in `venv_dir`."""
    repo = build_repo(tmp_path / "demo")
    shutil.rmtree(repo / ".venv")
    git(repo, "rm", "-q", "uv.lock")
    (repo / "requirements.txt").write_text(f"pytest=={pytest_version}\n")
    if venv_dir:
        make_venv(repo / venv_dir, pytest_version, python)
    commit_all(repo)
    return repo


def guarded(repo, tmp_path):
    """Make any pytest session in `repo` leave a marker file; returns the marker's path."""
    marker = tmp_path / "pytest-ran"
    (repo / "conftest.py").write_text(MARKER_CONFTEST.format(marker=str(marker)))
    commit_all(repo)
    return marker


def test_uv_repo_without_venv_gets_one_from_its_lock(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    shutil.rmtree(repo / ".venv")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.0.0"\nrequires-python = ">=3.10"\ndependencies = ["pytest==8.3.5"]\n'
        '[tool.pytest.ini_options]\npythonpath = ["."]\nmarkers = ["e2e: slow end-to-end test"]\n'
    )
    (repo / "uv.lock").unlink()  # the template lock is a stub uv cannot parse
    subprocess.run([uv(), "lock", "-q"], cwd=repo, check=True)
    commit_all(repo)
    lock = (repo / "uv.lock").read_bytes()
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / ".venv" / "bin" / "python").is_file()
    assert (repo / "uv.lock").read_bytes() == lock
    assert len(snapshots(home, KEY)) == 1


def test_plain_venv_repo_is_measured_with_its_venv(tmp_path, home, cache):
    repo = plain_repo(tmp_path)
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    (snap,) = snapshots(home, KEY)
    assert load(snap)["pytest_version"].startswith("8.1")


def test_no_interpreter_stops_and_names_the_python_flag(tmp_path, home, cache):
    repo = plain_repo(tmp_path, venv_dir=None)
    result = weigh(repo, home, cache=cache)
    assert result.returncode != 0
    assert "--python" in result.stdout + result.stderr
    assert snapshots(home, KEY) == []


def test_explicit_python_wins_over_the_repos_venv(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    make_venv(tmp_path / "py312", "8.2.2")
    result = weigh(repo, home, "--python", str(tmp_path / "py312" / "bin" / "python"), cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    (snap,) = snapshots(home, KEY)
    assert load(snap)["pytest_version"].startswith("8.2")


def test_pytest_at_the_minimum_is_accepted(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo", pytest_version="7.0.0")
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    (snap,) = snapshots(home, KEY)
    assert load(snap)["pytest_version"].startswith("7.0")


def test_pytest_below_the_minimum_is_refused_before_any_test(tmp_path, home, cache):
    repo = plain_repo(tmp_path, venv_dir=".venv", pytest_version="6.2.5")
    marker = guarded(repo, tmp_path)
    result = weigh(repo, home, cache=cache)
    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "6.2.5" in message and "7.0" in message
    assert not marker.exists()
    assert snapshots(home, KEY) == []


def test_python_below_the_minimum_is_refused_before_any_test(tmp_path, home, cache):
    repo = plain_repo(tmp_path, venv_dir=".venv", pytest_version="8.3.5", python="3.9")
    marker = guarded(repo, tmp_path)
    result = weigh(repo, home, cache=cache)
    assert result.returncode != 0
    message = result.stdout + result.stderr
    assert "3.9" in message and "3.10" in message
    assert not marker.exists()
    assert snapshots(home, KEY) == []
