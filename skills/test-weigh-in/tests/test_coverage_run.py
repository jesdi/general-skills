"""Ticket tw/s4-coverage/07: the coverage run, flakiness, red suites, unique coverage and the overlay race.

Snapshot interface the tests pin (the spec leaves the JSON keys open), on top of fixture_repo.py:
  tests[node].coverage        {repo-relative path: sorted covered lines}: the union of the test's passing cases,
                              {} for a failed or skipped test
  tests[node].flaky           true when the outcome differs between the plain and the coverage run
  tests[node].unique_coverage number of lines the test covers that no other passing test covers
  overlaps                    [{"tests": [node, node], "jaccard": float}] for passing pairs with jaccard >= 0.9
  metrics.branch_coverage / metrics.no_unique_coverage   {value, score}; value in percent
Stdout: "<n> flaky test(s)" and "<n> tests are failing" warnings.
"""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import fixture_repo
from fixture_repo import FAKECLOCK, build_repo, git, load, snapshots, weigh
from red_repo import FAILING, SKIPPED, red_suite, skipped_suite

KEY = "github.com/jesdi/demo"


def ok(result):
    assert result.returncode == 0, result.stdout + result.stderr


def commit(repo, files):
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "extra")
    return repo


def snap(home):
    (path,) = snapshots(home, KEY)
    return load(path)


PARSE = "".join(f"def f{k}():\n    return {k}\n" for k in range(14))  # f<k> body is line 2k+2
PARSE_TEST = '''import pytest
from demo import parse


@pytest.mark.parametrize("n", range(14))
def test_parse(n):
    getattr(parse, f"f{n}")()
    assert n != 13
'''


def test_a_parametrized_test_is_one_test_with_the_union_of_its_passing_cases(tmp_path, home, cache):
    repo = build_repo(tmp_path / "demo")
    site = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    (site / "fakeclock.py").write_text(FAKECLOCK.replace("1.125", "0.1"))
    commit(repo, {"demo/parse.py": PARSE, "tests/test_parse.py": PARSE_TEST})
    result = weigh(repo, home, "--pytest-args", "tests/test_parse.py", cache=cache)
    ok(result)
    data = snap(home)
    assert (data["inventory"]["tests"], data["inventory"]["cases"]) == (1, 14)
    test = data["tests"]["tests/test_parse.py::test_parse"]
    assert (test["outcome"], test["cases"]) == ("failed", 14)
    assert test["duration"] == pytest.approx(1.4)
    assert test["coverage"]["demo/parse.py"] == [2 * k + 2 for k in range(13)]


RETRY = 'import sys\n\n\ndef test_retry():\n    assert "coverage" not in sys.modules\n'
# covered by test_solo alone, so its unique coverage keeps no_unique_coverage below 100%
SOLO = {"demo/solo.py": "def solo():\n    return 1\n",
        "tests/test_solo.py": "from demo.solo import solo\n\n\ndef test_solo():\n    assert solo() == 1\n"}


def test_a_test_that_differs_between_the_runs_is_flaky(tmp_path, home, cache):
    repo = commit(build_repo(tmp_path / "demo"), {"tests/test_retry.py": RETRY, **SOLO})
    result = weigh(repo, home, "--pytest-args", "tests/test_retry.py tests/test_board.py tests/test_solo.py", cache=cache)
    ok(result)
    tests = snap(home)["tests"]
    assert tests["tests/test_retry.py::test_retry"]["flaky"] is True
    assert [n for n, t in tests.items() if t.get("flaky")] == ["tests/test_retry.py::test_retry"]
    assert "1 flaky test" in result.stdout
    # failing under coverage, test_retry gives no value: it is out of the count, yet in the base of all tests
    others = [t for n, t in tests.items() if n != "tests/test_retry.py::test_retry"]
    assert snap(home)["metrics"]["no_unique_coverage"]["value"] == pytest.approx(
        100 * sum(not t["unique_coverage"] for t in others) / len(tests))


def test_a_repo_inside_a_folder_named_tests_keeps_its_source_coverage(tmp_path, home, cache):
    repo = commit(build_repo(tmp_path / "tests" / "demo"), SOLO)
    ok(weigh(repo, home, "--pytest-args", "tests/test_solo.py", cache=cache))
    assert snap(home)["tests"]["tests/test_solo.py::test_solo"]["coverage"]["demo/solo.py"] == [2]


def test_a_red_suite_is_measured_without_the_failing_tests_value(tmp_path, home, cache):
    repo = red_suite(tmp_path / "demo")
    result = weigh(repo, home, cache=cache)
    ok(result)
    data = snap(home)
    inv = data["inventory"]
    assert inv["tests"] == 40 and inv["runtime_seconds"] == pytest.approx(45)
    assert inv["test_lines"] == sum(len(p.read_text().splitlines()) for p in (repo / "tests").glob("test_*.py"))
    failed = {n for n, t in data["tests"].items() if t["outcome"] == "failed"}
    assert failed == set(FAILING)
    assert all(data["tests"][n]["coverage"] == {} for n in failed)
    assert not any(set(pair["tests"]) & failed for pair in data["overlaps"])
    assert data["metrics"]["branch_coverage"]["value"] == pytest.approx(50)
    # failing tests give no value: not in the count, yet in the base, a percentage of all 40 tests (req 17)
    passed = [t for t in data["tests"].values() if t["outcome"] == "passed"]
    assert all(data["tests"][n]["unique_coverage"] == 0 for n in failed)
    assert data["metrics"]["no_unique_coverage"]["value"] == pytest.approx(
        100 * sum(not t["unique_coverage"] for t in passed) / 40)
    assert data["pillars"]["regression"]["partial"] and data["pillars"]["economy"]["partial"]
    assert "3 tests are failing" in result.stdout


