"""The boundaries of the smell rules (spec requirement 18), through weigh.py; the suite is described in rule_repo.py."""
import json
import re

import pytest

from fixture_repo import load, weigh
from rule_repo import rules_suite

KEY = "github.com/jesdi/demo"
T = "tests/test_rules.py::"
M = "tests/test_maint_rules.py::"


@pytest.fixture(scope="module")
def home(tmp_path_factory, cache):
    root = tmp_path_factory.mktemp("rules")
    (root / "home").mkdir()
    result = weigh(rules_suite(root / "work" / "demo"), root / "home", "--no-open", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    return root / "home" / ".test-weigh-in" / KEY


@pytest.fixture(scope="module")
def snap(home):
    return load(sorted(home.glob("*.json"))[-1])


@pytest.fixture(scope="module")
def page(home):
    """The data the dashboard renders, as weigh.py wrote it into the page."""
    return json.loads(re.search(r"const data = (.*);\n", (home / "dashboard.html").read_text()).group(1))


def worst(page, key):
    return {w["test"]: w["reason"] for w in next(p for p in page["pillars"] if p["key"] == key)["worst"]}


def smelly(snap, smell):
    return {n.removeprefix(T) for n, t in snap["tests"].items() if smell in t["smells"]}


def test_check_and_expect_helpers_are_assertions(snap):
    assert snap["tests"][T + "test_check"]["outcome"] == "passed"
    assert smelly(snap, "No assertion") == {"TestA::test_board", "TestOther::test_inherited"}


def test_call_count_and_call_args_assertions_are_interaction_only_and_one_beside_a_result_check_is_not(snap):
    assert smelly(snap, "Interaction-only") == {"test_call_count", "test_call_args"}


def test_four_monkeypatch_setattr_calls_are_mock_heavy_and_three_are_not(snap):
    assert smelly(snap, "Mock-heavy") == {"test_setattr_4"}


def test_a_private_name_read_through_a_conftest_module_is_not_private_access(snap):
    assert smelly(snap, "Private access") == {"test_module_private", "test_local_private"}


def test_same_named_methods_of_two_classes_are_checked_apart(snap):
    a, b = snap["tests"][T + "TestA::test_board"], snap["tests"][T + "TestB::test_board"]
    assert "No assertion" in a["smells"] and "No assertion" not in b["smells"]


def test_an_inherited_test_is_its_base_class_method_not_a_same_named_one(snap):
    child = snap["tests"][T + "TestChild::test_inherited"]
    assert child["outcome"] == "passed"
    assert "No assertion" not in child["smells"] and child["lines"] == 2


def maint(snap, smell):
    return {n.removeprefix(M) for n, t in snap["tests"].items() if n.startswith(M) and smell in t["smells"]}


def test_a_literal_path_held_in_a_variable_is_a_mystery_guest(snap):
    assert maint(snap, "Mystery guest") == {"test_guest_name", "test_guest_path"}


def test_a_check_helper_closes_an_act_then_assert_cycle(snap):
    assert maint(snap, "Eager test") == {"test_eager_helpers"}


def test_a_handler_that_calls_a_check_helper_does_not_swallow(snap):
    assert snap["tests"][M + "test_swallow_checked"]["outcome"] == "passed"
    assert not maint(snap, "Swallowed exception")


def test_a_long_test_without_smells_ranks_among_the_worst_maintainability_tests(snap, page):
    assert snap["tests"][M + "test_long"]["smells"] == []
    assert worst(page, "maintainability")[M + "test_long"] == "54 lines"


def test_slow_tests_without_a_real_sleep_rank_among_the_worst_feedback_tests(page):
    reasons = worst(page, "feedback")
    assert len(reasons) == 10
    assert all(re.fullmatch(r"\d+\.\d\d s", r) for r in reasons.values())
