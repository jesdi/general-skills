"""Ticket tw/s5-smells/10: Eager test, Conditional test logic, Mystery guest, Swallowed exception, Real sleep and
Duplicate body, the partial default tier and the chat summary, through weigh.py.

Interface the tests pin (the spec leaves the JSON keys open), on top of test_smells.py and test_coverage_run.py:
  tests[node].smells   adds the names "Eager test", "Conditional test logic", "Mystery guest", "Swallowed exception",
                       "Real sleep" and "Duplicate body"
  clusters             [[node, ...]]: one list of node ids per duplicate-body cluster
  metrics.duplicate_clusters  {value, score}: percent of all tests that sit in a duplicate-body cluster
Stdout: one line per pillar holding its name, its displayed score and the word "partial" when the pillar is partial;
the inventory line ("<n> tests"); "<n> flaky test(s)"; the node ids of the 3 worst tests of the lowest pillar; the paths
of the snapshot and the dashboard.
"""
import re

import pytest

from fixture_repo import load, snapshots, weigh
from maint_repo import cluster_suite, smell_suite, summary_suite

KEY = "github.com/jesdi/demo"
NODE = re.compile(r"tests/[\w/]+\.py::\w+")


def build(tmp_path_factory, cache, name, make):
    root = tmp_path_factory.mktemp(name)
    (root / "home").mkdir()
    result = weigh(make(root / "work" / "demo"), root / "home", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    path = snapshots(root / "home", KEY)[-1]
    return load(path), result, path, root / "home" / ".test-weigh-in" / KEY / "dashboard.html"


@pytest.fixture(scope="module")
def smells(tmp_path_factory, cache):
    return build(tmp_path_factory, cache, "maint", smell_suite)[0]


@pytest.fixture(scope="module")
def cluster(tmp_path_factory, cache):
    return build(tmp_path_factory, cache, "cluster", cluster_suite)[0]


@pytest.fixture(scope="module")
def summary(tmp_path_factory, cache):
    return build(tmp_path_factory, cache, "summary", summary_suite)


def smelly(snap, smell):
    return {n for n, t in snap["tests"].items() if smell in t.get("smells", [])}


def test_three_act_then_assert_cycles_are_an_eager_test_and_two_are_not(smells):
    assert "smells" in smells["tests"]["tests/test_maint.py::test_flow_2"]
    assert smelly(smells, "Eager test") == {"tests/test_maint.py::test_flow_3"}


def test_a_loop_over_literals_is_conditional_test_logic(smells):
    assert smelly(smells, "Conditional test logic") == {"tests/test_maint.py::test_parse_all"}


def test_a_home_path_is_a_mystery_guest_but_tmp_path_is_not(smells):
    assert "smells" in smells["tests"]["tests/test_guest.py::test_a"]
    assert smelly(smells, "Mystery guest") == {"tests/test_guest.py::test_b"}


def test_a_try_except_that_does_nothing_is_a_swallowed_exception(smells):
    assert smelly(smells, "Swallowed exception") == {"tests/test_maint.py::test_load"}


def test_a_non_zero_sleep_is_a_real_sleep_and_a_zero_sleep_is_not(smells):
    assert "smells" in smells["tests"]["tests/test_sleep.py::test_a"]
    assert smelly(smells, "Real sleep") == {"tests/test_sleep.py::test_b"}


def test_tests_differing_in_a_literal_and_a_name_form_one_duplicate_cluster(cluster):
    intents = [f"tests/test_intent.py::test_intent_{i}" for i in range(1, 5)]
    assert cluster["inventory"]["tests"] == 40
    assert [sorted(c) for c in cluster["clusters"]] == [intents]
    assert smelly(cluster, "Duplicate body") == set(intents)
    assert cluster["metrics"]["duplicate_clusters"]["value"] == pytest.approx(10)


def test_a_run_without_mutation_weights_regression_and_economy_by_their_partial_weights(cluster):
    m, p = cluster["metrics"], cluster["pillars"]
    assert p["regression"]["partial"] and p["economy"]["partial"]
    assert p["regression"]["score"] == pytest.approx(.6 * m["branch_coverage"]["score"] + .4 * m["hollow_tests"]["score"])
    assert p["economy"]["score"] == pytest.approx(.6 * m["no_unique_coverage"]["score"] + .4 * m["duplicate_clusters"]["score"])


def test_the_chat_summary_reports_scores_inventory_flaky_worst_tests_and_paths_without_advice(summary):
    snap, result, snapshot, dashboard = summary
    out = result.stdout
    scores = {k: p["score"] for k, p in snap["pillars"].items()}
    assert min(scores, key=lambda k: float("inf") if scores[k] is None else scores[k]) == "economy"
    assert scores["maintainability"] is not None
    names = {"regression": "Protection against regressions", "refactoring": "Resistance to refactoring",
             "feedback": "Fast feedback", "maintainability": "Maintainability", "economy": "Economy"}
    for key, name in names.items():
        (line,) = [l for l in out.splitlines() if l.startswith(name)]
        assert re.search(r"\b\d{1,2}\b", line)
        if key in ("regression", "economy"):
            assert "partial" in line.lower()
        elif key in ("feedback", "maintainability"):
            assert "partial" not in line.lower()
    assert "40 tests" in out
    assert "1 flaky test" in out
    worst = set(NODE.findall(out)) - {"tests/test_retry.py::test_retry"}
    assert len(worst) == 3
    for node in worst:
        t = snap["tests"][node]
        assert "Duplicate body" in t["smells"] or t["unique_coverage"] == 0
    assert str(snapshot) in out and str(dashboard) in out
    assert not re.search(r"\b(consider|should|recommend|suggest|try|fix|improve|you)\b", out, re.I)
