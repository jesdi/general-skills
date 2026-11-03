
import pytest

from fixture_repo import build_repo, git, load, snapshots, weigh

KEY = "github.com/jesdi/demo"
MIXED = '''import pytest


@pytest.fixture
def skip_on_teardown():
    yield
    pytest.skip("teardown")


@pytest.fixture
def broken():
    raise RuntimeError("setup")


@pytest.mark.parametrize("n", [1, 2, 3])
def test_parse(n):
    assert n != 2


@pytest.mark.parametrize("n", [1, 2])
def test_skipped(n):
    pytest.skip("not here")


@pytest.mark.xfail
def test_expected_failure():
    assert False


def test_errors(broken):
    pass


def test_fails_then_skips_in_teardown(skip_on_teardown):
    assert False
'''


def committed(tmp_path, files, **repo_kwargs):
    """The default repo with extra committed files."""
    repo = build_repo(tmp_path / "demo", **repo_kwargs)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "extra")
    return repo


def test_cases_aggregate_into_tests_with_their_outcomes(tmp_path, home, cache):
    repo = committed(tmp_path, {"tests/test_mixed.py": MIXED})
    result = weigh(repo, home, "--pytest-args", "tests/test_mixed.py", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    (snap,) = snapshots(home, KEY)
    tests = {node.split("::")[1]: {k: t[k] for k in ("outcome", "duration", "cases")} for node, t in load(snap)["tests"].items()}
    assert tests == {
        "test_parse": {"outcome": "failed", "duration": pytest.approx(3.375), "cases": 3},
        "test_skipped": {"outcome": "skipped", "duration": pytest.approx(2.25), "cases": 2},
        "test_expected_failure": {"outcome": "passed", "duration": pytest.approx(1.125), "cases": 1},
        "test_errors": {"outcome": "failed", "duration": 0.0, "cases": 1},
        "test_fails_then_skips_in_teardown": {"outcome": "failed", "duration": pytest.approx(1.125), "cases": 1},
    }
    assert load(snap)["inventory"]["cases"] == 8


@pytest.mark.parametrize("files, args, says", [
    ({"tests/test_broken.py": "def test_x(:\n"}, "", "SyntaxError"),
    ({}, "tests/test_missing.py", "not found"),
])
def test_a_run_that_did_not_execute_the_tests_writes_no_snapshot(tmp_path, home, cache, files, args, says):
    repo = committed(tmp_path, files)
    result = weigh(repo, home, "--pytest-args", args, cache=cache)
    assert result.returncode != 0
    assert "without running the tests" in result.stderr and says in result.stderr
    assert snapshots(home, KEY) == []


COVERAGE_ONLY_FAILURE = '''import sys

if "coverage" in sys.modules:
    raise RuntimeError("collection fails under coverage")


def test_ok():
    pass
'''


def test_a_coverage_run_that_did_not_execute_the_tests_writes_no_snapshot(tmp_path, home, cache):
    repo = committed(tmp_path, {"tests/test_coverage_only.py": COVERAGE_ONLY_FAILURE})
    result = weigh(repo, home, cache=cache)
    assert result.returncode != 0
    assert "under coverage" in result.stderr and "collection fails under coverage" in result.stderr
    assert snapshots(home, KEY) == []


def test_python_files_with_spaces_are_counted(tmp_path, home, cache):
    repo = committed(tmp_path, {"demo/space name.py": "A = 1\nB = 2\n"})
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    (snap,) = snapshots(home, KEY)
    assert load(snap)["inventory"]["source_lines"] == 2 + sum(
        len(p.read_text().splitlines()) for p in (repo / "demo").glob("*.py") if p.name != "space name.py")


@pytest.mark.parametrize("origin", ["git@github.com:../../escaped.git", "git@github.com:jesdi/./demo.git"])
def test_an_origin_that_climbs_out_of_the_snapshot_dir_is_refused(tmp_path, home, cache, origin):
    repo = build_repo(tmp_path / "demo", origin=origin)
    result = weigh(repo, home, cache=cache)
    assert result.returncode != 0
    assert origin in result.stderr
    assert [p.name for p in home.iterdir()] == []


@pytest.mark.parametrize("args", ["--junitxml=report.xml", "-q --junit-xml report.xml", "--log-file=report.log",
                                  "--basetemp=tmp", "--debug"])
def test_file_writing_pytest_args_leave_the_repo_as_it_was(tmp_path, home, cache, args):
    repo = build_repo(tmp_path / "demo")
    result = weigh(repo, home, f"--pytest-args={args}", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "status", "--porcelain") == ""


@pytest.mark.parametrize("addopts", ["--junitxml=report.xml", "--debug"])
def test_file_writers_configured_by_the_repo_leave_it_as_it_was(tmp_path, home, cache, addopts):
    repo = build_repo(tmp_path / "demo")
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text().replace('"-p fakeclock"', f'"-p fakeclock {addopts}"')
                         + 'log_file = "report.log"\n')
    git(repo, "commit", "-q", "-am", "writers")
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "status", "--porcelain") == ""


def test_a_test_that_writes_where_it_runs_leaves_the_repo_as_it_was(tmp_path, home, cache):
    repo = committed(tmp_path, {"tests/test_leak.py": "from pathlib import Path\n\n\ndef test_leak():\n"
                                                      "    Path('leak.txt').write_text('x')\n"})
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    assert git(repo, "status", "--porcelain") == ""
    assert "tests/test_leak.py::test_leak" in load(snapshots(home, KEY)[0])["tests"]


@pytest.mark.parametrize("target", ["demo/board.py", "out.log"])
def test_a_run_that_changes_the_repo_anyway_is_refused(tmp_path, home, cache, target):
    """Written by absolute path, past the copy: an already-dirty tracked file or an ignored one, both invisible to git status."""
    repo = tmp_path / "demo"
    committed(tmp_path, {".gitignore": "*.log\n", "tests/test_leak.py": f"def test_leak():\n"
                         f"    with open({str(repo / target)!r}, 'a') as f:\n        f.write('# leaked\\n')\n"})
    with (repo / "demo/board.py").open("a") as f:
        f.write("# dirty before the run\n")
    result = weigh(repo, home, cache=cache)
    assert result.returncode != 0
    assert "changed the target repo" in result.stderr and target in result.stderr
    assert snapshots(home, KEY) == []


def test_source_imported_from_the_repo_itself_is_measured(tmp_path, home, cache):
    """An editable install points at the repo, not the copy the run uses: its code still counts, under its repo path."""
    repo = committed(tmp_path, {"src/editable/__init__.py": "def double(n):\n    return 2 * n\n",
                                "tests/test_editable.py": "from editable import double\n\n\ndef test_double():\n"
                                                          "    assert double(2) == 4\n"})
    site = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    (site / "editable.pth").write_text(str(repo / "src") + "\n")  # the venv ignores itself: the tree stays clean
    result = weigh(repo, home, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    test = load(snapshots(home, KEY)[0])["tests"]["tests/test_editable.py::test_double"]
    assert test["coverage"] == {"src/editable/__init__.py": [2]}
