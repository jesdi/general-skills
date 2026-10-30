import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from weigh_rules import load  # noqa: E402
from weigh_score import feedback_values, score  # noqa: E402


def test_tests_over_10s_is_a_percentage_of_all_tests():
    tests = {f"t{i}": {"duration": 20 if i == 0 else 0.1} for i in range(200)}
    assert feedback_values(tests, 30)["tests_over_10s"] == pytest.approx(0.5)


def test_without_mutation_regression_uses_the_partial_weights():
    # coverage 90 scores 90 and 10% hollow tests score 10: .60 * 90 + .40 * 10
    metrics, pillars = score(load(), {"branch_coverage": 90, "hollow_tests": 10})
    assert pillars["regression"] == {"score": pytest.approx(58), "partial": True}


def test_a_pillar_without_partial_weights_keeps_its_weights():
    values = {"total_runtime": 60, "p95_duration": 2, "slowest_decile_share": 40, "tests_over_10s": 2}
    _, pillars = score(load(), values)
    assert pillars["feedback"] == {"score": pytest.approx(.4 * 90 + .3 * 10 + .15 * 90 + .15 * 10), "partial": False}
