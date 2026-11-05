import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weigh_rules import load  # noqa: E402
from weigh_coverage import left_out, valued, value_kind  # noqa: E402
from weigh_dashboard import left_out_line, pillar_status  # noqa: E402
from weigh_score import feedback_values, score  # noqa: E402


def test_tests_over_10s_is_a_percentage_of_all_tests():
    tests = {f"t{i}": {"duration": 20 if i == 0 else 0.1} for i in range(200)}
    assert feedback_values(tests, 30)["tests_over_10s"] == pytest.approx(0.5)


def test_without_mutation_regression_uses_the_partial_weights():
    # coverage 90 scores 90 and 10% hollow tests score 10: .60 * 90 + .40 * 10
    metrics, pillars = score(load(), {"branch_coverage": 90, "hollow_tests": 10})
    assert pillars["regression"] == {"score": pytest.approx(58), "partial": True, "red_suite": False}


def test_a_pillar_without_partial_weights_keeps_its_weights():
    values = {"total_runtime": 60, "p95_duration": 2, "slowest_decile_share": 40, "tests_over_10s": 2}
    _, pillars = score(load(), values)
    assert pillars["feedback"] == {"score": pytest.approx(.4 * 90 + .3 * 10 + .15 * 90 + .15 * 10), "partial": False, "red_suite": False}


def test_a_red_suite_leaves_only_the_pillars_with_a_value_metric_partial_and_says_so():
    values = {"total_runtime": 60, "p95_duration": 2, "slowest_decile_share": 40, "tests_over_10s": 2}
    _, pillars = score(load(), values, red=True)
    assert pillars["regression"]["partial"] and pillars["regression"]["red_suite"]
    assert not pillars["feedback"]["partial"] and not pillars["feedback"]["red_suite"]


def t(outcome, flaky=False):
    return {"outcome": outcome, "flaky": flaky}


def test_left_out_names_each_kind_of_test_that_gives_no_value():
    assert left_out({"a": t("passed")}) == []
    one = left_out({"a": t("skipped"), "b": t("passed")})
    assert left_out_line(one) == "1 test skipped"
    mixed = left_out({"a": t("failed"), "b": t("failed", flaky=True), "c": t("skipped"), "d": t("passed", flaky=True)})
    assert left_out_line(mixed) == "2 tests failing, 1 skipped, 1 flaky"


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped", "error", "xfail"])
@pytest.mark.parametrize("flaky", [False, True])
def test_a_test_that_gives_no_value_is_always_named_left_out(outcome, flaky):
    # a red suite (some test not valued) must have a kind to name, or a gauge would read "0 tests not green"
    tests = {"ok": t("passed"), "x": t(outcome, flaky)}
    kinds = left_out(tests)
    assert sum(n for n, _ in kinds) == len(tests) - len(valued(tests))
    assert bool(kinds) == (value_kind(tests["x"]) != "valued")


def _status(tests, measured, red):
    """pillar_status of one pillar with 4 metrics, 2 of them value metrics, `measured` of them measured."""
    ids = ["branch_coverage", "mutation_score", "a", "b"]
    rules = {"pillars": {"p": {"metrics": {i: {"name": i.replace("_", " ").capitalize()} for i in ids}}}}
    data = {"tests": tests, "metrics": {i: {} for i in ids[:measured]},
            "pillars": {"p": {"partial": red or measured < 4, "red_suite": red}}}
    return pillar_status(data, rules)["p"]


def test_the_status_says_why_a_pillar_is_partial():
    one = {"a": t("skipped"), "b": t("passed")}
    mixed = {"a": t("failed"), "b": t("failed", flaky=True), "c": t("skipped"), "d": t("passed", flaky=True)}
    assert _status(one, 4, True)["reasons"] == ["1 test skipped"]
    assert _status(mixed, 0, True)["reasons"] == ["0 of 4 measured", "4 tests not green"]
    assert _status(mixed, 2, False) == {"head": "partial:", "reasons": ["2 of 4 metrics measured"], "note": None}
    assert _status({"a": t("passed")}, 4, False) == {"head": "all 4 metrics measured", "reasons": [], "note": None}
    assert _status(mixed, 4, True)["note"] == ("Partial: Branch coverage and Mutation score count only the tests that passed "
                                               "in both the plain and the coverage run. Left out: 2 tests failing, 1 skipped, 1 flaky.")
