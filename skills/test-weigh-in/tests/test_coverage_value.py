"""The coverage run's value data: branches count in uniqueness and overlap, an expected failure keeps its coverage,
and a coverage report that fails stops the run instead of reading as empty coverage."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixture_repo import build_repo, git, load, snapshots, weigh
from weigh_env import SetupError
from weigh_runs import _report

KEY = "github.com/jesdi/demo"
FLAG = "def flag(x):\n    if x:\n        pass\n    return 2\n"


def run(tmp_path, home, cache, files, args):
    repo = build_repo(tmp_path / "demo")
    for rel, text in files.items():
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "extra")
    result = weigh(repo, home, "--pytest-args", args, cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    (path,) = snapshots(home, KEY)
    return load(path)


def test_a_test_that_alone_takes_a_branch_has_unique_coverage_on_the_same_lines(tmp_path, home, cache):
    tests = ("from demo.flag import flag\n\n\ndef test_both():\n    flag(True)\n    flag(False)\n\n\n"
             "def test_true():\n    flag(True)\n")
    data = run(tmp_path, home, cache, {"demo/flag.py": FLAG, "tests/test_flag.py": tests}, "tests/test_flag.py")
    both, true = data["tests"]["tests/test_flag.py::test_both"], data["tests"]["tests/test_flag.py::test_true"]
    assert both["coverage"] == true["coverage"] == {"demo/flag.py": [2, 3, 4]}
    assert both["branches"] == {"demo/flag.py": [[2, 3], [2, 4]]} and true["branches"] == {"demo/flag.py": [[2, 3]]}
    assert (both["unique_coverage"], true["unique_coverage"]) == (1, 0)
    assert data["overlaps"] == []  # 4 of 5 lines and branches shared: Jaccard 0.8


def test_an_expected_failure_keeps_its_coverage(tmp_path, home, cache):
    tests = ("import pytest\nfrom demo.flag import flag\n\n\n@pytest.mark.xfail\ndef test_xfail():\n"
             "    flag(False)\n    assert False\n")
    data = run(tmp_path, home, cache, {"demo/flag.py": FLAG, "tests/test_xfail.py": tests}, "tests/test_xfail.py")
    test = data["tests"]["tests/test_xfail.py::test_xfail"]
    assert test["outcome"] == "passed" and test["coverage"] == {"demo/flag.py": [2, 4]}
    assert test["unique_coverage"] > 0 and data["metrics"]["no_unique_coverage"]["value"] == 0


def test_a_failing_coverage_report_is_an_error_with_its_output(tmp_path):
    def coverage(*args):
        return subprocess.CompletedProcess(args, 1, "", "Couldn't parse demo/x.py")

    with pytest.raises(SetupError, match="coverage json exited 1:\nCouldn't parse demo/x.py"):
        _report(coverage, tmp_path / "rc", tmp_path / "data", tmp_path / "out.json", "(?!)")
