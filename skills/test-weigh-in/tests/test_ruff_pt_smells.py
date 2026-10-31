"""Ticket tw/s5-smells/11: ruff's PT rules as maintainability smells, through weigh.py.

Interface the tests pin (the spec leaves the JSON keys open): a test's `smells` list holds the ruff rule code ("PT011")
for each PT violation inside that test; metrics.smelly_tests.value is the percent of all tests with at least one
maintainability smell. The target repo's ruff.toml (select PT001, ignore PT011) must not change which rules run.
"""
import pytest

from fixture_repo import load, snapshots, weigh
from pt_repo import pt_suite

KEY = "github.com/jesdi/demo"
T = "tests/test_pt.py::"


@pytest.fixture(scope="module")
def snap(tmp_path_factory, cache):
    root = tmp_path_factory.mktemp("pt")
    (root / "home").mkdir()
    result = weigh(pt_suite(root / "work" / "demo"), root / "home", cache=cache)
    assert result.returncode == 0, result.stdout + result.stderr
    return load(snapshots(root / "home", KEY)[-1])


def test_pytest_raises_without_match_lists_pt011_and_counts_as_smelly(snap):
    assert snap["inventory"]["tests"] == 40
    assert snap["tests"][T + "test_raises"]["smells"] == ["PT011"]
    assert snap["metrics"]["smelly_tests"]["value"] == pytest.approx(100 / 40)


def ran(snap):
    """The PT rules ran at all: a negative claim below only proves something when test_raises carries PT011."""
    assert "PT011" in snap["tests"][T + "test_raises"]["smells"]


def test_a_style_only_pt_rule_on_a_fixture_decorator_is_no_smell(snap):
    ran(snap)
    assert snap["tests"][T + "test_board"]["smells"] == []
    assert "PT001" not in {s for t in snap["tests"].values() for s in t["smells"]}
    assert snap["metrics"]["smelly_tests"]["value"] == pytest.approx(100 / 40)


def test_a_violation_in_a_helper_outside_any_test_counts_for_no_test(snap):
    ran(snap)
    assert snap["tests"][T + "test_uses_helper"]["smells"] == []
