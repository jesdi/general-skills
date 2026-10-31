"""The boundaries of the smell rules (spec requirement 18), through weigh.py; the suite is described in rule_repo.py."""
import pytest

from fixture_repo import load, snapshots, weigh
from rule_repo import rules_suite

KEY = "github.com/jesdi/demo"
T = "tests/test_rules.py::"


@pytest.fixture(scope="module")
def snap(tmp_path_factory, cache):
    root = tmp_path_factory.mktemp("rules")
    (root / "home").mkdir()
    result = weigh(rules_suite(root / "work" / "demo"), root / "home", "--no-open", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    return load(snapshots(root / "home", KEY)[-1])


def smelly(snap, smell):
    return {n.removeprefix(T) for n, t in snap["tests"].items() if smell in t["smells"]}


def test_check_and_expect_helpers_are_assertions(snap):
    assert snap["tests"][T + "test_check"]["outcome"] == "passed"
    assert smelly(snap, "No assertion") == {"TestA::test_board"}


def test_call_count_and_call_args_assertions_are_interaction_only(snap):
    assert smelly(snap, "Interaction-only") == {"test_call_count", "test_call_args"}


def test_four_monkeypatch_setattr_calls_are_mock_heavy_and_three_are_not(snap):
    assert smelly(snap, "Mock-heavy") == {"test_setattr_4"}


def test_a_private_name_read_through_a_conftest_module_is_not_private_access(snap):
    assert smelly(snap, "Private access") == {"test_module_private"}


def test_same_named_methods_of_two_classes_are_checked_apart(snap):
    a, b = snap["tests"][T + "TestA::test_board"], snap["tests"][T + "TestB::test_board"]
    assert "No assertion" in a["smells"] and "No assertion" not in b["smells"]
