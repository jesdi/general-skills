"""Ticket tw/s5-smells/09: No assertion, Interaction-only, Mock-heavy and Private access, through weigh.py.

Interface the tests pin (the spec leaves the JSON keys open): each entry of the snapshot's `tests` has `smells`, a list
of the smell names "No assertion", "Interaction-only", "Mock-heavy", "Private access"; the metrics hollow_tests,
mock_heavy, interaction_only and private_access hold the percentage of all tests (0-100) that have the smell.
"""
import pytest

from fixture_repo import load, snapshots, weigh
from smell_repo import smelly_suite

KEY = "github.com/jesdi/demo"
T = "tests/test_smells.py::"


@pytest.fixture(scope="module")
def snap(tmp_path_factory, cache):
    root = tmp_path_factory.mktemp("smells")
    (root / "home").mkdir()
    repo = smelly_suite(root / "work" / "demo")
    result = weigh(repo, root / "home", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    return load(snapshots(root / "home", KEY)[-1])


def smelly(snap, smell):
    return {n for n, t in snap["tests"].items() if smell in t.get("smells", [])}


def names(*tests):
    return {T + t for t in tests}


def test_a_test_without_assertion_has_the_smell_and_is_hollow(snap):
    assert T + "test_board_renders" in smelly(snap, "No assertion")
    assert snap["metrics"]["hollow_tests"]["value"] == pytest.approx(100 * 4 / len(snap["tests"]))


def test_failing_erroring_and_skipped_tests_are_checked_and_counted(snap):
    assert {T + "test_fail_hollow", T + "test_error_hollow", T + "test_skipped_hollow"} <= smelly(snap, "No assertion")
    assert smelly(snap, "No assertion") == names("test_board_renders", "test_fail_hollow", "test_error_hollow", "test_skipped_hollow")


def test_a_custom_assertion_helper_from_conftest_is_an_assertion(snap):
    assert "smells" in snap["tests"][T + "test_board"]
    assert T + "test_board" not in smelly(snap, "No assertion")


def test_pytest_raises_is_an_assertion(snap):
    assert "smells" in snap["tests"][T + "test_invalid"]
    assert T + "test_invalid" not in smelly(snap, "No assertion")


def test_pytest_warns_and_an_assertion_in_a_nested_helper_are_assertions(snap):
    assert "smells" in snap["tests"][T + "test_warns"]
    assert not {T + "test_warns", T + "test_nested_helper"} & smelly(snap, "No assertion")


def test_more_than_three_mock_constructs_is_mock_heavy(snap):
    assert smelly(snap, "Mock-heavy") == names("test_b_heavy")
    assert snap["metrics"]["mock_heavy"]["value"] == pytest.approx(100 / len(snap["tests"]))


def test_asserting_only_on_a_mock_is_interaction_only(snap):
    assert smelly(snap, "Interaction-only") == names("test_notify")
    assert snap["metrics"]["interaction_only"]["value"] == pytest.approx(100 / len(snap["tests"]))


def test_importing_a_private_name_is_private_access_but_dunder_and_conftest_names_are_not(snap):
    assert smelly(snap, "Private access") == names("test_b_private")
    assert snap["metrics"]["private_access"]["value"] == pytest.approx(100 / len(snap["tests"]))