def test_skipped_tests_cost_without_value(tmp_path, home, cache):
    repo = skipped_suite(tmp_path / "demo")
    result = weigh(repo, home, cache=cache)
    ok(result)
    data = snap(home)
    assert data["inventory"]["tests"] == 40
    assert data["inventory"]["test_lines"] == sum(len(p.read_text().splitlines()) for p in (repo / "tests").glob("test_*.py"))
    for node in SKIPPED:
        assert data["tests"][node]["outcome"] == "skipped"
        assert data["tests"][node]["coverage"] == {}
    assert not any(set(pair["tests"]) & set(SKIPPED) for pair in data["overlaps"])


BOARD = ["def render(n):", "    return str(n)", *[f"# line {n}" for n in range(3, 9)], "def part1():",
         *[f"    _ = {n}" for n in range(10, 21)], "def part2():", *[f"    _ = {n}" for n in range(22, 26)],
         "def part3():", "    _ = 27"]
BOARD_TESTS = '''from demo import board


def _make(name, *parts):
    def run():
        for part in parts:
            getattr(board, part)()
    return run


test_board_empty = _make("test_board_empty", "part1")
test_board_none = _make("test_board_none", "part1", "{other}")
'''
EMPTY, NONE = "tests/test_board_cases.py::test_board_empty", "tests/test_board_cases.py::test_board_none"


def board_run(tmp_path, home, cache, other):
    assert BOARD.index("def part1():") == 8 and BOARD.index("def part2():") == 20
    repo = commit(build_repo(tmp_path / "demo"), {"demo/board.py": "\n".join(BOARD) + "\n",
                                                  "tests/test_board_cases.py": BOARD_TESTS.replace("{other}", other)})
    ok(weigh(repo, home, "--pytest-args", "tests/test_board_cases.py", cache=cache))
    return snap(home)


def test_a_test_covered_by_another_has_no_unique_coverage_and_a_far_pair_is_no_overlap(tmp_path, home, cache):
    data = board_run(tmp_path, home, cache, "part2")  # lines 10-20 against 10-25: Jaccard 11/15
    assert data["tests"][EMPTY]["coverage"]["demo/board.py"] == list(range(10, 21))
    assert data["tests"][NONE]["coverage"]["demo/board.py"] == [*range(10, 21), *range(22, 26)]
    assert data["tests"][EMPTY]["unique_coverage"] == 0
    assert data["tests"][NONE]["unique_coverage"] > 0
    assert data["metrics"]["no_unique_coverage"]["value"] == pytest.approx(50)
    assert data["overlaps"] == []


def test_a_pair_at_jaccard_09_or_more_is_an_overlap(tmp_path, home, cache):
    data = board_run(tmp_path, home, cache, "part3")  # lines 10-20 against 10-20 and 27: Jaccard 11/12
    (pair,) = data["overlaps"]
    assert sorted(pair["tests"]) == sorted([EMPTY, NONE])
    assert pair["jaccard"] == pytest.approx(11 / 12)
    assert data["tests"][EMPTY]["unique_coverage"] == 0


def test_two_first_runs_share_one_complete_overlay(tmp_path, home, cache, monkeypatch):
    fresh = tmp_path / "fresh-cache"
    repos = [build_repo(tmp_path / "work" / name, origin=f"git@github.com:jesdi/{name}.git") for name in ("a", "b")]
    homes = [home / name for name in ("a", "b")]
    for h in homes:
        h.mkdir()
    real = fixture_repo.uv()
    slow = tmp_path / "slow-uv"  # the overlay install takes 3s, so the second run starts while the first is mid-build
    slow.write_text(f'#!/bin/sh\ncase "$1" in pip) sleep 3;; esac\nexec {real} "$@"\n')
    slow.chmod(0o755)
    monkeypatch.setattr(fixture_repo, "uv", lambda: str(slow))

    def staggered(i):
        time.sleep(i)
        return weigh(repos[i], homes[i], cache=fresh)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(staggered, (0, 1)))
    for result in results:
        ok(result)
    for name, h in zip("ab", homes):
        (path,) = snapshots(h, f"github.com/jesdi/{name}")
        tests = load(path)["tests"]
        assert len(tests) == 40 and all(t["coverage"] for t in tests.values())
    (overlay,) = fresh.iterdir()
    assert overlay.name.startswith("overlay-")
    assert (overlay / "weigh_pytest_plugin.py").is_file() and (overlay / "coverage").is_dir()
